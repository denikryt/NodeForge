"""Canonical function ownership must not accept duplicated Blender ID properties."""

from helpers import *
from NodeForge.blender import library_groups
from NodeForge.catalog import LibraryEntryRecord, _path_key
from NodeForge.compiler_identities import library_function_id_for_record
from NodeForge.function_instances import stamp_function_metadata


def test_automatic_module_owner_lookup_rejects_copied_group_without_mutation():
    original = '''def authority_module_leaf(x: Float) -> Float:
    return x + 1

v = authority_module_leaf(3)
output(v)
'''
    root = compile_group(original, 'NFTest_module_authority')
    owned = next(g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == 'authority_module_leaf')
    copy = owned.copy()
    root_pointer = root.as_pointer()
    owned_pointer = owned.as_pointer()
    original_nodes = tuple((node.bl_idname, node.name) for node in owned.nodes)
    try:
        with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
            compiler.update_expression_group(root, original.replace('x + 1', 'x + 2'))
        assert root.as_pointer() == root_pointer
        assert owned.as_pointer() == owned_pointer
        assert tuple((n.bl_idname, n.name) for n in owned.nodes) == original_nodes
        assert tuple((n.bl_idname, n.name) for n in copy.nodes) == original_nodes
    finally:
        bpy.data.node_groups.remove(copy)


def test_selected_and_automatic_library_owner_checks_share_identity(tmp_path):
    path = tmp_path / 'authority.nf'
    path.write_text('def f(x: Float):\n    return x\n', encoding='utf-8')
    origin = _path_key(path)
    record = LibraryEntryRecord('local', 'authority', 'script', path,
                                source_path=path, source_origin=origin)
    group = bpy.data.node_groups.new('Authority Local', 'GeometryNodeTree')
    library_groups._write_package_metadata(group, record)
    stamp_function_metadata(group, instance_key='',
                            definition_owner=library_function_id_for_record(record).stable_key())
    copy = group.copy()
    try:
        with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
            library_groups._find_owned_library_entry_group(record)
        with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
            library_groups._validate_selected_library_owner(group, record, instance_key='')
        with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
            library_groups._validate_selected_library_owner(copy, record, instance_key='')
    finally:
        bpy.data.node_groups.remove(copy)
        bpy.data.node_groups.remove(group)


def test_different_unique_instance_keys_are_not_authority_collisions(tmp_path):
    path = tmp_path / 'instances.nf'
    path.write_text('def f(x: Float):\n    return x\n', encoding='utf-8')
    record = LibraryEntryRecord('local', 'instances', 'script', path,
                                source_path=path, source_origin=_path_key(path))
    candidates = []
    try:
        for key in ('a' * 32, 'b' * 32):
            group = bpy.data.node_groups.new('Instance', 'GeometryNodeTree')
            library_groups._write_package_metadata(group, record)
            stamp_function_metadata(group, instance_key=key,
                                    definition_owner=library_function_id_for_record(record).stable_key())
            candidates.append(group)
        for key, group in zip(('a' * 32, 'b' * 32), candidates):
            assert library_groups._find_owned_library_entry_group(record, instance_key=key) is group
            assert library_groups._validate_selected_library_owner(group, record, instance_key=key) is group
        assert library_groups._find_owned_library_entry_group(record, instance_key='') is None
    finally:
        for group in candidates:
            bpy.data.node_groups.remove(group)


def test_duplicate_owner_id_properties_remain_ambiguous_after_save_reopen(tmp_path):
    source = '''def persistent_authority_leaf(x: Float):
    return x + 2

v = persistent_authority_leaf(1)
output(v)
'''
    root = compile_group(source, 'NFTest_authority_reopen')
    owned = next(g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == 'persistent_authority_leaf')
    copy = owned.copy()
    root.use_fake_user = True
    owned.use_fake_user = True
    copy.use_fake_user = True
    file_path = str(tmp_path / 'authority-copy.blend')
    bpy.ops.wm.save_as_mainfile(filepath=file_path)
    bpy.ops.wm.open_mainfile(filepath=file_path)
    reopened_root = bpy.data.node_groups['NFTest_authority_reopen']
    groups = [g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == 'persistent_authority_leaf']
    assert len(groups) == 2
    before = tuple((g.name, len(g.nodes)) for g in groups)
    with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
        compiler.update_expression_group(reopened_root, source)
    assert tuple((g.name, len(g.nodes)) for g in groups) == before
