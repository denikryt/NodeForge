"""Blender integration regressions for qualified package-callable package callable namespaces."""

from helpers import *

import json
import tempfile
from pathlib import Path

from NodeForge import packages


def _write_source_package(root: Path, package_id: str, functions: dict[str, str]) -> None:
    """Write one source-only package with the provided public function sources."""
    function_dir = root / "functions"
    function_dir.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "id": package_id,
        "name": package_id,
        "version": "1.0.0",
        "author": "Tests",
        "description": "qualified package-callable Blender namespace regression fixture.",
        "nodeforge_min_version": "0.65.0",
        "nodeforge_max_version": None,
        "contents": {"functions": "functions"},
        "permissions": {"python": False},
    }
    (root / "nodeforge_package.json").write_text(json.dumps(manifest), encoding="utf-8")
    for name, source in functions.items():
        (function_dir / f"{name}.nf").write_text(source, encoding="utf-8")


def _package_call_groups(root_group, member: str):
    """Return materialized source-call groups for one public member name."""
    return [
        node.node_tree
        for node in root_group.nodes
        if node.bl_idname == "GeometryNodeGroup"
        and node.node_tree is not None
        and node.node_tree.get("nodeforge_library_name") == member
    ]
