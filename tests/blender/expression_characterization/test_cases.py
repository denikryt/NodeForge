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


def test_top_level_baselines_pin_sharing_and_import_side_effects():
    """Prevent re-recording snapshots that silently lose shared/unique identity."""
    shared = load_case_baseline("top_level_import_shared_unique")["graph"]
    references = [node["group"] for node in shared["nodes"]]
    assert references[0]["id"] == references[1]["id"]
    assert references[0]["id"] != references[2]["id"]
    assert [ref["unique"] for ref in references] == [False, False, True]

    aliases = load_case_baseline("top_level_import_alias_identity")["graph"]
    assert aliases["nodes"][0]["group"]["id"] == aliases["nodes"][1]["group"]["id"]

    imported = load_case_baseline("top_level_import_multi")["graph"]
    assert [node["bl_idname"] for node in imported["nodes"]] == [
        "GeometryNodeGroup", "GeometryNodeGroup",
    ]
    stretch = imported["nodes"][1]["group"]["graph"]
    nested = [node["group"]["id"] for node in stretch["nodes"] if "group" in node]
    assert nested == [imported["nodes"][0]["group"]["id"]]


def test_top_level_baselines_pin_nesting_and_absent_unused_functions():
    """A definition chain materializes three levels; shadowed defs remain unused."""
    definitions = load_case_baseline("top_level_definition_chain")
    assert definitions["graph"]["nodes"] == []  # groups-only does not place root instances
    published = {item["name"]: item["graph"] for item in definitions["definitions"]}
    assert {"entry", "independent", "leaf", "middle"} == set(published)
    entry_graph = published["entry"]
    middle = next(node["group"]["graph"] for node in entry_graph["nodes"] if "group" in node)
    leaf = next(node["group"]["graph"] for node in middle["nodes"] if "group" in node)
    assert any(node["bl_idname"] == "ShaderNodeMath" for node in leaf["nodes"])

    shadowed = load_case_baseline("top_level_lexical_shadowing")["graph"]
    assert len(shadowed["nodes"]) == 1
    assert all(node["bl_idname"] != "GeometryNodeGroup"
               for node in shadowed["nodes"][0]["group"]["graph"]["nodes"])


def test_top_level_baselines_pin_interface_outputs_and_existing_links():
    """Check semantic details that can be lost by blindly regenerating JSON baselines."""
    panel = load_case_baseline("top_level_panel_defaults")
    assert panel["graph"]["nodes"] == []
    interface = panel["definitions"][0]["graph"]["interface"]
    assert [(item["name"], item["default"]) for item in interface if item["in_out"] == "INPUT"] == [
        ("Bias", 3.0), ("Amount", 2.0),
    ]

    outputs = load_case_baseline("top_level_tuple_outputs")["graph"]
    interface = outputs["nodes"][0]["group"]["graph"]["interface"]
    assert [item["name"] for item in interface if item["in_out"] == "OUTPUT"] == [
        "Twice", "Plus One",
    ]
    assert len(outputs["links"]) == 2

    preserved = load_case_baseline("top_level_preserve_user_link")["graph"]
    assert [node["bl_idname"] for node in preserved["nodes"]] == ["GeometryNodeMeshCube"]
    assert preserved["links"] == []  # root-producing Compile replaces all ordinary nodes


def test_groups_only_baselines_pin_shared_helper_without_root_instances():
    """Entry groups share a helper, while the selected root remains physically unchanged."""
    baseline = load_case_baseline("top_level_shared_entries")
    assert baseline["graph"]["nodes"] == []
    groups = {row["name"]: row["graph"] for row in baseline["definitions"]}
    assert set(groups) == {"_common", "first", "second"}
    helper_graph = groups["_common"]
    assert any(node["bl_idname"] == "ShaderNodeMath" for node in helper_graph["nodes"])
    for name in ("first", "second"):
        assert any(node["bl_idname"] == "GeometryNodeGroup" for node in groups[name]["nodes"])


def test_top_level_baseline_pins_only_reachable_function_groups():
    """A→B→C and direct C share one leaf; D and E→B stay unmaterialized."""
    graph = load_case_baseline("top_level_reachable_subgraph")["graph"]
    root_nodes = graph["nodes"]
    assert [node["bl_idname"] for node in root_nodes] == [
        "GeometryNodeMeshCube", "GeometryNodeGroup",
        "GeometryNodeGroup", "GeometryNodeMeshCube",
    ]
    assert graph["links"] == [
        {"from": ["n0", "Mesh"], "to": ["n1", "Socket_0"]},
        {"from": ["n0", "Mesh"], "to": ["n2", "Socket_0"]},
    ]

    a = root_nodes[1]["group"]
    b = next(node["group"] for node in a["graph"]["nodes"] if "group" in node)
    c = next(node["group"] for node in b["graph"]["nodes"] if "group" in node)
    direct_c = root_nodes[2]["group"]
    assert len({a["id"], b["id"], c["id"]}) == 3
    assert direct_c == {"id": c["id"], "unique": False}
    assert [node["bl_idname"] for node in c["graph"]["nodes"]] == [
        "NodeGroupInput", "NodeGroupOutput", "GeometryNodeTransform",
    ]


