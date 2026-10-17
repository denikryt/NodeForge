"""Exercise the actual Blender UI operators for Local Python and Mesh Compute."""

from __future__ import annotations

import bpy
import pytest

from NodeForge import local_sources, ui
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import (
    SOURCE, configured_local, mesh_object, _evaluated_positions,
)

pytestmark = pytest.mark.blender


@pytest.fixture
def registered_ui():
    """Provide operator and Scene property registration without double-registering."""
    owned = not hasattr(bpy.types.Scene, "gn_script_mvp")
    if owned:
        ui.register()
    yield
    if owned:
        ui.unregister()


def _select(owner):
    """Set a deterministic active Object in headless Blender."""
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    owner.select_set(True)
    bpy.context.view_layer.objects.active = owner


def test_compile_to_modifier_operator_evaluates_real_gn(registered_ui, configured_local, mesh_object):
    """UI compiles an explicitly trusted script and produces updated GN geometry."""
    text = bpy.data.texts.new("MeshComputeUI_positive.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        assert bpy.ops.nodeforge.compile_mesh_compute_modifier() == {'FINISHED'}
        assert len(mesh_object.modifiers) == 1
        binding = runtime.get_modifier_binding(mesh_object)
        assert binding.status == "READY"
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        bpy.data.texts.remove(text)


def test_compile_to_modifier_operator_denies_untrusted_source(registered_ui, configured_local, mesh_object):
    """No group, scene or modifier can be published when Local Python is denied."""
    text = bpy.data.texts.new("MeshComputeUI_blocked.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        local_sources.set_local_python_permission(configured_local, allowed=False)
        before_groups = {x.as_pointer() for x in bpy.data.node_groups}
        before_scenes = {x.as_pointer() for x in bpy.data.scenes}
        with pytest.raises(RuntimeError, match='not allowed'):
            bpy.ops.nodeforge.compile_mesh_compute_modifier()
        assert not mesh_object.modifiers
        assert {x.as_pointer() for x in bpy.data.node_groups} == before_groups
        assert {x.as_pointer() for x in bpy.data.scenes} == before_scenes
    finally:
        bpy.data.texts.remove(text)


def test_local_permission_operator_grants_only_registered_root(registered_ui, configured_local, tmp_path):
    """UI toggles the durable Local grant and rejects an unregistered directory."""
    source = configured_local / 'shift.py'
    assert local_sources.local_python_permission(source)
    assert bpy.ops.nodeforge.set_local_python_permission(path=str(configured_local), allowed=False) == {'FINISHED'}
    assert not local_sources.local_python_permission(source)
    assert bpy.ops.nodeforge.set_local_python_permission(path=str(configured_local), allowed=True) == {'FINISHED'}
    assert local_sources.local_python_permission(source)
    foreign = tmp_path / 'unregistered'
    foreign.mkdir()
    with pytest.raises(RuntimeError, match='registered root'):
        bpy.ops.nodeforge.set_local_python_permission(path=str(foreign), allowed=True)
    assert local_sources.local_python_permission(source)


def test_mesh_compute_ui_poll_rejects_nonmesh_or_existing_modifier(registered_ui, configured_local, mesh_object):
    """UI never installs runtime onto a user-owned existing modifier stack."""
    text = bpy.data.texts.new('MeshComputeUI_disabled.nf')
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        modifier = mesh_object.modifiers.new('User Bevel', 'BEVEL')
        assert not ui.NODEFORGE_OT_compile_mesh_compute_modifier.poll(bpy.context)
        assert mesh_object.modifiers[0].as_pointer() == modifier.as_pointer()
        mesh_object.modifiers.remove(modifier)
        assert ui.NODEFORGE_OT_compile_mesh_compute_modifier.poll(bpy.context)
    finally:
        bpy.data.texts.remove(text)


def test_reload_compute_operator_evaluates_changed_local_source(registered_ui, configured_local, mesh_object):
    """UI Reload replaces callback on the same modifier and evaluates its output."""
    from NodeForge.tests.blender.test_mesh_compute_reload import _new_kernel_source
    text = bpy.data.texts.new("MeshComputeUI_Reload.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        assert bpy.ops.nodeforge.compile_mesh_compute_modifier() == {'FINISHED'}
        original_modifier = mesh_object.modifiers[0].as_pointer()
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
        (configured_local / 'shift.py').write_text(_new_kernel_source())
        assert ui.NODEFORGE_OT_reload_mesh_compute_modifier.poll(bpy.context)
        assert bpy.ops.nodeforge.reload_mesh_compute_modifier() == {'FINISHED'}
        assert mesh_object.modifiers[0].as_pointer() == original_modifier
        assert runtime.get_modifier_binding(mesh_object).status == "READY"
        assert _evaluated_positions(mesh_object)[0] == (4., 5., 6.)
    finally:
        bpy.data.texts.remove(text)


def test_reload_compute_operator_denies_unsupported_root_and_no_changes(registered_ui, configured_local, mesh_object):
    """Reload is unavailable on a non-runtime modifier or absent Text Block."""
    text = bpy.data.texts.new("MeshComputeUI_ReloadDenied.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        mesh_object.modifiers.new("User", "BEVEL")
        assert not ui.NODEFORGE_OT_reload_mesh_compute_modifier.poll(bpy.context)
        assert len(mesh_object.modifiers) == 1
        mesh_object.modifiers.clear()
        assert bpy.ops.nodeforge.compile_mesh_compute_modifier() == {'FINISHED'}
        bpy.context.scene.gn_script_mvp.text_block = None
        assert not ui.NODEFORGE_OT_reload_mesh_compute_modifier.poll(bpy.context)
    finally:
        bpy.data.texts.remove(text)


def test_reload_operator_recovers_missing_saved_runtime(registered_ui, configured_local, mesh_object):
    """Reload button is available after load invalidates the persisted GN output."""
    text = bpy.data.texts.new('MeshComputeUI_SavedReload.nf')
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        assert bpy.ops.nodeforge.compile_mesh_compute_modifier() == {'FINISHED'}
        previous = runtime.get_modifier_binding(mesh_object)
        old_source, old_scene = previous.source_object, previous.source_scene
        runtime.on_file_load_post()
        assert runtime.saved_runtime_status(mesh_object) == 'MISSING_RUNTIME'
        assert ui.NODEFORGE_OT_reload_mesh_compute_modifier.poll(bpy.context)
        assert bpy.ops.nodeforge.reload_mesh_compute_modifier() == {'FINISHED'}
        assert runtime.saved_runtime_status(mesh_object) is None
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        if 'old_source' in locals() and any(obj is old_source for obj in bpy.data.objects):
            bpy.data.objects.remove(old_source, do_unlink=True)
        if 'old_scene' in locals() and any(scene is old_scene for scene in bpy.data.scenes):
            bpy.data.scenes.remove(old_scene, do_unlink=True)
        bpy.data.texts.remove(text)


def test_catalog_node_group_reports_mesh_compute_modifier_workflow(configured_local, mesh_object):
    """A Local .nf root that imports runtime Python cannot be added as a regular GN group."""
    from NodeForge.compiler import create_local_source_group, create_mesh_compute_modifier
    from NodeForge.errors import CompileError

    source_path = configured_local / "runtime_entry.nf"
    source_path.write_text(SOURCE, encoding="utf-8")
    before_groups = {group.as_pointer() for group in bpy.data.node_groups}
    before_scenes = {scene.as_pointer() for scene in bpy.data.scenes}

    with pytest.raises(CompileError, match="Compile to Modifier.*cannot be added as a library node group"):
        create_local_source_group(str(source_path))

    assert {group.as_pointer() for group in bpy.data.node_groups} == before_groups
    assert {scene.as_pointer() for scene in bpy.data.scenes} == before_scenes
    assert not mesh_object.modifiers

    group = create_mesh_compute_modifier(SOURCE, mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    assert runtime.get_modifier_binding(mesh_object).group is group
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)


def _select_local_script(props, directory, path):
    """Refresh the one Local browser directory and select a specific entry script."""
    props.local_browser_path = str(directory)
    ui._refresh_catalog_items(props, "local")
    props.local_index = next(i for i, item in enumerate(props.local_items)
                             if item.path == str(path))


def test_local_mesh_compute_compiles_selected_nf_without_text_block(
        registered_ui, configured_local, mesh_object):
    """Local browser's compile action reads its selected .nf root, not Text Script."""
    script = configured_local / 'mesh_compute_entry.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    old_text = props.text_block
    try:
        props.text_block = None
        _select(mesh_object)
        _select_local_script(props, configured_local, script)
        assert ui.NODEFORGE_OT_compile_local_mesh_compute_modifier.poll(bpy.context)
        assert bpy.ops.nodeforge.compile_local_mesh_compute_modifier() == {'FINISHED'}
        assert props.text_block is None
        assert len(mesh_object.modifiers) == 1
        assert runtime.get_modifier_binding(mesh_object).status == 'READY'
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        props.text_block = old_text


def test_local_mesh_compute_rejects_revoked_python_permission(
        registered_ui, configured_local, mesh_object):
    """Local browser action must preserve the existing trust gate and rollback."""
    script = configured_local / 'mesh_compute_denied.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, script)
    local_sources.set_local_python_permission(configured_local, allowed=False)
    before_groups = {group.as_pointer() for group in bpy.data.node_groups}
    before_scenes = {scene.as_pointer() for scene in bpy.data.scenes}
    with pytest.raises(RuntimeError, match='not allowed'):
        bpy.ops.nodeforge.compile_local_mesh_compute_modifier()
    assert not mesh_object.modifiers
    assert {group.as_pointer() for group in bpy.data.node_groups} == before_groups
    assert {scene.as_pointer() for scene in bpy.data.scenes} == before_scenes


def test_local_mesh_compute_action_rejects_python_row_and_existing_modifier(
        registered_ui, configured_local, mesh_object):
    """Offer the root action only for .nf entries and eligible Mesh Objects."""
    script = configured_local / 'mesh_compute_entry.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, configured_local / 'shift.py')
    assert not ui.NODEFORGE_OT_compile_local_mesh_compute_modifier.poll(bpy.context)
    _select_local_script(props, configured_local, script)
    modifier = mesh_object.modifiers.new('User', 'BEVEL')
    assert not ui.NODEFORGE_OT_compile_local_mesh_compute_modifier.poll(bpy.context)
    assert len(mesh_object.modifiers) == 1
    mesh_object.modifiers.remove(modifier)
    assert ui.NODEFORGE_OT_compile_local_mesh_compute_modifier.poll(bpy.context)


def test_local_mesh_compute_action_rejects_nonruntime_nf(
        registered_ui, configured_local, mesh_object):
    """A regular .nf is not silently converted to a Mesh Compute modifier."""
    script = configured_local / 'normal_group.nf'
    script.write_text('output("Value", 1.0)\n', encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, script)
    with pytest.raises(RuntimeError, match='requires one imported Local Mesh Compute'):
        bpy.ops.nodeforge.compile_local_mesh_compute_modifier()
    assert not mesh_object.modifiers


def test_local_panel_exposes_separate_compile_to_modifier_action(
        registered_ui, configured_local, mesh_object, monkeypatch):
    """UI exposes both explicit actions without reading scripts during redraw."""
    from types import SimpleNamespace

    script = configured_local / 'mesh_compute_entry.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, script)
    shown = []

    class Layout:
        """Record drawn Blender operators using the panel's structural API."""
        enabled = True

        def row(self, **kwargs):
            return self

        def operator(self, name, **kwargs):
            shown.append(name)
            return SimpleNamespace(namespace=None, path=None)

        def template_list(self, *args, **kwargs):
            pass

    monkeypatch.setattr(ui, '_active_gn_tree', lambda _: object())
    ui._draw_library_catalog_panel(Layout(), bpy.context, 'local', 'local_items',
                                   'local_index', rows=7)
    assert 'nodeforge.create_function_group' in shown
    assert 'nodeforge.compile_local_mesh_compute_modifier' in shown


def test_local_mesh_compute_compiles_nf_in_nested_browser_folder(
        registered_ui, configured_local, mesh_object):
    """Local folder navigation does not change the root-scoped compilation contract."""
    nested = configured_local / 'terrain_erosion'
    nested.mkdir()
    script = nested / 'erosion_numpy.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, nested, script)
    assert bpy.ops.nodeforge.compile_local_mesh_compute_modifier() == {'FINISHED'}
    assert runtime.get_modifier_binding(mesh_object).status == 'READY'
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)


def test_local_mesh_compute_rejects_missing_selected_file(
        registered_ui, configured_local, mesh_object):
    """Deleted .nf entries fail closed before creating any Blender resources."""
    script = configured_local / 'entry_missing.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, script)
    script.unlink()
    before_groups = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(RuntimeError, match='Cannot read Local script'):
        bpy.ops.nodeforge.compile_local_mesh_compute_modifier()
    assert not mesh_object.modifiers
    assert {group.as_pointer() for group in bpy.data.node_groups} == before_groups


def test_local_mesh_compute_compiles_adjacent_python_from_nested_folder(
        registered_ui, configured_local, mesh_object):
    """The .nf and .py files may share one browsed subdirectory, as in erosion."""
    nested = configured_local / 'terrain_erosion'
    nested.mkdir()
    kernel = nested / 'erosion.py'
    kernel.write_text((configured_local / 'shift.py').read_text(encoding='utf-8')
                      .replace('def shift(', 'def erosion('), encoding='utf-8')
    script = nested / 'erosion_numpy.nf'
    script.write_text(SOURCE.replace('from local import shift', 'from local import erosion')
                      .replace('geo = shift(', 'geo = erosion('), encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, nested, script)
    assert bpy.ops.nodeforge.compile_local_mesh_compute_modifier() == {'FINISHED'}
    assert runtime.get_modifier_binding(mesh_object).status == 'READY'
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
