"""Static regression tests for the collapsible Library panel layout."""

from pathlib import Path


UI_SOURCE = Path(__file__).resolve().parents[2] / "ui.py"


def test_library_uses_nested_blender_panels_in_requested_order():
    """Library must be a collapsible parent with Local, Functions, Examples child panels."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    assert "class NODEFORGE_PT_library(Panel):" in source
    assert 'bl_label = "Library"' in source
    assert 'bl_parent_id = "GNSCRIPT_MVP_PT_panel"' in source

    local = source.index("class NODEFORGE_PT_library_local")
    functions = source.index("class NODEFORGE_PT_library_functions")
    examples = source.index("class NODEFORGE_PT_library_examples")
    assert local < functions < examples

    for class_name, label, order in (
        ("NODEFORGE_PT_library_local", "Local", "0"),
        ("NODEFORGE_PT_library_functions", "Functions", "1"),
        ("NODEFORGE_PT_library_examples", "Examples", "2"),
    ):
        start = source.index(f"class {class_name}(Panel):")
        end = source.find("\n\nclass ", start + 1)
        block = source[start:] if end == -1 else source[start:end]
        assert 'bl_parent_id = "NODEFORGE_PT_library"' in block
        assert f'bl_label = "{label}"' in block
        assert f"bl_order = {order}" in block


def test_local_panel_draws_local_actions_before_catalog_list():
    """Local section should put user-owned local actions at the top of the Library block."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    start = source.index("class NODEFORGE_PT_library_local")
    end = source.index("class NODEFORGE_PT_library_functions", start)
    block = source[start:end]
    new_folder = block.index("NODEFORGE_OT_create_local_folder")
    save = block.index("NODEFORGE_OT_save_to_local")
    catalog = block.index('_draw_library_catalog_panel(layout, context, "local"')
    assert new_folder < catalog
    assert save < catalog
