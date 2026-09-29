"""Regression gates for the Stage-36 physical legacy-compiler removal."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from NodeForge.builtin_call_semantics import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    IR_CAPABLE_BUILTIN_NAMES,
)
from NodeForge.builtins import registry as builtin_registry
from NodeForge.call_resolution import CallableEnvironment
from NodeForge.compile_time import CompileTimeSnapshot
from NodeForge.compiler_identities import BindingId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic_body import lower_basic_body
from NodeForge.semantic_ir import IRIf, IRRepeat
from NodeForge.values import ObjectValue, Value


ROOT = Path(__file__).resolve().parents[2]
_DELETED_COMPILER_MODULES = (
    "expression_compiler.py",
    "statement_compiler.py",
    "runtime.py",
    "statements.py",
    "geometry_builder.py",
)
_DELETED_BUILTIN_MODULES = (
    "builtins/vector.py",
    "builtins/fields.py",
    "builtins/geometry.py",
    "builtins/geometry_builder.py",
    "builtins/instancing.py",
    "builtins/io.py",
    "builtins/node_wrappers.py",
    "builtins/runtime.py",
)


def _callables() -> CallableEnvironment:
    """Return the permanent core callable inventory for pure body tests."""
    return CallableEnvironment(
        frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES),
        {},
        {},
        {},
    )


def _lower(source: str, *, bindings=None):
    """Lower one body through the permanent Semantic Body boundary."""
    return lower_basic_body(
        ast.parse(source, mode="exec").body,
        initial_runtime_bindings=bindings or {},
        initial_compile_time=CompileTimeSnapshot({}),
        reserved_name_labels={},
        callable_environment=_callables(),
        owner_scope="stage36-test",
    )


def _production_python_sources():
    """Yield checked-in production Python sources, excluding tests and caches."""
    for path in ROOT.rglob("*.py"):
        if "tests" in path.parts or "__pycache__" in path.parts or ".git" in path.parts:
            continue
        yield path


# I1 — one supported production compilation route.
def test_i1_positive_compiler_facade_routes_through_semantic_preparation():
    """The public group-build facade prepares typed semantic compilation before backend publication."""
    source = (ROOT / "compiler.py").read_text(encoding="utf-8")
    assert "analyze_group_source(" in source
    assert "BlenderGroupBackend" in source
    assert "prepared_compilation" in source


def test_i1_negative_old_ast_executor_modules_are_physically_absent():
    """No second source-AST compiler remains importable or present on disk."""
    for relative in _DELETED_COMPILER_MODULES:
        assert not (ROOT / relative).exists(), relative
    for module_name in (
        "NodeForge.expression_compiler",
        "NodeForge.statement_compiler",
        "NodeForge.runtime",
        "NodeForge.statements",
        "NodeForge.geometry_builder",
    ):
        assert importlib.util.find_spec(module_name) is None, module_name


# I2 — live physical helpers belong to permanent backend owners.
def test_i2_positive_repeat_and_store_helpers_live_with_permanent_backend_owners():
    """Repeat-zone and Store Named Attribute realization live in their final backend modules."""
    repeat_source = (ROOT / "blender_ir_lowering.py").read_text(encoding="utf-8")
    geometry_source = (ROOT / "geometry.py").read_text(encoding="utf-8")
    assert "def _create_repeat_zone(" in repeat_source
    assert "def _repeat_item_type_for_nf_type(" in repeat_source
    assert "def _store_named_attribute(" in geometry_source
    assert "def _attribute_domain(" in geometry_source


def test_i2_negative_permanent_backend_does_not_import_deleted_mixed_owners():
    """Permanent backend modules do not reach back into the deleted mixed AST/compiler modules."""
    for relative in ("blender_ir_lowering.py", "geometry.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        for token in (
            "from .runtime",
            "from .statements",
            "from .geometry_builder",
            "expression_compiler",
            "statement_compiler",
        ):
            assert token not in source, (relative, token)


def test_i2_relocated_backend_helpers_retain_live_dependencies():
    """Stage-36 backend ownership moves keep the dependencies their live helpers still use."""
    from NodeForge import blender_ir_lowering
    from NodeForge.builtins import raw_nodes

    assert callable(blender_ir_lowering._new_node)
    assert raw_nodes._normalize_json_value((1.0, 2.0, 3.0)) == [1.0, 2.0, 3.0]


# I3 — builtin namespace is declarative, not executable.
def test_i3_positive_builtin_registry_exactly_matches_permanent_semantic_inventory():
    """Reserved/callable names derive only from permanent semantic builtin inventories."""
    expected_callable = frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES)
    assert builtin_registry.CALLABLE_BUILTIN_NAMES == expected_callable
    assert builtin_registry.BUILTIN_NAMES == expected_callable | {"range", "repeat_range"}
    assert builtin_registry.has_callable_builtin("cube")
    assert builtin_registry.has_builtin("repeat_range")


def test_i3_negative_builtin_registry_exposes_no_ast_execution_dispatch():
    """The builtin namespace cannot execute source AST or retain handler tables."""
    assert not hasattr(builtin_registry, "compile_call")
    assert not hasattr(builtin_registry, "_HANDLERS")
    source = (ROOT / "builtins" / "registry.py").read_text(encoding="utf-8")
    assert "compile_call" not in source
    assert "_HANDLERS" not in source


# I4 — backend value carriers are minimal and do not own frontend structure.
def test_i4_positive_value_and_object_value_remain_backend_socket_carriers():
    """Ordinary backend values retain exactly typed socket transport and Object Info cache state."""
    socket = object()
    value = Value(socket, NFType.FLOAT)
    obj = ObjectValue(socket)
    assert value.socket is socket and value.typ is NFType.FLOAT
    assert obj.socket is socket and obj.typ is NFType.OBJECT
    assert set(vars(obj)) == {"socket", "typ", "_object_info_outputs", "_object_info_cache_config"}


def test_i4_negative_legacy_structural_backend_containers_are_absent():
    """Tuple/named-result frontend structure cannot leak back through retired backend containers."""
    import NodeForge.values as values

    assert not hasattr(values, "TupleValue")
    assert not hasattr(values, "NodeResult")
    assert not hasattr(values, "CompileTimeObject")


# I5 — the permanent semantic boundary is total: result or controlled error.
def test_i5_positive_supported_body_returns_typed_permanent_ir_without_retry_signal():
    """A supported body returns typed Semantic Body IR directly."""
    result = _lower("x = 1\noutput(x)")
    assert result.body.statements
    assert result.body.__class__.__name__ == "IRBody"


def test_i5_negative_invalid_body_raises_instead_of_returning_legacy_retry_sentinel():
    """Unsupported source is rejected at the semantic boundary rather than delegated elsewhere."""
    with pytest.raises(CompileError):
        _lower("x = definitely_not_a_callable(1)\noutput(x)")
    for relative in ("semantic_analysis.py", "semantic_body.py", "semantic_control_flow.py", "semantic_group.py"):
        source = (ROOT / relative).read_text(encoding="utf-8")
        for token in ("BODY_UNSUPPORTED", "legacy_binding_names", "unsupported_sentinel", "_BuiltinOperandUnsupported"):
            assert token not in source, (relative, token)


# I6 — structured control-flow semantics survive executor deletion unchanged.
def test_i6_positive_runtime_if_and_repeat_still_lower_to_structured_ir():
    """Runtime branch and Repeat constructs remain permanent structured IR operations."""
    if_result = _lower(
        "x = 1\n"
        "if index() > 0:\n"
        "    x = 2\n"
        "else:\n"
        "    x = 3\n"
        "output(x)\n"
    )
    assert any(isinstance(statement, IRIf) for statement in if_result.body.statements)

    bindings = {"x": RuntimeBindingSymbol(BindingId("stage36-test", 0), NFType.INT)}
    repeat_result = _lower(
        "for i in repeat_range(2):\n"
        "    x = x + 1\n"
        "output(x)\n",
        bindings=bindings,
    )
    assert any(isinstance(statement, IRRepeat) for statement in repeat_result.body.statements)


def test_i6_negative_repeat_still_rejects_type_changing_carried_state():
    """Removing the legacy executor does not weaken exact Repeat state typing."""
    bindings = {"x": RuntimeBindingSymbol(BindingId("stage36-test", 0), NFType.INT)}
    with pytest.raises(CompileError, match="changed type from INT to FLOAT"):
        _lower(
            "for i in repeat_range(2):\n"
            "    x = x + 1.0\n"
            "output(x)\n",
            bindings=bindings,
        )


# I7 — legacy package/data recognition remains diagnostic-only and separate from execution.
def test_i7_positive_persisted_and_deferred_v2_compatibility_boundaries_remain_present():
    """Persisted Blender compatibility and the deferred v2-hybrid diagnostic survive compiler deletion."""
    interface_source = (ROOT / "interface.py").read_text(encoding="utf-8")
    library_source = (ROOT / "library.py").read_text(encoding="utf-8")
    semantic_source = (ROOT / "semantic_analysis.py").read_text(encoding="utf-8")
    assert "def _legacy_socket_type(" in interface_source
    marker = "Source-backed library owners with interface.py are reserved for the"
    assert marker in library_source
    assert marker in semantic_source


def test_i7_negative_v1_package_recognition_has_no_executable_registry_api():
    """Recognized v1 layouts cannot regain a handler/compile-call execution surface."""
    from NodeForge.systems import registry as systems_registry

    for name in (
        "get_handler",
        "get_resolved_handler",
        "compile_call",
        "compile_resolved_call",
        "resolve_constructors",
        "_load_handlers",
    ):
        assert not hasattr(systems_registry, name)




# I8 — Extension v2 remains the package execution boundary.
def test_i8_positive_extension_calls_lower_through_extension_ir_kind():
    """Permanent extension analysis/lowering retains the typed Extension v2 IR boundary."""
    analysis_source = (ROOT / "semantic_analysis.py").read_text(encoding="utf-8")
    lowering_source = (ROOT / "semantic_lowering.py").read_text(encoding="utf-8")
    assert "CallableKind.EXTENSION" in analysis_source
    assert "IRCallableKind.EXTENSION" in lowering_source


def test_i8_negative_permanent_extension_path_contains_no_v1_callable_kinds_or_compile_hook():
    """The permanent extension path has no SYSTEM/BACKEND_HELPER/Compiler.compile escape lane."""
    combined = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in ("semantic_analysis.py", "semantic_lowering.py", "extension_registry.py")
    )
    for token in ("CallableKind.SYSTEM", "CallableKind.BACKEND_HELPER", "Compiler.compile("):
        assert token not in combined


# I9 — deletion must not be replaced by new temporary compatibility lanes.
def test_i9_positive_only_preexisting_deferred_migration_markers_remain():
    """The two deferred hybrid and two Stage-26 residualization markers remain explicit."""
    occurrences = []
    for path in _production_python_sources():
        for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if "TODO(nodeforge-migration):" in line:
                occurrences.append((path.relative_to(ROOT).as_posix(), line_no, line.strip()))
    assert [item[0] for item in occurrences].count("library.py") == 1
    assert [item[0] for item in occurrences].count("semantic_analysis.py") == 1
    assert [item[0] for item in occurrences].count("consteval.py") == 2
    assert len(occurrences) == 4


def test_i9_negative_no_new_compat_or_stage36_migration_marker_exists():
    """Stage 36 introduces neither fallback compatibility markers nor a checked-in broken workflow."""
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _production_python_sources())
    assert "TODO(nodeforge-compat):" not in combined
    assert "Stage 36" not in "\n".join(
        line for line in combined.splitlines() if "TODO(nodeforge-migration):" in line
    )


# I10 — the coordinated contract is a 0.62.2 patch cutover, not a stale 0.62.1 core.
def test_i10_positive_core_contract_reports_nodeforge_0622():
    """The completed refactor advertises the coordinated NodeForge patch version."""
    import NodeForge

    assert NodeForge.bl_info["version"] == (0, 62, 2)


def test_i10_negative_core_contract_no_longer_reports_pre_cutover_0621():
    """The legacy-free artifact cannot masquerade as the pre-cutover core version."""
    import NodeForge

    assert NodeForge.bl_info["version"] != (0, 62, 1)
