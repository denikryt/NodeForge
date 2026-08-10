"""Static contract tests for linked Local source folders."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
LIBRARY = (ROOT / "library.py").read_text(encoding="utf-8")
UI = (ROOT / "ui.py").read_text(encoding="utf-8")


def test_linked_local_roots_are_stored_outside_addon_package():
    assert 'path="nodeforge", create=True' in LIBRARY
    assert 'return root / "local_sources.json"' in LIBRARY
    assert "os.replace(temp_name, path)" in LIBRARY


def test_linked_sources_are_direct_roots_not_symlinks_or_copies():
    start = LIBRARY.index("def link_local_source_folder")
    end = LIBRARY.index("def unlink_local_source_folder", start)
    helper = LIBRARY[start:end]
    assert "symlink" not in helper
    assert "copy" not in helper
    assert "_write_local_source_registry" in helper


def test_local_ui_exposes_live_source_and_managed_folder_workflow():
    assert 'text="Link Folder"' in UI
    assert 'text="Unlink Source"' in UI
    assert 'text="Managed Destination"' in UI
    assert 'text="Use Selected"' in UI
    assert 'text="Copy Files"' in UI
