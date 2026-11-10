"""Blender integration coverage for first-class runtime Bundle values."""

from helpers import *

from NodeForge import local_sources

import json
import math
import tempfile
from pathlib import Path

import bpy

from NodeForge import compiler, packages
from NodeForge.semantic.constants import TYPE_BUNDLE


def _one_node(group, bl_idname):
    nodes = [node for node in group.nodes if node.bl_idname == bl_idname]
    check(len(nodes) == 1, f"expected one {bl_idname}, found {len(nodes)}")
    return nodes[0]


def _interface_socket(group, name, in_out="INPUT"):
    return next(
        item for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET"
        and getattr(item, "in_out", None) == in_out
        and item.name == name
    )


def _bundle_items(node):
    return [(item.name, item.socket_type, item.structure_type) for item in node.bundle_items]


def _evaluate_single_vertex(group, name):
    mesh = bpy.data.meshes.new(name + "_Mesh")
    mesh.from_pydata([(0.0, 0.0, 0.0)], [], [])
    mesh.update()
    obj = bpy.data.objects.new(name + "_Object", mesh)
    bpy.context.collection.objects.link(obj)
    mod = obj.modifiers.new("NodeForge", "NODES")
    mod.node_group = group
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    out = evaluated.to_mesh()
    try:
        return tuple(float(c) for c in out.vertices[0].co)
    finally:
        evaluated.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)


def _write_package_manifest(root, package_id):
    (root / "nodeforge_package.json").write_text(json.dumps({
        "schema_version": 1,
        "id": package_id,
        "name": package_id,
        "version": "1.0.0",
        "author": "Tests",
        "description": "Runtime Bundle fixture",
        "nodeforge_min_version": "0.49.47",
        "nodeforge_max_version": None,
        "contents": {"functions": "functions"},
        "permissions": {"python": False},
    }), encoding="utf-8")


def test_bundle_construct_get_set_nested_and_runtime_path_evaluate():
    group = compile_group('''
def nf_fixture(geo: Geometry['Geometry'], material: Material['Material'], object_value: Object['Object'], path: String['Path'] = "nested/value", count: Int['Count'] = 2):
    inner = bundle(value=1.0, label="inner")
    state = bundle(flag=True, count=count, nested=inner, direction=vector(1.0, 2.0, 3.0), geometry=geo, material=material, object=object_value)
    state = bundle_set(state, "nested/value", 3.0)
    nested_state = bundle_get(state, "nested", typ=Bundle)
    nested_value = bundle_get(nested_state, "value", typ=Float)
    x = bundle_get(state, path, typ=Float)
    result = set_position(geo, position() + vector(x + nested_value - nested_value, 0.0, 0.0))
    geometry = result
    return (geometry, state,)
''', "NFTest_bundle_runtime_eval")

    combine_nodes = [node for node in group.nodes if node.bl_idname == "NodeCombineBundle"]
    check(len(combine_nodes) == 2, f"expected two Combine Bundle nodes, got {len(combine_nodes)}")
    inner = next(node for node in combine_nodes if [item.name for item in node.bundle_items] == ["value", "label"])
    outer = next(node for node in combine_nodes if [item.name for item in node.bundle_items] == ["flag", "count", "nested", "direction", "geometry", "material", "object"])
    check(_bundle_items(inner) == [("value", "FLOAT", "AUTO"), ("label", "STRING", "AUTO")], f"inner bundle signature mismatch: {_bundle_items(inner)}")
    check(_bundle_items(outer) == [
        ("flag", "BOOLEAN", "AUTO"),
        ("count", "INT", "AUTO"),
        ("nested", "BUNDLE", "AUTO"),
        ("direction", "VECTOR", "AUTO"),
        ("geometry", "GEOMETRY", "AUTO"),
        ("material", "MATERIAL", "AUTO"),
        ("object", "OBJECT", "AUTO"),
    ], f"outer bundle signature mismatch: {_bundle_items(outer)}")
    check(_interface_socket(group, "State", "OUTPUT").socket_type == "NodeSocketBundle", "Bundle output socket mismatch")
    get_nodes = [node for node in group.nodes if node.bl_idname == "NodeGetBundleItem"]
    check(len(get_nodes) == 3, f"expected Bundle plus two Float get nodes, got {len(get_nodes)}")
    check(sorted(node.socket_type for node in get_nodes) == ["BUNDLE", "FLOAT", "FLOAT"], f"bundle_get socket types mismatch: {[node.socket_type for node in get_nodes]}")
    check(any(node.socket_type == "FLOAT" and node.inputs["Path"].is_linked for node in get_nodes), "runtime String path did not wire Get Bundle Item")
    store_node = _one_node(group, "NodeStoreBundleItem")
    check(store_node.socket_type == "FLOAT", "bundle_set Float socket type mismatch")
    position = _evaluate_single_vertex(group, "NFTest_bundle_eval")
    check(abs(position[0] - 3.0) < 1e-5, f"bundle_set/get runtime result mismatch: {position}")