def test_extension_baselines_pin_inline_vs_grouped_topology():
    """The same owner must not silently acquire or lose a physical group boundary."""
    inline = load_case_baseline('extension_inline_root')['graph']
    assert [node['bl_idname'] for node in inline['nodes']] == [
        'ShaderNodeValue', 'ShaderNodeMath', 'ShaderNodeMath',
    ]
    assert [node['properties'].get('operation') for node in inline['nodes'][1:]] == [
        'MULTIPLY', 'MULTIPLY',
    ]
    assert len(inline['links']) == 2

    grouped = load_case_baseline('extension_grouped_root')['graph']
    children = [node['group'] for node in grouped['nodes'] if 'group' in node]
    assert len(children) == 2
    assert children[0]['id'] != children[1]['id']
    assert all(any(n['bl_idname'] == 'ShaderNodeMath'
                   for n in child['graph']['nodes']) for child in children)
    assert len(grouped['links']) == 2

    mixed = load_case_baseline('extension_inline_and_grouped')['graph']
    assert [node['bl_idname'] for node in mixed['nodes']] == [
        'ShaderNodeValue', 'ShaderNodeMath', 'GeometryNodeGroup', 'ShaderNodeMath',
    ]
    assert len(mixed['links']) == 3

    nested = load_case_baseline('extension_nested_function')['graph']
    root_group, = [node['group'] for node in nested['nodes'] if 'group' in node]
    inner = root_group['graph']
    assert any(node['bl_idname'] == 'ShaderNodeMath' for node in inner['nodes'])
    child, = [node['group'] for node in inner['nodes'] if 'group' in node]
    assert any(node['bl_idname'] == 'ShaderNodeMath' for node in child['graph']['nodes'])


def test_extension_baselines_pin_dynamic_typed_outputs_and_semantic_dependencies():
    """Call-specific result types, tuple arity and compacted semantic input are observable."""
    heterogeneous = load_case_baseline('extension_dynamic_mixed')['graph']
    child, = [node['group']['graph'] for node in heterogeneous['nodes'] if 'group' in node]
    assert [(item['name'], item['socket_type']) for item in child['interface']
            if item['in_out'] == 'OUTPUT'] == [
        ('Result_0', 'NodeSocketFloat'), ('Result_1', 'NodeSocketVector'),
    ]
    assert any(node['bl_idname'] == 'ShaderNodeSeparateXYZ' for node in heterogeneous['nodes'])

    singleton = load_case_baseline('extension_dynamic_singleton')['graph']
    child_groups = [node['group']['graph'] for node in singleton['nodes'] if 'group' in node]
    assert len(child_groups) == 2
    signatures = [[item['socket_type'] for item in group['interface']
                   if item['in_out'] == 'OUTPUT'] for group in child_groups]
    assert signatures == [['NodeSocketVector'], ['NodeSocketFloat']]
    assert [item['name'] for group in child_groups for item in group['interface']
            if item['in_out'] == 'OUTPUT'] == ['Result_0', 'Result_0']

    semantic = load_case_baseline('extension_semantic_dependency')['graph']
    child, = [node['group']['graph'] for node in semantic['nodes'] if 'group' in node]
    assert [(item['name'], item['socket_type']) for item in child['interface']
            if item['in_out'] == 'INPUT'] == [('Dependency_0', 'NodeSocketFloat')]
    assert len(semantic['links']) == 1


def test_extension_baselines_pin_capture_rotation_both_materialization_paths():
    """Dynamic capture items must survive root transfer and grouped publication."""
    root = load_case_baseline('extension_capture_rotation')['graph']
    grouped, = [node['group']['graph'] for node in root['nodes'] if 'group' in node]
    expected_items = [
        {'name': 'Spin', 'data_type': 'QUATERNION'},
        {'name': 'Amount', 'data_type': 'FLOAT'},
    ]
    for graph in (root, grouped):
        capture, = [node for node in graph['nodes']
                    if node['bl_idname'] == 'GeometryNodeCaptureAttribute']
        assert capture['capture_items'] == expected_items
        assert capture['properties']['domain'] == 'POINT'
        assert list(capture['defaults'].values()) == [0.5]
        assert any(link['to'][0] == capture['id'] and link['from'][1] == 'Rotation'
                   for link in graph['links'])

    error = load_case_baseline('extension_invalid_inline_modifier')
    assert error['kind'] == 'compile_error'
    assert error['exception'] == 'CompileError'
    assert '__inline__ must be a compile-time Bool' in error['message']


def test_harness_capture_items_change_snapshot():
    """Changes to capture item types cannot be invisible to characterization JSON."""
    group = bpy.data.node_groups.new('NFCharHarnessCaptureItems', 'GeometryNodeTree')
    try:
        node = group.nodes.new('GeometryNodeCaptureAttribute')
        node.capture_items.clear()
        node.capture_items.new('FLOAT', 'Value')
        first = harness.group_snapshot(group)
        node.capture_items.clear()
        node.capture_items.new('ROTATION', 'Value')
        second = harness.group_snapshot(group)
        assert first != second
        assert first['nodes'][0]['capture_items'][0]['data_type'] == 'FLOAT'
        assert second['nodes'][0]['capture_items'][0]['data_type'] == 'QUATERNION'
    finally:
        bpy.data.node_groups.remove(group)
