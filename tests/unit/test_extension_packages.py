"""Installer regression coverage for v2 declaration normalization and collisions."""

from __future__ import annotations

import builtins
import json
from pathlib import Path

import pytest

from NodeForge import packages
from NodeForge.systems import registry as systems_registry

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_packages(tmp_path):
    """Use one empty package inventory for every installer fixture."""
    packages.set_packages_dir_for_tests(tmp_path / "inventory")
    systems_registry.invalidate_cache()
    yield
    packages.set_packages_dir_for_tests(None)
    systems_registry.invalidate_cache()


def _manifest(root: Path, package_id: str, *, systems: bool = False, functions: bool = False) -> None:
    """Write one package manifest with the requested public content roots."""
    contents = {}
    if systems:
        contents["systems"] = "systems"
    if functions:
        contents["functions"] = "functions"
    root.mkdir(parents=True, exist_ok=True)
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": package_id,
                "name": package_id,
                "version": "1.0.0",
                "author": "Tests",
                "description": "declarative extension installer fixture",
                "nodeforge_min_version": "0.59.0",
                "nodeforge_max_version": "0.62.2",
                "contents": contents,
                "permissions": {"python": True},
            }
        ),
        encoding="utf-8",
    )


def _system(root: Path, public_name: str) -> None:
    """Create one v2 system exporting exactly one runtime Float callable."""
    owner = root / "systems" / "demo"
    owner.mkdir(parents=True, exist_ok=True)
    (owner / "interface.py").write_text(
        f"""
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {{{public_name!r}: ".operations:build"}}
def {public_name}(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
        encoding="utf-8",
    )
    (owner / "operations.py").write_text("def build(context, value): return value\n", encoding="utf-8")


def test_native_library_interface_executes_once_during_candidate_install(tmp_path):
    """Collision validation reuses the candidate's retained normalized v2 inventory."""
    marker = "_nodeforge_stage32_install_native_hits"
    if hasattr(builtins, marker):
        delattr(builtins, marker)
    source = tmp_path / "source"
    _manifest(source, "vendor.native", functions=True)
    owner = source / "functions" / "foo"
    owner.mkdir(parents=True)
    (owner / "interface.py").write_text(
        f"""
import builtins
from typing import Annotated
from NodeForge import EvaluationMode, Float
builtins.{marker} = getattr(builtins, {marker!r}, 0) + 1
EXTENSION_API = 2
EXTENSIONS = {{"foo": ".operations:build"}}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
        encoding="utf-8",
    )
    (owner / "operations.py").write_text("def build(context, value): return value\n", encoding="utf-8")

    packages.install_package_directory(source, allow_python=True)

    try:
        assert getattr(builtins, marker) == 1
    finally:
        if hasattr(builtins, marker):
            delattr(builtins, marker)


def test_retained_system_declaration_validator_does_not_execute_v2_interface(tmp_path):
    """Active-package validation remains v1-only and never executes v2 Python."""
    marker = "_nodeforge_stage32_v1_validator_v2_hits"
    if hasattr(builtins, marker):
        delattr(builtins, marker)
    source = tmp_path / "source"
    _manifest(source, "vendor.structural", systems=True)
    owner = source / "systems" / "demo"
    owner.mkdir(parents=True, exist_ok=True)
    (owner / "interface.py").write_text(
        f"import builtins\nbuiltins.{marker} = getattr(builtins, {marker!r}, 0) + 1\n"
        "raise RuntimeError('v2 interface must not execute here')\n",
        encoding="utf-8",
    )

    manifest = packages.validate_package_root(source)
    packages.validate_package_system_declarations(manifest)

    assert not hasattr(builtins, marker)


def test_candidate_function_collides_with_existing_v2_system_inventory(tmp_path):
    """Existing installed v2 system names participate in candidate collision validation."""
    first = tmp_path / "first"
    _manifest(first, "vendor.system", systems=True)
    _system(first, "taken")
    packages.install_package_directory(first, allow_python=True)

    second = tmp_path / "second"
    _manifest(second, "vendor.function", functions=True)
    (second / "functions").mkdir(parents=True, exist_ok=True)
    (second / "functions" / "taken.nf").write_text("output(1.0)\n", encoding="utf-8")

    with pytest.raises(packages.PackageError, match="Public name collision 'taken'"):
        packages.install_package_directory(second, allow_python=True)