def test_input_bundle_panel_and_passthrough_interface():
    group = compile_group('''
def nf_fixture(state: Bundle['State']):
    panel([state], name="State Panel")
    state = state
    return (state,)
''', "NFTest_bundle_interface")
    inp = _interface_socket(group, "State", "INPUT")
    out = _interface_socket(group, "State", "OUTPUT")
    check(inp.socket_type == "NodeSocketBundle", f"input_bundle type mismatch: {inp.socket_type}")
    check(out.socket_type == "NodeSocketBundle", f"Bundle output type mismatch: {out.socket_type}")
    check(getattr(inp, "parent", None) is not None and inp.parent.name == "State Panel", "Bundle input was not placed in panel")








def test_raw_node_bundle_type_and_static_bundle_sockets():
    group = compile_group('''
def nf_fixture():
    state = bundle(value=2.0)
    value = node(
        "NodeGetBundleItem",
        props={"socket_type": "FLOAT"},
        inputs={"Bundle": state, "Path": "value"},
        output="Item",
        typ=Float,
    )
    state2 = node(
        "NodeStoreBundleItem",
        props={"socket_type": "FLOAT"},
        inputs={"Bundle": state, "Path": "value", "Item": value},
        output="Bundle",
        typ=Bundle,
    )
    state = state2
    return (state,)
''', "NFTest_bundle_raw")
    get_node = _one_node(group, "NodeGetBundleItem")
    store_node = _one_node(group, "NodeStoreBundleItem")
    check(get_node.inputs["Bundle"].is_linked, "raw Get Bundle Item Bundle input not linked")
    check(store_node.outputs["Bundle"].bl_idname == "NodeSocketBundle", "raw Bundle output type mismatch")



def test_compile_time_list_can_hold_and_unroll_bundle_values():
    group = compile_group('''
def nf_fixture(total: Float['Base'] = 0.0):
    a = bundle(value=1.0)
    b = bundle(value=2.0)
    states = [a, b]
    for state in states:
        total = total + bundle_get(state, "value", typ=Float)
    total = total
    return (total,)
''', "NFTest_bundle_compile_time_list")
    gets = [node for node in group.nodes if node.bl_idname == "NodeGetBundleItem"]
    check(len(gets) == 2, f"compile-time Bundle list did not unroll two reads: {len(gets)}")

def test_bundle_runtime_if_and_repeat_range_state():
    switch_group = compile_group('''
def nf_fixture(flag: Bool['Flag']):
    a = bundle(value=1.0)
    b = bundle(value=2.0)
    state = a if flag else b
    state = state
    value = bundle_get(state, 'value', typ=Float)
    return (state, value,)
''', "NFTest_bundle_switch")
    switch = _one_node(switch_group, "GeometryNodeSwitch")
    check(switch.input_type == "BUNDLE", f"Bundle switch type mismatch: {switch.input_type}")
    check(switch.outputs[0].bl_idname == "NodeSocketBundle", "Bundle Switch output socket mismatch")

    repeat_group = compile_group('''
def nf_fixture(count: Int['Count'] = 3):
    state = bundle(value=1.0)
    for i in repeat_range(count):
        value = bundle_get(state, "value", typ=Float)
        state = bundle_set(state, "value", value + 1.0)
    state = state
    value = bundle_get(state, 'value', typ=Float)
    return (state, value,)
''', "NFTest_bundle_repeat")
    repeat_output = _one_node(repeat_group, "GeometryNodeRepeatOutput")
    state_item = next(item for item in repeat_output.repeat_items if item.name == "state")
    check(state_item.socket_type == "BUNDLE", f"Repeat Bundle state item type mismatch: {state_item.socket_type}")


def test_bundle_transactional_update_rebuilds_dynamic_signature_and_preserves_identity():
    before = '''
def nf_fixture(a: Float['A'], b: Vector['B']):
    state = bundle(a=a, b=b)
    state = state
    return (state,)
'''
    after = '''
def nf_fixture(b: Vector['B'], c: Bool['C']):
    state = bundle(b=b, c=c)
    state = state
    return (state,)
'''
    group = compile_group(before, "NFTest_bundle_update")
    pointer = group.as_pointer()
    combine = _one_node(group, "NodeCombineBundle")
    check(_bundle_items(combine) == [("a", "FLOAT", "AUTO"), ("b", "VECTOR", "AUTO")], "initial Bundle signature mismatch")
    prepared_create_or_update(after, group.name, existing_group=group)
    check(group.as_pointer() == pointer, "Bundle update replaced root datablock identity")
    combine = _one_node(group, "NodeCombineBundle")
    check(_bundle_items(combine) == [("b", "VECTOR", "AUTO"), ("c", "BOOLEAN", "AUTO")], f"updated Bundle signature mismatch: {_bundle_items(combine)}")
    real_inputs = [socket for socket in combine.inputs if socket.bl_idname != "NodeSocketVirtual"]
    check([socket.name for socket in real_inputs] == ["b", "c"], f"updated Bundle input order mismatch: {[socket.name for socket in real_inputs]}")
    check(all(socket.is_linked for socket in real_inputs), "updated Bundle dynamic inputs lost links")



