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
def reset_lsystem_fault_injection_flags():
    """Ensure L-system fault-injection flags do not leak between tests."""
    from NodeForge.systems.lsystem import resources as generated_resources
    from NodeForge import compiler

    try:
        yield
    finally:
        generated_resources._TEST_FAIL_AFTER_OBJECT_CREATE = False
        generated_resources._TEST_FAIL_AFTER_MESH_CREATE = False
        generated_resources._TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE = False
        compiler._TEST_CUTOVER_FAIL_AFTER_RESET = False
