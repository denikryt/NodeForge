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
from NodeForge.systems.lsystem import backends as lsystem_backends
from NodeForge.systems.lsystem.analysis import analyze as analyze_lsystem
from NodeForge.systems.lsystem.expander import expand as expand_lsystem
from NodeForge.systems.lsystem.backends import MAX_LSYSTEM_BRANCH_DEPTH
from NodeForge.systems.lsystem.runtime_tables import (
    build_branch_aware_command_table,
    build_branch_free_command_table,
)
from NodeForge.values import Value
from NodeForge.systems.lsystem.runtime_tables import (
    ANCHOR_MASK_ATTR,
    DRAW_MASK_ATTR,
    HEADING_INDEX_ATTR,
    MOVE_MASK_ATTR,
    PARENT_ATTACH_INDEX_ATTR,
    PATH_DEPTH_ATTR,
    PATH_ID_ATTR,
)



def _static_lsystem_source(iterations=1, step=0.1):
    return """
geo = ls_system(ls_axiom("F"), ls_rule("F", "F+F--F+F"), ls_iterations(%d), ls_angle(60), ls_step(%s))
output("Geometry", geo)
""" % (iterations, step)


LSYSTEM_GALLERY_EXAMPLES = {
    "static_koch_curve": '''
geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F+F--F+F"),
    ls_iterations(3),
    ls_angle(60),
    ls_step(0.1),
)
output("Geometry", geo)
''',
    "runtime_branch_free_curve": '''
angle_value = input_float("Angle", default=90.0)
step_value = input_float("Step", default=0.25)

geo = ls_system(
    ls_axiom("F+F+F+F"),
    ls_iterations(0),
    ls_angle(angle_value),
    ls_step(step_value),
)
output("Geometry", geo)
''',
    "runtime_branched_plant": '''
angle_value = input_float("Angle", default=25.0)
step_value = input_float("Step", default=0.12)

geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(2),
    ls_angle(angle_value),
    ls_step(step_value),
)
output("Geometry", geo)
''',
    "grammar_symbols": '''
geo = ls_system(
    ls_axiom("X"),
    ls_rule("X", "F+X"),
    ls_iterations(3),
    ls_angle(60),
    ls_step(0.1),
)
output("Geometry", geo)
''',
    "composed_geometry": '''
plant = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(1),
    ls_angle(25),
    ls_step(0.2),
)
plant = transform(plant, translation=vector(0, 0, 1))
base = grid(2, 2)
geo = join(base, plant)
output("Geometry", geo)
''',
}

LSYSTEM_BENCHMARK_FIXTURES = (
    {
        "name": "static_straight_1k",
        "category": "static_straight",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 10,
        "runtime": False,
    },
    {
        "name": "static_straight_10k",
        "category": "static_straight",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 14,
        "runtime": False,
    },
    {
        "name": "static_straight_large",
        "category": "static_straight",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 16,
        "runtime": False,
    },
    {
        "name": "static_branched",
        "category": "static_branched",
        "axiom": "F",
        "rules": (("F", "F[+F]F[-F]F"),),
        "iterations": 4,
        "runtime": False,
    },
    {
        "name": "branch_free_runtime_line_large",
        "category": "branch_free_runtime_line",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 11,
        "runtime": True,
    },
    {
        "name": "branch_free_runtime_turns",
        "category": "branch_free_runtime_turns",
        "axiom": "F",
        "rules": (("F", "F+F--F+F"),),
        "iterations": 3,
        "runtime": True,
    },
    {
        "name": "branched_runtime_shallow_wide",
        "category": "branched_runtime_shallow_wide",
        "axiom": "F" + "[+F]" * 64,
        "rules": (),
        "iterations": 0,
        "runtime": True,
    },
    {
        "name": "branched_runtime_deep_narrow",
        "category": "branched_runtime_deep_narrow",
        "axiom": "[" * MAX_LSYSTEM_BRANCH_DEPTH + "F" + "]" * MAX_LSYSTEM_BRANCH_DEPTH,
        "rules": (),
        "iterations": 0,
        "runtime": True,
    },
    {
        "name": "limit_symbols",
        "category": "limit_failure",
        "axiom": "F",
        "rules": (("F", "FF"),),
        "iterations": 18,
        "runtime": False,
        "expect_error": True,
    },
    {
        "name": "limit_branch_depth",
        "category": "limit_failure",
        "axiom": "[" * (MAX_LSYSTEM_BRANCH_DEPTH + 1) + "F" + "]" * (MAX_LSYSTEM_BRANCH_DEPTH + 1),
        "rules": (),
        "iterations": 0,
        "runtime": True,
        "expect_error": True,
    },
)

