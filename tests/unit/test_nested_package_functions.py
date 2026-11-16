"""Nested Functions structural inventory and validation regression tests."""

from pathlib import Path

import pytest

from NodeForge.package_function_index import scan_package_functions
from NodeForge.errors import CompileError
from NodeForge import catalog, packages


def _tree(root: Path, files: dict[str, str]) -> Path:
    """Create a physical Functions tree from relative fixture paths."""
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
    return root


def test_nested_source_modules_and_same_stem_directory_are_all_discovered(tmp_path):
    """A folder and module sharing a prefix are distinct owners."""
    root = _tree(tmp_path / "functions", {
        "mesh/primitives.nf": "def cube(x: Float):\n    return x\n",
        "mesh/primitives/detail.nf": "def cube(x: Float):\n    return x\n",
        "mesh/ops/modifiers/complex/merge.nf": "def merge(x: Float):\n    return x\n",
        "mesh/legacy/source.nf": "def old(x: Float):\n    return x\n",
        "mesh/native/interface.py": "EXTENSIONS = {}\n",
        "mesh/native/backend.py": "pass\n",
        "_private/hidden.nf": "def hidden():\n    return 1\n",
    })
    owners = scan_package_functions(root)
    assert [(owner.canonical_owner, owner.kind) for owner in owners] == [
        ("mesh.legacy", "script"),
        ("mesh.native", "extension"),
        ("mesh.ops.modifiers.complex.merge", "script"),
        ("mesh.primitives", "script"),
        ("mesh.primitives.detail", "script"),
    ]
    library_root = packages.LibraryRoot(
        path=root, package_id="vendor.test", package_name="Test", package_version="1.0",
        namespace="functions", origin="user",
    )
    discovered = catalog.candidate_records_from_inputs(
        "functions", package_roots=(library_root,), include_builtin_catalog=False,
    )
    assert len(discovered) == 5
    assert {item.folder_path for item in discovered} == {
        "mesh", "mesh/ops/modifiers/complex", "mesh/primitives",
    }


@pytest.mark.parametrize("invalid", ["mesh-bad/read.nf", "mesh/class.nf", "mesh/something-bad.nf"])
def test_public_namespace_components_are_validated_at_scan_boundary(tmp_path, invalid):
    """Publicly named invalid paths must fail before package publication."""
    root = _tree(tmp_path / "functions", {invalid: "def ok():\n    return 1\n"})
    with pytest.raises(CompileError, match="Invalid package Functions namespace component"):
        scan_package_functions(root)


def test_symlink_and_hybrid_owners_are_rejected(tmp_path):
    """Owner source paths cannot escape or combine native and source models."""
    root = _tree(tmp_path / "functions", {"mesh/ok.nf": "def ok():\n    return 1\n"})
    (root / "mesh" / "link.nf").symlink_to(root / "mesh" / "ok.nf")
    with pytest.raises(CompileError, match="symlink"):
        scan_package_functions(root)
    (root / "mesh" / "link.nf").unlink()
    _tree(root, {"hybrid/source.nf": "def ok():\n    return 1\n",
                 "hybrid/interface.py": "EXTENSIONS = {}\n"})
    with pytest.raises(CompileError, match="mixed source.nf and interface.py"):
        scan_package_functions(root)


def test_legacy_owner_backends_are_not_publicly_discovered(tmp_path):
    """Backend modules are implementation details, not extra package sources."""
    root = _tree(tmp_path / "functions", {
        "native/interface.py": "EXTENSIONS = {}\n",
        "native/backend/extra.nf": "def leaked():\n    return 1\n",
        "legacy/source.nf": "def old():\n    return 1\n",
        "legacy/hidden.nf": "def hidden():\n    return 1\n",
    })
    assert [owner.canonical_owner for owner in scan_package_functions(root)] == ["legacy", "native"]
