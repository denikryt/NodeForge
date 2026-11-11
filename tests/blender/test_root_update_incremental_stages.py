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
