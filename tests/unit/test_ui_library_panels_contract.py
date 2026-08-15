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
    assert "NODEFORGE_OT_reload_selected_library_group.poll(context)" not in main_block

    catalog_start = source.index("def _draw_library_catalog_panel")
    catalog_end = source.index("class GNSCRIPT_MVP_PT_panel", catalog_start)
    catalog_block = source[catalog_start:catalog_end]
    assert "NODEFORGE_OT_reload_selected_library_group" not in catalog_block



def test_reload_poll_keeps_catalog_resolution_out_of_ui_hot_path():
    """Reload poll should inspect provenance only; source resolution belongs to execute()."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    start = source.index("class NODEFORGE_OT_reload_selected_library_group")
    poll_start = source.index("def poll", start)
    execute_start = source.index("def execute", poll_start)
    poll_block = source[poll_start:execute_start]
    execute_end = source.index("class ", execute_start)
    execute_block = source[execute_start:execute_end]

    assert "nodeforge_library_namespace" in poll_block
    assert "nodeforge_library_name" in poll_block
    assert "resolve_reloadable_library_entry" not in poll_block
    assert "resolve_reloadable_library_entry" in execute_block


def test_main_panel_extracts_embedded_source_once_per_draw():
    """Selected-group redraw should avoid repeated source extraction work."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    start = source.index("class GNSCRIPT_MVP_PT_panel(Panel):")
    end = source.index("class NODEFORGE_PT_library(Panel):", start)
    block = source[start:end]
    assert block.count("_extract_group_source(") == 1

def test_local_panel_uses_folder_only_import_and_explicit_selected_actions():
    """Local panel should add external folders and expose ownership-specific actions."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    start = source.index("class NODEFORGE_PT_library_local")
    end = source.index("class NODEFORGE_PT_library_functions", start)
    block = source[start:end]
    assert 'text="Add Folder..."' in block
    helper_start = source.index("def _draw_local_selected_action")
    helper_end = source.index("def _draw_library_catalog_panel", helper_start)
    helper = source[helper_start:helper_end]
    assert 'text="Delete File"' in helper
    assert 'text="Delete Folder"' in helper
    assert 'text="Remove from Local"' in helper


def test_local_folder_rows_remain_selectable_and_use_separate_open_action():
    """Folder labels must not be operators so UIList selection remains available for removal."""
    source = UI_SOURCE.read_text(encoding="utf-8")
    start = source.index("class NODEFORGE_UL_function_library")
    end = source.index("class NODEFORGE_UL_packages", start)
    block = source[start:end]
    assert "row.label(text=item.name, icon='FILE_FOLDER')" in block
    open_start = block.index('"nodeforge.open_local_folder"')
    open_end = block.index("op.path = item.path", open_start)
    open_call = block[open_start:open_end]
    assert 'text=""' in open_call
    assert "icon='FORWARD'" in open_call
    assert "text=item.name" not in open_call
