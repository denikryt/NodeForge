"""Shared source/extension candidate lifecycle regression checks."""

from helpers import *
from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
import uuid


def _publisher():
    return compiler._new_group_backend()


def _fill_math(group, resources, *, operation):
    group.interface.new_socket(name='Value', in_out='OUTPUT', socket_type='NodeSocketFloat')
    node = group.nodes.new('ShaderNodeMath')
    node.operation = operation
    outlet = group.nodes.new('NodeGroupOutput')
    group.links.new(node.outputs['Value'], outlet.inputs['Value'])


def test_shared_publication_new_and_updated_group_keeps_external_instance():
    """The public source builder and extension builder share one actual cutover."""
    backend = _publisher()
    tx = BlenderGroupBuildTransaction()
    name = 'PR2_shared_candidate_' + uuid.uuid4().hex
    first = backend.publish_candidate(
        name=name, existing_group=None, build_tx=tx,
        populate=lambda group, resources: _fill_math(group, resources, operation='ADD'),
    )
    wrapper = bpy.data.node_groups.new('PR2_candidate_manual_' + uuid.uuid4().hex, 'GeometryNodeTree')
    instance = wrapper.nodes.new('GeometryNodeGroup')
    instance.node_tree = first
    original_pointer = first.as_pointer()
    backend.publish_candidate(
        name=name, existing_group=first, build_tx=tx,
        populate=lambda group, resources: _fill_math(group, resources, operation='MULTIPLY'),
    )
    assert first.as_pointer() == original_pointer
    assert instance.node_tree is first
    assert [n.operation for n in first.nodes if n.bl_idname == 'ShaderNodeMath'] == ['MULTIPLY']
    tx.commit()
    assert instance.node_tree is first


def test_shared_publication_failure_restores_existing_authority_and_generated_id():
    """A failed nested candidate never publishes a partial group or its owned IDs."""
    backend = _publisher()
    initial_tx = BlenderGroupBuildTransaction()
    name = 'PR2_shared_rollback_' + uuid.uuid4().hex
    authoritative = backend.publish_candidate(
        name=name, existing_group=None, build_tx=initial_tx,
        populate=lambda group, resources: _fill_math(group, resources, operation='ADD'),
    )
    initial_tx.commit()
    before_groups = {g.as_pointer() for g in bpy.data.node_groups}
    before_meshes = {m.as_pointer() for m in bpy.data.meshes}
    tx = BlenderGroupBuildTransaction()

    def broken_populate(group, resources):
        _fill_math(group, resources, operation='MULTIPLY')
        mesh = bpy.data.meshes.new('PR2_orphan_mesh_' + uuid.uuid4().hex)
        resources.add(mesh, 'MESH', 'test')
        raise RuntimeError('injected extension population error')

    with pytest.raises(RuntimeError, match='injected extension population error'):
        backend.publish_candidate(name=name, existing_group=authoritative,
                                  build_tx=tx, populate=broken_populate)
    assert [n.operation for n in authoritative.nodes if n.bl_idname == 'ShaderNodeMath'] == ['ADD']
    assert {g.as_pointer() for g in bpy.data.node_groups} == before_groups
    assert {m.as_pointer() for m in bpy.data.meshes} == before_meshes
    # A candidate failure rolls back to the nested savepoint but leaves
    # the outer transaction available for other legitimate publications.
    assert not tx._closed
    tx.rollback()


def test_shared_publication_rejects_closed_attempt_before_creating_candidate():
    backend = _publisher()
    tx = BlenderGroupBuildTransaction()
    tx.commit()
    before = {g.as_pointer() for g in bpy.data.node_groups}
    with pytest.raises(ValueError, match='open group transaction'):
        backend.publish_candidate(name='PR2_closed_candidate', existing_group=None,
                                  build_tx=tx, populate=lambda group, resources: None)
    assert {g.as_pointer() for g in bpy.data.node_groups} == before
