"""Blender 5.2 regressions for the first Mesh Compute physical boundary primitives."""

from __future__ import annotations

from array import array

import bpy
import pytest

from NodeForge.blender.generated_resources import GeneratedResourceTransaction
from NodeForge.blender.mesh_compute_runtime import (
    evaluate_mesh_callback, mesh_inputs_from_evaluated,
    prefix_from_geometry_socket, runtime_parameters_from_evaluated,
)
from NodeForge.blender.mesh_compute_transports import direct_mesh_update
from NodeForge.extension_api import ExtensionBackendValue
from NodeForge.nf_types import NFType

pytestmark = pytest.mark.blender


def _sample_mesh(name):
    """Make a real triangulated source with POINT and CORNER data layers."""
    mesh = bpy.data.meshes.new(name)
    mesh.from_pydata([(0, 0, 0), (1, 0, 1), (1, 1, 2), (0, 1, 3)], [], [(0, 1, 2), (0, 2, 3)])
    mesh.update()
    attr = mesh.attributes.new("height", "FLOAT", "POINT")
    attr.data.foreach_set("value", array("f", (0, 1, 2, 3)))
    mesh.uv_layers.new(name="TestUV")
    material = bpy.data.materials.new(name + "_mat")
    mesh.materials.append(material)
    return mesh, material


def _make_source(name):
    """Create an independent Scene Object with a stock Mesh for evaluation."""
    scene = bpy.data.scenes.new(name + "_scene")
    mesh, material = _sample_mesh(name + "_mesh")
    obj = bpy.data.objects.new(name + "_obj", mesh)
    scene.collection.objects.link(obj)
    return scene, obj, mesh, material


def _remove_if_live(ids, obj):
    """Only release the physical test-owned Blender ID when it remains live."""
    if obj is None:
        return
    try:
        if any(x is obj for x in ids):
            ids.remove(obj, do_unlink=True)
    except ReferenceError:
        return


def _cleanup(scene, obj, mesh, material, extra_mesh=None, group=None, prefix=None):
    """Remove strictly test-created IDs including the generated modifier graph."""
    _remove_if_live(bpy.data.objects, obj)
    _remove_if_live(bpy.data.scenes, scene)
    _remove_if_live(bpy.data.meshes, extra_mesh)
    _remove_if_live(bpy.data.meshes, mesh)
    _remove_if_live(bpy.data.materials, material)
    _remove_if_live(bpy.data.node_groups, prefix)
    _remove_if_live(bpy.data.node_groups, group)


def test_direct_mesh_snapshot_preserves_unmodified_geometry_and_applies_arrays():
    """Direct publication changes only declared point arrays, preserving existing layers."""
    scene, obj, mesh, material = _make_source("nf_direct_pos")
    published = None
    try:
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            scene.view_layers[0].update()
            dg = bpy.context.evaluated_depsgraph_get()
            evaluated_obj = obj.evaluated_get(dg)
            evaluated_mesh = evaluated_obj.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
            try:
                def kernel(inputs, *, amount):
                    positions = array("f", inputs.positions)
                    for i in range(2, len(positions), 3):
                        positions[i] += amount
                    values = array("f", (x + amount for x in inputs.point_attributes["height"]))
                    return {"positions": positions, "point_attributes": {"height": values, "water": [0.25] * inputs.vertex_count}}

                evaluated = evaluate_mesh_callback(evaluated_mesh, kernel=kernel, reads=("height",), writes=("height", "water"), parameters={"amount": 2.0})
                assert len(evaluated.inputs.positions) == 12
                published = direct_mesh_update.prepare_publication(evaluated_obj, dg, evaluated)
            finally:
                evaluated_obj.to_mesh_clear()

        assert len(published.vertices) == 4
        assert len(published.polygons) == len(mesh.polygons) == 2
        assert len(published.uv_layers) == 1
        assert published.materials[0] is material
        assert [tuple(p.vertices) for p in published.polygons] == [tuple(p.vertices) for p in mesh.polygons]
        assert [round(v.co.z, 4) for v in published.vertices] == [2, 3, 4, 5]
        assert [round(v.co.z, 4) for v in mesh.vertices] == [0, 1, 2, 3]
        assert [round(x.value, 4) for x in published.attributes["height"].data] == [2, 3, 4, 5]
        assert [round(x.value, 4) for x in published.attributes["water"].data] == [0.25] * 4
    finally:
        _cleanup(scene, obj, mesh, material, extra_mesh=published)


