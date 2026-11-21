"""Build an isolated native-interface root candidate for physical extension calls."""

from __future__ import annotations

from dataclasses import dataclass

import bpy

from ..errors import CompileError
from ..function_instances import COMPOSITION_ROOT_ID_PROP
from .generated_resources import GeneratedResourceTransaction, GROUP_MANIFEST_PROP
from .group_authority import BACKEND_PRIVATE_GROUP_PROPERTIES, mark_transaction_private
from .group_backend import _copy_node_properties
from .ir_lowering import RootInputEndpoint
from .root_update import _interface_signature, validate_base_target


@dataclass(frozen=True)
class PrivateRootCandidate:
    """Borrow one attempt-owned Blender stage, shared input endpoint and resources."""

    stage: object
    endpoint: RootInputEndpoint
    resource_transaction: GeneratedResourceTransaction
    interface_signature: tuple


def discard_private_root_candidate(candidate: PrivateRootCandidate):
    """Dispose a stage; the outer root attempt owns resource rollback/commit."""
    if any(group is candidate.stage for group in bpy.data.node_groups):
        bpy.data.node_groups.remove(candidate.stage, do_unlink=True)


def _new_private_copy(target):
    """Create an empty private graph while preserving Blender's native interface IDs."""
    interface_before = _interface_signature(target)
    stage = target.copy()
    try:
        mark_transaction_private(stage)
        stage.animation_data_clear()  # The copy must not share the source Action.
        stage.nodes.clear()
        for name in tuple(stage.keys()):
            if name not in BACKEND_PRIVATE_GROUP_PROPERTIES:
                del stage[name]
        if _interface_signature(stage) != interface_before:
            raise CompileError('Private root candidate changed native interface identifiers')
        return stage, interface_before
    except Exception:
        bpy.data.node_groups.remove(stage, do_unlink=True)
        raise


def build_private_root_candidate(prepared, target, backend, session, *, transaction, root_owner_uuid):
    """Lower physical root IR into private ``target.copy()`` only, never the user root."""
    from .direct_composition import compose_prepared_initial

    validate_base_target(target)
    if not isinstance(root_owner_uuid, str) or not root_owner_uuid:
        raise ValueError('Root candidate requires the prepared physical owner UUID')
    source_signature = _interface_signature(target)
    stage, stage_signature = _new_private_copy(target)
    tx = GeneratedResourceTransaction(owner_group_uuid=root_owner_uuid)
    endpoint = RootInputEndpoint()
    candidate = PrivateRootCandidate(stage, endpoint, tx, stage_signature)
    try:
        first_existing_input = next(
            (node for node in target.nodes if node.bl_idname == 'NodeGroupInput'), None
        )
        if first_existing_input is not None:
            copied_input = stage.nodes.new('NodeGroupInput')
            _copy_node_properties(first_existing_input, copied_input)
            endpoint.node = copied_input
            endpoint.created_by_attempt = False
            # Strict native sockets and properties are also checked during transfer.
            old_sockets = tuple((item.identifier, item.name, item.bl_idname)
                                for item in first_existing_input.outputs)
            new_sockets = tuple((item.identifier, item.name, item.bl_idname)
                                for item in copied_input.outputs)
            if old_sockets != new_sockets:
                raise CompileError('Private root Group Input changed native socket identifiers')
        if _interface_signature(target) != source_signature:
            raise CompileError('Authoritative root interface changed before private lowering')
        compose_prepared_initial(
            prepared, stage, backend, session, transaction=transaction,
            root_input_endpoint=endpoint, generated_resource_transaction=tx,
        )
        if (_interface_signature(stage) != stage_signature
                or _interface_signature(target) != source_signature):
            raise CompileError('Private lowering modified the native root interface')
        if COMPOSITION_ROOT_ID_PROP in stage or GROUP_MANIFEST_PROP in stage:
            raise CompileError('Private candidate inherited authoritative root ownership metadata')
        return candidate
    except Exception:
        try:
            tx.rollback()
        finally:
            discard_private_root_candidate(candidate)
        raise


__all__ = ['PrivateRootCandidate', 'build_private_root_candidate', 'discard_private_root_candidate']
