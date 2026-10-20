"""Smoke-test an installed Blender Extension and its independently versioned Packages."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import bpy
import pytest


def _installed_extension():
    """Find the Blender-managed installation, or skip regular legacy test sessions."""
    module_name = os.environ.get("NODEFORGE_EXTENSION_MODULE", "bl_ext.user_default.nodeforge")
    try:
        module = importlib.import_module(module_name)
    except ModuleNotFoundError:
        pytest.skip(f"NodeForge Blender Extension is not installed under {module_name}")
    return module


def test_real_extension_provides_installed_packages_and_compiles_examples():
    """Verify release ZIP bootstrap, owner imports and live Geometry Nodes lowering."""
    extension = _installed_extension()
    from importlib import import_module

    packages = import_module(extension.__name__ + ".packages")
    compiler = import_module(extension.__name__ + ".compiler")
    assert extension.__name__.startswith("bl_ext.")
    expected = {"nodeforge.math": ("2.2", "noise_terrain.nf"),
                "nodeforge.lsystem": ("4.0", "koch_curve.nf")}
    active = {item.manifest.package_id: item.manifest for item in packages.active_package_records()}
    assert set(expected).issubset(active)
    for package_id, (version, example_name) in expected.items():
        manifest = active[package_id]
        assert manifest.version == version
        assert manifest.root.is_dir()
        script = (manifest.root / "examples" / example_name).read_text(encoding="utf-8")
        group = compiler.create_expression_group(script, name="NFSuite_" + package_id)
        try:
            assert group.bl_idname == "GeometryNodeTree"
            assert len(group.nodes) > 1
        finally:
            bpy.data.node_groups.remove(group)


def test_extension_packages_do_not_require_user_consent():
    """Existing package ZIPs are active without a separate Python grant."""
    extension = _installed_extension()
    packages = importlib.import_module(extension.__name__ + ".packages")
    records = packages.active_package_records()
    assert {r.manifest.package_id for r in records} >= {"nodeforge.math", "nodeforge.lsystem"}
    assert all("allow_python" not in r.state_record for r in records)
    assert all(packages.package_compatibility_warning(r.manifest) for r in records)
