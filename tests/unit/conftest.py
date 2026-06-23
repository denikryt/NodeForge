"""Configuration for ordinary CPython unit tests."""

import sys


def test_environment_does_not_preload_bpy():
    """Unit tests should not depend on Blender's bpy module being preloaded."""
    assert "bpy" not in sys.modules
