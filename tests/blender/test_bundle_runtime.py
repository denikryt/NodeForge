"""Blender integration coverage for first-class runtime Bundle values."""

from helpers import *

import json
import math
import tempfile
from pathlib import Path

import bpy

from NodeForge import compiler, packages
from NodeForge.constants import TYPE_BUNDLE


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
geo = input_geometry("Geometry")
path = input_string("Path", default="nested/value")
material = input_material("Material")
object_value = input_object("Object")
count = input_int("Count", default=2)
inner = bundle(value=1.0, label="inner")
state = bundle(flag=True, count=count, nested=inner, direction=vector(1.0, 2.0, 3.0), geometry=geo, material=material, object=object_value)
state = bundle_set(state, "nested/value", 3.0)
nested_state = bundle_get(state, "nested", typ=Bundle)
nested_value = bundle_get(nested_state, "value", typ=Float)
x = bundle_get(state, path, typ=Float)
result = set_position(geo, position() + vector(x + nested_value - nested_value, 0.0, 0.0))
output("Geometry", result)
output("State", state)
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
state = input_bundle("State")
panel([state], name="State Panel")
output("State", state)
''', "NFTest_bundle_interface")
    inp = _interface_socket(group, "State", "INPUT")
    out = _interface_socket(group, "State", "OUTPUT")
    check(inp.socket_type == "NodeSocketBundle", f"input_bundle type mismatch: {inp.socket_type}")
    check(out.socket_type == "NodeSocketBundle", f"Bundle output type mismatch: {out.socket_type}")
    check(getattr(inp, "parent", None) is not None and inp.parent.name == "State Panel", "Bundle input was not placed in panel")


def test_bundle_local_function_parameter_return_tuple_and_hidden_capture():
    group = compile_group('''
def identity(state: Bundle):
    return state

def pair(state: Bundle):
    return state, bundle_get(state, "value", typ=Float)

captured = bundle(value=4.0)
def use_capture():
    return captured

source = bundle(value=2.0)
runtime = identity(source)
state, value = pair(runtime)
hidden = use_capture()
output("State", state)
output("Value", value)
output("Hidden", hidden)
''', "NFTest_bundle_local_functions")
    helpers = {
        node.node_tree.get("nodeforge_local_function_name"): node.node_tree
        for node in group.nodes
        if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None and node.node_tree.get("nodeforge_local_function_name")
    }
    check({"identity", "pair", "use_capture"} <= set(helpers), f"missing Bundle helpers: {set(helpers)}")
    check(_interface_socket(helpers["identity"], "state").socket_type == "NodeSocketBundle", "Bundle local parameter mismatch")
    check(_interface_socket(helpers["identity"], "Value", "OUTPUT").socket_type == "NodeSocketBundle", "Bundle local return mismatch")
    pair_outputs = [item for item in helpers["pair"].interface.items_tree if getattr(item, "in_out", None) == "OUTPUT"]
    check([item.socket_type for item in pair_outputs] == ["NodeSocketBundle", "NodeSocketFloat"], f"Bundle tuple return mismatch: {[item.socket_type for item in pair_outputs]}")
    capture_inputs = [item for item in helpers["use_capture"].interface.items_tree if getattr(item, "in_out", None) == "INPUT"]
    check(any(item.socket_type == "NodeSocketBundle" for item in capture_inputs), "hidden Bundle capture did not materialize as Bundle input")


def test_bundle_packaged_library_function_round_trip():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "vendor.bundle"
        functions = root / "functions"
        functions.mkdir(parents=True)
        _write_package_manifest(root, "vendor.bundle")
        (functions / "bundle_identity.nf").write_text(
            'state = input_bundle("State")\noutput("State", state)\n',
            encoding="utf-8",
        )
        packages.set_packages_dir_for_tests(tmp / "inventory")
        try:
            packages.install_package_directory(root, allow_python=False)
            group = compile_group('''
