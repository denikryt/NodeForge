"""Real Blender Geometry Nodes materialization for nested Examples source owners."""

from __future__ import annotations

import bpy
import pytest

from NodeForge import catalog
from NodeForge.compiler import create_examples_source_group, create_expression_group
from NodeForge.errors import CompileError

pytestmark = pytest.mark.blender


@pytest.fixture
def example_catalog(tmp_path, monkeypatch):
    """Build one isolated bundled Examples directory with two duplicate source stems."""
    root = tmp_path / 'examples'
    for folder in ('terrain', 'water'):
        path = root / folder / 'erosion.nf'
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('geo = input_geometry("Geometry")\noutput("Geometry", geo)\n', encoding='utf8')
    original = catalog.catalog_dir
    monkeypatch.setattr(catalog, 'catalog_dir', lambda ns: root if ns == 'examples' else original(ns))
    return root


def test_dotted_examples_import_materializes_real_node_group(example_catalog):
    """Qualified Examples source calls yield distinct materialized Blender GN owners."""
    text = ('from examples.terrain import erosion\n'
            'geo = input_geometry("Geometry")\n'
            'output("Geometry", erosion(geo))\n')
    group = create_expression_group(text)
    assert group.bl_idname == 'GeometryNodeTree'
    assert any(node.bl_idname == 'GeometryNodeGroup' for node in group.nodes)


def test_ui_exact_record_selection_never_uses_ambiguous_flat_name(example_catalog):
    """Two equal stems from distinct physical directories remain different source groups."""
    first = create_examples_source_group(str(example_catalog / 'terrain/erosion.nf'))
    second = create_examples_source_group(str(example_catalog / 'water/erosion.nf'))
    assert first.as_pointer() != second.as_pointer()
    assert first.name != second.name


def test_deleted_selected_source_fails_without_publication(example_catalog):
    """A stale Examples selection must not allocate a partial Geometry Nodes group."""
    missing = example_catalog / 'terrain/erosion.nf'
    missing.unlink()
    before = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='unavailable'):
        create_examples_source_group(str(missing))
    assert {group.as_pointer() for group in bpy.data.node_groups} == before
