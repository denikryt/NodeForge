"""Ensure refreshing one library UI catalog never captures unrelated Local sources."""

import bpy
import pytest

from NodeForge import ui, local_sources
from NodeForge import environment_resolution

pytestmark = pytest.mark.blender


def test_refresh_examples_does_not_discover_local(monkeypatch, tmp_path):
    """Examples enumeration is unrelated to the Local semantic/source index."""
    managed = tmp_path / 'managed'
    managed.mkdir()
    registry = tmp_path / 'registry.json'
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: managed)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: registry)
    monkeypatch.setattr(environment_resolution, '_capture_local_catalog',
                        lambda: pytest.fail('Examples refresh unexpectedly captured Local'))
    registered_here = not hasattr(bpy.types.Scene, 'gn_script_mvp')
    if registered_here:
        ui.register()
    try:
        props = bpy.context.scene.gn_script_mvp
        count = ui._refresh_catalog_items(props, 'examples')
        assert len(props.example_items) == count
    finally:
        if registered_here:
            ui.unregister()


def test_refresh_examples_does_not_depend_on_invalid_local_tree(monkeypatch, tmp_path):
    """Broken Local scripts remain compile errors but do not block Examples browsing."""
    managed = tmp_path / 'managed'
    (managed / 'terrain').mkdir(parents=True)
    (managed / 'terrain' / 'source.nf').write_text('output(1.0)\n')
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: managed)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'registry.json')
    registered_here = not hasattr(bpy.types.Scene, 'gn_script_mvp')
    if registered_here:
        ui.register()
    try:
        props = bpy.context.scene.gn_script_mvp
        count = ui._refresh_catalog_items(props, 'examples')
        assert count == len(props.example_items)
    finally:
        if registered_here:
            ui.unregister()


def test_refresh_operators_ignore_unopened_local_subtrees(monkeypatch, tmp_path):
    """Real Refresh buttons must list direct sources without indexing every nested root."""
    managed = tmp_path / 'managed'
    (managed / 'deep').mkdir(parents=True)
    (managed / 'top.nf').write_text('output("Value", 1.0)\n')
    (managed / 'deep' / 'source.nf').write_text('output(1.0)\n')
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: managed)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'registry.json')
    registered_here = not hasattr(bpy.types.Scene, 'gn_script_mvp')
    if registered_here:
        ui.register()
    try:
        props = bpy.context.scene.gn_script_mvp
        props.local_browser_path = ''
        assert bpy.ops.nodeforge.refresh_function_library(namespace='local') == {'FINISHED'}
        assert {item.name for item in props.local_items} == {'top', 'deep'}
        assert bpy.ops.nodeforge.refresh_function_library(namespace='examples') == {'FINISHED'}
    finally:
        if registered_here:
            ui.unregister()
