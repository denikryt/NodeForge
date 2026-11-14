from helpers import *

import json
import tempfile
from pathlib import Path

from NodeForge import packages

from NodeForge.function_instances import (
    FUNCTION_INSTANCE_KEY_PROP,
    FUNCTION_ROOT_OWNER_ID_PROP,
)


def _local_helpers(function_name):
    return [
        group for group in bpy.data.node_groups
        if group.get("nodeforge_generated_kind") == "local_function_helper"
        and group.get("nodeforge_local_function_name") == function_name
    ]


def _group_nodes(group):
    return [node for node in group.nodes if getattr(node, "bl_idname", None) == "GeometryNodeGroup"]










def test_unique_modifier_rejects_unsupported_calls():
    cases = (
        'x = sin(0.5, __unique__=False)\noutput("x", x)\n',
        'x = sin(0.5, __unique__=True)\noutput("x", x)\n',
        'x = vector(1, 2, 3, __unique__=True)\noutput("x", x)\n',
        'x = node("ShaderNodeValue", __unique__=True, output="Value", typ=Float)\noutput("x", x)\n',
    )
    for index, source in enumerate(cases):
        expect_compile_error(source, f"NFTest_unique_unsupported_{index}")








def _write_imported_dependency_package(root: Path, *, version: str, offset: float) -> None:
    """Write one source-only imported dependency package for freshness tests."""
    functions = root / "functions"
    functions.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "id": "vendor.imported_dependency",
        "name": "vendor.imported_dependency",
        "version": version,
        "author": "Tests",
        "description": "Imported cache-hit dependency regression fixture.",
        "nodeforge_min_version": "0.50.0",
        "nodeforge_max_version": None,
        "contents": {"functions": "functions"},
        "permissions": {"python": False},
    }
    (root / "nodeforge_package.json").write_text(
        json.dumps(manifest), encoding="utf-8"
    )
    (functions / "imported_dependency_probe.nf").write_text(
        'value = input_float("Value")\n'
        f'output("Value", value + {offset})\n',
        encoding="utf-8",
    )
