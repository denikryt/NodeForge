"""Native Blender regressions for Local canonical ownership, reload and rollback."""

import bpy
import pytest

from helpers import compile_group
from NodeForge import compiler, local_sources
from NodeForge.errors import CompileError


@pytest.fixture
def local_tree(tmp_path, monkeypatch):
    """Isolate Local storage and clean every native datablock produced by the test."""
    root = tmp_path / 'Local'
    root.mkdir()
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: root)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'registry.json')
    before = {group.as_pointer() for group in bpy.data.node_groups}
    yield root
    for group in list(bpy.data.node_groups):
        if group.as_pointer() not in before:
            bpy.data.node_groups.remove(group, do_unlink=True)


def write(root, relative, expression='x'):
    """Create one single-input reusable physical source."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'x = input_float("X")\noutput("Value", {expression})\n')
    return path


def children(group):
    """Return source-backed group nodes in native root node order."""
    return [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']


def metadata_values(group):
    """Snapshot ID properties by value, including nested Blender wrappers."""
    def freeze(value):
        """Recursively copy mutable property groups and array contents."""
        if hasattr(value, 'to_dict'):
            return {key: freeze(item) for key, item in value.to_dict().items()}
        if hasattr(value, 'to_list'):
            return tuple(freeze(item) for item in value.to_list())
        if isinstance(value, dict):
            return {key: freeze(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return tuple(freeze(item) for item in value)
        return value
    return {key: freeze(value) for key, value in group.items()}