def _lsystem_source(axiom, *, rules=(), iterations=0, angle="60", step="1.0", runtime=False):
    """Build an L-system source fixture from explicit constructor values."""
    lines = []
    angle_expr = str(angle)
    step_expr = str(step)
    if runtime:
        lines.extend([
            f'angle_value = input_float("Angle", default={angle})',
            f'step_value = input_float("Step", default={step})',
        ])
        angle_expr = "angle_value"
        step_expr = "step_value"
    parts = [f'ls_axiom("{axiom}")']
    for symbol, replacement in rules:
        parts.append(f'ls_rule("{symbol}", "{replacement}")')
    parts.extend([f"ls_iterations({iterations})", f"ls_angle({angle_expr})", f"ls_step({step_expr})"])
    lines.append("geo = ls_system(" + ", ".join(parts) + ")")
    lines.append('output("Geometry", geo)')
    return "\n".join(lines) + "\n"

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
    """Wrap a compiled L-system group with Curve to Mesh so depsgraph output is inspectable."""
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

def _assert_branch_free_modifier_runtime_updates(compiled_group):
    """Evaluate branch-free runtime output and mutate Angle/Step without recompilation."""
    manifest_before = generated_resources.read_group_manifest(compiled_group)
    generation_before = manifest_before["generation_uuid"]
    wrapper, obj, mesh_data, mod = _attach_runtime_eval_modifier(compiled_group, "NFTest_lsystem_branch_free_runtime_eval")
    try:
        _set_modifier_input(mod, wrapper, "Angle", 90.0)
        _set_modifier_input(mod, wrapper, "Step", 1.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_90, edges_90, polygons_90 = _evaluated_mesh_snapshot(obj)
        check(vertices_90 == ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)), f"runtime Angle=90 vertices changed: {vertices_90}")
        check(edges_90 == ((0, 1), (2, 3)), f"runtime Angle=90 edges changed: {edges_90}")
        check(polygons_90 == 0, "runtime evaluated mesh unexpectedly has polygons")

        _set_modifier_input(mod, wrapper, "Angle", 0.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_angle_changed, edges_angle_changed, _polygons = _evaluated_mesh_snapshot(obj)
        check(vertices_angle_changed == ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)), f"runtime Angle update did not change evaluated positions: {vertices_angle_changed}")
        check(edges_angle_changed == edges_90, "runtime Angle update changed topology")
        check(generated_resources.read_group_manifest(compiled_group)["generation_uuid"] == generation_before, "runtime Angle update churned generated resources")

        _set_modifier_input(mod, wrapper, "Step", 2.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_step_changed, edges_step_changed, _polygons = _evaluated_mesh_snapshot(obj)
        check(vertices_step_changed == ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 0.0, 0.0), (4.0, 0.0, 0.0)), f"runtime Step update did not scale evaluated positions: {vertices_step_changed}")
        check(edges_step_changed == edges_90, "runtime Step update changed topology")
        check(generated_resources.read_group_manifest(compiled_group)["generation_uuid"] == generation_before, "runtime Step update churned generated resources")
    finally:
        _cleanup_runtime_eval_objects(wrapper, obj, mesh_data)

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

def _assert_branch_aware_sample_index_uses_safe_parent_index(group):
    sample_nodes = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeSampleIndex"]
    check(sample_nodes, "branch-aware runtime graph has no Sample Index node")
    for node in sample_nodes:
        index_source = _linked_source_node(group, node, "Index")
        check(
            getattr(index_source, "bl_idname", "") == "GeometryNodeSwitch",
            "branch-aware Sample Index must receive a depth-safe parent attach index, not raw parent_attach_index",
        )
        false_source = _linked_source_node(group, index_source, 1)
        true_source = _linked_source_node(group, index_source, 2)
        check(
            getattr(false_source, "bl_idname", "") == "GeometryNodeInputNamedAttribute"
            and _named_attribute_node_name(false_source) == PARENT_ATTACH_INDEX_ATTR,
            "safe parent attach switch must preserve raw parent_attach_index for non-root paths",
        )
        check(
            getattr(true_source, "bl_idname", "") == "FunctionNodeInputInt"
            and getattr(true_source, "integer", None) == 0,
            "safe parent attach switch must replace root sentinel with a valid root anchor index",
        )

