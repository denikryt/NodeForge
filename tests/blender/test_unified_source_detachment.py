"""Explicit single-user function-group detachment preserves user graph/resources."""

from helpers import *
from NodeForge.compiler_identities import parse_module_definition_function_id
from NodeForge.blender.group_authority import detach_selected_source_owner, TRANSACTION_PRIVATE_PROP
from NodeForge.blender import library_groups
from NodeForge.catalog import LibraryEntryRecord, _path_key
from NodeForge.compiler_identities import library_function_id_for_record
from NodeForge.function_instances import FUNCTION_DEFINITION_OWNER_PROP, stamp_function_metadata


def _group(name):
    return next(g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == name
                and g.get(FUNCTION_DEFINITION_OWNER_PROP))


def test_module_single_user_detachment_restores_canonical_updates():
    initial = '''def detachable_function(x: Float):
    return x + 1

v = detachable_function(1)
v = v
'''
    root = bpy.data.node_groups.new('NFTest_module_detachment', 'GeometryNodeTree')
    compiler.compose_source_initial(initial, root)
    original = _group('detachable_function')
    from NodeForge.compiler_identities import parse_module_definition_function_id
    module_context = parse_module_definition_function_id(original['nodeforge_module_definition']).module.origin
    copy = original.copy()
    copy["nodeforge_resource_provenance_probe"] = "retain"
    before_nodes = tuple((node.bl_idname, node.name) for node in copy.nodes)
    try:
        with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
            compiler.update_library_catalog_group(original, source=initial.replace('x + 1', 'x + 2'), module_context=module_context)
        detach_selected_source_owner(copy)
        assert copy.get(FUNCTION_DEFINITION_OWNER_PROP) is None
        assert copy.get('nodeforge_resource_provenance_probe') == 'retain'
        assert copy.get('nodeforge_module_name') == 'detachable_function'
        assert copy.get(TRANSACTION_PRIVATE_PROP) is None
        pointer = original.as_pointer()
        compiler.update_library_catalog_group(original, source=initial.replace('x + 1', 'x + 2'), module_context=module_context)
        assert original.as_pointer() == pointer
        assert copy.as_pointer() != pointer
        assert tuple((node.bl_idname, node.name) for node in copy.nodes) == before_nodes
    finally:
        bpy.data.node_groups.remove(copy)


def test_library_selected_copy_detachment_excludes_it_from_both_lookup_paths(tmp_path):
    path = tmp_path / 'detachable.nf'
    path.write_text('def f(x: Float):\n    return x\n', encoding='utf-8')
    record = LibraryEntryRecord('local', 'detachable', 'script', path,
                                source_path=path, source_origin=_path_key(path))
    owned = bpy.data.node_groups.new('Owned Local', 'GeometryNodeTree')
    library_groups._write_package_metadata(owned, record)
    stamp_function_metadata(owned, instance_key='',
                            definition_owner=library_function_id_for_record(record).stable_key())
    copy = owned.copy()
    try:
        with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
            library_groups._validate_selected_library_owner(owned, record, instance_key='')
        detach_selected_source_owner(copy)
        assert copy.get('nodeforge_library_namespace') is None
        assert copy.get('nodeforge_library_name') == 'detachable'
        assert library_groups._find_owned_library_entry_group(record) is owned
        assert library_groups._validate_selected_library_owner(owned, record, instance_key='') is owned
        with pytest.raises(CompileError, match='not the sole canonical source owner'):
            library_groups._validate_selected_library_owner(copy, record, instance_key='')
    finally:
        bpy.data.node_groups.remove(copy)
        bpy.data.node_groups.remove(owned)


def test_detachment_requires_a_selected_source_owned_copy():
    plain = bpy.data.node_groups.new('Plain independent user group', 'GeometryNodeTree')
    try:
        with pytest.raises(CompileError, match='no canonical NodeForge source ownership'):
            detach_selected_source_owner(plain)
    finally:
        bpy.data.node_groups.remove(plain)
    original = bpy.data.node_groups.new('NFTest_cannot_detach_canonical_without_copy', 'GeometryNodeTree')
    compiler.compose_source_initial('''def sole_owned_leaf(x: Float):
    return x

v = sole_owned_leaf(1)
v = v
''', original)
    owned = _group('sole_owned_leaf')
    with pytest.raises(CompileError, match='requires a selected single-user copy'):
        detach_selected_source_owner(owned)


def test_source_detachment_is_persistent_after_save_and_reopen(tmp_path):
    original_source = '''def persistent_detachment(x: Float):
    return x + 1

v = persistent_detachment(1)
v = v
'''
    root = bpy.data.node_groups.new('NFTest_persistent_detachment', 'GeometryNodeTree')
    compiler.compose_source_initial(original_source, root)
    owned = _group('persistent_detachment')
    copy = owned.copy()
    detached_name = copy.name
    detach_selected_source_owner(copy)
    root.use_fake_user = True
    owned.use_fake_user = True
    copy.use_fake_user = True
    filename = str(tmp_path / 'detached-copy.blend')
    bpy.ops.wm.save_as_mainfile(filepath=filename)
    bpy.ops.wm.open_mainfile(filepath=filename)
    current_copy = bpy.data.node_groups[detached_name]
    original = _group('persistent_detachment')
    assert current_copy.get(FUNCTION_DEFINITION_OWNER_PROP) is None
    assert original.get(FUNCTION_DEFINITION_OWNER_PROP)
    pointer = original.as_pointer()
    compiler.update_library_catalog_group(original, source=original_source.replace('x + 1', 'x + 3'), module_context=parse_module_definition_function_id(original['nodeforge_module_definition']).module.origin)
    assert original.as_pointer() == pointer
    assert current_copy.get(FUNCTION_DEFINITION_OWNER_PROP) is None


def test_legacy_local_helper_copy_cannot_rejoin_shared_lookup_after_detachment():
    from NodeForge.blender.local_functions import _find_local_helper
    from NodeForge.function_instances import FUNCTION_INSTANCE_KEY_PROP
    owner = bpy.data.node_groups.new('Local helper original', 'GeometryNodeTree')
    owner['nodeforge_generated_kind'] = 'local_function_helper'
    owner['nodeforge_local_function_namespace'] = 'scope'
    owner['nodeforge_local_function_name'] = 'my_func'
    owner['nodeforge_local_function_signature'] = 'Float'
    owner[FUNCTION_DEFINITION_OWNER_PROP] = 'stable-local-owner'
    owner[FUNCTION_INSTANCE_KEY_PROP] = ''
    copy = owner.copy()
    try:
        with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
            _find_local_helper(namespace='scope', function_name='my_func', signature='Float',
                               definition_owner='stable-local-owner', instance_key='')
        detach_selected_source_owner(copy)
        assert owner.get('nodeforge_local_function_namespace') == 'scope'
        assert copy.get('nodeforge_local_function_namespace') is None
        assert copy.get('nodeforge_generated_kind') == 'local_function_helper'
        assert _find_local_helper(namespace='scope', function_name='my_func', signature='Float',
                                  definition_owner='stable-local-owner', instance_key='') is owner
    finally:
        bpy.data.node_groups.remove(copy)
        bpy.data.node_groups.remove(owner)
