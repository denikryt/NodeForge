"""Selected MODULE_DEF source Reload preserves canonical owners and physical datablocks."""

from helpers import *

from NodeForge.blender.group_authority import detach_selected_source_owner
from NodeForge.compiler_identities import parse_module_definition_function_id
from NodeForge.function_instances import (
    FUNCTION_DEFINITION_OWNER_PROP, FUNCTION_INSTANCE_KEY_PROP, module_definition_owner_scope,
)


_CONTEXT = 'selected-module-reload-context'
_SOURCE = '''def inc(x: Float) -> Float:
    return x + 1

a = inc(1)
b = inc(2, __unique__=True)
c = inc(3, __unique__=True)
'''


def _materialized(source=_SOURCE):
    """Return one shared and two unique instances in one existing target tree."""
    target = bpy.data.node_groups.new('NFTest-selected-module-root', 'GeometryNodeTree')
    compiler.compose_source_initial(source, target, module_context=_CONTEXT)
    nodes = [node for node in target.nodes if node.bl_idname == 'GeometryNodeGroup']
    groups = [node.node_tree for node in nodes]
    assert len(groups) == 3 and len({group.as_pointer() for group in groups}) == 3
    return target, groups


def _identity(group):
    """Capture complete canonical owner fields for before/after verification."""
    fid = parse_module_definition_function_id(group['nodeforge_module_definition'])
    key = str(group.get(FUNCTION_INSTANCE_KEY_PROP) or '')
    assert group.get(FUNCTION_DEFINITION_OWNER_PROP) == fid.stable_key()
    return fid, key, module_definition_owner_scope(fid, instance_key=key), group.as_pointer()


def test_selected_module_unique_reload_preserves_all_instances():
    """Reload a unique instance without changing another unique or shared owner."""
    _target, (shared, first, second) = _materialized()
    original = [_identity(group) for group in (shared, first, second)]
    assert original[0][1] == ''
    assert len(original[1][1]) == len(original[2][1]) == 32
    assert original[1][1] != original[2][1]

    # Both the equivalent/no-op path and a changed definition retain identity.
    assert compiler.update_library_catalog_group(first, source=_SOURCE, module_context=_CONTEXT) is first
    assert _identity(first) == original[1]
    updated = _SOURCE.replace('x + 1', 'x + 4')
    assert compiler.update_library_catalog_group(first, source=updated, module_context=_CONTEXT) is first
    assert [_identity(group) for group in (shared, first, second)] == original


def test_selected_module_copy_collision_and_explicit_detach():
    """Copied metadata blocks Selected Reload before detachment and remains independent."""
    _target, (shared, first, second) = _materialized()
    copied = first.copy()
    before = [_identity(group) for group in (shared, first, second)]
    copied_pointer = copied.as_pointer()
    with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
        compiler.update_library_catalog_group(first, source=_SOURCE, module_context=_CONTEXT)
    assert [_identity(group) for group in (shared, first, second)] == before
    detach_selected_source_owner(copied)
    assert copied.as_pointer() == copied_pointer
    assert copied.get(FUNCTION_DEFINITION_OWNER_PROP) is None
    assert compiler.update_library_catalog_group(first, source=_SOURCE, module_context=_CONTEXT) is first
    with pytest.raises(CompileError, match='metadata is incomplete|detached'):
        compiler.update_library_catalog_group(copied, source=_SOURCE, module_context=_CONTEXT)
    assert [_identity(group) for group in (shared, first, second)] == before


def test_selected_module_reload_fails_when_source_disappears_or_changes_signature():
    """Missing declaration, invalid context and type change leave selected group intact."""
    _target, (_shared, first, _second) = _materialized()
    before = _identity(first)
    with pytest.raises(CompileError, match='no longer declares'):
        compiler.update_library_catalog_group(first, source='def other(x: Float) -> Float:\n    return x',
                                              module_context=_CONTEXT)
    with pytest.raises(CompileError, match='original module context'):
        compiler.update_library_catalog_group(first, source=_SOURCE, module_context='wrong')
    with pytest.raises(CompileError, match='incompatible|conflicts'):
        compiler.update_library_catalog_group(first, source=_SOURCE.replace('x: Float', 'x: Int'),
                                              module_context=_CONTEXT)
    assert _identity(first) == before