def _runtime_branched_source(axiom="F[+F]F[-F]F", angle_default=90.0, step_default=1.0):
    return '''
angle_value = input_float("Angle", default={angle_default})
step_value = input_float("Step", default={step_default})
geo = ls_system(ls_axiom("{axiom}"), ls_iterations(0), ls_angle(angle_value), ls_step(step_value))
output("Geometry", geo)
'''.format(axiom=axiom, angle_default=angle_default, step_default=step_default)

def _assert_branch_aware_modifier_runtime_updates(compiled_group):
    manifest_before = generated_resources.read_group_manifest(compiled_group)
    generation_before = manifest_before["generation_uuid"]
    wrapper, obj, mesh_data, mod = _attach_runtime_eval_modifier(compiled_group, "NFTest_lsystem_branch_aware_runtime_eval")
    try:
        _set_modifier_input(mod, wrapper, "Angle", 90.0)
        _set_modifier_input(mod, wrapper, "Step", 1.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_90, edges_90, polygons_90 = _evaluated_mesh_snapshot(obj)
        check(polygons_90 == 0, "branched runtime evaluated mesh unexpectedly has polygons")
        check(len(edges_90) == 3, f"branched runtime expected 3 drawn edges, got {edges_90}")
        expected_vertices = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0))
        expected_edges = ((0, 1), (1, 2), (3, 4))
        check(vertices_90 == expected_vertices, f"branched runtime Angle=90 vertices changed: {vertices_90}")
        check(edges_90 == expected_edges, f"branched runtime Angle=90 edges changed: {edges_90}")

        _set_modifier_input(mod, wrapper, "Angle", 0.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_angle_changed, edges_angle_changed, _polygons = _evaluated_mesh_snapshot(obj)
        expected_zero = ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0))
        check(vertices_angle_changed == expected_zero, f"branched runtime Angle update did not change evaluated positions: {vertices_angle_changed}")
        check(edges_angle_changed == edges_90, "branched runtime Angle update changed topology")
        check(generated_resources.read_group_manifest(compiled_group)["generation_uuid"] == generation_before, "branched runtime Angle update churned resources")

        _set_modifier_input(mod, wrapper, "Step", 2.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_step_changed, edges_step_changed, _polygons = _evaluated_mesh_snapshot(obj)
        expected_step = ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (4.0, 0.0, 0.0), (2.0, 0.0, 0.0), (4.0, 0.0, 0.0))
        check(vertices_step_changed == expected_step, f"branched runtime Step update did not scale positions: {vertices_step_changed}")
        check(edges_step_changed == edges_90, "branched runtime Step update changed topology")
        check(generated_resources.read_group_manifest(compiled_group)["generation_uuid"] == generation_before, "branched runtime Step update churned resources")
    finally:
        _cleanup_runtime_eval_objects(wrapper, obj, mesh_data)

def _assert_branch_aware_evaluated_fixture(axiom, expected_vertices, expected_edges, name_suffix):
    group = compile_group(_runtime_branched_source(axiom), "NFTest_lsystem_branch_aware_eval_" + name_suffix)
    _assert_branch_aware_sample_index_uses_safe_parent_index(group)
    wrapper, obj, mesh_data, mod = _attach_runtime_eval_modifier(group, "NFTest_lsystem_branch_aware_eval_" + name_suffix)
    try:
        _set_modifier_input(mod, wrapper, "Angle", 90.0)
        _set_modifier_input(mod, wrapper, "Step", 1.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices, edges, polygons = _evaluated_mesh_snapshot(obj)
        check(vertices == expected_vertices, f"branched runtime fixture {axiom} vertices changed: {vertices}")
        check(edges == expected_edges, f"branched runtime fixture {axiom} edges changed: {edges}")
        check(polygons == 0, f"branched runtime fixture {axiom} unexpectedly has polygons")
    finally:
        _cleanup_runtime_eval_objects(wrapper, obj, mesh_data)



__all__ = [name for name in globals() if not name.startswith("__")]
