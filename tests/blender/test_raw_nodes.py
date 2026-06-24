from helpers import *

import json

from NodeForge.builtins import raw_nodes


def _nodes(group, bl_idname):
    return [node for node in group.nodes if getattr(node, "bl_idname", None) == bl_idname]


def _one_node(group, bl_idname):
    nodes = _nodes(group, bl_idname)
    check(len(nodes) == 1, f"expected one {bl_idname}, found {len(nodes)}")
    return nodes[0]


def _input_contracts(node):
    return json.loads(node[raw_nodes.RAW_INPUTS_JSON_PROP])


def _contract(node, name):
    for entry in _input_contracts(node):
        if entry["name"] == name:
            return entry
    raise AssertionError(f"missing raw input contract {name!r}")


def _same_blender_ref(left, right):
    if left is right:
        return True
    try:
        return left == right
    except Exception:
        pass
    try:
        return left.as_pointer() == right.as_pointer()
    except Exception:
        return False


def _incoming_count(group, node, socket_name):
    socket = raw_nodes.resolve_socket(node.inputs, socket_name, direction="input", context="test")
    return sum(
        1
        for link in group.links
        if _same_blender_ref(link.to_node, node) and _same_blender_ref(link.to_socket, socket)
    )


def test_raw_compare_single_output_literal_default_and_metadata():
    group = compile_group('''
mask = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "GREATER_THAN"},
    inputs={"A": position().z, "B": 0.5},
    output="Result",
    typ=Bool,
)
output("mask", mask)
''', "NFTest_raw_compare")
    node = _one_node(group, "FunctionNodeCompare")
    check(raw_nodes.is_raw_node(node), "raw compare node missing metadata marker")
    check(node.data_type == "FLOAT", "raw compare data_type mismatch")
    check(node.operation == "GREATER_THAN", "raw compare operation mismatch")
    check(abs(float(node.inputs["B"].default_value) - 0.5) < 1e-6, "literal default was not assigned")
    check(_contract(node, "A")["mode"] == raw_nodes.INPUT_SINGLE_LINK, "linked input contract missing")
    check(_contract(node, "B")["mode"] == raw_nodes.INPUT_LITERAL, "literal input contract missing")
    check(abs(float(_contract(node, "B")["default"]) - 0.5) < 1e-6, "literal default metadata mismatch")
    check("Bool" not in [item.name for item in group.interface.items_tree], "Bool became an implicit input")


def test_raw_node_result_attribute_and_subscript_outputs():
    group = compile_group('''
sep = node(
    "ShaderNodeSeparateXYZ",
    inputs={"Vector": (1.0, 2.0, 3.0)},
    outputs={"X": Float, "Y": Float},
)
out = sep["X"] + sep.Y
output("out", out)
''', "NFTest_raw_multi_output")
    node = _one_node(group, "ShaderNodeSeparateXYZ")
    check(raw_nodes.is_raw_node(node), "separate xyz raw metadata missing")
    check(_contract(node, "Vector")["mode"] == raw_nodes.INPUT_LITERAL, "vector default contract missing")


def test_raw_join_geometry_multi_input():
    group = compile_group('''
a = cube(1)
b = cube(2)
c = cube(3)
geo = node(
    "GeometryNodeJoinGeometry",
    inputs={"Geometry": [a, b, c]},
    output="Geometry",
    typ=Geometry,
)
output("Geometry", geo)
''', "NFTest_raw_join_geometry")
    node = _one_node(group, "GeometryNodeJoinGeometry")
    check(_contract(node, "Geometry")["mode"] == raw_nodes.INPUT_MULTI_LINK, "multi-link contract missing")
    check(_contract(node, "Geometry")["links"] == 3, "multi-link contract count mismatch")
    check(_incoming_count(group, node, "Geometry") == 3, "multi-input links were not created")


def test_compare_wrappers_use_raw_builder():
    group = compile_group('''
a = greater_than(position().z, 0.25)
b = less_equal(position().x, 1.0)
out = a and b
output("out", out)
''', "NFTest_raw_compare_wrappers")
    compares = _nodes(group, "FunctionNodeCompare")
    raw_compares = [node for node in compares if raw_nodes.is_raw_node(node)]
    check(len(raw_compares) == 2, "compare wrappers did not create raw compare nodes")
    check({node.operation for node in raw_compares} == {"GREATER_THAN", "LESS_EQUAL"}, "compare wrapper operations mismatch")


