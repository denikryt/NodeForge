"""Source constants contribute to effective function fingerprints and Blender output."""

from helpers import *
from NodeForge.function_instances import stored_fingerprint


def _evaluate_float_output(tree, output_socket):
    """Evaluate a function result through an explicit test-only Geometry output."""
    tree.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    xyz = tree.nodes.new("ShaderNodeCombineXYZ")
    line = tree.nodes.new("GeometryNodeMeshLine")
    line.inputs["Count"].default_value = 1
    result = tree.nodes.new("NodeGroupOutput")
    tree.links.new(output_socket, xyz.inputs["X"])
    tree.links.new(xyz.outputs[0], line.inputs["Start Location"])
    tree.links.new(line.outputs["Mesh"], result.inputs["Geometry"])
    mesh = bpy.data.meshes.new("NF-fingerprint-eval-mesh")
    obj = bpy.data.objects.new("NF-fingerprint-eval-object", mesh)
    bpy.context.collection.objects.link(obj)
    try:
        obj.modifiers.new("NF-eval", "NODES").node_group = tree
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        evaluated_mesh = evaluated.to_mesh()
        try:
            return evaluated_mesh.vertices[0].co.x
        finally:
            evaluated.to_mesh_clear()
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)


def test_reload_tracks_used_module_constant_without_replacing_shared_datablock():
    """Updating a used constant must update the realized Geometry Nodes result."""
    context = "test:fingerprint-module-constant"
    source = "K=2\ndef f(x: Float=1):\n    return x+K\na=f()\n"
    base = bpy.data.node_groups.new("NF-fingerprint-constant-base", "GeometryNodeTree")
    compile_root(source, base, module_context=context)
    call, = (node for node in base.nodes if node.bl_idname == "GeometryNodeGroup")
    group = call.node_tree
    pointer = group.as_pointer()
    before = stored_fingerprint(group)
    assert round(_evaluate_float_output(base, call.outputs[0]), 5) == 3

    assert compiler.update_library_catalog_group(
        group, source=source.replace("K=2", "K=8"), module_context=context,
    ) is group
    assert group.as_pointer() == pointer
    assert stored_fingerprint(group) != before
    assert round(_evaluate_float_output(base, call.outputs[0]), 5) == 9


def test_reload_ignores_unrelated_sibling_and_module_constant():
    """Unreferenced declarations and constants must not force group rebuilding."""
    context = "test:fingerprint-unrelated-sibling"
    source = (
        "K=2\nUNUSED=20\ndef f(x: Float=1):\n    return x+K\n"
        "def other(x: Float):\n    return x+3\na=f()\n"
    )
    base = bpy.data.node_groups.new("NF-fingerprint-sibling-base", "GeometryNodeTree")
    compile_root(source, base, module_context=context)
    calls = [node for node in base.nodes if node.bl_idname == "GeometryNodeGroup"]
    assert [node.node_tree.get("nodeforge_module_name") for node in calls] == ["f", "other"]
    call = calls[0]
    group = call.node_tree
    before_fingerprint = stored_fingerprint(group)
    before_nodes = tuple(node.as_pointer() for node in group.nodes)
    changed = source.replace("UNUSED=20", "UNUSED=999").replace("return x+3", "return x+999")
    assert compiler.update_library_catalog_group(group, source=changed, module_context=context) is group
    assert stored_fingerprint(group) == before_fingerprint
    assert tuple(node.as_pointer() for node in group.nodes) == before_nodes
    assert round(_evaluate_float_output(base, call.outputs[0]), 5) == 3


def test_new_compilation_updates_reusable_group_when_constant_changes():
    """Automatic canonical lookup refreshes a shared group using its new IR."""
    context = "test:fingerprint-automatic-reuse"
    source = "K=2\ndef f(x: Float=1):\n    return x+K\na=f()\n"
    first = bpy.data.node_groups.new("NF-fingerprint-automatic-first", "GeometryNodeTree")
    compile_root(source, first, module_context=context)
    original, = (node for node in first.nodes if node.bl_idname == "GeometryNodeGroup")
    pointer = original.node_tree.as_pointer()
    old_fingerprint = stored_fingerprint(original.node_tree)
    assert round(_evaluate_float_output(first, original.outputs[0]), 5) == 3

    second = bpy.data.node_groups.new("NF-fingerprint-automatic-second", "GeometryNodeTree")
    compile_root(source.replace("K=2", "K=8"), second, module_context=context)
    new, = (node for node in second.nodes if node.bl_idname == "GeometryNodeGroup")
    assert new.node_tree.as_pointer() == original.node_tree.as_pointer() == pointer
    assert stored_fingerprint(original.node_tree) != old_fingerprint
    assert round(_evaluate_float_output(first, original.outputs[0]), 5) == 9
