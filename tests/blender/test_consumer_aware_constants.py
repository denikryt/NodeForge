"""Blender 5.2 reference behavior for constant-input and absent-input semantics.

These tests deliberately construct Blender nodes without using the NodeForge
compiler. They are the behavioral oracle for later consumer-aware delivery.
"""

import bpy
import pytest


def _fixture(name):
    """Create an evaluated Geometry Nodes modifier and a cube geometry source."""
    group = bpy.data.node_groups.new(name + "_tree", "GeometryNodeTree")
    group.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    output = group.nodes.new("NodeGroupOutput")
    cube = group.nodes.new("GeometryNodeMeshCube")
    cube.inputs["Size"].default_value = (2, 3, 4)
    mesh = bpy.data.meshes.new(name + "_mesh")
    obj = bpy.data.objects.new(name + "_object", mesh)
    bpy.context.collection.objects.link(obj)
    modifier = obj.modifiers.new("GeometryNodes", "NODES")
    modifier.node_group = group
    return group, cube, output, obj, mesh


def _positions(obj):
    """Read evaluated mesh vertices without retaining a depsgraph mesh."""
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    mesh = evaluated.to_mesh()
    try:
        return tuple(sorted(tuple(round(float(c), 5) for c in v.co) for v in mesh.vertices))
    finally:
        evaluated.to_mesh_clear()


def _cleanup(group, obj, mesh, *other_groups):
    """Release transient test datablocks and the modifier's object."""
    bpy.data.objects.remove(obj, do_unlink=True)
    bpy.data.meshes.remove(mesh, do_unlink=True)
    for item in (group, *other_groups):
        bpy.data.node_groups.remove(item, do_unlink=True)


@pytest.mark.parametrize(
    "node_type,input_name,source_name,output_name,source_output",
    [
        ("GeometryNodeSetPosition", "Position", "Geometry", "Geometry", "Mesh"),
        ("GeometryNodeMeshToPoints", "Position", "Mesh", "Points", "Mesh"),
    ],
)
def test_hidden_position_absence_differs_from_connected_zero(
    node_type, input_name, source_name, output_name, source_output
):
    """Unlinked position fields differ from an explicit connected zero Vector."""
    name = "NFRef_" + node_type
    group, cube, output, obj, mesh = _fixture(name)
    try:
        consumer = group.nodes.new(node_type)
        group.links.new(cube.outputs[source_output], consumer.inputs[source_name])
        if node_type == "GeometryNodeMeshToPoints":
            convert = group.nodes.new("GeometryNodePointsToVertices")
            group.links.new(consumer.outputs[output_name], convert.inputs["Points"])
            group.links.new(convert.outputs["Mesh"], output.inputs["Geometry"])
        else:
            group.links.new(consumer.outputs[output_name], output.inputs["Geometry"])
        socket = consumer.inputs[input_name]
        assert socket.hide_value and tuple(socket.default_value) == (0.0, 0.0, 0.0)
        omitted = _positions(obj)
        assert len(set(omitted)) == 8
        socket.default_value = (0, 0, 0)
        assert _positions(obj) == omitted
        zero = group.nodes.new("ShaderNodeCombineXYZ")
        group.links.new(zero.outputs["Vector"], socket)
        connected = _positions(obj)
        assert len(connected) == 8 and set(connected) == {(0.0, 0.0, 0.0)}
        assert connected != omitted
    finally:
        _cleanup(group, obj, mesh)


def test_noise_unconnected_vector_is_implicit_position_not_zero():
    """Noise with no Vector link varies spatially; linked zero is uniform."""
    group, cube, output, obj, mesh = _fixture("NFRef_noise")
    try:
        noise = group.nodes.new("ShaderNodeTexNoise")
        store = group.nodes.new("GeometryNodeStoreNamedAttribute")
        store.data_type = "FLOAT"
        store.domain = "POINT"
        store.inputs["Name"].default_value = "nf_ref_noise"
        group.links.new(cube.outputs["Mesh"], store.inputs["Geometry"])
        group.links.new(noise.outputs["Fac"], store.inputs["Value"])
        group.links.new(store.outputs["Geometry"], output.inputs["Geometry"])

        def values():
            bpy.context.view_layer.update()
            evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
            evaluated_mesh = evaluated.to_mesh()
            try:
                data = evaluated_mesh.attributes["nf_ref_noise"].data
                return tuple(round(float(v.value), 5) for v in data)
            finally:
                evaluated.to_mesh_clear()

        socket = noise.inputs["Vector"]
        assert socket.hide_value
        omitted = values()
        assert len(set(omitted)) > 1
        socket.default_value = (0, 0, 0)
        assert values() == omitted
        zero = group.nodes.new("ShaderNodeCombineXYZ")
        group.links.new(zero.outputs["Vector"], socket)
        assert len(set(values())) == 1
    finally:
        _cleanup(group, obj, mesh)


