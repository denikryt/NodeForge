"""Shared Blender pytest fixtures and runtime guards."""

from pathlib import Path
import sys

import pytest

bpy = pytest.importorskip(
    "bpy",
    reason="Blender tests must be run through Blender Python via tests/run_pytest_in_blender.py",
)

BLENDER_TEST_ROOT = Path(__file__).resolve().parent
if str(BLENDER_TEST_ROOT) not in sys.path:
    sys.path.insert(0, str(BLENDER_TEST_ROOT))


def pytest_collection_modifyitems(items):
    """Mark every collected Blender test with the blender marker."""
    for item in items:
        item.add_marker("blender")



@pytest.fixture(autouse=True)
def isolated_package_inventory(tmp_path):
    """Run core Blender tests without relying on user-installed NodeForge packages."""
    from NodeForge import packages
    packages.set_packages_dir_for_tests(tmp_path / "packages")
    packages.invalidate_caches()
    try:
        yield
    finally:
        packages.set_packages_dir_for_tests(None)
        packages.invalidate_caches()
