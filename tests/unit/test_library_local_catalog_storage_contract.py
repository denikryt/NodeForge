"""Static regression tests for Local catalog storage ownership."""

from pathlib import Path


LIBRARY_SOURCE = Path(__file__).resolve().parents[2] / "library.py"


def test_local_catalog_uses_blender_user_resource_not_package_local_dir():
    """Local catalog files must persist outside the installed add-on package."""
    source = LIBRARY_SOURCE.read_text(encoding="utf-8")
    helper_start = source.index("def _default_local_catalog_dir")
    catalog_start = source.index("def catalog_dir", helper_start)
    helper = source[helper_start:catalog_start]
    assert 'bpy.utils.user_resource("DATAFILES", path="nodeforge/local", create=True)' in helper

    catalog_end = source.index("def _is_valid_function_name", catalog_start)
    catalog = source[catalog_start:catalog_end]
    assert 'catalog.namespace == "local"' in catalog
    assert "return _default_local_catalog_dir()" in catalog
    assert "return _package_root() / catalog.dirname" in catalog