from functions import bundle_identity
state = bundle(value=2.0)
result = bundle_identity(state)
value = bundle_get(result, "value", typ=Float)
output("State", result)
output("Value", value)
''', "NFTest_bundle_library")
            calls = [node for node in group.nodes if node.bl_idname == "GeometryNodeGroup"]
            check(len(calls) == 1, f"expected one Bundle library call, got {len(calls)}")
            call = calls[0]
            check(call.inputs["State"].bl_idname == "NodeSocketBundle" and call.inputs["State"].is_linked, "library Bundle input mismatch")
            check(call.outputs["State"].bl_idname == "NodeSocketBundle", "library Bundle output mismatch")
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()


def test_bundle_local_catalog_round_trip():
    local = library.ensure_local_catalog_dir()
    source = local / "bundle_local_catalog_probe.nf"
    try:
        source.write_text(
            'state = input_bundle("State")\noutput("State", state)\n',
            encoding="utf-8",
        )
        group = compile_group(
            'from local import bundle_local_catalog_probe\n'
            'state = bundle(value=7.0)\n'
            'result = bundle_local_catalog_probe(state)\n'
            'value = bundle_get(result, "value", typ=Float)\n'
            'output("State", result)\n'
            'output("Value", value)\n',
            "NFTest_bundle_local_catalog",
        )
        calls = [node for node in group.nodes if node.bl_idname == "GeometryNodeGroup"]
        check(len(calls) == 1, f"expected one local Bundle call, got {len(calls)}")
        call = calls[0]
        check(call.inputs["State"].bl_idname == "NodeSocketBundle", "local Bundle input mismatch")
        check(call.outputs["State"].bl_idname == "NodeSocketBundle", "local Bundle output mismatch")
    finally:
        source.unlink(missing_ok=True)


def test_raw_node_bundle_type_and_static_bundle_sockets():
    group = compile_group('''
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
output("State", state2)
''', "NFTest_bundle_raw")
    get_node = _one_node(group, "NodeGetBundleItem")
    store_node = _one_node(group, "NodeStoreBundleItem")
    check(get_node.inputs["Bundle"].is_linked, "raw Get Bundle Item Bundle input not linked")
    check(store_node.outputs["Bundle"].bl_idname == "NodeSocketBundle", "raw Bundle output type mismatch")



def test_compile_time_list_can_hold_and_unroll_bundle_values():
    group = compile_group('''
a = bundle(value=1.0)
b = bundle(value=2.0)
states = [a, b]
total = input_float("Base", default=0.0)
for state in states:
    total = total + bundle_get(state, "value", typ=Float)
output("Total", total)
''', "NFTest_bundle_compile_time_list")
    gets = [node for node in group.nodes if node.bl_idname == "NodeGetBundleItem"]
    check(len(gets) == 2, f"compile-time Bundle list did not unroll two reads: {len(gets)}")

def test_bundle_runtime_if_and_repeat_range_state():
    switch_group = compile_group('''
flag = input_bool("Flag")
a = bundle(value=1.0)
b = bundle(value=2.0)
state = a if flag else b
output("State", state)
output("Value", bundle_get(state, "value", typ=Float))
''', "NFTest_bundle_switch")
    switch = _one_node(switch_group, "GeometryNodeSwitch")
    check(switch.input_type == "BUNDLE", f"Bundle switch type mismatch: {switch.input_type}")
    check(switch.outputs[0].bl_idname == "NodeSocketBundle", "Bundle Switch output socket mismatch")

    repeat_group = compile_group('''
count = input_int("Count", default=3)
state = bundle(value=1.0)
for i in repeat_range(count):
    value = bundle_get(state, "value", typ=Float)
    state = bundle_set(state, "value", value + 1.0)
output("State", state)
output("Value", bundle_get(state, "value", typ=Float))
''', "NFTest_bundle_repeat")
    repeat_output = _one_node(repeat_group, "GeometryNodeRepeatOutput")
    state_item = next(item for item in repeat_output.repeat_items if item.name == "state")
    check(state_item.socket_type == "BUNDLE", f"Repeat Bundle state item type mismatch: {state_item.socket_type}")


def test_bundle_transactional_update_rebuilds_dynamic_signature_and_preserves_identity():
    before = '''
a = input_float("A")
b = input_vector("B")
state = bundle(a=a, b=b)
output("State", state)
'''
    after = '''
