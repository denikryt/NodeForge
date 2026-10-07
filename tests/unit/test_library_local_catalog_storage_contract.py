"""Static regression tests for Local catalog storage ownership."""

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
LOCAL_SOURCE = ROOT / "local_sources.py"
CATALOG_SOURCE = ROOT / "catalog.py"


def test_local_catalog_uses_blender_user_resource_not_package_local_dir():
    """Positive: Local catalog files persist at the same Blender user-data path."""
    source = LOCAL_SOURCE.read_text(encoding="utf-8")
    helper_start = source.index("def _default_local_catalog_dir")
    helper_end = source.index("def _catalog_input_paths", helper_start)
    helper = source[helper_start:helper_end]
    assert 'bpy.utils.user_resource("DATAFILES", path="nodeforge/local", create=True)' in helper
    assert "path.mkdir(parents=True, exist_ok=True)" in source


def test_catalog_has_no_local_persistent_root_special_case():
    """Negative: catalog_dir cannot perform platform-specific Local path discovery."""
    source = CATALOG_SOURCE.read_text(encoding="utf-8")
    assert "bpy.utils.user_resource" not in source
    assert "_default_local_catalog_dir" not in source
    assert "ensure_local_catalog_dir" not in source
