"""Regression gates for the permanent legacy-free compiler boundary."""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path

import pytest

from NodeForge.semantic.builtin_calls import (
    INPUT_DECLARATION_BUILTIN_NAMES,
    IR_CAPABLE_BUILTIN_NAMES,
)
from NodeForge.semantic import builtin_registry
from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.semantic.compile_time import CompileTimeSnapshot
from NodeForge.compiler_identities import BindingId
from NodeForge.errors import CompileError
from NodeForge.nf_types import NFType
from NodeForge.semantic.runtime_bindings import RuntimeBindingSymbol
from NodeForge.semantic.body import lower_basic_body
from NodeForge.semantic.ir import IRIf, IRRepeat
from NodeForge.blender.values import ObjectValue, Value


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
        owner_scope="legacy-free-core-test",
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
        "NodeForge.blender.geometry_builder",
    ):
        assert importlib.util.find_spec(module_name) is None, module_name


# I2 — live physical helpers belong to permanent backend owners.
def test_i2_positive_repeat_and_store_helpers_live_with_permanent_backend_owners():
    """Repeat-zone and Store Named Attribute realization live in their final backend modules."""
    repeat_source = (ROOT / "blender/ir_lowering.py").read_text(encoding="utf-8")
    geometry_source = (ROOT / "blender/geometry.py").read_text(encoding="utf-8")
    domain_source = (ROOT / "semantic/attribute_domains.py").read_text(encoding="utf-8")
    assert "def _create_repeat_zone(" in repeat_source
    assert "def _repeat_item_type_for_nf_type(" in repeat_source
    assert "def _store_named_attribute(" in geometry_source
    assert "def normalize_attribute_domain(" in domain_source
    assert "def _attribute_domain(" not in geometry_source
    assert "_ALLOWED_DOMAINS" not in geometry_source


def test_i2_negative_permanent_backend_does_not_import_deleted_mixed_owners():
    """Permanent backend modules do not reach back into the deleted mixed AST/compiler modules."""
    for relative in ("blender/ir_lowering.py", "blender/geometry.py"):
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
    """Relocated backend helpers retain the dependencies their live implementations require."""
    from NodeForge.blender import ir_lowering as blender_ir_lowering
    from NodeForge.blender import raw_nodes

    assert callable(blender_ir_lowering._new_node)
    assert raw_nodes._normalize_json_value((1.0, 2.0, 3.0)) == [1.0, 2.0, 3.0]


# I3 — builtin namespace is declarative, not executable.
def test_i3_positive_builtin_registry_exactly_matches_permanent_semantic_inventory():
    """Reserved/callable names derive only from permanent semantic builtin inventories."""
    expected_callable = frozenset(IR_CAPABLE_BUILTIN_NAMES | INPUT_DECLARATION_BUILTIN_NAMES)
    assert builtin_registry.CALLABLE_BUILTIN_NAMES == expected_callable
    assert builtin_registry.BUILTIN_NAMES == expected_callable | {"range", "repeat_range"}
    assert "cube" in builtin_registry.CALLABLE_BUILTIN_NAMES
    assert "repeat_range" in builtin_registry.BUILTIN_NAMES


def test_i3_negative_builtin_registry_exposes_no_ast_execution_dispatch():
    """The builtin namespace cannot execute source AST or retain handler tables."""
    assert not hasattr(builtin_registry, "compile_call")
    assert not hasattr(builtin_registry, "_HANDLERS")
    source = (ROOT / "semantic" / "builtin_registry.py").read_text(encoding="utf-8")
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
    import NodeForge.blender.values as values

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
    for relative in ("semantic/analysis.py", "semantic/body.py", "semantic/control_flow.py", "semantic/group.py"):
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

    bindings = {"x": RuntimeBindingSymbol(BindingId("legacy-free-core-test", 0), NFType.INT)}
    repeat_result = _lower(
        "for i in repeat_range(2):\n"
        "    x = x + 1\n"
        "output(x)\n",
        bindings=bindings,
    )
    assert any(isinstance(statement, IRRepeat) for statement in repeat_result.body.statements)


def test_i6_negative_repeat_still_rejects_type_changing_carried_state():
    """Removing the legacy executor does not weaken exact Repeat state typing."""
    bindings = {"x": RuntimeBindingSymbol(BindingId("legacy-free-core-test", 0), NFType.INT)}
    with pytest.raises(CompileError, match="changed type from INT to FLOAT"):
        _lower(
            "for i in repeat_range(2):\n"
            "    x = x + 1.0\n"
            "output(x)\n",
            bindings=bindings,
        )


# I7 — legacy package/data recognition remains diagnostic-only and separate from execution.
def test_i7_positive_persisted_and_unsupported_owner_boundaries_remain_present():
    """Persisted Blender compatibility and mixed source/interface rejection remain explicit."""
    interface_source = (ROOT / "blender/interface.py").read_text(encoding="utf-8")
    catalog_source = (ROOT / "catalog.py").read_text(encoding="utf-8")
    semantic_source = (ROOT / "semantic/analysis.py").read_text(encoding="utf-8")
    assert "def _legacy_socket_type(" in interface_source
    assert "both source.nf and interface.py is intentionally unsupported" in catalog_source
    assert "Mixed source.nf + interface.py owners are outside the supported callable model" in semantic_source
    assert "uses an unsupported mixed source.nf + interface.py package owner" in semantic_source


