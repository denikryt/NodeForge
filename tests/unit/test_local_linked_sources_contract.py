"""Static contracts for Local managed-tree and imported-folder ownership."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LOCAL_SOURCES = (ROOT / "local_sources.py").read_text(encoding="utf-8")
CATALOG = (ROOT / "catalog.py").read_text(encoding="utf-8")
UI = (ROOT / "ui.py").read_text(encoding="utf-8")


def test_imported_local_roots_are_stored_outside_addon_package_atomically():
    """Positive: registry persistence remains user-owned and atomic."""
    assert 'path="nodeforge", create=True' in LOCAL_SOURCES
    assert 'return root / "local_sources.json"' in LOCAL_SOURCES
    assert "os.replace(temp_name, path)" in LOCAL_SOURCES


def test_catalog_does_not_own_local_persistent_path_lookup():
    """Negative: pure catalog code cannot reach Blender user-resource Local paths."""
    assert "bpy.utils.user_resource" not in CATALOG
    assert "_default_local_catalog_dir" not in CATALOG
    assert "ensure_local_catalog_dir" not in CATALOG
    assert "from . import local_sources" not in CATALOG
    assert "from .local_sources" not in CATALOG


def test_local_ui_import_is_folder_only():
    start = UI.index("class NODEFORGE_OT_import_local")
    end = UI.index("class NODEFORGE_OT_delete_local_file", start)
    block = UI[start:end]
    assert 'bl_label = "Add Folder"' in block
    assert "link_local_source_folder" in block
    assert "link_local_source_file" not in block
    assert "OperatorFileListElement" not in UI
    assert "self.files" not in block
    assert 'text="Add Folder..."' in UI


def test_local_ui_uses_ownership_explicit_destructive_actions():
    assert 'text="Delete File"' in UI
    assert 'text="Delete Folder"' in UI
    assert 'text="Remove from Local"' in UI
    assert "unlink_local_source_folder" in UI
    assert "unlink_local_source_file" in UI


def test_managed_mutations_are_path_addressed_not_public_name_resolved():
    start = LOCAL_SOURCES.index("def delete_local_source")
    block = LOCAL_SOURCES[start:]
    assert 'find_local_entry_record("local"' not in block
    assert "_managed_local_target" in block
    assert "_resolve_owned_local_path" in block
    assert "os.replace" in block


def test_imported_root_registration_rejects_overlap_but_not_duplicate_names():
    start = LOCAL_SOURCES.index("def link_local_source_folder")
    end = LOCAL_SOURCES.index("def local_source_files", start)
    block = LOCAL_SOURCES[start:end]
    assert "_paths_overlap(target, managed)" in block
    assert "_paths_overlap(target, existing)" in block
    assert "unique_records" not in block
