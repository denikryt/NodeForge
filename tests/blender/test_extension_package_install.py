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
    examples = {"nodeforge.math": "noise_terrain.nf",
                "nodeforge.lsystem": "koch_curve.nf"}
    active = {item.manifest.package_id: item.manifest for item in packages.active_package_records()}
    assert set(examples).issubset(active)
    for package_id, example_name in examples.items():
        manifest = active[package_id]
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


def test_browser_lists_populate_when_startup_refresh_runs():
    """The startup refresh callback populates the catalogs in background Blender."""
    extension = _installed_extension()
    ui = importlib.import_module(extension.__name__ + ".ui")
    # Tests may already have consumed the one-shot timer. Blender's headless
    # pytest runner cannot guarantee when a UI event-loop callback is scheduled.
    assert ui._refresh_startup_browser_items() is None
    props = bpy.context.scene.gn_script_mvp
    assert {item.package_id for item in props.package_items} >= {
        "nodeforge.math", "nodeforge.lsystem"
    }
    assert any(item.name == "noise_terrain" for item in props.example_items)
    assert any(item.name == "koch_curve" for item in props.example_items)
    assert len(props.function_items) > 0


def test_startup_catalog_error_does_not_block_other_lists(monkeypatch, capsys):
    """A failed Functions catalog cannot prevent Examples and Packages refreshing."""
    extension = _installed_extension()
    ui = importlib.import_module(extension.__name__ + ".ui")
    original = ui._refresh_catalog_items
    props = bpy.context.scene.gn_script_mvp
    props.example_items.clear()

    def fail_functions(scene_props, namespace):
        """Inject a failure in one independent startup catalog."""
        if namespace == "functions":
            raise RuntimeError("simulated Functions failure")
        return original(scene_props, namespace)

    monkeypatch.setattr(ui, "_refresh_catalog_items", fail_functions)
    assert ui._refresh_startup_browser_items() is None
    assert "simulated Functions failure" in capsys.readouterr().out
    assert any(item.name == "noise_terrain" for item in props.example_items)
    assert len(props.package_items) >= 2


def test_z_reenable_upgrades_an_older_included_package(tmp_path):
    """Re-enabling the built Extension upgrades an older ordinary installed package."""
    import json
    import zipfile

    extension = _installed_extension()
    packages = importlib.import_module(extension.__name__ + ".packages")
    included_math = sorted((Path(extension.__file__).parent / "package_install").glob("nodeforge.math-*.zip"))
    if not included_math:
        pytest.skip("Extension distribution does not bundle a Math package ZIP")
    math_zip = included_math[0]
    old_zip = tmp_path / "math-older.zip"
    active_before = {r.manifest.package_id: r.manifest.version for r in packages.active_package_records()}
    with zipfile.ZipFile(math_zip) as source, zipfile.ZipFile(old_zip, "w") as old:
        for entry in source.infolist():
            payload = source.read(entry.filename)
            if entry.filename.endswith("nodeforge_package.json"):
                manifest = json.loads(payload)
                assert manifest["id"] == "nodeforge.math"
                included_version = manifest["version"]
                # Version is test input for the upgrade flow, not a pinned release.
                manifest["version"] = "0.0.1"
                payload = json.dumps(manifest).encode("utf-8")
            old.writestr(entry, payload)

    packages.install_package_zip(old_zip, replace=True)
    current = {r.manifest.package_id: r.manifest.version for r in packages.active_package_records()}
    assert current["nodeforge.math"] == "0.0.1"
    assert current["nodeforge.lsystem"] == active_before["nodeforge.lsystem"]

    bpy.ops.preferences.addon_disable(module=extension.__name__)
    assert extension.__name__ not in bpy.context.preferences.addons
    bpy.ops.preferences.addon_enable(module=extension.__name__)
    assert extension.__name__ in bpy.context.preferences.addons
    packages = importlib.import_module(extension.__name__ + ".packages")
    updated = {r.manifest.package_id: r.manifest.version for r in packages.active_package_records()}
    assert updated["nodeforge.math"] == included_version
    assert updated["nodeforge.lsystem"] == active_before["nodeforge.lsystem"]
    ui = importlib.import_module(extension.__name__ + ".ui")
    assert ui._refresh_startup_browser_items() is None
    props = bpy.context.scene.gn_script_mvp
    assert {row.package_id for row in props.package_items} >= set(updated)
