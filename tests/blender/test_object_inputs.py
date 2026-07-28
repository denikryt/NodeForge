"""Blender integration coverage for Object sockets and lazy Object Info properties."""

import json
import math
import tempfile
from pathlib import Path

import bpy

from helpers import (
    check,
    compile_group,
    expect_compile_error,
    _evaluated_mesh_snapshot,
    _socket_identifier_by_name,
)
from NodeForge import packages


def _interface_socket(group, name, in_out):
    return next(
        item
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET"
        and item.name == name
        and item.in_out == in_out
    )


def _object_info_nodes(group):
    return [node for node in group.nodes if node.bl_idname == "GeometryNodeObjectInfo"]


def _new_mesh_object(name, vertices, faces=()):
    mesh = bpy.data.meshes.new(name + "Mesh")
    mesh.from_pydata(vertices, [], faces)
    mesh.update()
    obj = bpy.data.objects.new(name, mesh)
    bpy.context.collection.objects.link(obj)
    return obj, mesh


def _attach_modifier(group, name):
    target, target_mesh = _new_mesh_object(name, [])
    modifier = target.modifiers.new("NodeForge", "NODES")
    modifier.node_group = group
    return target, target_mesh, modifier


def _set_object_input(modifier, group, socket_name, value):
    identifier = _socket_identifier_by_name(group, socket_name, "INPUT")
    prop = getattr(modifier.properties.inputs, identifier)
    prop.value = value


def _cleanup_objects(*items):
    for item in items:
        if isinstance(item, bpy.types.Object):
            if bpy.data.objects.get(item.name) is item:
                bpy.data.objects.remove(item, do_unlink=True)
        elif isinstance(item, bpy.types.Mesh):
            if bpy.data.meshes.get(item.name) is item:
                bpy.data.meshes.remove(item, do_unlink=True)


def _write_package_manifest(root, package_id):
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": package_id,
                "name": package_id,
                "version": "1.0.0",
                "nodeforge_min_version": "0.49.52",
                "contents": {"functions": "functions"},
                "permissions": {"python": False},
            }
        ),
        encoding="utf-8",
    )


def test_object_input_defaults_lazy_cache_chaining_and_properties():
    group = compile_group(
        '''
obj = input_object("Source")
same = obj.info()
location = same.location
rotation = obj.rotation
scale = obj.scale
output("Geometry", obj.geometry)
''',
        "NFTest_object_defaults_properties",
    )
    check(_interface_socket(group, "Source", "INPUT").socket_type == "NodeSocketObject", "Object interface socket missing")
    nodes = _object_info_nodes(group)
    check(len(nodes) == 1, "Object properties must share one Object Info node")
    check(nodes[0].transform_space == "ORIGINAL", "Object Info default transform space changed")
    check(nodes[0].inputs["As Instance"].default_value is True, "Object Info default As Instance changed")
    check(nodes[0].outputs.get("Location") is not None, "Object Info Location output missing")
    check(nodes[0].outputs.get("Rotation") is not None, "Object Info Rotation output missing")
    check(nodes[0].outputs.get("Scale") is not None, "Object Info Scale output missing")
    check(nodes[0].outputs.get("Geometry") is not None, "Object Info Geometry output missing")


def test_object_info_partial_unresolved_updates_and_explicit_configuration():
    group = compile_group(
        '''
obj = input_object("Source")
obj.info(transform_space="RELATIVE")
obj.info(as_instance=False)
output("Geometry", obj.geometry)
''',
        "NFTest_object_partial_config",
    )
    nodes = _object_info_nodes(group)
    check(len(nodes) == 1, "partial configuration created multiple Object Info nodes")
    check(nodes[0].transform_space == "RELATIVE", "partial transform_space update was lost")
    check(nodes[0].inputs["As Instance"].default_value is False, "partial as_instance update was lost")


def test_object_compile_time_contract_and_unknown_properties():
    invalid_sources = (
        ('obj = input_object("O", default=None)\noutput("G", cube(1.0))', "default"),
        ('obj = input_object("O", transform_space="RELATIVE")\noutput("G", cube(1.0))', "input transform_space"),
        ('obj = input_object("O", as_instance=False)\noutput("G", cube(1.0))', "input as_instance"),
        ('obj = input_object("O", "extra")\noutput("G", cube(1.0))', "input positional"),
        ('x = input_float("X", as_instance=True)\noutput("G", cube(1.0))', "keyword leak float"),
        ('g = input_geometry("G", transform_space="RELATIVE")\noutput("G", g)', "keyword leak geometry"),
        ('obj = input_object("O")\nobj.info("RELATIVE")\noutput("G", cube(1.0))', "info positional"),
        ('obj = input_object("O")\nobj.info(unknown=True)\noutput("G", cube(1.0))', "info unknown keyword"),
        ('obj = input_object("O")\nobj.info(transform_space="WORLD")\noutput("G", cube(1.0))', "info invalid transform"),
        ('obj = input_object("O")\nspace = input_float("Space")\nobj.info(transform_space=space)\noutput("G", cube(1.0))', "info runtime transform"),
        ('obj = input_object("O")\nobj.info(as_instance=1)\noutput("G", cube(1.0))', "info non-bool instance"),
        ('obj = input_object("O")\nflag = input_bool("Flag")\nobj.info(as_instance=flag)\noutput("G", cube(1.0))', "info runtime instance"),
        ('obj = input_object("O")\ng = obj.geometry\nobj.info()\noutput("G", g)', "info resolution lock"),
        ('obj = input_object("O")\ng = obj.geometry\nobj.info(as_instance=True)\noutput("G", g)', "info identical resolution lock"),
        ('obj = input_object("O")\nx = obj.transform\noutput("G", cube(1.0))', "unknown Object property"),
        ('x = input_float("X")\nx.info()\noutput("G", cube(1.0))', "info non-object"),
    )
    for source, suffix in invalid_sources:
        expect_compile_error(source, "NFTest_object_invalid_" + suffix.replace(" ", "_"))


