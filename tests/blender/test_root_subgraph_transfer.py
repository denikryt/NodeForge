"""Fail-closed private-stage transfer before the user-root cutover is enabled."""

import bpy
import pytest

from NodeForge.errors import CompileError
from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
from NodeForge.blender.root_candidate import build_private_root_candidate, discard_private_root_candidate
from NodeForge.blender.root_subgraph_transfer import transfer_private_root_subgraph
from NodeForge.blender.root_update import _interface_signature
from NodeForge.function_instances import new_root_owner_id
from test_private_root_candidate import _prepare


def _stage(source='x = cube(1)', *, existing_input=False):
    root = bpy.data.node_groups.new('NFExt_transfer_root', 'GeometryNodeTree')
    root.interface.new_socket(name='Radius', in_out='INPUT', socket_type='NodeSocketFloat')
    original = root.nodes.new('GeometryNodeMeshCube')
    if existing_input:
        entry = root.nodes.new('NodeGroupInput')
        entry.location = (-340, 120)
    prepared, backend, session = _prepare(source, root)
    tx = BlenderGroupBuildTransaction()
    candidate = build_private_root_candidate(prepared, root, backend, session, transaction=tx,
                                              root_owner_uuid=new_root_owner_id())
    return root, original, candidate, tx


def _close(root, candidate, tx):
    discard_private_root_candidate(candidate)
    tx.rollback()


def test_transfer_keeps_old_root_nodes_and_clones_new_nodes():
    root, old, candidate, tx = _stage()
    try:
        before = old.as_pointer()
        transferred = transfer_private_root_subgraph(candidate, root,
                                                      interface_signature=_interface_signature(root))
        assert old.as_pointer() == before and old in root.nodes[:]
        assert transferred.created_nodes
        assert sum(n.bl_idname == 'GeometryNodeMeshCube' for n in root.nodes) == 2
    finally:
        _close(root, candidate, tx)


def test_transfer_uses_first_existing_group_input_without_modification():
    root, old, candidate, tx = _stage(existing_input=True)
    try:
        first = next(n for n in root.nodes if n.bl_idname == 'NodeGroupInput')
        initial = (first.as_pointer(), tuple(first.location))
        transferred = transfer_private_root_subgraph(candidate, root,
                                                      interface_signature=_interface_signature(root))
        assert transferred.new_group_input is None
        assert len([n for n in root.nodes if n.bl_idname == 'NodeGroupInput']) == 1
        assert (first.as_pointer(), tuple(first.location)) == initial
    finally:
        _close(root, candidate, tx)


def test_transfer_rejects_unauthorized_structural_output_before_target_mutation():
    root, old, candidate, tx = _stage()
    try:
        candidate.stage.nodes.new('NodeGroupOutput')
        before = tuple(n.as_pointer() for n in root.nodes)
        with pytest.raises(CompileError, match='Group Output'):
            transfer_private_root_subgraph(candidate, root,
                                           interface_signature=_interface_signature(root))
        assert tuple(n.as_pointer() for n in root.nodes) == before
    finally:
        _close(root, candidate, tx)


def test_transfer_rejects_modified_root_interface_before_target_mutation():
    root, old, candidate, tx = _stage()
    try:
        initial = _interface_signature(root)
        root.interface.new_socket(name='New', in_out='INPUT', socket_type='NodeSocketFloat')
        before = tuple(n.as_pointer() for n in root.nodes)
        with pytest.raises(CompileError, match='interface changed'):
            transfer_private_root_subgraph(candidate, root, interface_signature=initial)
        assert tuple(n.as_pointer() for n in root.nodes) == before
    finally:
        _close(root, candidate, tx)


def test_transfer_rejects_unpaired_repeat_input_before_target_mutation():
    root, old, candidate, tx = _stage()
    try:
        candidate.stage.nodes.new('GeometryNodeRepeatInput')
        before = tuple(n.as_pointer() for n in root.nodes)
        with pytest.raises(CompileError, match='Unpaired'):
            transfer_private_root_subgraph(candidate, root,
                                           interface_signature=_interface_signature(root))
        assert tuple(n.as_pointer() for n in root.nodes) == before
    finally:
        _close(root, candidate, tx)


