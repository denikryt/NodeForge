"""Static regression tests for the collapsible Library panel layout."""

from pathlib import Path


UI_SOURCE = Path(__file__).resolve().parents[2] / "ui.py"


def test_library_uses_nested_blender_panels_in_requested_order():
    """Library must be a top-level parent with collapsed Local, Functions, Examples child panels."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    start = source.index("class NODEFORGE_PT_library(Panel):")
    end = source.index("class NODEFORGE_PT_library_local", start)
    library_block = source[start:end]
    assert 'bl_label = "Library"' in library_block
    assert "bl_parent_id" not in library_block

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
        assert "bl_options = {'DEFAULT_CLOSED'}" in block


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


def test_library_catalog_draw_does_not_refresh_scene_state():
    """Panel draw must not mutate Scene collection state by auto-refreshing catalogs."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    start = source.index("def _draw_library_catalog_panel")
    end = source.index("class NODEFORGE_PT_library", start)
    block = source[start:end]
    assert "_refresh_catalog_items(props, namespace)" not in block
    assert "Click Refresh to scan this catalog" in block


def test_reload_from_source_is_main_panel_action_only():
    """Reload belongs to the selected-group panel, not catalog browsing controls."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    main_start = source.index("class GNSCRIPT_MVP_PT_panel(Panel):")
    main_end = source.index("class NODEFORGE_PT_library(Panel):", main_start)
    main_block = source[main_start:main_end]
    assert 'text="Reload from Source"' in main_block
    assert "NODEFORGE_OT_reload_selected_library_group.poll(context)" in main_block

    catalog_start = source.index("def _draw_library_catalog_panel")
    catalog_end = source.index("class GNSCRIPT_MVP_PT_panel", catalog_start)
    catalog_block = source[catalog_start:catalog_end]
    assert "NODEFORGE_OT_reload_selected_library_group" not in catalog_block
