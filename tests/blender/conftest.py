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