def test_direct_mesh_invalid_output_and_domain_conflict_never_mutate_source():
    """Invalid callback results fail before publication; domain conflicts clean candidates."""
    scene, obj, mesh, material = _make_source("nf_direct_bad")
    mesh.attributes.new("water", "FLOAT", "FACE")
    try:
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            scene.view_layers[0].update()
            dg = bpy.context.evaluated_depsgraph_get()
            evaluated_obj = obj.evaluated_get(dg)
            extracted = evaluated_obj.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
            try:
                with pytest.raises(ValueError, match="expected 4"):
                    evaluate_mesh_callback(extracted, kernel=lambda _mesh: {"point_attributes": {"water": [1.0]}}, reads=(), writes=("water",), parameters={})
                evaluated = evaluate_mesh_callback(extracted, kernel=lambda inp: {"point_attributes": {"water": [0.3] * inp.vertex_count}}, reads=(), writes=("water",), parameters={})
                before = len(bpy.data.meshes)
                with pytest.raises(ValueError, match="conflicts"):
                    direct_mesh_update.prepare_publication(evaluated_obj, dg, evaluated)
                assert len(bpy.data.meshes) == before
                assert mesh.attributes["water"].domain == "FACE"
            finally:
                evaluated_obj.to_mesh_clear()
    finally:
        _cleanup(scene, obj, mesh, material)


def test_prefix_extracts_uniform_scalar_parameter_without_leaking_to_output():
    """Copied prefix can evaluate one source Geometry branch and its scalar input."""
    scene, obj, mesh, material = _make_source("nf_prefix")
    group = bpy.data.node_groups.new("nf_prefix_root", "GeometryNodeTree")
    tx = GeneratedResourceTransaction("nf_prefix_owner")
    prefix = None
    try:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket(name="Amount", in_out="INPUT", socket_type="NodeSocketFloat").default_value = 2.0
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        inp = group.nodes.new("NodeGroupInput")
        out = group.nodes.new("NodeGroupOutput")
        group.links.new(inp.outputs["Geometry"], out.inputs["Geometry"])
        runtime_value = ExtensionBackendValue(inp.outputs["Amount"], NFType.FLOAT)
        prefix, names = prefix_from_geometry_socket(group, inp.outputs["Geometry"], {"amount": runtime_value}, tx)
        assert len(tx.resources) == 1 and tx.resources[0].kind == "NODE_GROUP"
        modifier = obj.modifiers.new("Mesh Prefix", "NODES")
        modifier.node_group = prefix
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            scene.view_layers[0].update()
            dg = bpy.context.evaluated_depsgraph_get()
            evaluated_obj = obj.evaluated_get(dg)
            extracted = evaluated_obj.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
            try:
                assert runtime_parameters_from_evaluated(extracted, names) == {"amount": 2.0}
                assert mesh_inputs_from_evaluated(extracted, ("height",)).vertex_count == 4
                assert ".nf_mesh_compute_param_amount" in extracted.attributes
                assert ".nf_mesh_compute_param_amount" not in mesh.attributes
            finally:
                evaluated_obj.to_mesh_clear()
    finally:
        _cleanup(scene, obj, mesh, material, group=group, prefix=prefix)


