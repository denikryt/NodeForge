"""Public root Compile publishes Python backends only through private candidates."""

import tempfile
from pathlib import Path

import bpy
import pytest

from NodeForge import compiler, packages
from NodeForge.errors import CompileError
from NodeForge.blender.generated_resources import read_group_manifest
from NodeForge.blender.extension_group_materializer import EXTENSION_AUTHORITY_PROP
from NodeForge.function_instances import COMPOSITION_ROOT_ID_PROP
from test_extension_dynamic_results import _write_package


def _package(temp, *, bad=''):
    directory = Path(temp) / 'package'
    _write_package(directory, bad=bad)
    packages.install_package_directory(directory)


def test_root_inline_dynamic_backend_publishes_without_append_and_stable_identity():
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp)
        root = bpy.data.node_groups.new('PR2_public_inline', 'GeometryNodeTree')
        old = root.nodes.new('GeometryNodeMeshCube')
        code = 'from packages import dynamic\na, b = foo(1.0)\n'
        first = compiler.compile_source_in_tree(code, root)
        assert first.outcome == 'ROOT_REPLACEMENT'
        assert not any(n.bl_idname == 'GeometryNodeMeshCube' for n in root.nodes)
        assert sum(n.bl_idname == 'ShaderNodeMath' for n in root.nodes) == 1
        assert not any(n.bl_idname == 'GeometryNodeGroup' for n in root.nodes)
        root_uuid = root[COMPOSITION_ROOT_ID_PROP]
        manifest = read_group_manifest(root)
        assert manifest['owner_group_uuid'] == root_uuid
        assert len(manifest['resources']) == 1
        root.name = 'PR2_public_inline_renamed'
        second = compiler.compile_source_in_tree(code, root)
        assert second.outcome == 'ROOT_REPLACEMENT'
        assert root[COMPOSITION_ROOT_ID_PROP] == root_uuid
        assert len([n for n in root.nodes if n.bl_idname == 'ShaderNodeMath']) == 1
        assert len(read_group_manifest(root)['resources']) == 1


def test_root_grouped_extension_has_per_root_authority_and_preserves_manual_input():
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp)
        roots = [bpy.data.node_groups.new('PR2_public_grouped'+str(i), 'GeometryNodeTree') for i in range(2)]
        for root in roots:
            root.interface.new_socket(name='Value', in_out='INPUT', socket_type='NodeSocketFloat')
            inlet = root.nodes.new('NodeGroupInput')
            inlet.label = 'Keep input'
        code = 'from packages import dynamic\na, b = foo(1.0, __inline__=False)\n'
        first = []
        for root in roots:
            compiler.compile_source_in_tree(code, root, module_context='text:same')
            groups = [n for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
            assert len(groups) == 1
            assert [n.label for n in root.nodes if n.bl_idname == 'NodeGroupInput'] == ['Keep input']
            first.append(groups[0].node_tree)
        assert first[0].as_pointer() != first[1].as_pointer()
        assert first[0][EXTENSION_AUTHORITY_PROP] != first[1][EXTENSION_AUTHORITY_PROP]
        for root, prior in zip(roots, first):
            compiler.compile_source_in_tree(code, root, module_context='text:same')
            group, = [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
            assert group.as_pointer() == prior.as_pointer()


def test_failed_root_backend_keeps_old_root_and_no_owner_stamp():
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp, bad='short')
        root = bpy.data.node_groups.new('PR2_public_backend_failure', 'GeometryNodeTree')
        manual = root.nodes.new('GeometryNodeMeshCube')
        pointer = manual.as_pointer()
        before_groups = {g.as_pointer() for g in bpy.data.node_groups}
        before_meshes = {m.as_pointer() for m in bpy.data.meshes}
        with pytest.raises(CompileError, match='2-item tuple'):
            compiler.compile_source_in_tree('from packages import dynamic\na,b = foo(1.0)', root)
        assert root.nodes[0].as_pointer() == pointer and len(root.nodes) == 1
        assert COMPOSITION_ROOT_ID_PROP not in root
        assert {g.as_pointer() for g in bpy.data.node_groups} == before_groups
        assert {m.as_pointer() for m in bpy.data.meshes} == before_meshes


def test_groups_only_after_root_extension_keeps_root_and_manifest_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp)
        root = bpy.data.node_groups.new('PR2_group_only_after_root', 'GeometryNodeTree')
        compiler.compile_source_in_tree('from packages import dynamic\na, b = foo(1.0)', root)
        before_nodes = tuple(n.as_pointer() for n in root.nodes)
        before_manifest = root.get('nodeforge_generated_resources_manifest_v1')
        before_uuid = root[COMPOSITION_ROOT_ID_PROP]
        assert compiler.compile_source_in_tree('FLAG = False', root).outcome == 'GROUPS_ONLY'
        assert tuple(n.as_pointer() for n in root.nodes) == before_nodes
        assert root[COMPOSITION_ROOT_ID_PROP] == before_uuid
        assert root.get('nodeforge_generated_resources_manifest_v1') == before_manifest


