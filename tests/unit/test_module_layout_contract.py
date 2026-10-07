"""Incremental physical ownership contract for NodeForge module organization."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _assert_paths_exist(paths: set[str]) -> None:
    """Require every canonical path in *paths* to exist."""
    missing = sorted(path for path in paths if not (PACKAGE_ROOT / path).exists())
    assert not missing, f"Missing canonical module paths: {missing}"


def _assert_paths_absent(paths: set[str]) -> None:
    """Require every historical path in *paths* to be absent."""
    present = sorted(path for path in paths if (PACKAGE_ROOT / path).exists())
    assert not present, f"Historical module paths still present: {present}"


def test_owner_package_scaffolding_exists():
    """Positive: the three internal owner packages exist before module cutover."""
    _assert_paths_exist({"semantic/__init__.py", "extensions/__init__.py", "blender/__init__.py"})


def test_owner_package_initializers_remain_minimal():
    """Negative: package initializers must not recreate aggregate compatibility facades."""
    for relative in ("semantic/__init__.py", "extensions/__init__.py", "blender/__init__.py"):
        source = (PACKAGE_ROOT / relative).read_text(encoding="utf-8")
        assert "import *" not in source
        assert "__all__ =" not in source


def test_catalog_environment_and_local_owners_are_canonical():
    """Positive: discovery, Local persistence, environment construction and physical groups are split."""
    _assert_paths_exist({
        "catalog.py", "local_sources.py", "resolved_environment.py", "environment_resolution.py",
        "blender/library_groups.py",
    })


def test_mixed_library_owner_path_is_absent():
    """Negative: the old mixed library owner cannot survive as a forwarding module."""
    _assert_paths_absent({"library.py"})


def test_ctfe_residualization_and_evaluation_implementation_have_semantic_owners():
    """Positive: compile-time implementation lives at canonical semantic paths."""
    _assert_paths_exist({
        "semantic/compile_time.py",
        "semantic/consteval.py",
        "semantic/residualization.py",
        "semantic/evaluation_resolution.py",
        "evaluation_modes.py",
    })


def test_old_flat_ctfe_owner_paths_are_absent():
    """Negative: moved CTFE implementation cannot survive as flat forwarding modules."""
    _assert_paths_absent({"compile_time.py", "consteval.py"})


def test_source_callable_analysis_session_bindings_and_parsing_have_semantic_owners():
    """Positive: source-call responsibilities live at their canonical semantic paths."""
    _assert_paths_exist({
        "semantic/source_bindings.py",
        "semantic/source_callables.py",
        "semantic/source_callable_session.py",
        "semantic/call_modifiers.py",
        "semantic/parsing.py",
    })


def test_old_mixed_source_callable_and_parsing_paths_are_absent():
    """Negative: split source-call owners cannot survive behind flat forwarding modules."""
    _assert_paths_absent({"source_callables.py", "parsing.py"})


def test_physical_group_assembly_has_blender_owner():
    """Positive: prepared semantic groups are physically assembled by the Blender layer."""
    _assert_paths_exist({"blender/group_assembly.py"})


def test_compiler_facade_no_longer_owns_group_assembly_implementation():
    """Negative: compiler.py must not retain a second physical group assembly implementation."""
    source = (PACKAGE_ROOT / "compiler.py").read_text(encoding="utf-8")
    assert "def _populate_group(" not in source
    assert "def _assert_prepared_interface_parity(" not in source
    assert "lower_body(" not in source


def test_semantic_frontend_cluster_has_canonical_package_owners():
    """Positive: permanent frontend responsibilities live under semantic/."""
    _assert_paths_exist({
        "semantic/analysis.py",
        "semantic/body.py",
        "semantic/ir.py",
        "semantic/lowering.py",
        "semantic/control_flow.py",
        "semantic/geometry_builder.py",
        "semantic/values.py",
        "semantic/builtin_calls.py",
        "semantic/builtin_registry.py",
        "semantic/call_resolution.py",
        "semantic/callable_contracts.py",
        "semantic/constants.py",
        "semantic/numeric_semantics.py",
        "semantic/runtime_bindings.py",
        "semantic/group_context.py",
        "semantic/attribute_domains.py",
        "semantic/group.py",
    })


def test_old_flat_semantic_owner_paths_are_absent():
    """Negative: canonical semantic owners cannot survive as flat compatibility modules."""
    _assert_paths_absent({
        "semantic_analysis.py",
        "semantic_body.py",
        "semantic_ir.py",
        "semantic_lowering.py",
        "semantic_control_flow.py",
        "semantic_geometry_builder.py",
        "semantic_values.py",
        "builtin_call_semantics.py",
        "builtins/registry.py",
        "call_resolution.py",
        "callable_contracts.py",
        "constants.py",
        "numeric_semantics.py",
        "runtime_bindings.py",
        "group_context.py",
        "attribute_domains.py",
        "semantic_group.py",
    })


def test_extension_v2_internals_have_canonical_package_owners():
    """Positive: internal Extension v2 contracts, registry and semantic values live under extensions/."""
    _assert_paths_exist({
        "extensions/contracts.py",
        "extensions/interface.py",
        "extensions/registry.py",
        "extensions/semantics.py",
        "extensions/values.py",
        "extension_api.py",
        "extension_annotations.py",
        "extension_semantic_api.py",
    })


def test_old_internal_extension_owner_paths_are_absent():
    """Negative: internal Extension v2 modules cannot remain as root forwarding aliases."""
    _assert_paths_absent({
        "extension_contracts.py",
        "extension_interface.py",
        "extension_registry.py",
        "extension_semantics.py",
        "extension_values.py",
    })


def test_low_level_blender_runtime_has_canonical_physical_owners():
    """Positive: physical values, nodes, sockets and lowering live under blender/."""
    _assert_paths_exist({
        "blender/values.py",
        "blender/socket_types.py",
        "blender/nodes.py",
        "blender/geometry.py",
        "blender/interface.py",
        "blender/bundle.py",
        "blender/object_info.py",
        "blender/raw_nodes.py",
        "blender/extension_backend.py",
        "blender/ir_lowering.py",
    })


def test_old_low_level_blender_owner_paths_are_absent():
    """Negative: moved physical helpers cannot survive at historical root/builtins paths."""
    _assert_paths_absent({
        "values.py",
        "blender_socket_types.py",
        "nodes.py",
        "geometry.py",
        "interface.py",
        "builtins/bundle.py",
        "builtins/object_info.py",
        "builtins/raw_nodes.py",
        "blender_extension_backend.py",
        "blender_ir_lowering.py",
    })