def test_i7_negative_v1_package_recognition_has_no_executable_registry_api():
    """The removed v1/global system-name registry cannot regain an execution surface."""
    assert not (ROOT / "systems" / "registry.py").exists()


# I8 — Extension v2 remains the package execution boundary.
def test_i8_positive_extension_calls_lower_through_extension_ir_kind():
    """Permanent extension analysis/lowering retains the typed Extension v2 IR boundary."""
    analysis_source = (ROOT / "semantic/analysis.py").read_text(encoding="utf-8")
    lowering_source = (ROOT / "semantic/lowering.py").read_text(encoding="utf-8")
    assert "CallableKind.EXTENSION" in analysis_source
    assert "IRCallableKind.EXTENSION" in lowering_source


def test_i8_negative_permanent_extension_path_contains_no_v1_callable_kinds_or_compile_hook():
    """The permanent extension path has no SYSTEM/BACKEND_HELPER/Compiler.compile escape lane."""
    combined = "\n".join(
        (ROOT / relative).read_text(encoding="utf-8")
        for relative in ("semantic/analysis.py", "semantic/lowering.py", "extensions/registry.py")
    )
    for token in ("CallableKind.SYSTEM", "CallableKind.BACKEND_HELPER", "Compiler.compile("):
        assert token not in combined


# I9 — permanent production code contains no temporary migration/fallback markers.
def test_i9_positive_production_python_has_no_temporary_migration_markers():
    """Completed compiler boundaries are documented as permanent contracts, not deferred migrations."""
    combined = "\n".join(path.read_text(encoding="utf-8") for path in _production_python_sources())
    assert "TODO(nodeforge-migration):" not in combined
    assert "TODO(nodeforge-compat):" not in combined


def test_i9_negative_removed_temporary_marker_vocabulary_is_not_reintroduced():
    """Production code must not reopen a temporary fallback lane after the legacy-free cutover."""
    for path in _production_python_sources():
        source = path.read_text(encoding="utf-8")
        assert "TODO(nodeforge-migration):" not in source, path
        assert "TODO(nodeforge-compat):" not in source, path


# I10 — the permanent compiler contract has a legacy-free version floor, not an exact future pin.
def test_i10_positive_core_contract_is_at_or_above_legacy_free_core_cutover_floor():
    """Later releases retain the legacy-free cutover rather than pinning one patch version."""
    import NodeForge

    assert NodeForge.bl_info["version"] >= (0, 62, 2)


def test_i10_negative_core_contract_cannot_report_a_pre_cutover_version():
    """The legacy-free artifact cannot advertise a core version below the permanent cutover floor."""
    import NodeForge

    assert NodeForge.bl_info["version"] >= (0, 62, 2)


# I11 — normalized source defaults belong to frontend semantics, not physical helpers.
def test_i11_positive_audited_physical_helpers_require_normalized_source_state_explicitly():
    """Physical helpers expose no duplicate source-language defaults for audited arguments."""
    import inspect
    from NodeForge.blender import geometry, interface, raw_nodes

    required = {
        geometry._store_named_attribute: ("selection", "domain", "data_type_override"),
        geometry._store_named_attribute_geometry: ("selection", "domain", "data_type_override"),
        geometry._capture_attribute_geometry: ("selection", "domain", "data_type"),
        geometry._instance_on_points: ("selection", "scale", "rotation", "realize"),
        geometry._set_position_geometry: ("selection",),
        geometry._transform_geometry: ("translation", "scale", "rotation"),
        interface._create_interface_panel: ("collapsed",),
        interface._create_group_input_socket: ("default",),
        raw_nodes.build_materialized_raw_node: ("props", "inputs", "output", "typ", "outputs"),
    }
    for function, parameter_names in required.items():
        signature = inspect.signature(function)
        for parameter_name in parameter_names:
            assert signature.parameters[parameter_name].default is inspect.Parameter.empty, (
                function.__name__,
                parameter_name,
            )


def test_i11_negative_lowering_does_not_reconstruct_audited_source_defaults():
    """Guaranteed normalized options use required access rather than backend fallback defaults."""
    source = (ROOT / "blender/ir_lowering.py").read_text(encoding="utf-8")
    forbidden = (
        'options.get("domain"',
        'options.get("data_type"',
        'options.get("scale"',
        'options.get("rotation"',
        'options.get("realize"',
        'options.get("translation"',
        'options.get("props"',
        'options.get("inputs"',
        'options.get("output"',
        'options.get("typ"',
        'options.get("outputs"',
        'options.get("clamp"',
    )
    for token in forbidden:
        assert token not in source, token

    semantic_lowering_source = (ROOT / "semantic/lowering.py").read_text(encoding="utf-8")
    assert 'option_map.get("raw_output_mode")' not in semantic_lowering_source
    assert 'option_map["raw_output_mode"]' in semantic_lowering_source


# I12 — Release-coupled package fixtures track the current public core version.
def test_i12_positive_release_coupled_fixture_ceiling_matches_current_core_version():
    """The current public core release and synthetic package ceilings advance together."""
    import NodeForge

    current = ".".join(str(part) for part in NodeForge.bl_info["version"])
    expected = f'"nodeforge_max_version": "{current}"'
    for relative in (
        "tests/unit/test_extension_bootstrap.py",
        "tests/unit/test_extension_packages.py",
        "tests/blender/test_extension_api_v2.py",
    ):
        source = (ROOT / relative).read_text(encoding="utf-8")
        assert expected in source, relative
