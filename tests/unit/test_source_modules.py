"""Regression coverage for source snapshot parsing and declaration collection."""

import ast

import pytest

from NodeForge.compiler_identities import ModuleId, SourceDefinitionRef
from NodeForge.errors import CompileError
from NodeForge.semantic.source_modules import parse_source_module


def _module() -> ModuleId:
    """Return a non-file context without implying a Local source path."""
    return ModuleId("ROOT_TEXT", "test/text-block")


def test_mixed_module_preserves_exact_revision_and_original_positions():
    """Declarations are available independently of their textual placement."""
    text = (
        "\n\nfrom local import geometry\n"
        "before = move(1)\n"
        "def move(value: Float) -> Float:\n"
        "    return value\n"
        "after = move(2)\n"
    )
    module = parse_source_module(text, _module())
    assert module.source is text
    assert len(module.tree.body) == 4
    assert [item.lineno for item in module.tree.body] == [3, 4, 5, 7]
    assert [item.lineno for item in module.statements] == [4, 7]
    assert module.definitions["move"].lineno == 5
    assert module.definition_ref("move") == SourceDefinitionRef(_module(), "move")
    assert len(module.imports) == 1
    assert len(module.compile_time_candidates) == 2
    assert module.definitions["move"] is module.tree.body[2]


def test_snapshot_rejects_duplicate_declaration_at_second_source_location():
    """A duplicate function cannot silently overwrite a prior declaration."""
    with pytest.raises(CompileError, match=r"Duplicate function 'a' at line 4, column 1"):
        parse_source_module("\ndef a():\n    return 1\ndef a():\n    return 2\n", _module())


def test_snapshot_syntax_error_preserves_original_line_and_column():
    """Parse failures retain offsets even when source begins with whitespace."""
    with pytest.raises(CompileError, match=r"line 3"):
        parse_source_module("\n\nx = (\n", _module())


def test_module_identity_is_not_based_on_content_or_import_alias():
    """A revision change retains definition identity; a module change does not."""
    a = parse_source_module("def fn():\n    return 1\n", _module())
    b = parse_source_module("def fn():\n    return 2\n", _module())
    c = parse_source_module("def fn():\n    return 1\n", ModuleId("ROOT_TEXT", "other/text"))
    assert a.definition_ref("fn") == b.definition_ref("fn")
    assert a.definition_ref("fn") != c.definition_ref("fn")
    assert a.source != b.source
    with pytest.raises(CompileError, match="Unknown function"):
        a.definition_ref("missing")


@pytest.mark.parametrize("kind,origin,package", [
    ("UNKNOWN", "foo", ""), ("LOCAL", "", ""),
    ("PACKAGE", "shapes.nf", ""), ("ROOT_TEXT", "text", "pkg"),
])
def test_module_identity_rejects_ambiguous_sources(kind, origin, package):
    """Invalid identities cannot reach definition lookup or physical ownership."""
    with pytest.raises(ValueError):
        ModuleId(kind, origin, package)


def test_package_and_local_ids_are_distinct():
    """Package relative source names never collide with Local origins."""
    assert ModuleId("PACKAGE", "functions/geometry.nf", "pkg:shapes") != ModuleId(
        "LOCAL", "/tmp/geometry.nf"
    )


def test_source_module_reuse_does_not_reparse_or_mutate_source(monkeypatch):
    """A validated snapshot is reused through semantic preparation."""
    from NodeForge.semantic import group
    from NodeForge.compiler_identities import GroupCompilationIdentity

    text = 'x = 1\noutput("x", x)'
    snapshot = parse_source_module(text, _module())
    # Snapshot matching must happen before the compiler asks for environment state.
    with pytest.raises(CompileError, match="does not match"):
        group.analyze_group_source(
            text + "\n", compilation_identity=GroupCompilationIdentity(None, "a", "b", "c"),
            resolved_environment=None, source_module=snapshot,
        )
    assert isinstance(snapshot.tree, ast.Module)
    assert snapshot.source == text


def test_declaration_resolution_precedes_runtime_statements_and_uses_module_refs():
    """A forward call resolves to its source identity, not its textual position."""
    from NodeForge.semantic.source_modules import resolve_source_module
    from NodeForge.semantic.call_resolution import CallableEnvironment, resolve_simple_callable

    source = 'before = move(2)\ndef move(value: Float) -> Float:\n    return value\n'
    snapshot = parse_source_module(source, _module())
    resolved = resolve_source_module(snapshot)
    env = CallableEnvironment(
        frozenset(), snapshot.definitions, {}, local_definition_refs=resolved.definition_refs
    )
    assert resolve_simple_callable('move', env).target == snapshot.definition_ref('move')
    assert resolved.has_executable_statements
    assert [stmt.lineno for stmt in resolved.preprocessed.statements] == [1]


