"""Adversarial root-input authorization and retired extension-owner regressions."""

import tempfile
from pathlib import Path

import bpy
import pytest

from NodeForge import compiler, packages
from NodeForge.errors import CompileError
from NodeForge.blender.extension_group_materializer import EXTENSION_AUTHORITY_PROP
from NodeForge.blender.group_authority import SESSION_PUBLISHED_GROUPS_KEY
from test_extension_dynamic_results import _write_package


def _install_fixture(directory, *, backend_mode='ordinary'):
    """Install one v2 backend with optional ambient input access or owned IDs."""
    root = Path(directory) / 'fixture'
    _write_package(root)
    backend = root / 'systems' / 'dynamic' / 'backend.py'
    source = backend.read_text()
    if backend_mode == 'ambient':
        source = source.replace(
            'node.inputs[1].default_value = 2.0',
            '''node.inputs[1].default_value = 2.0
    for candidate in context.group.nodes:
        if candidate.bl_idname == 'NodeGroupInput':
            context.group.links.new(candidate.outputs['Radius'], node.inputs[1])''',
        )
    elif backend_mode == 'inject_input':
        source = source.replace(
            'node.inputs[1].default_value = 2.0',
            '''node.inputs[1].default_value = 2.0
    context.group.nodes.new('NodeGroupInput')''',
        )
    elif backend_mode == 'owned_resources':
        source = source.replace(
            'context.new_generated_mesh(role="dynamic-rollback", name_hint="Probe")',
            '''mesh = context.new_generated_mesh(role="owned-mesh", name_hint="Probe")
    obj = context.new_generated_object(mesh, role="owned-object", name_hint="Probe")
    info = context.group.nodes.new('GeometryNodeObjectInfo')
    info.inputs['Object'].default_value = obj''',
        )
    backend.write_text(source)
    packages.install_package_directory(root)


