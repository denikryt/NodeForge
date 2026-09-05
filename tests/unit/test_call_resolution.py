"""Pure callable resolution and modifier precedence contracts for Semantic Call IR."""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from NodeForge.call_resolution import (
    CallableEnvironment,
    CallableKind,
    UNRESOLVED,
    resolve_simple_callable,
)
from NodeForge.compiler_identities import library_function_id
from NodeForge.function_instances import extract_function_call_modifiers


pytestmark = pytest.mark.unit


def _call(source):
    """Parse one expression call fixture."""
    return ast.parse(source, mode="eval").body


def _environment(**overrides):
    """Return an immutable resolver namespace with every category represented."""
    record = SimpleNamespace(package_id="vendor.pkg")
    binding = SimpleNamespace(namespace="functions", canonical_name="lib_fn", record=record)
    values = {
        "callable_builtins": {"same", "builtin"},
        "system_constructors": {"same": object(), "system": object()},
        "local_functions": {"same": object(), "local": object()},
        "backend_helper_names": {"same", "helper"},
        "imported_functions": {"same": binding, "lib": binding},
    }
    values.update(overrides)
    return CallableEnvironment(**values)


def test_resolution_precedence_matches_legacy_dispatch_exactly():
    env = _environment()
    assert resolve_simple_callable("same", env).kind is CallableKind.BUILTIN
    assert resolve_simple_callable("system", env).kind is CallableKind.SYSTEM
    assert resolve_simple_callable("local", env).kind is CallableKind.LOCAL_FUNCTION
    assert resolve_simple_callable("helper", env).kind is CallableKind.BACKEND_HELPER
    library = resolve_simple_callable("lib", env)
    assert library.kind is CallableKind.LIBRARY
    assert library.library_function_id == library_function_id("functions", "vendor.pkg", "lib_fn")
    assert resolve_simple_callable("output", env).kind is CallableKind.TOP_LEVEL_ONLY
    assert resolve_simple_callable("store", env).kind is CallableKind.TOP_LEVEL_ONLY
    assert resolve_simple_callable("missing", env) is UNRESOLVED


def test_environment_defensively_freezes_all_namespace_collections():
    builtins = {"builtin"}
    systems = {"system": object()}
    env = CallableEnvironment(builtins, systems, {}, {"helper"}, {})
    builtins.add("later")
    systems["later"] = object()
    assert "later" not in env.callable_builtins
    assert "later" not in env.system_constructors
    with pytest.raises(TypeError):
        env.system_constructors["x"] = object()


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

    constants, detached = build_semantic_constant_snapshot(consts or {})
    environment = SemanticEnvironment(
        MappingProxyType({}),
        frozenset(),
        constants,
        detached,
        MappingProxyType({}),
        CallableEnvironment(frozenset(), {}, {}, frozenset(), {}),
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
        ("builtin", "system", CallableKind.BUILTIN),
        ("builtin", "local", CallableKind.BUILTIN),
        ("builtin", "helper", CallableKind.BUILTIN),
        ("builtin", "library", CallableKind.BUILTIN),
        ("system", "local", CallableKind.SYSTEM),
        ("system", "helper", CallableKind.SYSTEM),
        ("system", "library", CallableKind.SYSTEM),
        ("local", "helper", CallableKind.LOCAL_FUNCTION),
        ("local", "library", CallableKind.LOCAL_FUNCTION),
        ("helper", "library", CallableKind.BACKEND_HELPER),
    ],
)
def test_every_pairwise_callable_collision_uses_legacy_precedence(first_kind, second_kind, expected):
    """Every constructible pairwise namespace collision selects the earlier legacy category."""
    record = SimpleNamespace(package_id="pkg", namespace="functions", name="same")
    binding = SimpleNamespace(namespace="functions", canonical_name="same", record=record)
    system = object()
    local = object()
    namespaces = {
        "builtin": {"callable_builtins": {"same"}},
        "system": {"system_constructors": {"same": system}},
        "local": {"local_functions": {"same": local}},
        "helper": {"backend_helper_names": {"same"}},
        "library": {"imported_functions": {"same": binding}},
    }
    values = {
        "callable_builtins": set(),
        "system_constructors": {},
        "local_functions": {},
        "backend_helper_names": set(),
        "imported_functions": {},
    }
    for category in (first_kind, second_kind):
        for field, payload in namespaces[category].items():
            if isinstance(values[field], set):
                values[field].update(payload)
            else:
                values[field].update(payload)
    assert resolve_simple_callable("same", CallableEnvironment(**values)).kind is expected


def test_resolution_preserves_exact_snapshot_objects_without_backend_helper_payloads():
    """System/library targets keep snapshot identity while helpers stay name-only records."""
    system = object()
    record = SimpleNamespace(package_id="vendor.pkg", namespace="functions", name="entry")
    binding = SimpleNamespace(namespace="functions", canonical_name="entry", record=record)
    env = CallableEnvironment(
        frozenset(),
        {"sys": system},
        {},
        {"helper"},
        {"alias": binding},
    )
    resolved_system = resolve_simple_callable("sys", env)
    resolved_library = resolve_simple_callable("alias", env)
    resolved_helper = resolve_simple_callable("helper", env)
    assert resolved_system.target is system
    assert resolved_library.target is binding
    assert resolved_library.library_function_id == library_function_id("functions", "vendor.pkg", "entry")
    assert resolved_helper.target == "helper"
    assert not callable(resolved_helper.target)


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

    constants, detached = build_semantic_constant_snapshot({})
    env = SemanticEnvironment(
        MappingProxyType({"obj": RuntimeBindingSymbol(BindingId("object-info", 0), TYPE_OBJECT)}),
        frozenset(),
        constants,
        detached,
        MappingProxyType({}),
        CallableEnvironment(frozenset(), {}, {}, frozenset(), {}),
    )
    expr = _call("obj.info(transform_space='RELATIVE')")
    analysis = analyze_expression(expr, env)
    assert analysis is not None
    assert analysis.facts[expr].analyzed_call.target.kind is CallableKind.OBJECT_INFO