def test_transfer_restores_heterogeneous_capture_items_and_links():
    root, old, candidate, tx = _stage()
    try:
        stage = candidate.stage
        cap = stage.nodes.new('GeometryNodeCaptureAttribute')
        cap.capture_items.clear()
        for typ, name in [('VECTOR', 'Pos'), ('INT', 'Index'), ('FLOAT', 'Alpha'),
                          ('FLOAT', 'Beta'), ('BOOLEAN', 'Selected'), ('RGBA', 'Tint'),
                          ('ROTATION', 'Spin')]:
            cap.capture_items.new(typ, name)
        cap.domain = 'POINT'
        mesh = next(n for n in stage.nodes if n.bl_idname == 'GeometryNodeMeshCube')
        stage.links.new(mesh.outputs['Mesh'], cap.inputs['Geometry'])
        capture_names = tuple((x.name, x.data_type) for x in cap.capture_items)
        transferred = transfer_private_root_subgraph(candidate, root,
                                                      interface_signature=_interface_signature(root))
        dst = next(n for n in transferred.created_nodes if n.bl_idname == 'GeometryNodeCaptureAttribute')
        assert tuple((x.name, x.data_type) for x in dst.capture_items) == capture_names
        assert len([l for l in root.links if l.to_node == dst and l.to_socket.name == 'Geometry']) == 1
    finally:
        _close(root, candidate, tx)


def test_transfer_restores_repeat_pair_and_state_sockets():
    root, old, candidate, tx = _stage()
    try:
        stage = candidate.stage
        loop_input = stage.nodes.new('GeometryNodeRepeatInput')
        loop_output = stage.nodes.new('GeometryNodeRepeatOutput')
        assert loop_input.pair_with_output(loop_output)
        loop_output.repeat_items.clear()
        loop_output.repeat_items.new('FLOAT', 'Mass')
        stage.links.new(loop_input.outputs['Mass'], loop_output.inputs['Mass'])
        transferred = transfer_private_root_subgraph(candidate, root,
                                                      interface_signature=_interface_signature(root))
        r_in = next(n for n in transferred.created_nodes if n.bl_idname == 'GeometryNodeRepeatInput')
        r_out = next(n for n in transferred.created_nodes if n.bl_idname == 'GeometryNodeRepeatOutput')
        assert r_in.paired_output == r_out
        assert tuple((i.name, i.socket_type) for i in r_out.repeat_items) == (('Mass','FLOAT'),)
        assert any(l.from_node == r_in and l.to_node == r_out and l.from_socket.name == 'Mass'
                   for l in root.links)
    finally:
        _close(root, candidate, tx)


def test_transfer_rejects_new_second_input_before_modifying_target():
    root, old, candidate, tx = _stage(existing_input=True)
    try:
        candidate.stage.nodes.new('NodeGroupInput')
        before = tuple(n.as_pointer() for n in root.nodes)
        with pytest.raises(CompileError, match='Group Input'):
            transfer_private_root_subgraph(candidate, root,
                                           interface_signature=_interface_signature(root))
        assert tuple(n.as_pointer() for n in root.nodes) == before
    finally:
        _close(root, candidate, tx)


@pytest.mark.parametrize('existing_input', [True, False])
def test_transfer_raw_nonconsecutive_native_input_and_exact_link(existing_input):
    root = bpy.data.node_groups.new('NFExt_native_socket_transfer', 'GeometryNodeTree')
    deleted = root.interface.new_socket(name='Removed', in_out='INPUT', socket_type='NodeSocketFloat')
    radius = root.interface.new_socket(name='Radius', in_out='INPUT', socket_type='NodeSocketFloat')
    root.interface.remove(deleted)
    inlet = root.nodes.new('NodeGroupInput') if existing_input else None
    if inlet:
        inlet.label = 'manual'
    old = root.nodes.new('GeometryNodeMeshCube')
    source = ('value = node("NodeGroupInput", output=ID("'+radius.identifier+'"), typ=Float)\n'
              'a = cube(value)')
    prepared, backend, session = _prepare(source, root)
    tx = BlenderGroupBuildTransaction()
    candidate = build_private_root_candidate(prepared, root, backend, session, transaction=tx,
                                              root_owner_uuid=new_root_owner_id())
    try:
        nodes = transfer_private_root_subgraph(candidate, root, interface_signature=_interface_signature(root))
        inputs = [n for n in root.nodes if n.bl_idname == 'NodeGroupInput']
        assert len(inputs) == 1
        assert inputs[0].as_pointer() == (inlet.as_pointer() if inlet else nodes.new_group_input.as_pointer())
        if inlet:
            assert inlet.label == 'manual'
        assert any(l.from_node == inputs[0] and l.from_socket.identifier == radius.identifier
                   for l in root.links)
        assert old in root.nodes[:]
    finally:
        _close(root, candidate, tx)


