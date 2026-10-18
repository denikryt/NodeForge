"""Blender integration of selected Examples Python owners and UI modifier compilation."""

from __future__ import annotations

import bpy
import pytest

from NodeForge import catalog, local_sources, ui
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import (
    SOURCE, mesh_object, _evaluated_positions,
)

pytestmark = pytest.mark.blender


@pytest.fixture
def selected_examples(tmp_path, monkeypatch):
    """Isolate the bundled Examples installation root and user trust registry."""
    bundled = tmp_path / 'bundled'
    folder = bundled / 'mesh_compute_example'
    folder.mkdir(parents=True)
    existing = catalog.catalog_dir
    monkeypatch.setattr(catalog, 'catalog_dir', lambda ns: bundled if ns == 'examples' else existing(ns))
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'consent.json')
    (folder / 'shift.py').write_text('from NodeForge import mesh_compute\n'
        '@mesh_compute(writes=("height",))\n'
        'def shift(mesh, amount: float = 2.0):\n'
        '    p = list(mesh.positions)\n'
        '    for n in range(2, len(p), 3):\n'
        '        p[n] += amount\n'
        '    return {"positions": p, "point_attributes": '
        '{"height": [amount] * mesh.vertex_count}}\n', encoding='utf8')
    nf = folder / 'root.nf'
    nf.write_text(SOURCE.replace('from local import shift',
                                 'from examples.mesh_compute_example import shift'), encoding='utf8')
    return bundled, nf


@pytest.fixture
def registered_ui():
    """Ensure real Blender operators and Scene properties are registered once."""
    owns = not hasattr(bpy.types.Scene, 'gn_script_mvp')
    if owns:
        ui.register()
    yield
    if owns:
        ui.unregister()


def _select_mesh(owner):
    """Select one owned Blender Mesh as the sole active modifier target."""
    for obj in bpy.context.selected_objects:
        obj.select_set(False)
    owner.select_set(True)
    bpy.context.view_layer.objects.active = owner


def _select_source(nf):
    """Select a current exact Examples physical source via real UI catalog refresh."""
    props = bpy.context.scene.gn_script_mvp
    ui._refresh_catalog_items(props, 'examples')
    props.example_index = next(i for i, item in enumerate(props.example_items)
                               if item.path == str(nf))
    return props


def test_selected_examples_compiles_modifier_and_revocation_fails_closed(
        selected_examples, registered_ui, mesh_object):
    """Only consented Python executes, and revocation invalidates active evaluation."""
    bundled, nf = selected_examples
    _select_mesh(mesh_object)
    _select_source(nf)
    # Examples selection is independent of the main Blender Text Script.
    original_groups = {group.as_pointer() for group in bpy.data.node_groups}
    assert not local_sources.examples_python_root_permission(bundled)
    with pytest.raises(RuntimeError, match='not authorized'):
        bpy.ops.nodeforge.create_function_group(namespace="examples")
    assert not mesh_object.modifiers
    assert {group.as_pointer() for group in bpy.data.node_groups} == original_groups

    assert bpy.ops.nodeforge.set_examples_python_permission(allowed=True) == {'FINISHED'}
    assert bpy.ops.nodeforge.create_function_group(namespace="examples") == {'FINISHED'}
    assert len(mesh_object.modifiers) == 1
    binding = runtime.get_modifier_binding(mesh_object)
    assert binding.prepared.source_namespace == 'examples'
    assert binding.prepared.source_package_id == ''
    assert binding.status == 'READY'
    assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    assert bpy.ops.nodeforge.set_examples_python_permission(allowed=False) == {'FINISHED'}
    with pytest.raises(PermissionError, match='revoked'):
        runtime.evaluate_modifier_binding(mesh_object)
    assert binding.trust_revoked


def test_examples_modifier_poll_and_stale_source_failure(
        selected_examples, registered_ui, mesh_object):
    """UI refuses foreign modifiers and stale selected Examples source ownership."""
    bundled, nf = selected_examples
    _select_mesh(mesh_object)
    _select_source(nf)
    user_modifier = mesh_object.modifiers.new('User', 'BEVEL')
    mesh_object.modifiers.remove(user_modifier)
    local_sources.set_examples_python_permission(bundled, allowed=True)
    nf.unlink()
    before = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(RuntimeError, match='unavailable'):
        bpy.ops.nodeforge.create_function_group(namespace="examples")
    assert not mesh_object.modifiers
    assert {group.as_pointer() for group in bpy.data.node_groups} == before


def test_examples_python_cannot_be_published_as_regular_library_group(selected_examples):
    """An explicitly selected Python callback cannot bypass root modifier authority."""
    from NodeForge.compiler import create_examples_source_group
    from NodeForge.errors import CompileError
    bundled, nf = selected_examples
    local_sources.set_examples_python_permission(bundled, allowed=True)
    with pytest.raises(CompileError, match='unavailable'):
        create_examples_source_group(str(nf.with_name('shift.py')))


def test_examples_add_node_group_reuses_blender_empty_modifier(
        selected_examples, registered_ui, mesh_object):
    """Examples runtime scripts use the same already-present blank GN modifier."""
    from NodeForge.tests.blender.test_mesh_compute_ui import _add_empty_gn_modifier

    bundled, nf = selected_examples
    _select_mesh(mesh_object)
    _select_source(nf)
    local_sources.set_examples_python_permission(bundled, allowed=True)
    modifier, original_group = _add_empty_gn_modifier(mesh_object)
    pointer = modifier.as_pointer()
    try:
        assert bpy.ops.nodeforge.create_function_group(namespace='examples') == {'FINISHED'}
        assert len(mesh_object.modifiers) == 1
        assert modifier.as_pointer() == pointer
        assert runtime.get_modifier_binding(mesh_object, modifier).status == 'READY'
        assert _evaluated_positions(mesh_object)[0] == (2., 3., 4.)
    finally:
        if original_group in bpy.data.node_groups[:]:
            bpy.data.node_groups.remove(original_group, do_unlink=True)


def test_examples_unqualified_source_label_is_filename(selected_examples, registered_ui):
    """An Examples file is not displayed under its parent directory name."""
    _, nf = selected_examples
    props = _select_source(nf)
    item = props.example_items[props.example_index]
    assert item.name == 'root'
    assert item.source_label == ''


def test_examples_script_add_action_enabled_without_node_tree(
        selected_examples, registered_ui, mesh_object, monkeypatch):
    """Examples .nf remains selectable when Blender has not opened a GN tree."""
    from types import SimpleNamespace

    _, nf = selected_examples
    _select_mesh(mesh_object)
    _select_source(nf)
    monkeypatch.setattr(ui, '_active_gn_tree', lambda _: None)
    actions = []

    class Layout:
        """Capture enabled state of the Examples Add Node Group button."""
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

    ui._draw_library_catalog_panel(Layout(), bpy.context, 'examples', 'example_items', 'example_index', rows=7)
    assert ('nodeforge.create_function_group', True) in actions