def test_module_selected_reload_persists_identity_after_save_reopen(tmp_path):
    """A saved unique owner remains selected-reloadable with explicit source context."""
    _target, (shared, first, second) = _materialized()
    identities = [_identity(group) for group in (shared, first, second)]
    names = [group.name for group in (shared, first, second)]
    filename = str(tmp_path / 'selected-module-reload.blend')
    bpy.ops.wm.save_as_mainfile(filepath=filename)
    bpy.ops.wm.open_mainfile(filepath=filename)
    groups = [bpy.data.node_groups[name] for name in names]
    assert [group.get(FUNCTION_INSTANCE_KEY_PROP) for group in groups] == [item[1] for item in identities]
    target = groups[1]
    pointer = target.as_pointer()
    assert compiler.update_library_catalog_group(target, source=_SOURCE, module_context=_CONTEXT) is target
    assert target.as_pointer() == pointer
    assert target.get(FUNCTION_DEFINITION_OWNER_PROP) == identities[1][0].stable_key()


def test_selected_module_reload_rolls_back_before_backend_commit():
    """A cutover failure restores the existing body and canonical metadata."""
    from NodeForge.blender import group_backend
    _target, (_shared, first, _second) = _materialized()
    previous = _identity(first)
    nodes_before = [(node.bl_idname, node.name) for node in first.nodes]
    source_before = str(first['nodeforge_library_source'])
    try:
        group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = True
        with pytest.raises(Exception, match='[Ff]ault|[Ii]njected|[Cc]utover|[Ff]ail'):
            compiler.update_library_catalog_group(
                first, source=_SOURCE.replace('x + 1', 'x + 15'), module_context=_CONTEXT,
            )
    finally:
        group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = False
    assert _identity(first) == previous
    assert [(node.bl_idname, node.name) for node in first.nodes] == nodes_before
    assert str(first['nodeforge_library_source']) == source_before


def test_selected_module_reload_resolves_exact_local_source(tmp_path, monkeypatch):
    """Standalone Local Add and selected Reload share canonical file identity."""
    from NodeForge import local_sources
    from NodeForge.catalog import _path_key
    folder = tmp_path / 'local'
    folder.mkdir()
    path = folder / 'tools.nf'
    original = 'def alpha(x: Float) -> Float:\n    return x + 1\n'
    path.write_text(original, encoding='utf-8')
    monkeypatch.setattr(local_sources, '_catalog_input_paths', lambda: ((folder,), ()))
    group, modifier = compiler.create_selected_library_group_auto('local', _path_key(path))
    assert not modifier
    pointer = group.as_pointer()
    assert compiler.update_library_catalog_group(group) is group
    path.write_text(original.replace('+ 1', '+ 8'), encoding='utf-8')
    assert compiler.update_library_catalog_group(group) is group
    assert group.as_pointer() == pointer
    assert 'x + 8' in str(group.get('nodeforge_library_source') or '')
    path.unlink()
    with pytest.raises(CompileError, match='unavailable|no longer'):
        compiler.update_library_catalog_group(group)
    assert group.as_pointer() == pointer


def test_reload_does_not_recover_missing_annotation_from_saved_identity():
    """Selected Reload cannot use its serialized parameter types as source defaults."""
    _target, (_shared, selected, _second) = _materialized()
    before = _identity(selected)
    old_source = str(selected['nodeforge_library_source'])
    with pytest.raises(CompileError, match='requires an explicit type annotation'):
        compiler.update_library_catalog_group(
            selected, source=_SOURCE.replace('x: Float', 'x'), module_context=_CONTEXT,
        )
    assert _identity(selected) == before
    assert str(selected['nodeforge_library_source']) == old_source