def test_compile_time_only_module_has_no_runtime_statements():
    """Erased compile-time expressions alone do not trigger level-0 composition."""
    from NodeForge.semantic.source_modules import resolve_source_module

    snapshot = parse_source_module('names = ["a", "b"]\ndef build():\n    return 1', _module())
    resolved = resolve_source_module(snapshot)
    assert resolved.definition_refs['build'] == snapshot.definition_ref('build')
    assert not resolved.has_executable_statements
    assert not resolved.preprocessed.statements


def test_residual_runtime_assignments_remain_executable():
    """Runtime operations are not mistaken for compile-time-only declarations."""
    from NodeForge.semantic.source_modules import resolve_source_module

    snapshot = parse_source_module('value = cube(1)\ndef build():\n    return 1', _module())
    assert resolve_source_module(snapshot).has_executable_statements


def test_inherited_definitions_may_not_replace_module_declarations():
    """Inherited namespaces cannot silently override a new declared function."""
    from NodeForge.semantic.source_modules import resolve_source_module

    snapshot = parse_source_module('def same():\n    return 1\n', _module())
    with pytest.raises(CompileError, match=r"Duplicate function 'same' at line 1"):
        resolve_source_module(snapshot, inherited_definitions={'same': object()})


def test_resolver_cannot_publish_refs_for_wrong_module():
    """Resolved identity metadata rejects mixed-module snapshots before use."""
    from NodeForge.semantic.source_modules import ResolvedSourceModule, resolve_source_module

    snapshot = parse_source_module('def fn():\n    return 1', _module())
    result = resolve_source_module(snapshot)
    with pytest.raises(ValueError, match='source module identity'):
        ResolvedSourceModule(snapshot, {'fn': SourceDefinitionRef(ModuleId('ROOT_TEXT','other'), 'fn')}, result.preprocessed)


def test_forward_call_uses_one_frontend_resolution_path():
    """The semantic compiler accepts declaration order without a second parser."""
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
    from NodeForge.semantic.group import analyze_group_source

    source = 'result = move(2)\ndef move(value: Float):\n    return value\noutput("Result", result)'
    environment = ResolvedEnvironment({
        'functions': ResolvedCatalog('functions', {}),
        'examples': ResolvedCatalog('examples', {}),
        'local': ResolvedCatalog('local', {}),
    })
    snapshot = parse_source_module(source, _module())
    result = analyze_group_source(
        source,
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/forward', 'root-text', 'root-text'),
        resolved_environment=environment,
        source_module=snapshot,
        source_callable_session=__import__('NodeForge.semantic.source_callable_session', fromlist=['SourceCallableSession']).SourceCallableSession(resolved_environment=environment),
    )
    assert result.source == source
    assert result.interface.outputs[0].display_name == 'Result'


def _empty_environment():
    """Return the minimal complete resolved environment for frontend tests."""
    from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment

    return ResolvedEnvironment({
        'functions': ResolvedCatalog('functions', {}),
        'examples': ResolvedCatalog('examples', {}),
        'local': ResolvedCatalog('local', {}),
    })


def test_incompatible_function_binding_fails_before_runtime_lowering():
    """A declaration may not be replaced by a later source value binding."""
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.compiler_identities import GroupCompilationIdentity

    source = 'result = good(1)\ndef good(value: Float):\n    return value\ngood = 2\noutput(result)'
    with pytest.raises(CompileError, match=r'Cannot assign to good'):
        analyze_group_source(
            source,
            compilation_identity=GroupCompilationIdentity(None, 's', 'd', 'c'),
            resolved_environment=_empty_environment(),
        )


def test_unresolved_forward_call_does_not_create_a_false_definition_ref():
    """Unknown calls must not be inferred as declarations from their spelling."""
    from NodeForge.semantic.call_resolution import CallableEnvironment, resolve_simple_callable, UNRESOLVED
    from NodeForge.semantic.source_modules import resolve_source_module

    snapshot = parse_source_module('result = missing(1)\ndef good():\n    return 1', _module())
    resolution = resolve_source_module(snapshot)
    env = CallableEnvironment(frozenset(), snapshot.definitions, {}, local_definition_refs=resolution.definition_refs)
    assert resolve_simple_callable('missing', env) is UNRESOLVED
