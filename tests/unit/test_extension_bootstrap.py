"""Root-bootstrap regression coverage for v2 package admission and deferred failures."""

from __future__ import annotations

import json
import sys
import types
from pathlib import Path

import pytest

from NodeForge import packages
from NodeForge.errors import CompileError
from NodeForge.resolved_environment import resolve_environment
from NodeForge.systems import registry as systems_registry

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def _isolated_packages(tmp_path, monkeypatch):
    """Run every bootstrap fixture against one isolated installed-package inventory."""
    fake_bpy = types.ModuleType("bpy")
    fake_bpy.utils = types.SimpleNamespace(
        user_resource=lambda *_args, **_kwargs: str(tmp_path / "blender-user")
    )
    fake_bpy.data = types.SimpleNamespace(node_groups=())
    monkeypatch.setitem(sys.modules, "bpy", fake_bpy)
    loaded_library = sys.modules.get("NodeForge.library")
    if loaded_library is not None:
        monkeypatch.setattr(loaded_library, "bpy", fake_bpy, raising=False)
    packages.set_packages_dir_for_tests(tmp_path / "inventory")
    systems_registry.invalidate_cache()
    yield
    packages.set_packages_dir_for_tests(None)
    systems_registry.invalidate_cache()


def _write_manifest(root: Path, package_id: str) -> None:
    """Write one Python-enabled package manifest compatible with this release."""
    root.mkdir(parents=True, exist_ok=True)
    (root / "nodeforge_package.json").write_text(
        json.dumps(
            {
                "schema_version": 1,
                "id": package_id,
                "name": package_id,
                "version": "1.0.0",
                "author": "Tests",
                "description": "Stage 32 bootstrap fixture",
                "nodeforge_min_version": "0.59.0",
                "nodeforge_max_version": "0.59.0",
                "contents": {"systems": "systems", "functions": "functions"},
                "permissions": {"python": True},
            }
        ),
        encoding="utf-8",
    )


def _write_system(root: Path, public_name: str = "sys_value") -> None:
    """Create one valid v2 system owner."""
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
    (owner / "operations.py").write_text(
        "def build(context, value):\n    return value\n",
        encoding="utf-8",
    )


def _write_library(root: Path, name: str = "lib_value") -> None:
    """Create one valid native-only v2 library owner."""
    owner = root / "functions" / name
    owner.mkdir(parents=True, exist_ok=True)
    (owner / "interface.py").write_text(
        f"""
from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {{{name!r}: ".operations:build"}}
def {name}(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
""",
        encoding="utf-8",
    )
    (owner / "operations.py").write_text(
        "def build(context, value):\n    return value\n",
        encoding="utf-8",
    )


def _installed_root(package_id: str) -> Path:
    """Return the validated installed directory selected by package state."""
    state = packages.load_package_state()
    return (packages.packages_dir() / state["packages"][package_id]["installed_path"]).resolve()


def test_invalid_system_suppresses_deferred_broken_library_owner(tmp_path):
    """A rejected package discards package-library snapshot failures before catalog replay."""
    source = tmp_path / "source"
    _write_manifest(source, "vendor.atomic")
    _write_system(source)
    _write_library(source)
    packages.install_package_directory(source, allow_python=True)

    installed = _installed_root("vendor.atomic")
    (installed / "systems" / "demo" / "interface.py").write_text(
        "EXTENSION_API = 2\nEXTENSIONS = {}\n",
        encoding="utf-8",
    )
    library_owner = installed / "functions" / "lib_value"
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    (library_owner / "bad.py").symlink_to(outside)

    environment = resolve_environment()

    assert environment.system_names() == ()
    assert environment.catalog("functions").names() == frozenset()


def test_admitted_package_replays_deferred_library_snapshot_failure(tmp_path):
    """A library snapshot failure becomes observable once its package is admitted."""
    source = tmp_path / "source"
    _write_manifest(source, "vendor.replay")
    _write_system(source)
    _write_library(source)
    packages.install_package_directory(source, allow_python=True)

    installed = _installed_root("vendor.replay")
    library_owner = installed / "functions" / "lib_value"
    outside = tmp_path / "outside.py"
    outside.write_text("value = 1\n", encoding="utf-8")
    (library_owner / "bad.py").symlink_to(outside)

    environment = resolve_environment()

    assert "sys_value" in environment.system_names()
    with pytest.raises(CompileError, match="symlink"):
        environment.catalog("functions").names()
