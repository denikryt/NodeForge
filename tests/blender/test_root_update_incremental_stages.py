"""Regression probes for direct lowering using a caller-owned transaction."""

from helpers import *
from NodeForge.blender.direct_composition import compose_prepared_initial
from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
from NodeForge.blender import ir_lowering


def _prepare(source, name):
    """Prepare typed source into an eligible, empty physical tree."""
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    prepared, backend, session, snapshot = compiler._prepare_direct_source(source, tree)
    return tree, prepared, backend, session, snapshot


def test_caller_transaction_is_not_committed_on_success(monkeypatch):
    """Externally owned transactions are not committed by direct lowering."""
    tree, prepared, backend, session, snapshot = _prepare('a = cube(1)', 'NFR5_stage1_owned')
    transaction = BlenderGroupBuildTransaction()

    def forbidden():
        """Fail if lowerer attempts to commit its caller-owned resource."""
        raise AssertionError('caller transaction must remain uncommitted')

    monkeypatch.setattr(transaction, 'commit', forbidden)
    compose_prepared_initial(prepared, tree, backend, session,
                             source_module=snapshot, transaction=transaction)
    assert sum(n.bl_idname == 'GeometryNodeMeshCube' for n in tree.nodes) == 1
    transaction.rollback()


def test_caller_transaction_failure_removes_new_nodes_without_committing(monkeypatch):
    """Injected lowering fault removes attempt-only nodes without touching old nodes."""
    tree, prepared, backend, session, snapshot = _prepare('a = cube(1)\nb = cube(2)', 'NFR5_stage1_failure')
    old = tree.nodes.new('GeometryNodeMeshCube')
    original = ir_lowering._cube_geometry
    count = 0

    def fail_second(*args, **kwargs):
        """Inject a failure after the second node insertion."""
        nonlocal count
        result = original(*args, **kwargs)
        count += 1
        if count == 2:
            raise RuntimeError('lowering injection')
        return result

    monkeypatch.setattr(ir_lowering, '_cube_geometry', fail_second)
    transaction = BlenderGroupBuildTransaction()
    with pytest.raises(RuntimeError, match='lowering injection'):
        compose_prepared_initial(prepared, tree, backend, session,
                                 source_module=snapshot, transaction=transaction)
    assert {n.as_pointer() for n in tree.nodes} == {old.as_pointer()}
    transaction.rollback()


from NodeForge.blender import root_update


def _new_root(name):
    """Create a root with manually edited ordinary and I/O-connected nodes."""
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    outlet = tree.nodes.new('NodeGroupOutput')
    old = tree.nodes.new('GeometryNodeMeshCube')
    tree.links.new(old.outputs['Mesh'], outlet.inputs['Geometry'])
    return tree, old, outlet


def test_stage_preserves_old_graph_and_creates_private_copy():
    """Positive stage leaves old ordinary pointers and links intact."""
    tree, old, outlet = _new_root('NFR5_stage2_positive')
    old_links = {(e.from_node.as_pointer(), e.to_node.as_pointer()) for e in tree.links}
    preexisting = {n.as_pointer() for n in tree.nodes}
    prepared, backend, session, snapshot = compiler._prepare_direct_source('a = cube(3)', tree)
    stage = root_update.stage_prepared_root(prepared, tree, backend, session, source_module=snapshot)
    assert len(stage.new) == 1
    assert preexisting.issubset({n.as_pointer() for n in tree.nodes})
    assert old_links.issubset({(e.from_node.as_pointer(), e.to_node.as_pointer()) for e in tree.links})
    assert any(group is stage.backup for group in bpy.data.node_groups)
    root_update._remove_staged(tree, stage.preexisting_ptrs)
    stage.transaction.rollback()
    root_update._remove_backup(stage.backup)


def test_stage_failure_restores_old_root_and_removes_private_copy(monkeypatch):
    """Negative stage rolls back newly inserted nodes and private backup."""
    tree, old, outlet = _new_root('NFR5_stage2_negative')
    preexisting = {n.as_pointer() for n in tree.nodes}
    before_groups = {g.as_pointer() for g in bpy.data.node_groups}
    prepared, backend, session, snapshot = compiler._prepare_direct_source('a = cube(1)\nb = cube(2)', tree)
    original = ir_lowering._cube_geometry
    count = 0

    def fail_second(*args, **kwargs):
        """Fail after the second stage node has been created."""
        nonlocal count
        result = original(*args, **kwargs)
        count += 1
        if count == 2:
            raise RuntimeError('stage failure')
        return result

    monkeypatch.setattr(ir_lowering, '_cube_geometry', fail_second)
    with pytest.raises(root_update.RootUpdateError, match='stage failure'):
        root_update.stage_prepared_root(prepared, tree, backend, session, source_module=snapshot)
    assert {n.as_pointer() for n in tree.nodes} == preexisting
    assert len(tree.links) == 1
    assert {g.as_pointer() for g in bpy.data.node_groups} == before_groups



def test_cutover_replaces_manual_group_node_and_preserves_io():
    """Successful cutover removes ordinary nodes while retaining physical root I/O."""
    tree, old, outlet = _new_root('NFR5_stage3_positive')
    sub = bpy.data.node_groups.new('NFR5_manual_stage3', 'GeometryNodeTree')
    sub.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group = tree.nodes.new('GeometryNodeGroup')
    group.node_tree = sub
    old_ids = {old.as_pointer(), group.as_pointer()}
    io_id = outlet.as_pointer()
    root_id = tree.as_pointer()
    prepared, backend, session, snapshot = compiler._prepare_direct_source('a = cube(3)', tree)
    stage = root_update.stage_prepared_root(prepared, tree, backend, session, source_module=snapshot)
    assert old_ids.issubset({n.as_pointer() for n in tree.nodes})
    result = root_update.cutover_staged_root(stage)
    assert result.target is tree and tree.as_pointer() == root_id
    assert outlet.as_pointer() == io_id
    assert not old_ids.intersection(n.as_pointer() for n in tree.nodes)
    assert len([n for n in tree.nodes if n.bl_idname == 'GeometryNodeMeshCube']) == 1


def test_cutover_failure_attempts_recovery(monkeypatch):
    """After deletion starts, a late exception triggers best-effort restoration."""
    tree, old, outlet = _new_root('NFR5_stage3_negative')
    prepared, backend, session, snapshot = compiler._prepare_direct_source('a = cube(3)', tree)
    stage = root_update.stage_prepared_root(prepared, tree, backend, session, source_module=snapshot)
    original = root_update._interface_signature
    def invalid_signature(target):
        """Cause the post-cutover contract check to fail."""
        return ('injected mismatch',)
    monkeypatch.setattr(root_update, '_interface_signature', invalid_signature)
    with pytest.raises(root_update.RootUpdateError, match='Update changed root identity') as info:
        root_update.cutover_staged_root(stage)
    assert info.value.may_have_mutated
    assert len([n for n in tree.nodes if n.bl_idname == 'GeometryNodeMeshCube']) == 1
