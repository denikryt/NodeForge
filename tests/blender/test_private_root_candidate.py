"""The root extension backend must see only an isolated candidate tree."""

import tempfile
from pathlib import Path

import bpy
import pytest

from helpers import compiler
from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
from NodeForge.blender.root_candidate import build_private_root_candidate, discard_private_root_candidate
from NodeForge.blender.root_update import _interface_signature
from NodeForge.function_instances import COMPOSITION_ROOT_ID_PROP, new_root_owner_id
from NodeForge.blender.generated_resources import GROUP_MANIFEST_PROP
from NodeForge import packages
from test_extension_dynamic_results import _write_package


def _prepare(source, root):
    # Isolated builder checks before checkpoint 07. Public root Compile must
    # retain its physical extension preflight until transfer/cutover is ready.
    from unittest.mock import patch
    with patch('NodeForge.blender.direct_composition.validate_prepared_direct'):
        return compiler._prepare_direct_source(source, root, module_context='text:extension-private-candidate')[:3]


def test_candidate_has_native_interface_but_no_original_graph_action_or_owner():
    root = bpy.data.node_groups.new('NFExt_stage_input', 'GeometryNodeTree')
    socket = root.interface.new_socket(name='Amount', in_out='INPUT', socket_type='NodeSocketFloat')
    initial_id = socket.identifier
    src_input = root.nodes.new('NodeGroupInput')
    root.nodes.new('GeometryNodeMeshCube')
    root[COMPOSITION_ROOT_ID_PROP] = new_root_owner_id()
    root[GROUP_MANIFEST_PROP] = 'previous-root-manifest'
    before = tuple(n.as_pointer() for n in root.nodes)
    signature = _interface_signature(root)
    prepared, backend, session = _prepare('x = cube(1)', root)
    tx = BlenderGroupBuildTransaction()
    candidate = build_private_root_candidate(prepared, root, backend, session,
                                              transaction=tx, root_owner_uuid='new-temporary-owner')
    try:
        assert candidate.stage is not root
        assert _interface_signature(candidate.stage) == signature
        assert root.interface.items_tree[0].identifier == initial_id
        assert tuple(n.as_pointer() for n in root.nodes) == before
        assert not any(n.bl_idname == 'NodeGroupInput' for n in candidate.stage.nodes)
        assert len([n for n in candidate.stage.nodes if n.bl_idname == 'GeometryNodeMeshCube']) == 1
        assert candidate.endpoint.node is None
        assert candidate.endpoint.existing_input.as_pointer() == src_input.as_pointer()
        assert COMPOSITION_ROOT_ID_PROP not in candidate.stage
        assert GROUP_MANIFEST_PROP not in candidate.stage
        assert candidate.stage.animation_data is None
    finally:
        discard_private_root_candidate(candidate)
        tx.rollback()


def test_extension_in_private_candidate_does_not_modify_real_root():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / 'package'
        _write_package(directory)
        packages.install_package_directory(directory)
        root = bpy.data.node_groups.new('NFExt_stage_extension', 'GeometryNodeTree')
        original = root.nodes.new('GeometryNodeMeshCube')
        before = original.as_pointer()
        prepared, backend, session = _prepare('from packages import dynamic\nr = foo(1.0)\na = r.double\nb = r.original', root)
        tx = BlenderGroupBuildTransaction()
        candidate = build_private_root_candidate(prepared, root, backend, session,
                                                  transaction=tx, root_owner_uuid='extension-stage-extension')
        try:
            assert [n.bl_idname for n in root.nodes] == ['GeometryNodeMeshCube']
            assert root.nodes[0].as_pointer() == before
            assert any(n.bl_idname == 'ShaderNodeMath' for n in candidate.stage.nodes)
            assert all(n.bl_idname != 'GeometryNodeMeshCube' for n in candidate.stage.nodes)
        finally:
            discard_private_root_candidate(candidate)
            tx.rollback()


def test_failing_backend_cannot_mutate_authoritative_root_via_context_group():
    from NodeForge.errors import CompileError
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / 'package'
        _write_package(directory, bad='short')
        packages.install_package_directory(directory)
        root = bpy.data.node_groups.new('NFExt_stage_backend_fails', 'GeometryNodeTree')
        old = root.nodes.new('GeometryNodeMeshCube')
        before = old.as_pointer()
        prepared, backend, session = _prepare('from packages import dynamic\nr = foo(1.0)\na = r.double\nb = r.original', root)
        groups = {g.as_pointer() for g in bpy.data.node_groups}
        meshes = {m.as_pointer() for m in bpy.data.meshes}
        tx = BlenderGroupBuildTransaction()
        with pytest.raises(CompileError, match='exact result keys'):
            build_private_root_candidate(prepared, root, backend, session,
                                         transaction=tx, root_owner_uuid='extension-stage-error')
        tx.rollback()
        assert len(root.nodes) == 1 and root.nodes[0].as_pointer() == before
        assert {g.as_pointer() for g in bpy.data.node_groups} == groups
        assert {m.as_pointer() for m in bpy.data.meshes} == meshes


def test_candidate_discards_inherited_action_but_does_not_change_root_animation():
    root = bpy.data.node_groups.new('NFExt_stage_action', 'GeometryNodeTree')
    action = bpy.data.actions.new('NFExt_preserve_action')
    root.animation_data_create().action = action
    prepared, backend, session = _prepare('x = cube(1)', root)
    tx = BlenderGroupBuildTransaction()
    candidate = build_private_root_candidate(prepared, root, backend, session,
                                              transaction=tx, root_owner_uuid='new-action-owner')
    try:
        assert candidate.stage.animation_data is None
        assert root.animation_data.action is action
    finally:
        discard_private_root_candidate(candidate)
        tx.rollback()