def test_bundle_empty_and_store_creates_nested_path():
    group = compile_group('''
def nf_fixture(geo: Geometry['Geometry']):
    state = bundle()
    state = bundle_set(state, "created/value", 5.0)
    value = bundle_get(state, "created/value", typ=Float)
    geometry = set_position(geo, position() + vector(value, 0.0, 0.0))
    state = state
    return (geometry, state,)
''', "NFTest_bundle_create_nested_path")
    combine = _one_node(group, "NodeCombineBundle")
    check(len(combine.bundle_items) == 0, "empty bundle() unexpectedly created items")
    position = _evaluate_single_vertex(group, "NFTest_bundle_created_path_eval")
    check(abs(position[0] - 5.0) < 1e-5, f"bundle_set did not create nested path: {position}")


def test_bundle_transactional_failure_rolls_back_dynamic_signature():
    before = '''
def nf_fixture(a: Float['A'], b: Vector['B']):
    state = bundle(a=a, b=b)
    state = state
    return (state,)
'''
    after = '''
def nf_fixture(c: Bool['C']):
    state = bundle(c=c)
    state = state
    return (state,)
'''
    group = compile_group(before, "NFTest_bundle_update_rollback")
    original_pointer = group.as_pointer()
    original_signature = _bundle_items(_one_node(group, "NodeCombineBundle"))
    blender_group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        try:
            prepared_create_or_update(after, group.name, existing_group=group)
        except RuntimeError:
            pass
        else:
            raise AssertionError("injected Bundle cutover failure did not raise")
    finally:
        blender_group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = False
    check(group.as_pointer() == original_pointer, "failed Bundle update replaced group identity")
    restored = _one_node(group, "NodeCombineBundle")
    check(_bundle_items(restored) == original_signature, f"failed Bundle update did not restore signature: {_bundle_items(restored)}")
    real_inputs = [socket for socket in restored.inputs if socket.bl_idname != "NodeSocketVirtual"]
    check(all(socket.is_linked for socket in real_inputs), "failed Bundle update rollback lost dynamic input links")

def test_bundle_errors_are_controlled():
    cases = (
        ('''
def nf_fixture():
    x = bundle(1.0)
    x = x
    return (x,)
''', "positional bundle item"),
        ('''
def nf_fixture():
    x = bundle(a=1.0, a=2.0)
    x = x
    return (x,)
''', "duplicate bundle item"),
        ('''
def nf_fixture():
    values = [1.0, 2.0]
    x = bundle(values=values)
    x = x
    return (x,)
''', "array bundle item"),
        ('''
def nf_fixture():
    x = bundle(value=1.0)
    y = bundle_get(1.0, "value", typ=Float)
    y = y
    return (y,)
''', "get non bundle"),
        ('''
def nf_fixture():
    x = bundle(value=1.0)
    y = bundle_get(x, "value", typ=Missing)
    y = y
    return (y,)
''', "invalid type token"),
        ('''
def nf_fixture():
    x = bundle(value=1.0)
    y = bundle_get(x, 1.0, typ=Float)
    y = y
    return (y,)
''', "non string path"),
        ('''
def nf_fixture():
    x = bundle(value=1.0)
    y = bundle_set(x, "value", [1.0, 2.0])
    y = y
    return (y,)
''', "array set value"),
    )
    for index, (source, _label) in enumerate(cases):
        expect_compile_error(source, f"NFTest_bundle_error_{index}")


def test_straight_line_bundle_body_uses_permanent_call_ir():
    """Straight-line Bundle calls compile through the permanent typed Call IR route."""
    group = compile_group(
        '''
def nf_fixture(path: String['Path'] = 'value'):
    state = bundle(value=2.0)
    value = bundle_get(state, path, typ=Float)
    state = bundle_set(state, "value", value + 1.0)
    state = state
    return (state,)
''',
        "NFTest_bundle_semantic_body_ir",
    )
    check(len([node for node in group.nodes if node.bl_idname == "NodeCombineBundle"]) == 1, "Bundle IRBody combine topology changed")
    check(len([node for node in group.nodes if node.bl_idname == "NodeGetBundleItem"]) == 1, "Bundle IRBody get topology changed")
    check(len([node for node in group.nodes if node.bl_idname == "NodeStoreBundleItem"]) == 1, "Bundle IRBody set topology changed")
    bpy.data.node_groups.remove(group)
