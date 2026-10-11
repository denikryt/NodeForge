from helpers import *
from NodeForge.extension_api import ExtensionBackendContext




def test_addon_register_unregister_paths():
    bpy.ops.preferences.addon_enable(module='NodeForge')
    check('NodeForge' in bpy.context.preferences.addons, 'addon_enable did not register NodeForge')
    bpy.ops.preferences.addon_disable(module='NodeForge')
    check('NodeForge' not in bpy.context.preferences.addons, 'addon_disable did not unregister NodeForge')
    NodeForge.register()
    NodeForge.unregister()
    print('STARTUP_SHUTDOWN_OK')


def test_addon_unregister_is_idempotent_after_manual_lifecycle():
    """Manual lifecycle scripts should not poison Blender shutdown with double unregister."""
    NodeForge.register()
    NodeForge.unregister()
    NodeForge.unregister()
    NodeForge.register()
    NodeForge.unregister()


def test_addon_unregister_preserves_live_generated_resources_and_geometry():
    """Unregister must leave resources referenced by compiled Geometry Nodes intact."""
    NodeForge.register()
    group = bpy.data.node_groups.new("NFTest_unregister_resource_group", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_output = group.nodes.new("NodeGroupOutput")
    group_output.is_active_output = True

    transaction = generated_resources.GeneratedResourceTransaction(
        owner_group_uuid=generated_resources.ensure_owner_group_uuid(group)
    )
    resource_context = ExtensionBackendContext(
        group=group,
        location=(0.0, 0.0),
        generated_resource_transaction=transaction,
    )
    curve = resource_context.new_generated_curve(
        role="static_baked_curve", name_hint="UnregisterPersistence"
    )
    curve.dimensions = "3D"
    curve.resolution_u = 1
    spline = curve.splines.new("POLY")
    spline.points.add(1)
    spline.points[0].co = (0.0, 0.0, 0.0, 1.0)
    spline.points[1].co = (1.0, 0.0, 0.0, 1.0)
    generated_obj = resource_context.new_generated_object(
        curve, role="static_baked_object", name_hint="UnregisterPersistence"
    )
    bpy.context.collection.objects.link(generated_obj)
    generated_obj.hide_viewport = True
    generated_obj.hide_render = True
    generated_resources.write_group_manifest(group, transaction.manifest())
    transaction.mark_committed()

    object_info = group.nodes.new("GeometryNodeObjectInfo")
    next(socket for socket in object_info.inputs if socket.name == "Object").default_value = generated_obj
    group.links.new(next(socket for socket in object_info.outputs if socket.name == "Geometry"), group_output.inputs["Geometry"])

    wrapper = bpy.data.node_groups.new("NFTest_unregister_resource_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    wrapper_output = wrapper.nodes.new("NodeGroupOutput")
    wrapper_output.is_active_output = True
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    curve_to_mesh = wrapper.nodes.new("GeometryNodeCurveToMesh")
    wrapper.links.new(group_node.outputs["Geometry"], curve_to_mesh.inputs["Curve"])
    wrapper.links.new(curve_to_mesh.outputs["Mesh"], wrapper_output.inputs["Geometry"])

    base_mesh = bpy.data.meshes.new("NFTest_unregister_resource_base")
    host = bpy.data.objects.new("NFTest_unregister_resource_host", base_mesh)
    bpy.context.collection.objects.link(host)
    modifier = host.modifiers.new("NodeForge", "NODES")
    modifier.node_group = wrapper

    def evaluated_counts():
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        evaluated = host.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            return len(mesh.vertices), len(mesh.edges), len(mesh.polygons)
        finally:
            evaluated.to_mesh_clear()

    before = evaluated_counts()
    check(before[0] > 0, "generated-resource fixture produced no geometry")
    generated_obj_name = generated_obj.name
    curve_name = curve.name
    manifest_before = generated_resources.read_group_manifest(group)

    NodeForge.unregister()
    try:
        check(bpy.data.objects.get(generated_obj_name) is generated_obj, "unregister deleted generated Object")
        check(bpy.data.curves.get(curve_name) is curve, "unregister deleted generated Curve")
        check(_object_info_source(object_info) is generated_obj, "unregister cleared Object Info source")
        check(generated_resources.read_group_manifest(group) == manifest_before, "unregister changed generated-resource manifest")
        check(evaluated_counts() == before, "evaluated geometry changed after unregister")
    finally:
        NodeForge.register()
        if bpy.data.objects.get(host.name) is host:
            bpy.data.objects.remove(host, do_unlink=True)
        if bpy.data.meshes.get(base_mesh.name) is base_mesh:
            bpy.data.meshes.remove(base_mesh, do_unlink=True)
        if bpy.data.node_groups.get(wrapper.name) is wrapper:
            bpy.data.node_groups.remove(wrapper)
        if bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group)
        generated_resources.cleanup_restart_orphans()
