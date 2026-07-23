"""Shared helpers for split Blender regression tests."""

from __future__ import annotations

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
PACKAGE_PARENT = ROOT.parent
for path in (ROOT, PACKAGE_PARENT):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

import bpy
import pytest

import NodeForge
from NodeForge import compiler, library
from NodeForge.constants import _FLOAT_FUNCS_1, _FLOAT_FUNCS_2
from NodeForge.errors import CompileError
from NodeForge import generated_resources
from NodeForge.builtins import fields, geometry, instancing, io, vector, registry
from NodeForge.systems import registry as systems_registry
from NodeForge.values import Value



def check(condition, message):
    """Raise an assertion error when a regression check fails."""
    if not condition:
        raise AssertionError(message)

def compile_group(source, name):
    """Compile one source fixture into a named node group."""
    group = compiler.create_expression_group(source, name)
    check(getattr(group, "bl_idname", None) == "GeometryNodeTree", name)
    return group

def _math_keyword_expr(name, params):
    """Build one keyword-call expression for a table-driven math builtin."""
    values = {
        "value": "0.5",
        "a": "0.75",
        "b": "0.25",
        "factor": "0.5",
        "cond": "True",
        "false": "0.0",
        "true": "1.0",
        "from_min": "0.0",
        "from_max": "1.0",
        "to_min": "-1.0",
        "to_max": "1.0",
        "x": "0.5",
        "edge": "0.25",
        "edge0": "0.0",
        "edge1": "1.0",
        "length": "1.0",
        "min": "0.0",
        "max": "1.0",
        "in_min": "0.0",
        "in_max": "1.0",
        "out_min": "-1.0",
        "out_max": "1.0",
    }
    args = ", ".join(f"{param}={values[param]}" for param in params)
    return f"{name}({args})"

def expect_compile_error(source, name, exc_type=CompileError, **kwargs):
    """Compile one source and require a controlled error type."""
    try:
        compiler._make_group(source, name, **kwargs)
    except exc_type:
        return
    except AttributeError as exc:
        raise AssertionError(f"{name} raised uncontrolled AttributeError: {exc}") from exc
    else:
        raise AssertionError(f"{name} did not raise {exc_type.__name__}")

def _object_info_source(node):
    """Return the Object referenced by an Object Info node."""
    if hasattr(node, "object"):
        obj = getattr(node, "object", None)
        if obj is not None:
            return obj
    try:
        return node.inputs["Object"].default_value
    except Exception:
        pass
    for socket in node.inputs:
        if getattr(socket, "name", "") == "Object":
            return getattr(socket, "default_value", None)
    return None

def _manifest_refs(group):
    manifest = generated_resources.read_group_manifest(group)
    check(manifest is not None, "expected generated-resource manifest")
    return manifest, generated_resources.manifest_resources(manifest)

def _owned_generated_id_keys():
    """Return all live NodeForge-generated Blender ID metadata keys."""
    keys = set()
    for id_obj in list(bpy.data.objects) + list(bpy.data.curves) + list(bpy.data.meshes):
        ref = generated_resources.read_id_metadata(id_obj)
        if ref is not None:
            keys.add((ref.kind, ref.name, ref.owner_group_uuid, ref.generation_uuid))
    return keys

def _ref_by_kind(refs, kind):
    for ref in refs:
        if ref.kind == kind:
            return ref
    raise AssertionError(f"missing generated {kind} ref")

def _collection_for_ref(ref):
    if ref.kind == "CURVE":
        return bpy.data.curves
    if ref.kind == "MESH":
        return bpy.data.meshes
    return bpy.data.objects

def _create_user_object_using_generated_curve(ref, name):
    curve = bpy.data.curves.get(ref.name)
    check(curve is not None, f"generated Curve missing before user-share test: {ref.name}")
    user_obj = bpy.data.objects.new(name, curve)
    check(generated_resources.read_id_metadata(user_obj) is None, "user-created Object unexpectedly has NodeForge metadata")
    check(user_obj.data is curve, "user Object did not retain generated Curve data")
    try:
        scene = getattr(bpy.context, "scene", None)
        collection = getattr(scene, "collection", None) or getattr(bpy.context, "collection", None)
        if collection is not None:
            collection.objects.link(user_obj)
    except Exception:
        pass
    return curve, user_obj