def test_raw_node_update_cutover_preserves_props_defaults_and_links():
    initial = '''
mask = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "GREATER_THAN"},
    inputs={"A": position().z, "B": 0.25},
    output="Result",
    typ=Bool,
)
output("mask", mask)
'''
    group = compile_group(initial, "NFTest_raw_update")
    updated = '''
mask = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "LESS_THAN"},
    inputs={"A": position().x, "B": 0.75},
    output="Result",
    typ=Bool,
)
output("mask", mask)
'''
    compiler.update_expression_group(group, updated)
    node = _one_node(group, "FunctionNodeCompare")
    check(raw_nodes.is_raw_node(node), "updated raw node lost metadata")
    check(node.operation == "LESS_THAN", "updated raw compare operation mismatch")
    check(abs(float(node.inputs["B"].default_value) - 0.75) < 1e-6, "updated literal default mismatch")
    check(_contract(node, "B")["mode"] == raw_nodes.INPUT_LITERAL, "updated literal mode missing")
    check(_incoming_count(group, node, "A") == 1, "updated linked input mismatch")


def test_raw_join_multi_input_update_cutover_preserves_links():
    group = compile_group('''
a = cube(1)
b = cube(2)
geo = node("GeometryNodeJoinGeometry", inputs={"Geometry": [a, b]}, output="Geometry", typ=Geometry)
output("Geometry", geo)
''', "NFTest_raw_join_update")
    compiler.update_expression_group(group, '''
a = cube(1)
b = cube(2)
c = cube(3)
geo = node("GeometryNodeJoinGeometry", inputs={"Geometry": [a, b, c]}, output="Geometry", typ=Geometry)
output("Geometry", geo)
''')
    node = _one_node(group, "GeometryNodeJoinGeometry")
    check(_incoming_count(group, node, "Geometry") == 3, "cutover did not preserve raw multi-input links")
    check(_contract(node, "Geometry")["links"] == 3, "cutover did not preserve multi-input contract")


def test_raw_cutover_literal_default_mismatch_rolls_back(monkeypatch):
    group = compile_group('''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "GREATER_THAN"}, inputs={"A": position().z, "B": 0.25}, output="Result", typ=Bool)
output("mask", mask)
''', "NFTest_raw_rollback")
    original = raw_nodes.copy_raw_node_properties

    def corrupt_literal_default(src_node, dst_node):
        original(src_node, dst_node)
        if getattr(dst_node, "bl_idname", None) == "FunctionNodeCompare":
            try:
                expected_b = _contract(src_node, "B").get("default")
            except Exception:
                expected_b = None
            if abs(float(expected_b) - 0.75) < 1e-6:
                dst_node.inputs["B"].default_value = 0.0

    monkeypatch.setattr(raw_nodes, "copy_raw_node_properties", corrupt_literal_default)
    try:
        compiler.update_expression_group(group, '''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "LESS_THAN"}, inputs={"A": position().x, "B": 0.75}, output="Result", typ=Bool)
output("mask", mask)
''')
    except Exception:
        pass
    else:
        raise AssertionError("strict raw cutover mismatch did not fail")
    node = _one_node(group, "FunctionNodeCompare")
    check(node.operation == "GREATER_THAN", "rollback did not restore previous raw compare operation")
    check(abs(float(node.inputs["B"].default_value) - 0.25) < 1e-6, "rollback did not restore previous literal default")


def test_raw_node_error_fixtures_are_controlled_compile_errors():
    bad_sources = [
        'x = node("NoSuchNode", output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", props={"no_such_prop": 1}, output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", props={"operation": "NOPE"}, output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", inputs={"Nope": 1}, output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", output="Nope", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", inputs={"A": [1, 2]}, output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", inputs={"A": []}, output="Result", typ=Bool)\noutput("x", x)',
        'Bool = 1\noutput("x", 1)',
        'output("x", Bool)',
        'r = node("ShaderNodeSeparateXYZ", outputs={"X": Float})\noutput("x", r)',
        'r = node("ShaderNodeSeparateXYZ", outputs={"X": Float})\ny = r + 1\noutput("y", y)',
        'part = ls_axiom("F")\nx = node("FunctionNodeCompare", inputs={"A": part}, output="Result", typ=Bool)\noutput("x", x)',
        'x = equal(True, False)\noutput("x", x)',
    ]
    for index, source in enumerate(bad_sources):
        expect_compile_error(source, f"NFTest_raw_error_{index}")
