"""Pure callable resolution and modifier precedence contracts for Semantic Call IR."""

from __future__ import annotations

import ast
from types import SimpleNamespace

import pytest

from NodeForge.semantic.compile_time import CompileTimeSnapshot

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


def _extension_id(name: str, package_id: str = "vendor.pkg") -> ExtensionCallableId:
    """Return one detached package-extension identity for resolver tests."""
    return ExtensionCallableId(("system", package_id, "demo"), name)


def _package_binding(alias: str, exports: dict[str, ExtensionCallableId], *, package_id="vendor.pkg"):
    """Return one exact source package binding for pure resolver tests."""
    from NodeForge.resolved_environment import PackageCallableExport, ResolvedPackageNamespace
    from NodeForge.semantic_group import PackageNamespaceBinding

    namespace = ResolvedPackageNamespace(
        package_id=package_id,
        import_name=package_id.rsplit(".", 1)[-1],
        package_name=package_id,
        package_version="1.0",
        exports={
            name: PackageCallableExport(package_id, name, extension_callable_id=callable_id)
            for name, callable_id in exports.items()
        },
    )
    return PackageNamespaceBinding(alias, namespace)


def _environment(**overrides):
    """Return an immutable resolver namespace with every permanent category represented."""
    record = SimpleNamespace(package_id="vendor.pkg", namespace="functions", name="lib_fn")
    binding = SimpleNamespace(namespace="functions", canonical_name="lib_fn", record=record)
    package = _package_binding(
        "pkg",
        {"same": _extension_id("same"), "extension": _extension_id("extension")},
    )
    values = {
        "callable_builtins": {"same", "builtin"},
        "local_functions": {"local": object()},
        "imported_functions": {"lib": binding},
        "package_namespaces": {"pkg": package},
    }
    values.update(overrides)
    return CallableEnvironment(**values)


def test_core_bare_call_is_reserved_while_package_member_remains_qualified():
    env = _environment()
    assert resolve_simple_callable("same", env).kind is CallableKind.BUILTIN
    assert resolve_simple_callable("extension", env).kind is CallableKind.EXTENSION
    assert resolve_simple_callable("local", env).kind is CallableKind.LOCAL_FUNCTION
    library = resolve_simple_callable("lib", env)
    assert library.kind is CallableKind.LIBRARY
    assert library.library_function_id == library_function_id("functions", "vendor.pkg", "lib_fn")
    assert resolve_simple_callable("output", env).kind is CallableKind.TOP_LEVEL_ONLY
    assert resolve_simple_callable("store", env).kind is CallableKind.TOP_LEVEL_ONLY
    assert resolve_simple_callable("missing", env) is UNRESOLVED

    from NodeForge.call_resolution import resolve_package_callable

    qualified = resolve_package_callable("pkg", "same", env)
    assert qualified.kind is CallableKind.EXTENSION
    assert qualified.target == _extension_id("same")


def test_environment_defensively_freezes_package_namespace_bindings():
    builtins = {"builtin"}
    package = _package_binding("pkg", {"extension": _extension_id("extension")})
    namespaces = {"pkg": package}
    env = CallableEnvironment(builtins, {}, {}, namespaces)
    builtins.add("later")
    namespaces["later"] = package
    assert "later" not in env.callable_builtins
    assert "later" not in env.package_namespaces
    with pytest.raises(TypeError):
        env.package_namespaces["x"] = package


def test_two_package_candidates_are_ambiguous_but_qualified_targets_remain_distinct():
    from NodeForge.call_resolution import resolve_package_callable

    a_id = _extension_id("foo", "vendor.a")
    b_id = _extension_id("foo", "vendor.b")
    env = CallableEnvironment(
        frozenset(),
        {},
        {},
        {
            "a": _package_binding("a", {"foo": a_id}, package_id="vendor.a"),
            "b": _package_binding("b", {"foo": b_id}, package_id="vendor.b"),
        },
    )
    with pytest.raises(Exception, match=r"Ambiguous callable 'foo'.*a\.foo.*b\.foo"):
        resolve_simple_callable("foo", env)
    assert resolve_package_callable("a", "foo", env).target == a_id
    assert resolve_package_callable("b", "foo", env).target == b_id


def test_qualified_missing_member_has_no_fallback_to_core_or_other_package():
    from NodeForge.call_resolution import resolve_package_callable

    env = CallableEnvironment(
        {"points"},
        {},
        {},
        {"pkg": _package_binding("pkg", {"other": _extension_id("other")})},
    )
    with pytest.raises(Exception, match="has no callable member 'points'"):
        resolve_package_callable("pkg", "points", env)

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
    with pytest.raises(Exception, match="Only simple or package-qualified function calls are supported"):
        _analyze_unresolved("factory()()")

def test_local_or_explicit_import_collision_with_package_is_ambiguous():
    package = _package_binding("pkg", {"foo": _extension_id("foo")})
    record = SimpleNamespace(package_id="vendor.local", namespace="functions", name="foo")
    binding = SimpleNamespace(namespace="functions", canonical_name="foo", record=record)

    with pytest.raises(Exception, match="Ambiguous callable 'foo'"):
        resolve_simple_callable("foo", CallableEnvironment(frozenset(), {"foo": object()}, {}, {"pkg": package}))
    with pytest.raises(Exception, match="Ambiguous callable 'foo'"):
        resolve_simple_callable("foo", CallableEnvironment(frozenset(), {}, {"foo": binding}, {"pkg": package}))


def test_different_source_aliases_preserve_canonical_package_target():
    from NodeForge.call_resolution import resolve_package_callable

    target = _extension_id("sin")
    first = CallableEnvironment(frozenset(), {}, {}, {"math": _package_binding("math", {"sin": target})})
    second = CallableEnvironment(frozenset(), {}, {}, {"m": _package_binding("m", {"sin": target})})
    assert resolve_package_callable("math", "sin", first).target == target
    assert resolve_package_callable("m", "sin", second).target == target


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