def _remove_user_object_and_generated_curve(user_obj, curve):
    try:
        if bpy.data.objects.get(user_obj.name) is user_obj:
            bpy.data.objects.remove(user_obj, do_unlink=True)
    except ReferenceError:
        pass
    except Exception:
        pass
    try:
        if bpy.data.curves.get(curve.name) is curve:
            generated_resources.delete_generated_id_object(curve)
            if bpy.data.curves.get(curve.name) is curve:
                bpy.data.curves.remove(curve, do_unlink=True)
    except ReferenceError:
        pass
    except Exception:
        pass

def _assert_static_baked_group(group):
    manifest, refs = _manifest_refs(group)
    check(len(refs) == 2, f"expected Curve/Object resources, got {refs}")
    kinds = {ref.kind for ref in refs}
    check(kinds == {"CURVE", "OBJECT"}, f"unexpected generated resource kinds: {kinds}")
    for ref in refs:
        coll = bpy.data.curves if ref.kind == "CURVE" else bpy.data.objects
        id_obj = coll.get(ref.name)
        check(id_obj is not None, f"generated {ref.kind} missing: {ref.name}")
        meta = generated_resources.read_id_metadata(id_obj)
        check(meta is not None, f"generated {ref.kind} lacks positive ownership metadata")
        check(meta.owner_group_uuid == manifest["owner_group_uuid"], "ID/group owner UUID mismatch")
    object_infos = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeObjectInfo"]
    check(len(object_infos) == 1, f"expected one Object Info node, got {len(object_infos)}")
    obj = _object_info_source(object_infos[0])
    check(obj is not None and generated_resources.read_id_metadata(obj) is not None, "Object Info does not source owned hidden object")
    geometry_outputs = [socket for socket in object_infos[0].outputs if getattr(socket, "name", "") == "Geometry"]
    check(geometry_outputs, "Object Info Geometry output missing")
    curve_line_nodes = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeCurvePrimitiveLine"]
    check(not curve_line_nodes, "static baked backend used per-segment Curve Line nodes")
    return manifest, refs, obj

def _rename_generated_refs(refs, suffix):
    """Rename generated IDs while preserving ownership metadata for cleanup checks."""
    renamed = []
    for ref in refs:
        coll = _collection_for_ref(ref)
        id_obj = coll.get(ref.name)
        check(id_obj is not None, f"generated resource missing before rename: {ref.name}")
        id_obj.name = id_obj.name + suffix
        renamed.append((ref.kind, id_obj.name))
        meta = generated_resources.read_id_metadata(id_obj)
        check(meta is not None and meta.name == ref.name, "generated ID metadata should remain stable after user rename")
    return renamed

def _socket_identifier_by_name(group, socket_name, in_out="INPUT"):
    for item in getattr(group.interface, "items_tree", []):
        if (
            getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "name", None) == socket_name
            and getattr(item, "in_out", None) == in_out
        ):
            return getattr(item, "identifier", None)
    raise AssertionError(f"missing {in_out} interface socket {socket_name!r}")

def _new_runtime_eval_wrapper(compiled_group, name):
    """Wrap a compiled geometry group with Curve to Mesh for depsgraph inspection."""
    wrapper = bpy.data.node_groups.new(name, "GeometryNodeTree")
    wrapper.interface.new_socket(name="Angle", in_out="INPUT", socket_type="NodeSocketFloat")
    wrapper.interface.new_socket(name="Step", in_out="INPUT", socket_type="NodeSocketFloat")
    wrapper.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input = wrapper.nodes.new("NodeGroupInput")
    group_output = wrapper.nodes.new("NodeGroupOutput")
    group_output.is_active_output = True
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = compiled_group
    curve_to_mesh = wrapper.nodes.new("GeometryNodeCurveToMesh")
    wrapper.links.new(group_input.outputs["Angle"], group_node.inputs["Angle"])
    wrapper.links.new(group_input.outputs["Step"], group_node.inputs["Step"])
    wrapper.links.new(group_node.outputs["Geometry"], curve_to_mesh.inputs["Curve"])
    wrapper.links.new(curve_to_mesh.outputs["Mesh"], group_output.inputs["Geometry"])
    return wrapper

