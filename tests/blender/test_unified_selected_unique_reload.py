"""Selected source Reload keeps shared/unique identity for real .nf module owners."""

import json

import bpy
import pytest

from helpers import check
from NodeForge import catalog, compiler, local_sources, packages
from NodeForge.blender.group_authority import detach_selected_source_owner
from NodeForge.compiler_identities import parse_module_definition_function_id
from NodeForge.function_instances import (
    FUNCTION_DEFINITION_OWNER_PROP, FUNCTION_INSTANCE_KEY_PROP,
    module_definition_owner_scope,
)
from NodeForge.errors import CompileError


SOURCE = 'def grow(x: Float) -> Float:\n    return x + 1\n'


def _source_owner(namespace, tmp_path, monkeypatch):
    """Expose one canonical def from a real Local, Examples or installed Package module."""
    if namespace == 'functions':
        packages.set_packages_dir_for_tests(tmp_path / 'installed')
        root = tmp_path / 'source_package'
        functions = root / 'functions'
        functions.mkdir(parents=True)
        (functions / 'growth.nf').write_text(SOURCE, encoding='utf-8')
        (root / 'nodeforge_package.json').write_text(json.dumps({
            'schema_version': 1, 'id': 'vendor.reload', 'import_name': 'reloadpkg',
            'name': 'Reload fixture', 'version': '1.0.0', 'author': 'Test',
            'description': 'Unique reload regression',
            'nodeforge_min_version': '0.65.0', 'nodeforge_max_version': None,
            'contents': {'functions': 'functions'}, 'permissions': {'python': False},
        }), encoding='utf-8')
        packages.install_package_directory(root)
        from NodeForge.environment_resolution import resolve_environment
        record = resolve_environment().package_by_id('vendor.reload').find('grow').record
        return record.source_path, 'from packages import reloadpkg\n', 'reloadpkg.grow'
    source_root = tmp_path / namespace
    source_root.mkdir()
    source_file = source_root / 'growth.nf'
    source_file.write_text(SOURCE, encoding='utf-8')
    if namespace == 'local':
        monkeypatch.setattr(local_sources, '_catalog_input_paths', lambda: ((source_root,), ()))
        return source_file, 'from local import growth\n', 'growth.grow'
    original_catalog_dir = catalog.catalog_dir
    monkeypatch.setattr(
        catalog, 'catalog_dir',
        lambda ns: source_root if ns == 'examples' else original_catalog_dir(ns),
    )
    return source_file, 'from examples import growth\n', 'growth.grow'