def test_hidden_selection_bool_can_be_semantically_ordinary():
    """hide_value alone does not prove alternative unconnected semantics."""
    group, cube, output, obj, mesh = _fixture("NFRef_bool_selection")
    try:
        moved = group.nodes.new("GeometryNodeSetPosition")
        group.links.new(cube.outputs["Mesh"], moved.inputs["Geometry"])
        zero = group.nodes.new("ShaderNodeCombineXYZ")
        group.links.new(zero.outputs["Vector"], moved.inputs["Position"])
        group.links.new(moved.outputs["Geometry"], output.inputs["Geometry"])
        selection = moved.inputs["Selection"]
        assert selection.hide_value
        for flag in (False, True):
            selection.default_value = flag
            omitted = _positions(obj)
            constant = group.nodes.new("FunctionNodeInputBool")
            constant.boolean = flag
            group.links.new(constant.outputs[0], selection)
            assert _positions(obj) == omitted
            group.nodes.remove(constant)
        assert len(set(omitted)) == 1
    finally:
        _cleanup(group, obj, mesh)


def test_group_interface_contextual_input_overrides_only_when_linked():
    """A group interface POSITION fallback differs from a connected zero."""
    group, cube, output, obj, mesh = _fixture("NFRef_group_input")
    inner = bpy.data.node_groups.new("NFRef_group_context", "GeometryNodeTree")
    try:
        inner.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        argument = inner.interface.new_socket(name="Location", in_out="INPUT", socket_type="NodeSocketVector")
        argument.default_input = "POSITION"
        input_node = inner.nodes.new("NodeGroupInput")
        inside_cube = inner.nodes.new("GeometryNodeMeshCube")
        inside_cube.inputs["Size"].default_value = (2, 3, 4)
        move = inner.nodes.new("GeometryNodeSetPosition")
        inside_out = inner.nodes.new("NodeGroupOutput")
        inner.links.new(inside_cube.outputs["Mesh"], move.inputs["Geometry"])
        inner.links.new(input_node.outputs["Location"], move.inputs["Position"])
        inner.links.new(move.outputs["Geometry"], inside_out.inputs["Geometry"])
        caller = group.nodes.new("GeometryNodeGroup")
        caller.node_tree = inner
        group.links.new(caller.outputs["Geometry"], output.inputs["Geometry"])
        socket = caller.inputs["Location"]
        assert socket.hide_value
        omitted = _positions(obj)
        assert len(set(omitted)) == 8
        socket.default_value = (0, 0, 0)
        assert _positions(obj) == omitted
        zero = group.nodes.new("ShaderNodeCombineXYZ")
        group.links.new(zero.outputs["Vector"], socket)
        assert set(_positions(obj)) == {(0.0, 0.0, 0.0)}
        group.links.remove(next(link for link in group.links if link.to_socket == socket))
        assert _positions(obj) == omitted
        argument.default_input = "VALUE"
        assert argument.default_input == "VALUE"
    finally:
        _cleanup(group, obj, mesh, inner)


def test_visible_typed_defaults_and_dynamic_socket_shapes():
    """Blender exposes ordinary defaults only on the active configured sockets."""
    group = bpy.data.node_groups.new("NFRef_defaults", "GeometryNodeTree")
    try:
        supported = (
            ("GeometryNodeMeshCube", "Vertices X", 3),
            ("GeometryNodeMeshCube", "Size", (2.0, 1.0, 3.0)),
            ("FunctionNodeInputBool", None, None),
            ("GeometryNodeStoreNamedAttribute", "Name", "hello"),
        )
        for bl_idname, input_name, value in supported:
            node = group.nodes.new(bl_idname)
            if input_name is None:
                assert node.bl_idname == bl_idname
                continue
            socket = node.inputs[input_name]
            assert hasattr(socket, "default_value") and not socket.hide_value
            socket.default_value = value
            if isinstance(value, tuple):
                assert tuple(socket.default_value) == value
            else:
                assert socket.default_value == value
        switch = group.nodes.new("GeometryNodeSwitch")
        switch.input_type = "VECTOR"
        assert switch.inputs["True"].bl_idname == "NodeSocketVector"
        switch.input_type = "RGBA"
        assert switch.inputs["True"].bl_idname == "NodeSocketColor"
        combine = group.nodes.new("NodeCombineBundle")
        combine.bundle_items.new("FLOAT", "count")
        combine.bundle_items.new("BOOLEAN", "enabled")
        assert [item.name for item in combine.bundle_items] == ["count", "enabled"]
        assert combine.inputs["count"].bl_idname == "NodeSocketFloat"
        assert combine.inputs["enabled"].bl_idname == "NodeSocketBool"
        assert combine.inputs["count"].default_value == 0.0
        assert not combine.inputs["count"].hide_value
        set_position = group.nodes.new("GeometryNodeSetPosition")
        assert set_position.inputs["Geometry"].bl_idname == "NodeSocketGeometry"
        assert not hasattr(set_position.inputs["Geometry"], "default_value")
    finally:
        bpy.data.node_groups.remove(group, do_unlink=True)