def test_private_prefix_never_inherits_authoritative_root_identity():
    """Copied prefixes keep inputs but not authoritative group ownership metadata."""
    from NodeForge.function_instances import FUNCTION_ROOT_OWNER_ID_PROP
    from NodeForge.blender.generated_resources import (
        GROUP_MANIFEST_PROP, build_manifest, write_group_manifest,
    )
    from NodeForge.blender.group_authority import is_transaction_private

    group = bpy.data.node_groups.new("nf_prefix_identity_original", "GeometryNodeTree")
    tx = GeneratedResourceTransaction("nf_prefix_identity_owner")
    try:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        node = group.nodes.new("NodeGroupInput")
        group[FUNCTION_ROOT_OWNER_ID_PROP] = "authoritative-root-id"
        write_group_manifest(group, build_manifest("authoritative-root-id", []))
        prefix, _ = prefix_from_geometry_socket(group, node.outputs["Geometry"], {}, tx)
        assert is_transaction_private(prefix)
        assert FUNCTION_ROOT_OWNER_ID_PROP not in prefix
        assert GROUP_MANIFEST_PROP not in prefix
        assert group[FUNCTION_ROOT_OWNER_ID_PROP] == "authoritative-root-id"
        assert GROUP_MANIFEST_PROP in group
    finally:
        tx.rollback()
        _remove_if_live(bpy.data.node_groups, group)


def test_prefix_fails_before_group_copy_on_invalid_scalar_or_foreign_geometry():
    """Reject unsupported external sockets and scalar operands at their known boundary."""
    group = bpy.data.node_groups.new("nf_prefix_invalid", "GeometryNodeTree")
    foreign = bpy.data.node_groups.new("nf_prefix_foreign", "GeometryNodeTree")
    try:
        source = group.nodes.new("NodeGroupInput")
        foreign_node = foreign.nodes.new("NodeGroupInput")
        tx = GeneratedResourceTransaction("nf_invalid_owner")
        count = len(bpy.data.node_groups)
        with pytest.raises(ValueError):
            prefix_from_geometry_socket(group, foreign_node.outputs[0] if foreign_node.outputs else None, {}, tx)
        with pytest.raises(ValueError):
            prefix_from_geometry_socket(group, source.outputs[0] if source.outputs else None, {}, tx)
        assert len(bpy.data.node_groups) == count
        assert not tx.resources
    finally:
        _remove_if_live(bpy.data.node_groups, group)
        _remove_if_live(bpy.data.node_groups, foreign)


def test_direct_published_mesh_flows_through_downstream_geometry_nodes():
    """A real root GN consumer sees the published mesh then applies a native suffix."""
    scene, source, mesh, material = _make_source("nf_bridge_source")
    group = bpy.data.node_groups.new("nf_bridge_suffix", "GeometryNodeTree")
    consumer_mesh = bpy.data.meshes.new("nf_bridge_consumer_mesh")
    consumer = bpy.data.objects.new("nf_bridge_consumer", consumer_mesh)
    proxy_mesh = bpy.data.meshes.new("nf_bridge_empty")
    proxy = bpy.data.objects.new("nf_bridge_proxy", proxy_mesh)
    published = None
    scene.collection.objects.link(consumer)
    try:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        boundary = direct_mesh_update.build_boundary(group, proxy)
        set_position = group.nodes.new("GeometryNodeSetPosition")
        set_position.inputs["Offset"].default_value = (0.0, 0.0, 0.5)
        group.links.new(boundary, set_position.inputs["Geometry"])
        output = group.nodes.new("NodeGroupOutput")
        group.links.new(set_position.outputs["Geometry"], output.inputs["Geometry"])
        mod = consumer.modifiers.new("NodeForge GN suffix", "NODES")
        mod.node_group = group
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            scene.view_layers[0].update()
            dg = bpy.context.evaluated_depsgraph_get()
            source_eval = source.evaluated_get(dg)
            original = source_eval.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
            try:
                calculated = evaluate_mesh_callback(
                    original,
                    kernel=lambda inp: {"positions": array("f", (
                        c + (2.0 if i % 3 == 2 else 0.0)
                        for i, c in enumerate(inp.positions)
                    ))},
                    reads=(), writes=(), parameters={},
                )
                published = direct_mesh_update.prepare_publication(source_eval, dg, calculated)
            finally:
                source_eval.to_mesh_clear()
            proxy.data = published
            proxy.update_tag(refresh={'OBJECT', 'DATA'})
            scene.view_layers[0].update()
            evaluated_consumer = consumer.evaluated_get(bpy.context.evaluated_depsgraph_get())
            output_mesh = evaluated_consumer.to_mesh(preserve_all_data_layers=True, depsgraph=bpy.context.evaluated_depsgraph_get())
            try:
                assert len(output_mesh.vertices) == len(mesh.vertices)
                assert [round(v.co.z, 4) for v in output_mesh.vertices] == [2.5, 3.5, 4.5, 5.5]
                assert len(output_mesh.polygons) == len(mesh.polygons)
                assert len(output_mesh.uv_layers) == 1
                assert output_mesh.materials[0].original is material
            finally:
                evaluated_consumer.to_mesh_clear()
    finally:
        _remove_if_live(bpy.data.objects, consumer)
        _remove_if_live(bpy.data.objects, proxy)
        _remove_if_live(bpy.data.meshes, consumer_mesh)
        _remove_if_live(bpy.data.meshes, proxy_mesh)
        _cleanup(scene, source, mesh, material, extra_mesh=published, group=group)


