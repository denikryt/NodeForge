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
