"""Selected package and Examples reload retains the complete shared/unique owner."""

from helpers import *
from NodeForge.blender import library_groups
from NodeForge.blender.group_authority import detach_selected_source_owner
from NodeForge.catalog import LibraryEntryRecord, _path_key
from NodeForge.compiler_identities import library_function_id_for_record
from NodeForge.function_instances import (
    FUNCTION_DEFINITION_OWNER_PROP, FUNCTION_INSTANCE_KEY_PROP,
    direct_library_owner_scope_for_id, stamp_function_metadata,
)


def _catalog_group(record, key):
    """Build a source-owned group using the same backend as ordinary catalog Add."""
    backend = compiler._new_group_backend()
    function_id = library_function_id_for_record(record)
    session = backend.new_source_callable_session()
    prepared = backend.prepare_source_compilation(
        record.source_path.read_text(encoding='utf-8'),
        compilation_identity=library_groups._direct_library_compilation_identity(
            function_id, instance_key=key),
        helper_namespace=record.name, source_callable_session=session,
    )

    def finalize(group):
        library_groups._write_package_metadata(group, record)
        stamp_function_metadata(group, instance_key=key,
                                definition_owner=function_id.stable_key())
    group = backend.create_or_update(BlenderGroupBuildRequest(
        prepared_compilation=prepared,
        name=f'NFTest_reload_{record.namespace}_{key or "shared"}',
        function_instance_key=key, source_callable_session=session,
    ), finalize_before_commit=finalize)
    group.use_fake_user = True
    return group


@pytest.mark.parametrize('namespace', ['functions', 'examples', 'local'])
def test_selected_unique_reload_preserves_owner_scope_and_unrelated_instances(tmp_path, namespace):
    """A selected unique reload is an in-place operation, never restamps SHARED."""
    file = tmp_path / f'{namespace}_reload.nf'
    file.write_text('value = 1\noutput(value)\n', encoding='utf-8')
    record = LibraryEntryRecord(namespace, 'unique_reload', 'script', file,
                                source_path=file, source_origin=_path_key(file),
                                package_id='vendor.reload' if namespace == 'functions' else None)
    shared = _catalog_group(record, '')
    first = _catalog_group(record, 'a' * 32)
    second = _catalog_group(record, 'b' * 32)
    try:
        pointer = first.as_pointer()
        expected = library_function_id_for_record(record).stable_key()
        assert first.get(FUNCTION_DEFINITION_OWNER_PROP) == expected
        assert library_groups._find_owned_library_entry_group(record, instance_key='') is shared
        assert library_groups._find_owned_library_entry_group(record, instance_key='a' * 32) is first
        result = library_groups.update_materialized_library_entry_group_for_record(
            record, first, compiler._new_group_backend())
        assert result.as_pointer() == pointer
        assert result.get(FUNCTION_INSTANCE_KEY_PROP) == 'a' * 32
        assert result.get(FUNCTION_DEFINITION_OWNER_PROP) == expected
        assert library_groups._direct_library_compilation_identity(
            library_function_id_for_record(record), instance_key='a' * 32
        ).owner_scope == direct_library_owner_scope_for_id(
            library_function_id_for_record(record), instance_key='a' * 32
        )
        assert library_groups._find_owned_library_entry_group(record, instance_key='') is shared
        assert library_groups._find_owned_library_entry_group(record, instance_key='b' * 32) is second
        file.write_text('value = 2\noutput(value)\n', encoding='utf-8')
        assert library_groups.update_materialized_library_entry_group_for_record(
            record, first, compiler._new_group_backend()).as_pointer() == pointer
        assert first.get(FUNCTION_INSTANCE_KEY_PROP) == 'a' * 32
        assert library_groups._find_owned_library_entry_group(record, instance_key='') is shared
        copied = first.copy()
        try:
            with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
                library_groups.update_materialized_library_entry_group_for_record(
                    record, first, compiler._new_group_backend())
            detach_selected_source_owner(copied)
            assert library_groups.update_materialized_library_entry_group_for_record(
                record, first, compiler._new_group_backend()).as_pointer() == pointer
            with pytest.raises(CompileError, match='no NodeForge library provenance'):
                library_groups.update_materialized_library_entry_group_for_record(
                    record, copied, compiler._new_group_backend())
        finally:
            bpy.data.node_groups.remove(copied)
    finally:
        for group in (shared, first, second):
            bpy.data.node_groups.remove(group)


def test_package_unique_reload_keeps_key_after_blend_reopen(tmp_path):
    """Blender serialization does not rewrite unique owner properties."""
    source_file = tmp_path / 'persistent.nf'
    source_file.write_text('output(value=1)\n', encoding='utf-8')
    record = LibraryEntryRecord('functions', 'reopen_unique', 'script', source_file,
                                source_path=source_file, package_id='vendor.reopen')
    shared = _catalog_group(record, '')
    unique = _catalog_group(record, 'c' * 32)
    unique_name = unique.name
    shared_name = shared.name
    filename = str(tmp_path / 'unique-reload.blend')
    bpy.ops.wm.save_as_mainfile(filepath=filename)
    bpy.ops.wm.open_mainfile(filepath=filename)
    reopened = bpy.data.node_groups[unique_name]
    pointer = reopened.as_pointer()
    assert library_groups.update_materialized_library_entry_group_for_record(
        record, reopened, compiler._new_group_backend()).as_pointer() == pointer
    assert reopened.get(FUNCTION_INSTANCE_KEY_PROP) == 'c' * 32
    assert library_groups._find_owned_library_entry_group(record, instance_key='') is bpy.data.node_groups[shared_name]
