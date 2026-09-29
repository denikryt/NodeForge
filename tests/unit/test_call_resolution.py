"""Pure callable resolution and modifier precedence contracts for Semantic Call IR."""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from NodeForge.compile_time import CompileTimeSnapshot

from NodeForge.call_resolution import (
    CallableEnvironment,
    CallableKind,
    UNRESOLVED,
    resolve_simple_callable,
)
from NodeForge.compiler_identities import library_function_id
from NodeForge.function_instances import extract_function_call_modifiers
from NodeForge.extension_contracts import ExtensionCallableId


pytestmark = pytest.mark.unit


def _call(source):
    """Parse one expression call fixture."""
    return ast.parse(source, mode="eval").body


def _extension_id(name: str) -> ExtensionCallableId:
    """Return one detached system-extension identity for resolver tests."""
    return ExtensionCallableId(("system", "vendor.pkg", "demo"), name)


def _environment(**overrides):
    """Return an immutable resolver namespace with every permanent category represented."""
    record = SimpleNamespace(package_id="vendor.pkg")
    binding = SimpleNamespace(namespace="functions", canonical_name="lib_fn", record=record)
    values = {
        "callable_builtins": {"same", "builtin"},
        "local_functions": {"same": object(), "local": object()},
        "imported_functions": {"same": binding, "lib": binding},
        "extension_system_callables": {"same": _extension_id("same"), "extension": _extension_id("extension")},
    }
    values.update(overrides)
    return CallableEnvironment(**values)


def test_resolution_precedence_uses_permanent_callable_categories():
    env = _environment()
    assert resolve_simple_callable("same", env).kind is CallableKind.BUILTIN
    extension = resolve_simple_callable("extension", env)
    assert extension.kind is CallableKind.EXTENSION
    assert extension.target == _extension_id("extension")
    assert resolve_simple_callable("local", env).kind is CallableKind.LOCAL_FUNCTION
    library = resolve_simple_callable("lib", env)
    assert library.kind is CallableKind.LIBRARY
    assert library.library_function_id == library_function_id("functions", "vendor.pkg", "lib_fn")
    assert resolve_simple_callable("output", env).kind is CallableKind.TOP_LEVEL_ONLY
    assert resolve_simple_callable("store", env).kind is CallableKind.TOP_LEVEL_ONLY
    assert resolve_simple_callable("missing", env) is UNRESOLVED


def test_environment_defensively_freezes_all_namespace_collections():
    builtins = {"builtin"}
    extensions = {"extension": _extension_id("extension")}
    env = CallableEnvironment(builtins, {}, {}, extensions)
    builtins.add("later")
    extensions["later"] = _extension_id("later")
    assert "later" not in env.callable_builtins
    assert "later" not in env.extension_system_callables
    with pytest.raises(TypeError):
        env.extension_system_callables["x"] = _extension_id("x")


def test_modifier_extraction_uses_detached_constants_and_preserves_explicit_false():
    cleaned, modifiers = extract_function_call_modifiers(
        _call("helper(x, __unique__=FLAG)"), "helper", {"FLAG": False}
    )
    assert modifiers.unique is False
    assert modifiers.unique_was_explicit is True
    assert [keyword.arg for keyword in cleaned.keywords] == []


def _analyze_unresolved(source, *, consts=None):
    """Analyze one unresolved call through the production diagnostic ordering."""
    from types import MappingProxyType
    from NodeForge.semantic_analysis import SemanticEnvironment, analyze_expression, build_semantic_constant_snapshot

    constants, detached = build_semantic_constant_snapshot(CompileTimeSnapshot(consts or {}))
    environment = SemanticEnvironment(
        MappingProxyType({}),
        constants,
        detached,
        MappingProxyType({}),
        CallableEnvironment(frozenset(), {}, {}, {}),
    )
    return analyze_expression(_call(source), environment)


@pytest.mark.parametrize(
    ("source", "message"),
    [
        ("foo(__unique__=True)", "foo() does not support __unique__"),
        ("foo(__unique__=False)", "foo() does not support __unique__"),
        ("foo(x=1)", "Keyword arguments are only supported for builtins, library functions, local functions or local backend helpers; foo is not registered as one"),
        ("foo()", "Unsupported function: foo"),
        ("foo(__unique__=1)", "foo() __unique__ must be a compile-time Bool"),
        ("foo(__unique__=runtime_bool)", "foo() __unique__ must be a compile-time Bool"),
        ("foo(__unique__=True, __unique__=False)", "foo() got duplicate __unique__"),
    ],
)
def test_unresolved_call_diagnostics_preserve_legacy_precedence(source, message):
    with pytest.raises(Exception) as exc_info:
        _analyze_unresolved(source)
    assert str(exc_info.value) == message


