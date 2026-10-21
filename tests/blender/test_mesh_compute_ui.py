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


def test_unified_compile_operator_evaluates_real_gn(registered_ui, configured_local, mesh_object):
    """UI compiles an explicitly trusted script and produces updated GN geometry."""
    text = bpy.data.texts.new("MeshComputeUI_positive.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
        assert len(mesh_object.modifiers) == 1
        binding = runtime.get_modifier_binding(mesh_object)
        assert binding.status == "READY"
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        bpy.data.texts.remove(text)


def test_unified_compile_rejects_removed_local_source(registered_ui, configured_local, mesh_object):
    """An unavailable Local source fails before publishing a modifier or GN group."""
    text = bpy.data.texts.new('MeshComputeUI_unlinked.nf')
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        (configured_local / 'shift.py').unlink()
        before_groups = {x.as_pointer() for x in bpy.data.node_groups}
        with pytest.raises(RuntimeError):
            bpy.ops.gn_script_mvp.compile_expression()
        assert not mesh_object.modifiers
        assert {x.as_pointer() for x in bpy.data.node_groups} == before_groups
    finally:
        bpy.data.texts.remove(text)


def test_local_python_source_uses_registered_owner_not_permission(registered_ui, configured_local, tmp_path):
    """The managed owner is usable and the obsolete grant operator is absent."""
    source = configured_local / 'shift.py'
    assert local_sources.is_registered_local_python_source(source)
    foreign = tmp_path / 'unregistered.py'
    foreign.write_text(source.read_text())
    assert not local_sources.is_registered_local_python_source(foreign)
    assert not hasattr(ui, 'NODEFORGE_OT_set_local_python_permission')


def test_unified_compile_rejects_occupied_modifier_only_for_runtime(
        registered_ui, configured_local, mesh_object):
    """Used Python rejects a foreign modifier; ordinary DSL remains compilable."""
    from NodeForge.compiler import compile_script_auto
    from NodeForge.errors import CompileError

    _select(mesh_object)
    modifier = mesh_object.modifiers.new('User Bevel', 'BEVEL')
    try:
        with pytest.raises(CompileError, match='without previous modifiers'):
            compile_script_auto(SOURCE, mesh_object)
        assert mesh_object.modifiers[0].as_pointer() == modifier.as_pointer()
        group, installed = compile_script_auto('output("Value", 1.0)\n', mesh_object)
        assert not installed
        assert group is not modifier
        assert len(mesh_object.modifiers) == 1
    finally:
        mesh_object.modifiers.remove(modifier)


def test_reload_compute_operator_evaluates_changed_local_source(registered_ui, configured_local, mesh_object):
    """UI Reload replaces callback on the same modifier and evaluates its output."""
    from NodeForge.tests.blender.test_mesh_compute_reload import _new_kernel_source
    text = bpy.data.texts.new("MeshComputeUI_Reload.nf")
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
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
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
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
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
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

    with pytest.raises(CompileError, match="Compile Script or Local/Examples Add Node Group.*cannot be added as a library node group"):
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
        assert bpy.ops.nodeforge.create_function_group(namespace="local") == {'FINISHED'}
        assert props.text_block is None
        assert len(mesh_object.modifiers) == 1
        assert runtime.get_modifier_binding(mesh_object).status == 'READY'
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        props.text_block = old_text


def test_local_mesh_compute_rejects_missing_selected_source(registered_ui, configured_local, mesh_object):
    """A missing Local runtime cannot publish a modifier even with no consent gate."""
    script = configured_local / 'mesh_compute_missing.nf'
    script.write_text(SOURCE)
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, script)
    (configured_local / 'shift.py').unlink()
    before = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(RuntimeError):
        bpy.ops.nodeforge.create_function_group(namespace='local')
    assert not mesh_object.modifiers
    assert {group.as_pointer() for group in bpy.data.node_groups} == before


