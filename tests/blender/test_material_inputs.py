"""Blender integration coverage for runtime Material values."""

import bpy

from helpers import check, compile_group, expect_compile_error
from NodeForge.constants import TYPE_MATERIAL
from NodeForge.library import _socket_type_to_value_type


def _interface_socket(group, name, in_out):
    return next(
        item
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET"
        and getattr(item, "name", None) == name
        and getattr(item, "in_out", None) == in_out
    )


def test_input_material_wires_set_material_and_preserves_named_material_mode():
    group = compile_group('''
mat = input_material("Surface Material")
geo = set_material(cube(1.0), mat)
output("Geometry", geo)
''', "NFTest_runtime_material")
    iface = _interface_socket(group, "Surface Material", "INPUT")
    check(iface.socket_type == "NodeSocketMaterial", "input_material did not create a Material interface socket")
    set_node = next(node for node in group.nodes if node.bl_idname == "GeometryNodeSetMaterial")
    check(set_node.inputs["Material"].is_linked, "runtime Material input was not linked")
    check(_socket_type_to_value_type(set_node.inputs["Material"]) == TYPE_MATERIAL, "Material socket type mapping failed")

    named = compile_group('''
geo = set_material(cube(1.0), "NFTest Named Material")
output("Geometry", geo)
''', "NFTest_named_material_compat")
    named_node = next(node for node in named.nodes if node.bl_idname == "GeometryNodeSetMaterial")
    check(named_node.inputs["Material"].default_value == bpy.data.materials["NFTest Named Material"], "named material compatibility failed")


def test_material_local_function_and_raw_node_sockets():
    local = compile_group('''
mat = input_material("Material")
def apply(geo, material):
    return set_material(geo, material)
out = apply(cube(1.0), mat)
output("Geometry", out)
''', "NFTest_local_material")
    helper = next(group for group in bpy.data.node_groups if group.get("nodeforge_generated_kind") == "local_function_helper" and group.get("nodeforge_local_function_name") == "apply")
    check(_interface_socket(helper, "material", "INPUT").socket_type == "NodeSocketMaterial", "local function Material parameter missing")

    raw = compile_group('''
mat = input_material("Material")
geo = node(
    "GeometryNodeSetMaterial",
    inputs={"Geometry": cube(1.0), "Material": mat},
    output="Geometry",
    typ=Geometry,
)
output("Geometry", geo)
''', "NFTest_raw_material")
    raw_node = next(node for node in raw.nodes if node.bl_idname == "GeometryNodeSetMaterial")
    check(raw_node.inputs["Material"].is_linked, "raw Material input was not linked")

    raw_output = compile_group('''
mat = node("GeometryNodeInputMaterial", output="Material", typ=Material)
geo = set_material(cube(1.0), mat)
output("Geometry", geo)
''', "NFTest_raw_material_output")
    input_node = next(node for node in raw_output.nodes if node.bl_idname == "GeometryNodeInputMaterial")
    check(input_node.outputs["Material"].is_linked, "raw Material output was not linked")


def test_set_material_rejects_non_material_runtime_value():
    expect_compile_error('''
value = input_float("Value")
geo = set_material(cube(1.0), value)
output("Geometry", geo)
''', "NFTest_bad_runtime_material")