def test_evaluated_input_attributes_fail_at_read_boundary():
    """Unknown and non-POINT FLOAT attributes are rejected before callback invocation."""
    scene, obj, mesh, material = _make_source("nf_missing_input")
    invoked = []
    try:
        assert mesh.attributes.new("face_marker", "FLOAT", "FACE")
        with pytest.raises(ValueError, match="POINT FLOAT"):
            evaluate_mesh_callback(mesh, kernel=lambda inp: invoked.append(True), reads=("face_marker",), writes=(), parameters={})
        with pytest.raises(ValueError, match="POINT FLOAT"):
            evaluate_mesh_callback(mesh, kernel=lambda inp: invoked.append(True), reads=("missing",), writes=(), parameters={})
        assert invoked == []
    finally:
        _cleanup(scene, obj, mesh, material)


def test_extension_backend_context_stages_one_local_compute_without_activating():
    """Physical API produces exactly one private candidate and a Geometry result socket."""
    from NodeForge.extension_api import ExtensionBackendContext
    from NodeForge.mesh_compute import mesh_compute

    group = bpy.data.node_groups.new("nf_stage_context", "GeometryNodeTree")
    tx = GeneratedResourceTransaction("nf_context_owner")
    try:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        inp = group.nodes.new("NodeGroupInput")
        geometry = ExtensionBackendValue(inp.outputs["Geometry"], NFType.GEOMETRY)
        context = ExtensionBackendContext(group=group, location=(100.0, 200.0), generated_resource_transaction=tx)

        @mesh_compute(reads=("height",), writes=("height",))
        def kernel(inputs, amount: float):
            """A trusted user callback represented here by a test callable."""
            return {"point_attributes": {"height": [amount] * inputs.vertex_count}}

        result = context.mesh_compute(geometry, kernel=kernel, parameters={"amount": 3.0}, reads=("height",), writes=("height",))
        assert result.typ is NFType.GEOMETRY
        assert result.socket.node.bl_idname == "GeometryNodeObjectInfo"
        assert [x.kind for x in tx.resources] == ["NODE_GROUP", "MESH", "OBJECT"]
        stage = tx.mesh_compute_stage
        assert stage.prefix is not group
        assert stage.kernel is kernel
        assert stage.reads == ("height",)
        assert stage.scalar_keys["amount"][1] is NFType.FLOAT
        assert stage.result_proxy.data is not None
        assert len(stage.result_proxy.data.vertices) == 0  # uncommitted stage cannot display stale geometry
        before = len(tx.resources)
        with pytest.raises(ValueError, match="One Mesh Compute stage"):
            context.mesh_compute(geometry, kernel=kernel, parameters={"amount": 3.0}, reads=("height",), writes=("height",))
        assert len(tx.resources) == before
    finally:
        tx.rollback()
        _remove_if_live(bpy.data.node_groups, group)


