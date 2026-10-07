"""Incremental physical ownership contract for NodeForge module organization."""

from pathlib import Path


PACKAGE_ROOT = Path(__file__).resolve().parents[2]


def _assert_paths_exist(paths: set[str]) -> None:
    """Require every canonical path in *paths* to exist."""
    missing = sorted(path for path in paths if not (PACKAGE_ROOT / path).exists())
    assert not missing, f"Missing canonical module paths: {missing}"


def _assert_paths_absent(paths: set[str]) -> None:
    """Require every historical path in *paths* to be absent."""
    present = sorted(path for path in paths if (PACKAGE_ROOT / path).exists())
    assert not present, f"Historical module paths still present: {present}"


def test_owner_package_scaffolding_exists():
    """Positive: the three internal owner packages exist before module cutover."""
    _assert_paths_exist({"semantic/__init__.py", "extensions/__init__.py", "blender/__init__.py"})


def test_owner_package_initializers_remain_minimal():
    """Negative: package initializers must not recreate aggregate compatibility facades."""
    for relative in ("semantic/__init__.py", "extensions/__init__.py", "blender/__init__.py"):
        source = (PACKAGE_ROOT / relative).read_text(encoding="utf-8")
        assert "import *" not in source
        assert "__all__ =" not in source


def test_catalog_environment_and_local_owners_are_canonical():
    """Positive: discovery, Local persistence, environment construction and physical groups are split."""
    _assert_paths_exist({
        "catalog.py", "local_sources.py", "resolved_environment.py", "environment_resolution.py",
        "blender/library_groups.py",
    })


def test_mixed_library_owner_path_is_absent():
    """Negative: the old mixed library owner cannot survive as a forwarding module."""
    _assert_paths_absent({"library.py"})
