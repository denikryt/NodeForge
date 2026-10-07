from helpers import *

import json
import tempfile
from pathlib import Path

from NodeForge import packages




def _write_package_manifest(root: Path, package_id: str, *, contents=None, python=False):
    payload = {
        "schema_version": 1,
        "id": package_id,
        "name": package_id,
        "version": "1.0.0",
        "author": "Tests",
        "description": "Blender package regression fixture.",
        "nodeforge_min_version": "0.49.47",
        "nodeforge_max_version": None,
        "contents": contents or {"functions": "functions"},
        "permissions": {"python": bool(python)},
    }
    (root / "nodeforge_package.json").write_text(json.dumps(payload), encoding="utf-8")


def test_packaged_library_functions_and_helper_scoping():
    flat_probe = ROOT / 'functions' / 'flat_legacy_probe.py'
    flat_source = ROOT / 'functions' / 'library_flat_probe.nf'
    flat_probe.parent.mkdir(parents=True, exist_ok=True)
    flat_probe.write_text("def compile_call(comp, expr, depth=0):\n    raise AssertionError('legacy flat layout loaded')\n", encoding='utf-8')
    flat_source.write_text('value = input_float("Value", default=2.0)\noutput("Value", value)\n', encoding='utf-8')
    try:
        check(not has_native_package_function('flat_legacy_probe'), 'legacy flat function module was discovered')
        check(not has_package_function('flat_legacy_probe'), 'legacy flat function appeared as library function')
        check(not has_package_function('library_flat_probe'), 'legacy flat .nf function appeared without package inventory')
    finally:
        flat_probe.unlink(missing_ok=True)
        flat_source.unlink(missing_ok=True)

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'vendor.library_probe'
        functions = root / 'functions'
        functions.mkdir(parents=True)
        _write_package_manifest(root, 'vendor.library_probe')
        (functions / 'library_flat_probe.nf').write_text('value = input_float("Value", default=2.0)\noutput("Value", value)\n', encoding='utf-8')
        (functions / 'library_vector_probe.nf').write_text('value = input_vector("Value", default=vector(1, 2, 3))\noutput("Value", value)\n', encoding='utf-8')
        packages.install_package_directory(root, allow_python=False)
        try:
            check(has_package_function('library_flat_probe'), 'package flat .nf function was not discovered')
            names = package_function_names()
            check('library_flat_probe' in names, 'package flat .nf function missing from public function names')
            compile_group('from packages import library_probe\nx = library_flat_probe(3)\noutput("x", x)', 'NFTest_flat_function_import')
            expect_compile_error('from packages import *\nx = library_flat_probe(3)\noutput("x", x)', 'NFTest_package_star_import_rejected')
            compile_group('from packages import library_probe\nv = library_vector_probe(vector(1, 2, 3))\noutput("v", v)', 'NFTest_flat_vector_function_import')
            expect_compile_error('from packages import library_probe\nv = library_vector_probe(3)\noutput("v", v)', 'NFTest_flat_vector_function_scalar_rejected')
        finally:
            packages.uninstall_package('vendor.library_probe')

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / 'vendor.private_probe'
        functions = root / 'functions'
        functions.mkdir(parents=True)
        _write_package_manifest(root, 'vendor.private_probe')
        (functions / '_library_private_probe.nf').write_text('value = input_float("Value", default=2.0)\noutput("Value", value)\n', encoding='utf-8')
        try:
            packages.install_package_directory(root, allow_python=False)
        except packages.PackageError:
            pass
        else:
            raise AssertionError('private package function entry was accepted')
    print('LIBRARY_AND_SCOPE_OK')



def test_same_named_package_functions_materialize_by_canonical_owner():
    """Qualified source calls keep exact package ownership through Blender materialization."""
    with tempfile.TemporaryDirectory() as tmp:
        roots = []
        for package_id, delta in (("vendor.alpha", 1.0), ("vendor.beta", 2.0)):
            root = Path(tmp) / package_id
            functions = root / "functions"
            functions.mkdir(parents=True)
            _write_package_manifest(root, package_id)
            (functions / "shared_probe.nf").write_text(
                f'value = input_float("Value", default=0.0)\noutput("Value", value + {delta})\n',
                encoding="utf-8",
            )
            packages.install_package_directory(root, allow_python=False)
            roots.append(package_id)
        try:
            group = compile_group(
                "from packages import alpha, beta\n"
                "a = alpha.shared_probe(3)\n"
                "b = beta.shared_probe(4)\n"
                'output("a", a)\n'
                'output("b", b)\n',
                "NFTest_same_named_package_owners",
            )
            realized = {
                (node.node_tree.get("nodeforge_package_id"), node.node_tree.get("nodeforge_library_name"))
                for node in group.nodes
                if node.bl_idname == "GeometryNodeGroup" and node.node_tree is not None
            }
            check(
                {("vendor.alpha", "shared_probe"), ("vendor.beta", "shared_probe")} <= realized,
                f"qualified calls lost canonical package ownership during materialization: {sorted(realized)}",
            )

            with pytest.raises(CompileError, match=r"Ambiguous callable 'shared_probe'.*alpha\.shared_probe.*beta\.shared_probe"):
                prepared_create_or_update(
                    "from packages import alpha, beta\n"
                    "value = shared_probe(3)\n"
                    'output("Value", value)\n',
                    "NFTest_same_named_package_bare_ambiguity",
                )
        finally:
            for package_id in reversed(roots):
                packages.uninstall_package(package_id)



def test_package_function_can_share_core_callable_name_without_shadowing_core():
    """A core-spelled package export stays qualified while the bare core builtin remains authoritative."""
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "vendor.geometry_tools"
        functions = root / "functions"
        functions.mkdir(parents=True)
        _write_package_manifest(root, "vendor.geometry_tools")
        (functions / "points.nf").write_text(
            'value = input_float("Value", default=0.0)\noutput("Value", value + 1.0)\n',
            encoding="utf-8",
        )
        packages.install_package_directory(root, allow_python=False)
        try:
            group = compile_group(
                "from packages import geometry_tools\n"
                "geo = points(3)\n"
                "value = geometry_tools.points(4)\n"
                'output("Geometry", geo)\n'
                'output("Value", value)\n',
                "NFTest_core_and_package_points",
            )
            check(
                any(node.bl_idname == "GeometryNodeMeshLine" for node in group.nodes),
                "bare points() stopped lowering through the core builtin",
            )
            check(
                any(
                    node.bl_idname == "GeometryNodeGroup"
                    and node.node_tree is not None
                    and node.node_tree.get("nodeforge_package_id") == "vendor.geometry_tools"
                    and node.node_tree.get("nodeforge_library_name") == "points"
                    for node in group.nodes
                ),
                "qualified package points() did not materialize its exact owner",
            )
        finally:
            packages.uninstall_package("vendor.geometry_tools")
