"""Build an isolated native-interface root candidate for physical extension calls."""

from __future__ import annotations

from dataclasses import dataclass

import bpy

from ..errors import CompileError
from ..function_instances import COMPOSITION_ROOT_ID_PROP
from .generated_resources import GeneratedResourceTransaction, GROUP_MANIFEST_PROP
from .group_authority import BACKEND_PRIVATE_GROUP_PROPERTIES, mark_transaction_private
from .ir_lowering import RootInputEndpoint
from .root_update import _interface_signature, validate_base_target


@dataclass(frozen=True)
class PrivateRootCandidate:
    """Borrow one attempt-owned Blender stage, shared input endpoint and resources."""

    stage: object
    endpoint: RootInputEndpoint
    resource_transaction: GeneratedResourceTransaction
    transaction: object


def discard_private_root_candidate(candidate: PrivateRootCandidate):
    """Dispose a stage; the outer root attempt owns resource rollback/commit."""
    if any(group is candidate.stage for group in bpy.data.node_groups):
        candidate.transaction.discard_abandoned_tree(candidate.stage)


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
    # Only an explicit raw DSL call may materialize a structural Group Input.
    # Retain the original for read-only parity checks, never as a stage node
    # visible to a Python extension backend before that call.
    first_existing_input = next(
        (node for node in target.nodes if node.bl_idname == 'NodeGroupInput'), None
    )
    endpoint = RootInputEndpoint(existing_input=first_existing_input)
    candidate = PrivateRootCandidate(stage, endpoint, tx, transaction)
    try:
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
    except Exception as error:
        for cleanup in (tx.rollback, lambda: discard_private_root_candidate(candidate)):
            try:
                cleanup()
            except Exception as exc:
                error.add_note(f"Private candidate cleanup also failed: {exc}")
        raise


__all__ = ['PrivateRootCandidate', 'build_private_root_candidate', 'discard_private_root_candidate']