def test_local_action_rejects_python_row_and_occupied_modifier(
        registered_ui, configured_local, mesh_object):
    """Python callbacks are not roots; existing modifiers reject runtime scripts."""
    from NodeForge.compiler import create_selected_library_group_auto
    from NodeForge.errors import CompileError

    script = configured_local / 'mesh_compute_entry.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, configured_local / 'shift.py')
    with pytest.raises(RuntimeError, match='Select a Local script, not a folder'):
        bpy.ops.nodeforge.create_function_group(namespace='local')
    _select_local_script(props, configured_local, script)
    modifier = mesh_object.modifiers.new('User', 'BEVEL')
    with pytest.raises(CompileError, match='without previous modifiers'):
        create_selected_library_group_auto('local', str(script), mesh_object)
    assert len(mesh_object.modifiers) == 1
    mesh_object.modifiers.remove(modifier)
    assert bpy.ops.nodeforge.create_function_group(namespace='local') == {'FINISHED'}


def test_local_normal_nf_keeps_group_route(registered_ui, configured_local, mesh_object):
    """Selected ordinary .nf materializes as a group, not a GN modifier."""
    from NodeForge.compiler import create_selected_library_group_auto

    script = configured_local / 'normal_group.nf'
    script.write_text('output("Value", 1.0)\n', encoding='utf-8')
    group, installed = create_selected_library_group_auto('local', str(script), mesh_object)
    assert not installed
    assert group.bl_idname == 'GeometryNodeTree'
    assert not mesh_object.modifiers


def test_local_panel_has_one_compile_action(
        registered_ui, configured_local, mesh_object, monkeypatch):
    """UI exposes one action without analyzing scripts during redraw."""
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
    assert shown.count('nodeforge.create_function_group') == 1
    assert not any('compile_local_mesh_compute_modifier' in entry for entry in shown)


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
    assert bpy.ops.nodeforge.create_function_group(namespace="local") == {'FINISHED'}
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
    with pytest.raises(RuntimeError, match='unavailable'):
        bpy.ops.nodeforge.create_function_group(namespace="local")
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
    assert bpy.ops.nodeforge.create_function_group(namespace="local") == {'FINISHED'}
    assert runtime.get_modifier_binding(mesh_object).status == 'READY'
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)


def test_unused_runtime_import_stays_detached(registered_ui, configured_local, mesh_object):
    """Only used runtime callable identities select the modifier route."""
    from NodeForge.compiler import compile_script_auto

    group, installed = compile_script_auto(
        'from local import shift\noutput("Value", 2.0)\n', mesh_object,
    )
    assert not installed
    assert group.bl_idname == 'GeometryNodeTree'
    assert not mesh_object.modifiers


def test_runtime_without_mesh_fails_before_publication(configured_local):
    """Runtime cannot downgrade into a detached group when no owner exists."""
    from NodeForge.compiler import compile_script_auto
    from NodeForge.errors import CompileError

    existing = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='selected Mesh Object'):
        compile_script_auto(SOURCE, None)
    assert {group.as_pointer() for group in bpy.data.node_groups} == existing


def test_removed_modifier_operators_are_unregistered(registered_ui):
    """The public UI exposes one Compile and one Add, with explicit Reload."""
    assert ui.GNSCRIPT_MVP_OT_compile_expression in ui.classes
    assert ui.NODEFORGE_OT_create_function_group in ui.classes
    assert ui.NODEFORGE_OT_reload_mesh_compute_modifier in ui.classes
    assert not any('compile_mesh_compute_modifier' in cls.__name__ for cls in ui.classes)


def test_ordinary_text_compile_inserts_only_group_node(registered_ui, mesh_object, monkeypatch):
    """Ordinary Text compilation keeps editor insertion even with a Mesh selected."""
    editor = bpy.data.node_groups.new('OrdinaryCompileEditor', 'GeometryNodeTree')
    text = bpy.data.texts.new('OrdinaryCompileText.nf')
    try:
        text.from_string('output("Value", 1.0)\n')
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        monkeypatch.setattr(ui, '_active_gn_tree', lambda context: editor)
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
        groups = [node for node in editor.nodes if node.bl_idname == 'GeometryNodeGroup']
        assert len(groups) == 1
        assert groups[0].node_tree is not None
        assert not mesh_object.modifiers
    finally:
        bpy.data.texts.remove(text)
        bpy.data.node_groups.remove(editor)


