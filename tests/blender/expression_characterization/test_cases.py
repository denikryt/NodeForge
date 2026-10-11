"""Parametrized source-to-Blender expression characterization baselines."""

from __future__ import annotations

import shutil

import bpy
import pytest

from . import harness
from .harness import (
    assert_case_result,
    load_case_baseline,
    load_manifest,
    run_case,
)


CASES = load_manifest()


@pytest.mark.parametrize("case", CASES, ids=[case["id"] for case in CASES])
def test_expression_characterization_case(case):
    """Compile one DSL fixture in Blender and compare its normalized observable result."""
    case_id = case["id"]
    actual = run_case(case)
    expected = load_case_baseline(case_id)
    assert_case_result(case_id, actual, expected)


def test_harness_literal_payload_changes_snapshot():
    """Literal payload changes must change the behavior snapshot even with identical topology."""
    group = bpy.data.node_groups.new("NFCharHarnessLiteral", "GeometryNodeTree")
    try:
        node = group.nodes.new("ShaderNodeValue")
        node.outputs[0].default_value = 1.0
        first = harness.group_snapshot(group)
        node.outputs[0].default_value = 999.0
        second = harness.group_snapshot(group)
        assert first != second
        assert first["nodes"][0]["payload"] == {"value": 1.0}
        assert second["nodes"][0]["payload"] == {"value": 999.0}
    finally:
        bpy.data.node_groups.remove(group)


def test_harness_ignores_inactive_defaults_and_keeps_semantic_defaults():
    """Snapshots exclude Blender-only defaults while retaining NodeForge-written defaults."""
    group = bpy.data.node_groups.new("NFCharHarnessDefaults", "GeometryNodeTree")
    try:
        math = group.nodes.new("ShaderNodeMath")
        math.operation = "ADD"
        math.inputs[-1].default_value = 0.123
        combine = group.nodes.new("ShaderNodeCombineXYZ")
        combine.inputs[1].default_value = 7.5
        snapshot = harness.group_snapshot(group)
        math_row = next(row for row in snapshot["nodes"] if row["bl_idname"] == "ShaderNodeMath")
        combine_row = next(row for row in snapshot["nodes"] if row["bl_idname"] == "ShaderNodeCombineXYZ")
        assert "defaults" not in math_row
        assert 7.5 in combine_row["defaults"].values()
    finally:
        bpy.data.node_groups.remove(group)


def test_harness_socket_refs_are_unique_for_duplicate_socket_names():
    """Socket references remain unique when Blender exposes duplicate display names."""
    group = bpy.data.node_groups.new("NFCharHarnessSockets", "GeometryNodeTree")
    try:
        node = group.nodes.new("FunctionNodeCompare")
        refs = [harness._socket_ref(node.inputs, socket) for socket in node.inputs]
        assert len(refs) == len(set(refs))
    finally:
        bpy.data.node_groups.remove(group)


def test_harness_rejects_actual_outcome_that_disagrees_with_manifest():
    """Recorder/test execution cannot bless a graph as an expected compile failure."""
    case = next(entry for entry in CASES if entry["id"] == "literal_float")
    wrong = dict(case, outcome="compile_error")
    with pytest.raises(AssertionError, match="expected outcome"):
        run_case(wrong)


def test_harness_removes_all_node_groups_created_by_case(monkeypatch):
    """Case isolation removes a root group before its referenced helper dependency."""
    before = harness._group_pointer_set()

    def fake_compile(source, name):
        root = bpy.data.node_groups.new(name, "GeometryNodeTree")
        helper = bpy.data.node_groups.new(name + "_helper", "GeometryNodeTree")
        call = root.nodes.new("GeometryNodeGroup")
        call.node_tree = helper
        return root

    monkeypatch.setattr(harness.compiler, "create_expression_group", fake_compile)
    case = next(entry for entry in CASES if entry["id"] == "literal_float")
    result = run_case(case)
    assert result["kind"] == "graph"
    assert harness._group_pointer_set() == before


def test_harness_pre_record_allows_only_selected_missing_baseline(monkeypatch, tmp_path):
    """Pre-record validation permits a selected missing baseline but strict validation does not."""
    expected_dir = tmp_path / "expected"
    expected_dir.mkdir()
    selected = CASES[0]["id"]
    for entry in CASES:
        if entry["id"] == selected:
            continue
        shutil.copy2(harness.EXPECTED_DIR / f"{entry['id']}.json", expected_dir / f"{entry['id']}.json")
    monkeypatch.setattr(harness, "EXPECTED_DIR", expected_dir)
    chosen = harness.select_cases(case_id=selected, pre_record=True)
    assert [entry["id"] for entry in chosen] == [selected]
    with pytest.raises(ValueError, match="missing baseline"):
        harness.select_cases(case_id=selected, pre_record=False)


def test_harness_validates_minimal_baseline_document_shape():
    """Baseline validation rejects malformed graph and compile-error documents."""
    harness._validate_baseline_document(
        {"kind": "graph", "graph": {"interface": [], "nodes": [], "links": []}},
        "synthetic_graph",
    )
    harness._validate_baseline_document(
        {"kind": "compile_error", "exception": "CompileError", "message": "boom"},
        "synthetic_error",
    )
    with pytest.raises(ValueError, match="exactly kind and graph"):
        harness._validate_baseline_document(
            {"kind": "graph", "graph": {"interface": [], "nodes": [], "links": []}, "extra": 1},
            "synthetic_graph",
        )
    with pytest.raises(ValueError, match="interface, nodes and links"):
        harness._validate_baseline_document(
            {"kind": "graph", "graph": {"interface": [], "nodes": []}},
            "synthetic_graph",
        )
    with pytest.raises(ValueError, match="exactly kind, exception and message"):
        harness._validate_baseline_document(
            {"kind": "compile_error", "exception": "CompileError", "message": "boom", "extra": 1},
            "synthetic_error",
        )