def test_unresolved_valid_bool_constant_unique_preserves_unsupported_modifier_diagnostic():
    with pytest.raises(Exception) as exc_info:
        _analyze_unresolved("foo(__unique__=FLAG_CONST)", consts={"FLAG_CONST": True})
    assert str(exc_info.value) == "foo() does not support __unique__"


def test_top_level_only_and_non_simple_call_diagnostics_are_preserved():
    with pytest.raises(Exception, match=r"output\(\) is only supported as a top-level call"):
        _analyze_unresolved("output()")
    with pytest.raises(Exception, match="Only simple function calls are supported"):
        _analyze_unresolved("factory()()")

@pytest.mark.parametrize(
    ("first_kind", "second_kind", "expected"),
    [
        ("builtin", "extension", CallableKind.BUILTIN),
        ("builtin", "local", CallableKind.BUILTIN),
        ("builtin", "library", CallableKind.BUILTIN),
        ("extension", "local", CallableKind.EXTENSION),
        ("extension", "library", CallableKind.EXTENSION),
        ("local", "library", CallableKind.LOCAL_FUNCTION),
    ],
)
def test_every_pairwise_callable_collision_uses_permanent_precedence(first_kind, second_kind, expected):
    """Every permanent pairwise namespace collision selects the earlier category."""
    record = SimpleNamespace(package_id="pkg", namespace="functions", name="same")
    binding = SimpleNamespace(namespace="functions", canonical_name="same", record=record)
    namespaces = {
        "builtin": {"callable_builtins": {"same"}},
        "extension": {"extension_system_callables": {"same": _extension_id("same")}},
        "local": {"local_functions": {"same": object()}},
        "library": {"imported_functions": {"same": binding}},
    }
    values = {
        "callable_builtins": set(),
        "local_functions": {},
        "imported_functions": {},
        "extension_system_callables": {},
    }
    for category in (first_kind, second_kind):
        for field, payload in namespaces[category].items():
            if isinstance(values[field], set):
                values[field].update(payload)
            else:
                values[field].update(payload)
    assert resolve_simple_callable("same", CallableEnvironment(**values)).kind is expected


def test_resolution_preserves_extension_and_library_snapshot_identity():
    """Extension/library targets retain only canonical snapshot identities."""
    extension_id = _extension_id("extension")
    record = SimpleNamespace(package_id="vendor.pkg", namespace="functions", name="entry")
    binding = SimpleNamespace(namespace="functions", canonical_name="entry", record=record)
    env = CallableEnvironment(
        frozenset(),
        {},
        {"alias": binding},
        {"extension": extension_id},
    )
    resolved_extension = resolve_simple_callable("extension", env)
    resolved_library = resolve_simple_callable("alias", env)
    assert resolved_extension.kind is CallableKind.EXTENSION
    assert resolved_extension.target is extension_id
    assert resolved_library.target is binding
    assert resolved_library.library_function_id == library_function_id("functions", "vendor.pkg", "entry")


def test_resolver_module_has_no_live_catalog_package_or_system_discovery_imports():
    """Pure resolution depends only on the immutable environment supplied by the caller."""
    from pathlib import Path
    import NodeForge.call_resolution as module

    source = Path(module.__file__).read_text(encoding="utf-8")
    assert "from .library import" not in source
    assert "from .packages import" not in source
    assert "from .systems.registry import" not in source
    assert "import bpy" not in source
    assert "from .values import" not in source
    assert "from .values import Value" not in source


def test_object_info_method_syntax_is_classified_by_semantic_analysis():
    """Object.info is represented as a resolved compiler-owned call, not dynamic dispatch."""
    from types import MappingProxyType
    from NodeForge.compiler_identities import BindingId
    from NodeForge.constants import TYPE_OBJECT
    from NodeForge.semantic_analysis import (
        RuntimeBindingSymbol,
        SemanticEnvironment,
        analyze_expression,
        build_semantic_constant_snapshot,
    )

    from NodeForge.semantic_values import ObjectInfoState, ObjectSemanticId, ObjectSemanticSnapshot

    constants, detached = build_semantic_constant_snapshot(CompileTimeSnapshot({}))
    binding_id = BindingId("object-info", 0)
    object_id = ObjectSemanticId(0)
    env = SemanticEnvironment(
        MappingProxyType({"obj": RuntimeBindingSymbol(binding_id, TYPE_OBJECT)}),
        constants,
        detached,
        MappingProxyType({}),
        CallableEnvironment(frozenset(), {}, {}, {}),
        object_semantics=ObjectSemanticSnapshot(
            {binding_id: object_id},
            {object_id: ObjectInfoState()},
            1,
        ),
    )
    expr = _call("obj.info(transform_space='RELATIVE')")
    analysis = analyze_expression(expr, env)
    assert analysis is not None
    assert analysis.facts[expr].analyzed_call.target.kind is CallableKind.OBJECT_INFO