def test_extension_backend_rejects_unverified_callbacks_before_allocations():
    """No candidate ID or descriptor is created for arbitrary Python functions."""
    from NodeForge.extension_api import ExtensionBackendContext

    group = bpy.data.node_groups.new("nf_stage_bad_context", "GeometryNodeTree")
    tx = GeneratedResourceTransaction("nf_bad_context_owner")
    try:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        inp = group.nodes.new("NodeGroupInput")
        context = ExtensionBackendContext(group=group, location=(0, 0), generated_resource_transaction=tx)
        value = ExtensionBackendValue(inp.outputs["Geometry"], NFType.GEOMETRY)
        with pytest.raises(ValueError, match="verified"):
            context.mesh_compute(value, kernel=lambda mesh: None, parameters={}, reads=(), writes=())
        assert not tx.resources
        assert getattr(tx, "mesh_compute_stage", None) is None
    finally:
        tx.rollback()
        _remove_if_live(bpy.data.node_groups, group)


def test_const_scalar_inputs_in_private_prefix_are_typed_and_uniform():
    """Defaulted compile-time float/int/bool values appear as uniform scalar fields."""
    scene, obj, mesh, material = _make_source("nf_prefix_constants")
    group = bpy.data.node_groups.new("nf_prefix_constants_root", "GeometryNodeTree")
    tx = GeneratedResourceTransaction("nf_prefix_constants_owner")
    prefix = None
    try:
        group.interface.new_socket(name="Geometry", in_out="INPUT", socket_type="NodeSocketGeometry")
        inp = group.nodes.new("NodeGroupInput")
        prefix, names = prefix_from_geometry_socket(group, inp.outputs["Geometry"],
            {"rain": 0.03, "steps": 500, "enabled": True}, tx)
        modifier = obj.modifiers.new("Mesh Prefix Constants", "NODES")
        modifier.node_group = prefix
        with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
            scene.view_layers[0].update()
            dg = bpy.context.evaluated_depsgraph_get()
            ev = obj.evaluated_get(dg)
            evaluated = ev.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
            try:
                assert runtime_parameters_from_evaluated(evaluated, names) == {"rain": pytest.approx(0.03), "steps": 500, "enabled": True}
            finally:
                ev.to_mesh_clear()
        assert not any(attr.name.startswith(".nf_mesh_compute_param_") for attr in mesh.attributes)
    finally:
        _cleanup(scene, obj, mesh, material, group=group, prefix=prefix)


def test_regular_group_compile_rejects_unbound_local_python_before_blender_mutation(tmp_path, monkeypatch):
    """Ordinary create_expression_group cannot accidentally publish an empty runtime group."""
    from NodeForge import local_sources
    from NodeForge.compiler import create_expression_group
    from NodeForge.errors import CompileError

    root = tmp_path / "Local"
    root.mkdir()
    (root / "zlift.py").write_text(
        'from NodeForge import mesh_compute\n'
        '@mesh_compute()\n'
        'def zlift(mesh, amount: float = 2.0):\n'
        '    return {"positions": mesh.positions}\n'
    )
    monkeypatch.setattr(local_sources, "_default_local_catalog_dir", lambda: root)
    monkeypatch.setattr(local_sources, "_local_sources_registry_path", lambda: tmp_path / "local_sources.json")
    local_sources.set_local_python_permission(root, allowed=True)
    source = ('from local import zlift\n'
              'geo = input_geometry("Geometry")\n'
              'geo = zlift(geo, amount=2.0)\n'
              'output("Geometry", geo)\n')
    existing = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match="Compile to Modifier"):
        create_expression_group(source)
    assert {group.as_pointer() for group in bpy.data.node_groups} == existing
