"""Smoke-test an installed Blender Extension and its independently versioned Packages."""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import bpy
import pytest

SMOKE_EXAMPLES = {
    "nodeforge.math": "noise_terrain.nf",
    "nodeforge.lsystem": "koch_curve.nf",
}


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
    bundled = import_module(extension.__name__ + ".bundled_packages")
    compiler = import_module(extension.__name__ + ".compiler")
    assert extension.__name__.startswith("bl_ext.")
    examples = SMOKE_EXAMPLES
    assert set(examples) == set(bundled.BUNDLED_PACKAGES)
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
    bundled = importlib.import_module(extension.__name__ + ".bundled_packages")
    records = packages.active_package_records()
    assert {r.manifest.package_id for r in records} >= set(bundled.BUNDLED_PACKAGES)
    assert all("allow_python" not in r.state_record for r in records)


def test_browser_lists_populate_when_startup_refresh_runs():
    """The startup refresh callback populates the catalogs in background Blender."""
    extension = _installed_extension()
    ui = importlib.import_module(extension.__name__ + ".ui")
    bundled = importlib.import_module(extension.__name__ + ".bundled_packages")
    # Tests may already have consumed the one-shot timer. Blender's headless
    # pytest runner cannot guarantee when a UI event-loop callback is scheduled.
    assert ui._refresh_startup_browser_items() is None
    props = bpy.context.scene.gn_script_mvp
    assert {item.package_id for item in props.package_items} >= set(bundled.BUNDLED_PACKAGES)
    for example_name in SMOKE_EXAMPLES.values():
        assert any(
            item.name == Path(example_name).stem
            for item in props.example_items
        )
    assert len(props.function_items) > 0


def test_startup_catalog_error_does_not_block_other_lists(monkeypatch, capsys):
    """A failed Functions catalog cannot prevent Examples and Packages refreshing."""
    extension = _installed_extension()
    ui = importlib.import_module(extension.__name__ + ".ui")
    bundled = importlib.import_module(extension.__name__ + ".bundled_packages")
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
    example_names = {Path(example).stem for example in SMOKE_EXAMPLES.values()}
    assert {item.name for item in props.example_items} >= example_names
    assert len(props.package_items) >= len(bundled.BUNDLED_PACKAGES)


def test_z_reenable_upgrades_an_older_included_package(tmp_path):
    """Re-enabling the built Extension upgrades an older ordinary installed package."""
    import json
    import zipfile

    extension = _installed_extension()
    packages = importlib.import_module(extension.__name__ + ".packages")
    bundled = importlib.import_module(extension.__name__ + ".bundled_packages")
    package_id = next(iter(bundled.BUNDLED_PACKAGES))
    included = sorted((Path(extension.__file__).parent / "package_install").glob(f"{package_id}-*.zip"))
    assert len(included) == 1
    old_zip = tmp_path / "older-package.zip"
    active_before = {r.manifest.package_id: r.manifest.version for r in packages.active_package_records()}
    with zipfile.ZipFile(included[0]) as source, zipfile.ZipFile(old_zip, "w") as old:
        for entry in source.infolist():
            payload = source.read(entry.filename)
            if entry.filename.endswith("nodeforge_package.json"):
                manifest = json.loads(payload)
                assert manifest["id"] == package_id
                included_version = manifest["version"]
                # Version is test input for the upgrade flow, not a pinned release.
                manifest["version"] = "0.0.1"
                payload = json.dumps(manifest).encode("utf-8")
            old.writestr(entry, payload)

    packages.install_package_zip(old_zip, replace=True)
    current = {r.manifest.package_id: r.manifest.version for r in packages.active_package_records()}
    assert current[package_id] == "0.0.1"
    assert all(current[other] == version for other, version in active_before.items() if other != package_id)

    bpy.ops.preferences.addon_disable(module=extension.__name__)
    assert extension.__name__ not in bpy.context.preferences.addons
    bpy.ops.preferences.addon_enable(module=extension.__name__)
    assert extension.__name__ in bpy.context.preferences.addons
    packages = importlib.import_module(extension.__name__ + ".packages")
    updated = {r.manifest.package_id: r.manifest.version for r in packages.active_package_records()}
    assert updated[package_id] == included_version
    assert all(updated[other] == version for other, version in active_before.items() if other != package_id)
    ui = importlib.import_module(extension.__name__ + ".ui")
    assert ui._refresh_startup_browser_items() is None
    props = bpy.context.scene.gn_script_mvp
    assert {row.package_id for row in props.package_items} >= set(updated)