def test_transfer_failure_leaves_manual_graph_and_discards_attempt_nodes(monkeypatch):
    from NodeForge.blender import root_subgraph_transfer, root_update
    root = bpy.data.node_groups.new('PR2_root_transfer_failure', 'GeometryNodeTree')
    old = root.nodes.new('GeometryNodeMeshCube')
    old_pointer = old.as_pointer()
    before = {group.as_pointer() for group in bpy.data.node_groups}
    original = root_subgraph_transfer._copy_node_properties
    calls = 0

    def fail_second(src, dst):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('PR2 copier failed after one node')
        return original(src, dst)

    monkeypatch.setattr(root_subgraph_transfer, '_copy_node_properties', fail_second)
    with pytest.raises(root_update.RootUpdateError, match='PR2 copier failed') as info:
        compiler.compile_source_in_tree('a = cube(1)\nb = cube(2)', root)
    assert not info.value.may_have_mutated
    assert len(root.nodes) == 1 and root.nodes[0].as_pointer() == old_pointer
    assert COMPOSITION_ROOT_ID_PROP not in root
    assert {group.as_pointer() for group in bpy.data.node_groups} == before


def test_root_backend_that_clears_canonical_input_is_rejected(monkeypatch, tmp_path):
    from test_unified_runtime_capability_preflight import _install_capability_fixture
    _install_capability_fixture(tmp_path)
    root = bpy.data.node_groups.new('PR2_root_unauthorized_input_removal', 'GeometryNodeTree')
    root.interface.new_socket(name='Value', in_out='INPUT', socket_type='NodeSocketFloat')
    inlet = root.nodes.new('NodeGroupInput')
    old = root.nodes.new('GeometryNodeMeshCube')
    before = {n.as_pointer() for n in root.nodes}
    with pytest.raises(CompileError, match='Group Input'):
        compiler.compile_source_in_tree('from packages import danger\nx = unsafe_wipe(1.0)', root)
    assert {n.as_pointer() for n in root.nodes} == before
    assert inlet in root.nodes[:]
    assert COMPOSITION_ROOT_ID_PROP not in root


def test_postcommit_root_resource_cleanup_exception_keeps_undo_signal(monkeypatch):
    import warnings
    from NodeForge.blender import generated_resources, root_update
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp)
        root = bpy.data.node_groups.new('PR2_postcommit_root_resource', 'GeometryNodeTree')
        compiler.compile_source_in_tree('from packages import dynamic\na,b=foo(1.0)', root)
        uuid_before = root[COMPOSITION_ROOT_ID_PROP]
        old_manifest = read_group_manifest(root)
        assert old_manifest and old_manifest['resources']

        def fail_retirement(*args, **kwargs):
            raise RuntimeError('PR2 failed after root publication')

        monkeypatch.setattr(root_update, 'cleanup_previous_after_commit', fail_retirement, raising=False)
        # Imported inside replace_prepared_root: patch the actual owner module.
        monkeypatch.setattr(generated_resources, 'cleanup_previous_after_commit', fail_retirement)
        with warnings.catch_warnings():
            warnings.simplefilter('error', RuntimeWarning)
            with pytest.raises(root_update.RootUpdateError, match='postcommit cleanup') as info:
                compiler.compile_source_in_tree('a = cube(1)', root)
        assert info.value.may_have_mutated
        assert root[COMPOSITION_ROOT_ID_PROP] == uuid_before
        assert len([n for n in root.nodes if n.bl_idname == 'GeometryNodeMeshCube']) == 1
        assert not read_group_manifest(root)['resources']


def test_groups_only_before_first_root_compile_does_not_stamp_composition_identity():
    root = bpy.data.node_groups.new('PR2_root_group_only_no_identity', 'GeometryNodeTree')
    manual = root.nodes.new('GeometryNodeMeshCube')
    before = manual.as_pointer()
    assert compiler.compile_source_in_tree('FLAG = False', root).outcome == 'GROUPS_ONLY'
    assert COMPOSITION_ROOT_ID_PROP not in root
    assert root.nodes[0].as_pointer() == before