def _child(root):
    """Return the one extension group referenced by a generated root node."""
    groups = [node.node_tree for node in root.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(groups) == 1
    return groups[0]


def test_backend_cannot_see_ambient_manual_root_input_without_raw_request():
    """Root with and without a manual Input produces identical backend access."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp, backend_mode='ambient')
        for existing in (False, True):
            root = bpy.data.node_groups.new('NFExt_ambient_input_' + str(existing), 'GeometryNodeTree')
            root.interface.new_socket(name='Radius', in_out='INPUT', socket_type='NodeSocketFloat')
            manual = root.nodes.new('NodeGroupInput') if existing else None
            prior = manual.as_pointer() if manual else None
            compiler.compile_source_in_tree('from packages import dynamic\na, b = foo(1.0)', root)
            assert not any(link.from_node.bl_idname == 'NodeGroupInput' for link in root.links)
            assert sum(node.bl_idname == 'NodeGroupInput' for node in root.nodes) == int(existing)
            if manual:
                assert next(node for node in root.nodes if node.bl_idname == 'NodeGroupInput').as_pointer() == prior


def test_backend_sees_input_only_after_explicit_raw_dsl_call():
    """An explicit NodeGroupInput read grants candidate-local structural access."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp, backend_mode='ambient')
        root = bpy.data.node_groups.new('NFExt_explicit_input', 'GeometryNodeTree')
        root.interface.new_socket(name='Radius', in_out='INPUT', socket_type='NodeSocketFloat')
        manual = root.nodes.new('NodeGroupInput')
        before = manual.as_pointer()
        compiler.compile_source_in_tree('''from packages import dynamic
radius = node('NodeGroupInput', output='Radius', typ=Float)
a, b = foo(radius)
''', root)
        assert next(node for node in root.nodes if node.bl_idname == 'NodeGroupInput').as_pointer() == before
        assert any(link.from_node.bl_idname == 'NodeGroupInput'
                   and link.to_node.bl_idname == 'ShaderNodeMath' for link in root.links)


def test_backend_inserted_group_input_is_not_adopted():
    """A backend-created structural input cannot impersonate an authorized DSL read."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp, backend_mode='inject_input')
        root = bpy.data.node_groups.new('NFExt_injected_input', 'GeometryNodeTree')
        root.interface.new_socket(name='Radius', in_out='INPUT', socket_type='NodeSocketFloat')
        manual = root.nodes.new('NodeGroupInput')
        before = manual.as_pointer()
        with pytest.raises(CompileError, match='unauthorized Group Input'):
            compiler.compile_source_in_tree('from packages import dynamic\na, b = foo(1.0)', root)
        assert len(root.nodes) == 1 and root.nodes[0].as_pointer() == before


def test_removed_extension_group_retires_with_connected_owned_resources():
    """An obsolete committed child and its manifest-owned Object/Mesh are retired."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp, backend_mode='owned_resources')
        root = bpy.data.node_groups.new('NFExt_retire_child', 'GeometryNodeTree')
        compiler.compile_source_in_tree('from packages import dynamic\na, b = foo(1.0, __inline__=False)', root)
        group = _child(root)
        authority = group[EXTENSION_AUTHORITY_PROP]
        info = next(node for node in group.nodes if node.bl_idname == 'GeometryNodeObjectInfo')
        obj = info.inputs['Object'].default_value
        mesh = obj.data
        obj_name, mesh_name = obj.name, mesh.name
        compiler.compile_source_in_tree('a = cube(1)', root)
        assert not any(g.get(EXTENSION_AUTHORITY_PROP) == authority for g in bpy.data.node_groups)
        assert obj_name not in bpy.data.objects
        assert mesh_name not in bpy.data.meshes


def test_manually_referenced_extension_group_and_resources_remain_live():
    """A manual Group Node in a separate root prevents child retirement."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp, backend_mode='owned_resources')
        root = bpy.data.node_groups.new('NFExt_preserve_child', 'GeometryNodeTree')
        compiler.compile_source_in_tree('from packages import dynamic\na, b = foo(1.0, __inline__=False)', root)
        child = _child(root)
        obj = next(node for node in child.nodes if node.bl_idname == 'GeometryNodeObjectInfo').inputs['Object'].default_value
        obj_name, mesh_name = obj.name, obj.data.name
        manual_root = bpy.data.node_groups.new('NFExt_manual_owner', 'GeometryNodeTree')
        manual = manual_root.nodes.new('GeometryNodeGroup')
        manual.node_tree = child
        compiler.compile_source_in_tree('a = cube(1)', root)
        assert any(g is child for g in bpy.data.node_groups)
        assert manual.node_tree is child
        assert obj_name in bpy.data.objects and mesh_name in bpy.data.meshes


def test_copied_group_authority_is_not_deletion_proof():
    """A duplicate copied authority must preserve ambiguous group ownership."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp)
        root = bpy.data.node_groups.new('NFExt_duplicate_authority', 'GeometryNodeTree')
        compiler.compile_source_in_tree('from packages import dynamic\na, b = foo(1.0, __inline__=False)', root)
        child = _child(root)
        copied = child.copy()
        compiler.compile_source_in_tree('a = cube(1)', root)
        assert any(g is child for g in bpy.data.node_groups)
        assert any(g is copied for g in bpy.data.node_groups)


def test_reopened_ownership_metadata_is_not_deletion_proof():
    """Metadata alone after loss of the session registry must not retire a group."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp)
        root = bpy.data.node_groups.new('NFExt_unverified_authority', 'GeometryNodeTree')
        compiler.compile_source_in_tree('from packages import dynamic\na, b = foo(1.0, __inline__=False)', root)
        child = _child(root)
        registry = bpy.app.driver_namespace[SESSION_PUBLISHED_GROUPS_KEY]
        registry.pop(child.as_pointer(), None)
        compiler.compile_source_in_tree('a = cube(1)', root)
        assert any(g is child for g in bpy.data.node_groups)


def test_source_group_rebuild_retires_removed_extension_occurrence():
    """Updating an authoritative source group also retires unused child groups."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp, backend_mode='owned_resources')
        source = '''from packages import dynamic

def nf_fixture(v: Float):
    a, b = foo(v, __inline__=False)
    return a
'''
        group = compiler.create_expression_group(source, 'NFExt_source_child_retirement')
        child = _child(group)
        authority = child[EXTENSION_AUTHORITY_PROP]
        obj = next(node for node in child.nodes if node.bl_idname == 'GeometryNodeObjectInfo').inputs['Object'].default_value
        obj_name, mesh_name = obj.name, obj.data.name
        compiler.update_expression_group(group, '''from packages import dynamic

def nf_fixture(v: Float):
    return v
''')
        assert not any(g.get(EXTENSION_AUTHORITY_PROP) == authority for g in bpy.data.node_groups)
        assert obj_name not in bpy.data.objects
        assert mesh_name not in bpy.data.meshes


def test_failed_replacement_does_not_retire_committed_child():
    """A failed root compile retains the prior group and its generated resources."""
    with tempfile.TemporaryDirectory() as tmp:
        _install_fixture(tmp, backend_mode='owned_resources')
        root = bpy.data.node_groups.new('NFExt_failure_preserves_child', 'GeometryNodeTree')
        source = 'from packages import dynamic\na,b=foo(1.0,__inline__=False)'
        compiler.compile_source_in_tree(source, root)
        original = _child(root)
        original_ptr = original.as_pointer()
        obj = next(node for node in original.nodes if node.bl_idname == 'GeometryNodeObjectInfo').inputs['Object'].default_value
        obj_name, mesh_name = obj.name, obj.data.name
        with pytest.raises(CompileError):
            compiler.compile_source_in_tree('a = cube(1)\nb = missing_function(2)', root)
        assert _child(root).as_pointer() == original_ptr
        assert obj_name in bpy.data.objects
        assert mesh_name in bpy.data.meshes
