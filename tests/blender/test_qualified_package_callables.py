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


def test_qualified_duplicate_members_materialize_exact_package_owners():
    """Qualified duplicate member names retain canonical package ownership in Blender."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        packages.set_packages_dir_for_tests(tmp / "inventory")
        try:
            first = tmp / "vendor.a"
            second = tmp / "vendor.b"
            _write_source_package(
                first,
                "vendor.a",
                {"foo": 'value = input_float("Value")\noutput("Value", value + 1.0)\n'},
            )
            _write_source_package(
                second,
                "vendor.b",
                {"foo": 'value = input_float("Value")\noutput("Value", value + 2.0)\n'},
            )
            packages.install_package_directory(first, allow_python=False)
            packages.install_package_directory(second, allow_python=False)

            group = compile_group(
                "from packages import a, b\n"
                "x = input_float('X')\n"
                "left = a.foo(x)\n"
                "right = b.foo(x)\n"
                "output('Value', left + right)\n",
                "NFTest_qualified_duplicate_owners",
            )
            calls = _package_call_groups(group, "foo")
            owners = sorted(str(call.get("nodeforge_package_id") or "") for call in calls)
            check(owners == ["vendor.a", "vendor.b"], f"qualified owners collapsed: {owners}")

            expect_compile_error(
                "from packages import a, b\n"
                "x = input_float('X')\n"
                "output('Value', foo(x))\n",
                "NFTest_qualified_duplicate_bare_ambiguous",
            )
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()


def test_core_points_and_owner_qualified_package_points_materialize_independently():
    """A package export named points coexists with the reserved bare core builtin."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        packages.set_packages_dir_for_tests(tmp / "inventory")
        try:
            root = tmp / "nodeforge.lsystem"
            _write_source_package(
                root,
                "nodeforge.lsystem",
                {"points": 'value = input_float("Value")\noutput("Value", value + 1.0)\n'},
            )
            packages.install_package_directory(root, allow_python=False)
            group = compile_group(
                "from packages import lsystem\n"
                "count = input_int('Count', default=2)\n"
                "x = input_float('X')\n"
                "core_geo = points(count)\n"
                "package_value = lsystem.points(x)\n"
                "output('Geometry', core_geo)\n"
                "output('Package Value', package_value)\n",
                "NFTest_core_and_package_points",
            )
            geometry_output = next(
                item for item in group.interface.items_tree
                if getattr(item, "in_out", None) == "OUTPUT" and item.name == "Geometry"
            )
            check(geometry_output.socket_type == "NodeSocketGeometry", "bare points() stopped resolving to core Geometry")
            calls = _package_call_groups(group, "points")
            check(len(calls) == 1, f"expected one qualified package points call, got {len(calls)}")
            check(calls[0].get("nodeforge_package_id") == "nodeforge.lsystem", "qualified points lost package owner")
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()


def test_qualified_package_call_in_local_helper_preserves_only_real_runtime_capture():
    """Package qualifiers are not hidden inputs while ordinary argument captures remain so."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        packages.set_packages_dir_for_tests(tmp / "inventory")
        try:
            root = tmp / "nodeforge.math"
            _write_source_package(
                root,
                "nodeforge.math",
                {"sin": 'value = input_float("Value")\noutput("Value", value)\n'},
            )
            packages.install_package_directory(root, allow_python=False)
            group = compile_group(
                "from packages import math as m\n"
                "offset = input_float('Offset')\n"
                "x = input_float('X')\n"
                "def helper(value):\n"
                "    return m.sin(value + offset)\n"
                "output('Value', helper(x))\n",
                "NFTest_qualified_local_capture",
            )
            helpers = [
                node.node_tree
                for node in group.nodes
                if node.bl_idname == "GeometryNodeGroup"
                and node.node_tree is not None
                and node.node_tree.get("nodeforge_local_function_name") == "helper"
            ]
            check(len(helpers) == 1, f"expected one local helper, got {len(helpers)}")
            helper = helpers[0]
            inputs = [
                item.name
                for item in helper.interface.items_tree
                if getattr(item, "in_out", None) == "INPUT"
            ]
            check(len(inputs) == 2, f"package alias became a hidden input: {inputs}")
            check(any(name == "offset" or "offset" in name.lower() for name in inputs), f"runtime capture missing: {inputs}")
            package_calls = _package_call_groups(helper, "sin")
            check(len(package_calls) == 1, "qualified package call was not materialized inside local helper")
            check(package_calls[0].get("nodeforge_package_id") == "nodeforge.math", "local qualified call lost canonical owner")
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()


def test_source_value_collision_rejects_bare_call_but_qualified_call_materializes():
    """A source value owns its bare name while qualification remains an explicit escape hatch."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        packages.set_packages_dir_for_tests(tmp / "inventory")
        try:
            root = tmp / "nodeforge.math"
            _write_source_package(
                root,
                "nodeforge.math",
                {"sin": 'value = input_float("Value")\noutput("Value", value)\n'},
            )
            packages.install_package_directory(root, allow_python=False)
            expect_compile_error(
                "from packages import math\n"
                "sin = input_float('Sin')\n"
                "x = input_float('X')\n"
                "output('Value', sin(x))\n",
                "NFTest_bare_source_value_collision",
            )
            group = compile_group(
                "from packages import math\n"
                "sin = input_float('Sin')\n"
                "x = input_float('X')\n"
                "output('Value', math.sin(x))\n",
                "NFTest_qualified_source_value_collision",
            )
            calls = _package_call_groups(group, "sin")
            check(len(calls) == 1, "qualified call did not materialize after source-value collision")
            check(calls[0].get("nodeforge_package_id") == "nodeforge.math", "qualified collision escape lost package owner")
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()