def test_runtime_text_compile_does_not_insert_editor_node(
        registered_ui, configured_local, mesh_object, monkeypatch):
    """Runtime publication uses the direct modifier root, not an editor group node."""
    editor = bpy.data.node_groups.new('RuntimeCompileEditor', 'GeometryNodeTree')
    text = bpy.data.texts.new('RuntimeCompileText.nf')
    try:
        text.from_string(SOURCE)
        bpy.context.scene.gn_script_mvp.text_block = text
        _select(mesh_object)
        monkeypatch.setattr(ui, '_active_gn_tree', lambda context: editor)
        assert bpy.ops.gn_script_mvp.compile_expression() == {'FINISHED'}
        assert not any(node.bl_idname == 'GeometryNodeGroup' for node in editor.nodes)
        binding = runtime.get_modifier_binding(mesh_object)
        assert mesh_object.modifiers[0].node_group is binding.group
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        bpy.data.texts.remove(text)
        bpy.data.node_groups.remove(editor)


def test_local_ordinary_root_without_editor_does_not_publish(
        registered_ui, configured_local, mesh_object):
    """The ordinary library path still requires an editor before resource creation."""
    from NodeForge.compiler import create_selected_library_group_auto
    from NodeForge.errors import CompileError

    script = configured_local / 'no_editor_root.nf'
    script.write_text('output("Value", 1.0)\n', encoding='utf-8')
    before = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='Open a Geometry Nodes editor'):
        create_selected_library_group_auto(
            'local', str(script), mesh_object, editor_available=False,
        )
    assert {group.as_pointer() for group in bpy.data.node_groups} == before
    assert not mesh_object.modifiers