def test_transfer_preserves_frame_parenting_and_multi_input_links():
    root, old, candidate, tx = _stage()
    try:
        stage = candidate.stage
        frame = stage.nodes.new('NodeFrame')
        frame.label = 'Subgraph'
        cubes = [n for n in stage.nodes if n.bl_idname == 'GeometryNodeMeshCube']
        cubes[0].parent = frame
        second = stage.nodes.new('GeometryNodeMeshCube')
        second.parent = frame
        join = stage.nodes.new('GeometryNodeJoinGeometry')
        join.parent = frame
        stage.links.new(cubes[0].outputs['Mesh'], join.inputs['Geometry'])
        stage.links.new(second.outputs['Mesh'], join.inputs['Geometry'])
        transferred = transfer_private_root_subgraph(candidate, root,
                                                      interface_signature=_interface_signature(root))
        dst_frame, = [n for n in transferred.created_nodes if n.bl_idname == 'NodeFrame']
        dst_join, = [n for n in transferred.created_nodes if n.bl_idname == 'GeometryNodeJoinGeometry']
        assert dst_join.parent == dst_frame
        assert len([l for l in root.links if l.to_node == dst_join]) == 2
        assert old in root.nodes[:]
    finally:
        _close(root, candidate, tx)


def test_transfer_preserves_external_datablock_references_and_curve_mapping():
    root, old, candidate, tx = _stage()
    try:
        stage = candidate.stage
        material = bpy.data.materials.new('NFExt_Transfer_Material')
        set_material = stage.nodes.new('GeometryNodeSetMaterial')
        set_material.inputs['Material'].default_value = material
        curve = stage.nodes.new('ShaderNodeFloatCurve')
        curve.mapping.curves[0].points.new(0.35, 0.65)
        curve.mapping.update()
        transferred = transfer_private_root_subgraph(candidate, root,
                                                      interface_signature=_interface_signature(root))
        material_node, = [n for n in transferred.created_nodes if n.bl_idname == 'GeometryNodeSetMaterial']
        curve_node, = [n for n in transferred.created_nodes if n.bl_idname == 'ShaderNodeFloatCurve']
        assert material_node.inputs['Material'].default_value is material
        assert len(curve_node.mapping.curves[0].points) == len(curve.mapping.curves[0].points)
        assert all(tuple(p.location) == pytest.approx(tuple(q.location))
                   for p, q in zip(curve_node.mapping.curves[0].points, curve.mapping.curves[0].points))
        assert old in root.nodes[:]
    finally:
        _close(root, candidate, tx)


def test_transfer_preserves_multi_input_link_sequence():
    root, old, candidate, tx = _stage()
    try:
        stage = candidate.stage
        cubes = [n for n in stage.nodes if n.bl_idname == 'GeometryNodeMeshCube']
        for size in (2.0, 3.0):
            n = stage.nodes.new('GeometryNodeMeshCube')
            n.inputs['Size'].default_value = (size, size, size)
            cubes.append(n)
        cubes[0].inputs['Size'].default_value = (1.0, 1.0, 1.0)
        join = stage.nodes.new('GeometryNodeJoinGeometry')
        for node in cubes:
            stage.links.new(node.outputs['Mesh'], join.inputs['Geometry'])
        expected = tuple(float(l.from_node.inputs['Size'].default_value[0]) for l in stage.links
                         if l.to_node == join)
        transferred = transfer_private_root_subgraph(candidate, root,
                                                      interface_signature=_interface_signature(root))
        target_join, = [n for n in transferred.created_nodes if n.bl_idname == 'GeometryNodeJoinGeometry']
        actual = tuple(float(l.from_node.inputs['Size'].default_value[0]) for l in root.links
                       if l.to_node == target_join)
        assert actual == expected
    finally:
        _close(root, candidate, tx)


def test_transfer_preserves_bundle_dynamic_items_even_without_links():
    root, old, candidate, tx = _stage()
    try:
        stage = candidate.stage
        bundle = stage.nodes.new('NodeCombineBundle')
        bundle.bundle_items.clear()
        bundle.bundle_items.new('FLOAT', 'Mass')
        bundle.bundle_items.new('VECTOR', 'Position')
        expected = tuple((i.name, i.socket_type) for i in bundle.bundle_items)
        result = transfer_private_root_subgraph(candidate, root,
                                                interface_signature=_interface_signature(root))
        copied, = [n for n in result.created_nodes if n.bl_idname == 'NodeCombineBundle']
        assert tuple((i.name, i.socket_type) for i in copied.bundle_items) == expected
    finally:
        _close(root, candidate, tx)
