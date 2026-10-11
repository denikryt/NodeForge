"""Tests for local test import bootstrap behavior."""

from pathlib import Path
import os
import subprocess
import sys


def test_repository_root_is_the_nodeforge_package_directory():
    """The local test commands use the real NodeForge package directory."""
    repo_root = Path(__file__).resolve().parents[2]
    assert repo_root.name == "NodeForge"
    assert (repo_root / "__init__.py").exists()
    assert not (repo_root / "NodeForge.py").exists()


def test_test_bootstrap_exposes_package_parent():
    """Pytest bootstrap should expose the package parent without a local import shim."""
    repo_root = Path(__file__).resolve().parents[2]
    package_parent = repo_root.parent
    assert str(package_parent) in sys.path


def test_nodeforge_import_uses_package_init_from_repo_root():
    """Local tests should import the real package entrypoint, not a sibling shim module."""
    import NodeForge

    repo_root = Path(__file__).resolve().parents[2]
    assert Path(NodeForge.__file__).resolve() == repo_root / "__init__.py"


def test_removed_systems_package_is_not_a_supported_import_surface():
    """The historical systems namespace is absent while the root package remains importable."""
    repo_root = Path(__file__).resolve().parents[2]
    script = (
        "import importlib.util\n"
        "import NodeForge\n"
        "assert importlib.util.find_spec('NodeForge.systems') is None\n"
        "print('NODEFORGE_PURE_IMPORTS_OK')\n"
    )
    result = subprocess.run(
        [sys.executable, "-c", script],
        cwd=repo_root,
        env={**os.environ, "PYTHONPATH": str(repo_root.parent)},
        check=True,
        text=True,
        capture_output=True,
    )
    assert result.stdout.strip() == "NODEFORGE_PURE_IMPORTS_OK"