def _modifier_input_prop(modifier, group, socket_name):
    identifier = _socket_identifier_by_name(group, socket_name, "INPUT")
    try:
        prop = getattr(modifier.properties.inputs, identifier)
    except Exception as exc:
        raise AssertionError(f"modifier input {socket_name!r} / {identifier!r} is unavailable") from exc
    if not hasattr(prop, "value"):
        raise AssertionError(f"modifier input {socket_name!r} / {identifier!r} has no runtime value property")
    return prop

def _set_modifier_input(modifier, group, socket_name, value):
    prop = _modifier_input_prop(modifier, group, socket_name)
    try:
        prop.value = float(value)
    except Exception as exc:
        raise AssertionError(f"could not set modifier input {socket_name!r}") from exc

def _attach_runtime_eval_modifier(compiled_group, name):
    wrapper = _new_runtime_eval_wrapper(compiled_group, name + "_Wrapper")
    mesh_data = bpy.data.meshes.new(name + "_BaseMesh")
    obj = bpy.data.objects.new(name + "_Object", mesh_data)
    bpy.context.collection.objects.link(obj)
    mod = obj.modifiers.new("NodeForge", "NODES")
    mod.node_group = wrapper
    return wrapper, obj, mesh_data, mod

def _evaluated_mesh_snapshot(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        vertices = tuple(tuple(round(float(coord), 6) for coord in vertex.co) for vertex in mesh.vertices)
        edges = tuple(tuple(int(index) for index in edge.vertices) for edge in mesh.edges)
        polygons = len(mesh.polygons)
    finally:
        evaluated.to_mesh_clear()
    return vertices, edges, polygons

def _cleanup_runtime_eval_objects(wrapper, obj, mesh_data):
    try:
        if bpy.data.objects.get(obj.name) is obj:
            bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        pass
    try:
        if bpy.data.meshes.get(mesh_data.name) is mesh_data:
            bpy.data.meshes.remove(mesh_data, do_unlink=True)
    except Exception:
        pass
    try:
        if bpy.data.node_groups.get(wrapper.name) is wrapper:
            bpy.data.node_groups.remove(wrapper, do_unlink=True)
    except Exception:
        pass

def _attribute_values(attr):
    values = []
    for item in attr.data:
        if hasattr(item, "value"):
            values.append(item.value)
        else:
            values.append(None)
    return tuple(values)

def _input_socket_by_name(node, name):
    if isinstance(name, int):
        try:
            return node.inputs[name]
        except Exception as exc:
            raise AssertionError(f"node {getattr(node, 'bl_idname', node)!r} has no input socket index {name}") from exc
    try:
        return node.inputs[name]
    except Exception:
        pass
    for socket in node.inputs:
        if getattr(socket, "name", "") == name:
            return socket
    raise AssertionError(f"node {getattr(node, 'bl_idname', node)!r} has no input socket {name!r}")

def _linked_source_node(group, node, input_name):
    socket = _input_socket_by_name(node, input_name)
    matches = [
        link
        for link in group.links
        if link.to_node == node
        and (link.to_socket == socket or getattr(link.to_socket, "identifier", None) == getattr(socket, "identifier", None))
    ]
    check(len(matches) == 1, f"expected one link into {getattr(node, 'bl_idname', node)}.{input_name}, got {len(matches)}")
    return matches[0].from_node

def _named_attribute_node_name(node):
    for socket in getattr(node, "inputs", []):
        if getattr(socket, "name", "") == "Name":
            return getattr(socket, "default_value", None)
    for socket in getattr(node, "inputs", []):
        value = getattr(socket, "default_value", None)
        if isinstance(value, str):
            return value
    return None


__all__ = [name for name in globals() if not name.startswith("__")]