def _add_empty_gn_modifier(obj):
    """Model the unedited Geometry Nodes modifier made by Blender's New button."""
    group = bpy.data.node_groups.new('Unedited Geometry Nodes', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    source = group.nodes.new('NodeGroupInput')
    target = group.nodes.new('NodeGroupOutput')
    group.links.new(source.outputs['Geometry'], target.inputs['Geometry'])
    modifier = obj.modifiers.new('GeometryNodes', 'NODES')
    modifier.node_group = group
    return modifier, group


def test_local_add_node_group_reuses_empty_gn_modifier(
        registered_ui, configured_local, mesh_object):
    """Selected Local runtime script reuses Blender's default empty modifier."""
    script = configured_local / 'terrain.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, script)
    modifier, original_group = _add_empty_gn_modifier(mesh_object)
    pointer = modifier.as_pointer()
    try:
        assert bpy.ops.nodeforge.create_function_group(namespace='local') == {'FINISHED'}
        assert len(mesh_object.modifiers) == 1
        assert mesh_object.modifiers[0].as_pointer() == pointer
        assert modifier.node_group is not original_group
        assert runtime.get_modifier_binding(mesh_object, modifier).status == 'READY'
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
        assert original_group in bpy.data.node_groups[:]
    finally:
        if original_group in bpy.data.node_groups[:]:
            bpy.data.node_groups.remove(original_group, do_unlink=True)


def test_local_empty_modifier_binding_failure_restores_original(
        registered_ui, configured_local, mesh_object, monkeypatch):
    """A failing runtime binding restores exactly the user's placeholder group."""
    from NodeForge import compiler

    script = configured_local / 'failed.nf'
    script.write_text(SOURCE, encoding='utf-8')
    _select(mesh_object)
    _select_local_script(bpy.context.scene.gn_script_mvp, configured_local, script)
    modifier, original_group = _add_empty_gn_modifier(mesh_object)
    groups_before = {g.as_pointer() for g in bpy.data.node_groups}
    scenes_before = {scene.as_pointer() for scene in bpy.data.scenes}
    pointer = modifier.as_pointer()

    def fail(*args, **kwargs):
        """Exercise the transactional candidate-to-live boundary."""
        raise ValueError('simulated binding failure')

    monkeypatch.setattr(runtime, 'prepare_modifier_binding', fail)
    try:
        with pytest.raises(RuntimeError, match='simulated binding failure'):
            bpy.ops.nodeforge.create_function_group(namespace='local')
        assert len(mesh_object.modifiers) == 1
        assert modifier.as_pointer() == pointer
        assert modifier.node_group is original_group
        assert {g.as_pointer() for g in bpy.data.node_groups} == groups_before
        assert {scene.as_pointer() for scene in bpy.data.scenes} == scenes_before
    finally:
        mesh_object.modifiers.remove(modifier)
        bpy.data.node_groups.remove(original_group, do_unlink=True)


def test_local_existing_nonempty_gn_modifier_is_rejected(
        registered_ui, configured_local, mesh_object):
    """A user-edited GN graph cannot be replaced by Mesh Compute."""
    from NodeForge.compiler import create_selected_library_group_auto
    from NodeForge.errors import CompileError

    script = configured_local / 'guard.nf'
    script.write_text(SOURCE, encoding='utf-8')
    modifier, original_group = _add_empty_gn_modifier(mesh_object)
    original_group.nodes.new('GeometryNodeSetPosition')
    groups_before = {g.as_pointer() for g in bpy.data.node_groups}
    try:
        with pytest.raises(CompileError, match='one empty Geometry Nodes modifier'):
            create_selected_library_group_auto('local', str(script), mesh_object)
        assert modifier.node_group is original_group
        assert {g.as_pointer() for g in bpy.data.node_groups} == groups_before
    finally:
        mesh_object.modifiers.remove(modifier)
        bpy.data.node_groups.remove(original_group, do_unlink=True)


def test_local_existing_modifier_without_group_is_reused(
        registered_ui, configured_local, mesh_object):
    """An unassigned single GN modifier can also own the runtime root."""
    script = configured_local / 'unassigned.nf'
    script.write_text(SOURCE, encoding='utf-8')
    modifier = mesh_object.modifiers.new('Empty Geometry Nodes', 'NODES')
    pointer = modifier.as_pointer()
    _select(mesh_object)
    _select_local_script(bpy.context.scene.gn_script_mvp, configured_local, script)
    assert bpy.ops.nodeforge.create_function_group(namespace='local') == {'FINISHED'}
    assert len(mesh_object.modifiers) == 1
    assert modifier.as_pointer() == pointer
    assert runtime.get_modifier_binding(mesh_object, modifier).status == 'READY'


def test_local_source_names_show_files_not_parent_folder(registered_ui, configured_local):
    """Browser rows display physical .nf/.py filenames rather than source labels."""
    nested = configured_local / 'terrain_erosion'
    nested.mkdir()
    (nested / 'erode.nf').write_text('output("Value", 1.0)\n', encoding='utf-8')
    (nested / 'shift.py').write_text((configured_local / 'shift.py').read_text(encoding='utf-8'), encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    props.local_browser_path = str(nested)
    ui._refresh_catalog_items(props, 'local')
    filenames = {item.name: item.source_label for item in props.local_items}
    assert filenames['erode'] == ''
    assert filenames['shift'] == ''


def test_local_script_add_action_enabled_without_node_tree(
        registered_ui, configured_local, mesh_object, monkeypatch):
    """Local .nf stays actionable without a GN editor; Python rows stay disabled."""
    from types import SimpleNamespace

    script = configured_local / 'terrain.nf'
    script.write_text(SOURCE, encoding='utf-8')
    props = bpy.context.scene.gn_script_mvp
    _select(mesh_object)
    _select_local_script(props, configured_local, script)
    monkeypatch.setattr(ui, '_active_gn_tree', lambda _: None)
    actions = []

    class Layout:
        """Capture enabled state of the action row in a real panel draw."""
        enabled = True

        def row(self, **kwargs):
            return Layout()

        def operator(self, name, **kwargs):
            actions.append((name, self.enabled))
            return SimpleNamespace(namespace=None, path=None)

        def template_list(self, *args, **kwargs):
            pass

        def label(self, **kwargs):
            pass

    ui._draw_library_catalog_panel(Layout(), bpy.context, 'local', 'local_items', 'local_index', rows=7)
    assert ('nodeforge.create_function_group', True) in actions
    _select_local_script(props, configured_local, configured_local / 'shift.py')
    actions.clear()
    ui._draw_library_catalog_panel(Layout(), bpy.context, 'local', 'local_items', 'local_index', rows=7)
    assert ('nodeforge.create_function_group', False) in actions
