"""Blender integration coverage for third-party package lifecycle operations."""

from helpers import compile_group, expect_compile_error

import json
import tempfile
import zipfile
from pathlib import Path

from NodeForge import library, packages


def _make_package(root: Path, *, version: str = "1.0.0", value: float = 1.0) -> None:
    functions = root / "functions"
    functions.mkdir(parents=True)
    (functions / "vendor_value.nf").write_text(
        f'value = input_float("Value", default={value})\noutput("Value", value)\n',
        encoding="utf-8",
    )
    (root / "nodeforge_package.json").write_text(
        json.dumps({
            "schema_version": 1,
            "id": "vendor.lifecycle",
            "name": "Vendor Lifecycle",
            "version": version,
            "author": "Tests",
            "description": "Third-party lifecycle fixture",
            "nodeforge_min_version": "0.49.47",
            "nodeforge_max_version": None,
            "contents": {"functions": "functions"},
            "permissions": {"python": False},
        }),
        encoding="utf-8",
    )


def test_directory_zip_replace_uninstall_and_reinstall_lifecycle():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        inventory = tmp / "inventory"
        packages.set_packages_dir_for_tests(inventory)
        try:
            source_v1 = tmp / "source_v1"
            _make_package(source_v1, version="1.0.0", value=1.0)
            installed = packages.install_package_directory(source_v1, allow_python=False)
            assert installed.package_id == "vendor.lifecycle"
            assert library.has_library_function("vendor_value")
            compile_group(
                'from functions import vendor_value\nx = vendor_value(2.0)\noutput("Value", x)',
                "NFTest_vendor_lifecycle_directory",
            )

            source_v2 = tmp / "source_v2"
            _make_package(source_v2, version="2.0.0", value=2.0)
            archive = tmp / "vendor.lifecycle.zip"
            with zipfile.ZipFile(archive, "w") as zf:
                for path in source_v2.rglob("*"):
                    if path.is_file():
                        zf.write(path, Path("vendor.lifecycle") / path.relative_to(source_v2))
            replaced = packages.install_package_zip(archive, allow_python=False, replace=True)
            assert replaced.version == "2.0.0"
            assert packages.load_package_state()["packages"]["vendor.lifecycle"]["installed_version"] == "2.0.0"

            packages.uninstall_package("vendor.lifecycle")
            assert not library.has_library_function("vendor_value")
            expect_compile_error(
                'from functions import vendor_value\nx = vendor_value(2.0)\noutput("Value", x)',
                "NFTest_vendor_lifecycle_uninstalled",
            )

            packages.install_package_directory(source_v1, allow_python=False)
            assert library.has_library_function("vendor_value")
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()


def test_invalid_package_record_can_be_removed_without_deleting_other_files():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        inventory = tmp / "inventory"
        packages.set_packages_dir_for_tests(inventory)
        try:
            source = tmp / "source"
            _make_package(source)
            packages.install_package_directory(source, allow_python=False)

            protected = tmp / "protected"
            protected.mkdir()
            marker = protected / "marker.txt"
            marker.write_text("keep", encoding="utf-8")

            state = packages.load_package_state()
            state["packages"]["vendor.lifecycle"]["installed_path"] = "../protected"
            packages.save_package_state(state)

            packages.uninstall_package("vendor.lifecycle")
            assert "vendor.lifecycle" not in packages.load_package_state()["packages"]
            assert marker.read_text(encoding="utf-8") == "keep"
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()