def test_local_function_object_input_and_output_keep_object_behavior():
    group = compile_group(
        '''
def passthrough(source):
    return source

obj = input_object("Source")
returned = passthrough(obj)
returned.info(transform_space="RELATIVE", as_instance=False)
output("Geometry", returned.geometry)
''',
        "NFTest_object_local_boundary",
    )
    group_nodes = [node for node in group.nodes if node.bl_idname == "GeometryNodeGroup"]
    check(group_nodes, "local Object helper group was not created")
    check(group_nodes[0].outputs[0].bl_idname == "NodeSocketObject", "local helper Object output type was lost")
    info_nodes = _object_info_nodes(group)
    check(len(info_nodes) == 1, "caller did not resolve returned local Object through Object Info")
    check(info_nodes[0].transform_space == "RELATIVE", "caller-owned local Object configuration was lost")


def test_raw_object_output_keeps_object_value_behavior():
    group = compile_group(
        '''
obj = node(
    "GeometryNodeInputObject",
    output="Object",
    typ=Object,
)
obj.info(as_instance=False)
output("Geometry", obj.geometry)
''',
        "NFTest_object_raw_output",
    )
    check(any(node.bl_idname == "GeometryNodeInputObject" for node in group.nodes), "raw Object node missing")
    info_nodes = _object_info_nodes(group)
    check(len(info_nodes) == 1, "raw Object output did not retain ObjectValue behavior")
    check(info_nodes[0].inputs["As Instance"].default_value is False, "raw Object configuration was lost")


def test_installed_library_object_input_and_output_keep_object_behavior():
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "vendor.objecttest"
        functions = root / "functions"
        functions.mkdir(parents=True)
        _write_package_manifest(root, "vendor.objecttest")
        (functions / "object_passthrough.nf").write_text(
            'source = input_object("Source")\noutput("Value", source)\n',
            encoding="utf-8",
        )
        packages.install_package_directory(root, allow_python=False)
        try:
            group = compile_group(
                '''
from functions import object_passthrough
obj = input_object("Source")
returned = object_passthrough(obj)
returned.info(transform_space="RELATIVE", as_instance=False)
output("Geometry", returned.geometry)
''',
                "NFTest_object_installed_library_boundary",
            )
            function_nodes = [node for node in group.nodes if node.bl_idname == "GeometryNodeGroup"]
            check(function_nodes, "installed Object library call node missing")
            check(function_nodes[0].outputs[0].bl_idname == "NodeSocketObject", "installed library Object output type was lost")
            info_nodes = _object_info_nodes(group)
            check(len(info_nodes) == 1, "installed library Object output did not retain property behavior")
        finally:
            packages.uninstall_package("vendor.objecttest")


def test_object_runtime_geometry_location_rotation_and_scale_evaluation():
    source_obj, source_mesh = _new_mesh_object(
        "NFTestObjectSource",
        [(0, 0, 0), (1, 0, 0), (0, 1, 0)],
        [(0, 1, 2)],
    )
    source_obj.location = (2.0, 3.0, 4.0)
    source_obj.rotation_euler = (0.25, 0.5, 0.75)
    source_obj.scale = (1.5, 2.0, 2.5)

    geometry_group = compile_group(
        'obj = input_object("Source")\nobj.info(as_instance=False)\noutput("Geometry", obj.geometry)',
        "NFTest_object_runtime_geometry",
    )
    target_obj, target_mesh, modifier = _attach_modifier(geometry_group, "NFTestObjectGeometryTarget")
    _set_object_input(modifier, geometry_group, "Source", source_obj)
    vertices, _, polygons = _evaluated_mesh_snapshot(target_obj)
    check(len(vertices) == 3 and polygons == 1, f"Object geometry runtime evaluation failed: {vertices}, {polygons}")

    vector_group = compile_group(
        '''
obj = input_object("Source")
location_point = point(obj.location)
rotation_point = point(obj.rotation + vector(10.0, 0.0, 0.0))
scale_point = point(obj.scale + vector(20.0, 0.0, 0.0))
output("Geometry", join(location_point, rotation_point, scale_point))
''',
        "NFTest_object_runtime_vectors",
    )
    vector_target, vector_mesh, vector_modifier = _attach_modifier(vector_group, "NFTestObjectVectorTarget")
    _set_object_input(vector_modifier, vector_group, "Source", source_obj)
    vector_vertices, _, _ = _evaluated_mesh_snapshot(vector_target)
    expected = {
        (2.0, 3.0, 4.0),
        (round(10.0 + 0.25, 6), 0.5, 0.75),
        (21.5, 2.0, 2.5),
    }
    check(set(vector_vertices) == expected, f"Object vector runtime outputs changed: {vector_vertices}")

    _cleanup_objects(target_obj, target_mesh, vector_target, vector_mesh, source_obj, source_mesh)