b = input_vector("B")
c = input_bool("C")
state = bundle(b=b, c=c)
output("State", state)
'''
    group = compile_group(before, "NFTest_bundle_update")
    pointer = group.as_pointer()
    combine = _one_node(group, "NodeCombineBundle")
    check(_bundle_items(combine) == [("a", "FLOAT", "AUTO"), ("b", "VECTOR", "AUTO")], "initial Bundle signature mismatch")
    compiler.update_expression_group(group, after)
    check(group.as_pointer() == pointer, "Bundle update replaced root datablock identity")
    combine = _one_node(group, "NodeCombineBundle")
    check(_bundle_items(combine) == [("b", "VECTOR", "AUTO"), ("c", "BOOLEAN", "AUTO")], f"updated Bundle signature mismatch: {_bundle_items(combine)}")
    real_inputs = [socket for socket in combine.inputs if socket.bl_idname != "NodeSocketVirtual"]
    check([socket.name for socket in real_inputs] == ["b", "c"], f"updated Bundle input order mismatch: {[socket.name for socket in real_inputs]}")
    check(all(socket.is_linked for socket in real_inputs), "updated Bundle dynamic inputs lost links")



def test_bundle_empty_and_store_creates_nested_path():
    group = compile_group('''
geo = input_geometry("Geometry")
state = bundle()
state = bundle_set(state, "created/value", 5.0)
value = bundle_get(state, "created/value", typ=Float)
output("Geometry", set_position(geo, position() + vector(value, 0.0, 0.0)))
output("State", state)
''', "NFTest_bundle_create_nested_path")
    combine = _one_node(group, "NodeCombineBundle")
    check(len(combine.bundle_items) == 0, "empty bundle() unexpectedly created items")
    position = _evaluate_single_vertex(group, "NFTest_bundle_created_path_eval")
    check(abs(position[0] - 5.0) < 1e-5, f"bundle_set did not create nested path: {position}")


def test_bundle_transactional_failure_rolls_back_dynamic_signature():
    before = '''
a = input_float("A")
b = input_vector("B")
state = bundle(a=a, b=b)
output("State", state)
'''
    after = '''
c = input_bool("C")
state = bundle(c=c)
output("State", state)
'''
    group = compile_group(before, "NFTest_bundle_update_rollback")
    original_pointer = group.as_pointer()
    original_signature = _bundle_items(_one_node(group, "NodeCombineBundle"))
    compiler._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        try:
            compiler.update_expression_group(group, after)
        except RuntimeError:
            pass
        else:
            raise AssertionError("injected Bundle cutover failure did not raise")
    finally:
        compiler._TEST_CUTOVER_FAIL_AFTER_RESET = False
    check(group.as_pointer() == original_pointer, "failed Bundle update replaced group identity")
    restored = _one_node(group, "NodeCombineBundle")
    check(_bundle_items(restored) == original_signature, f"failed Bundle update did not restore signature: {_bundle_items(restored)}")
    real_inputs = [socket for socket in restored.inputs if socket.bl_idname != "NodeSocketVirtual"]
    check(all(socket.is_linked for socket in real_inputs), "failed Bundle update rollback lost dynamic input links")

def test_bundle_errors_are_controlled():
    cases = (
        ('x = bundle(1.0)\noutput("X", x)', "positional bundle item"),
        ('x = bundle(a=1.0, a=2.0)\noutput("X", x)', "duplicate bundle item"),
        ('values = [1.0, 2.0]\nx = bundle(values=values)\noutput("X", x)', "array bundle item"),
        ('x = bundle(value=1.0)\ny = bundle_get(1.0, "value", typ=Float)\noutput("Y", y)', "get non bundle"),
        ('x = bundle(value=1.0)\ny = bundle_get(x, "value", typ=Missing)\noutput("Y", y)', "invalid type token"),
        ('x = bundle(value=1.0)\ny = bundle_get(x, 1.0, typ=Float)\noutput("Y", y)', "non string path"),
        ('x = bundle(value=1.0)\ny = bundle_set(x, "value", [1.0, 2.0])\noutput("Y", y)', "array set value"),
    )
    for index, (source, _label) in enumerate(cases):
        expect_compile_error(source, f"NFTest_bundle_error_{index}")