def _materialize(prefix, callee):
    """Make three level-0 calls with one shared and two distinct unique owners."""
    target = bpy.data.node_groups.new('NFTest_unique_reload_base', 'GeometryNodeTree')
    compiler.compose_source_initial(
        prefix + f'a = {callee}(1.0)\nb = {callee}(2.0, __unique__=True)\n'
                 f'c = {callee}(3.0, __unique__=True)\n',
        target, module_context='unique-reload-fixture',
    )
    groups = [node.node_tree for node in target.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(groups) == 3 and len({group.as_pointer() for group in groups}) == 3
    return target, groups


def _identity(group):
    """Return the complete canonical owner signature, including physical pointer."""
    function_id = parse_module_definition_function_id(group['nodeforge_module_definition'])
    instance_key = str(group.get(FUNCTION_INSTANCE_KEY_PROP) or '')
    assert group.get(FUNCTION_DEFINITION_OWNER_PROP) == function_id.stable_key()
    return function_id, instance_key, module_definition_owner_scope(
        function_id, instance_key=instance_key,
    ), group.as_pointer()


@pytest.mark.parametrize('namespace', ['functions', 'examples', 'local'])
def test_selected_unique_reload_preserves_owner_scope_and_unrelated_instances(
    namespace, tmp_path, monkeypatch,
):
    """Live source Reload preserves selected unique identity and other group instances."""
    path, prefix, callee = _source_owner(namespace, tmp_path, monkeypatch)
    try:
        _, (shared, first, second) = _materialize(prefix, callee)
        expected = [_identity(group) for group in (shared, first, second)]
        assert expected[0][1] == ''
        assert len(expected[1][1]) == len(expected[2][1]) == 32
        assert expected[1][1] != expected[2][1]
        assert compiler.update_library_catalog_group(first) is first
        assert [_identity(group) for group in (shared, first, second)] == expected
        path.write_text(SOURCE.replace('x + 1', 'x + 2'), encoding='utf-8')
        assert compiler.update_library_catalog_group(first) is first
        assert [_identity(group) for group in (shared, first, second)] == expected
        assert 'x + 2' in first['nodeforge_library_source']
        copied = first.copy()
        with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
            compiler.update_library_catalog_group(first)
        assert [_identity(group) for group in (shared, first, second)] == expected
        detach_selected_source_owner(copied)
        assert compiler.update_library_catalog_group(first) is first
        with pytest.raises(CompileError, match='incomplete|detached'):
            compiler.update_library_catalog_group(copied)
        assert [_identity(group) for group in (shared, first, second)] == expected
    finally:
        if namespace == 'functions':
            packages.set_packages_dir_for_tests(None)


def test_package_unique_reload_keeps_key_after_blend_reopen(tmp_path, monkeypatch):
    """A unique package MODULE_DEF is still reloadable after native .blend persistence."""
    _path, prefix, callee = _source_owner('functions', tmp_path, monkeypatch)
    try:
        _, (shared, first, second) = _materialize(prefix, callee)
        names = (shared.name, first.name, second.name)
        owners = [_identity(group)[:3] for group in (shared, first, second)]
        bpy.ops.wm.save_as_mainfile(filepath=str(tmp_path / 'reopen.blend'))
        bpy.ops.wm.open_mainfile(filepath=str(tmp_path / 'reopen.blend'))
        groups = [bpy.data.node_groups[name] for name in names]
        assert [_identity(group)[:3] for group in groups] == owners
        selected = groups[1]
        pointer = selected.as_pointer()
        assert compiler.update_library_catalog_group(selected) is selected
        assert selected.as_pointer() == pointer
        assert [_identity(group)[:3] for group in groups] == owners
    finally:
        packages.set_packages_dir_for_tests(None)


def test_rotation_shared_and_unique_package_reload_keeps_overrides(tmp_path):
    """Selected Rotation Reload preserves edited instance values and sibling owners."""
    from NodeForge.blender import update as blender_update
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    root = tmp_path / 'rotation_reload'
    functions = root / 'functions'
    functions.mkdir(parents=True)
    fn = functions / 'rotation.nf'
    before = 'def rotate(angle: Rotation = (0.0, 0.0, 0.0)) -> Rotation:\n    return angle\n'
    after = 'def rotate(angle: Rotation = (0.25, 0.0, 0.0)) -> Rotation:\n    return angle\n'
    fn.write_text(before, encoding='utf-8')
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.rotation.reload', 'import_name': 'rot_reload',
        'name': 'Rotation Reload', 'version': '1.0.0', 'author': 'Tests',
        'nodeforge_min_version': '0.65.0', 'contents': {'functions': 'functions'},
    }), encoding='utf-8')
    installed = packages.install_package_directory(root)
    target = bpy.data.node_groups.new('NFTest_RotationSharedUnique', 'GeometryNodeTree')
    compiler.compose_source_initial('''from packages import rot_reload

a = rot_reload.rotate()
b = rot_reload.rotate(__unique__=True)
''', target)
    nodes = [node for node in target.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(nodes) == 2
    shared, unique = nodes
    assert shared.node_tree != unique.node_tree
    shared_key = _identity(shared.node_tree)[:3]
    unique_key = _identity(unique.node_tree)[:3]
    blender_update._apply_group_defaults_to_node(unique)
    unique.inputs[0].default_value = (1e-7, 0.0, 0.0)
    (installed.root / 'functions' / 'rotation.nf').write_text(after, encoding='utf-8')
    assert compiler.update_library_catalog_group(unique.node_tree) is unique.node_tree
    assert _identity(shared.node_tree)[:3] == shared_key
    assert _identity(unique.node_tree)[:3] == unique_key
    assert tuple(unique.inputs[0].default_value) == (normalize_float_constant(1e-7), 0.0, 0.0)
    assert all(link.is_valid for link in unique.node_tree.links)
    assert all(link.is_valid for link in shared.node_tree.links)
