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
        check(not library.has_module_library_function('flat_legacy_probe'), 'legacy flat function module was discovered')
        check(not library.has_library_function('flat_legacy_probe'), 'legacy flat function appeared as library function')
        check(not library.has_library_function('library_flat_probe'), 'legacy flat .nf function appeared without package inventory')
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
            check(library.has_library_function('library_flat_probe'), 'package flat .nf function was not discovered')
            names = library.library_function_names()
            check('library_flat_probe' in names, 'package flat .nf function missing from public function names')
            compile_group('from functions import library_flat_probe\nx = library_flat_probe(3)\noutput("x", x)', 'NFTest_flat_function_import')
            compile_group('from functions import *\nx = library_flat_probe(3)\noutput("x", x)', 'NFTest_flat_function_star_import')
            compile_group('from functions import library_vector_probe\nv = library_vector_probe(vector(1, 2, 3))\noutput("v", v)', 'NFTest_flat_vector_function_import')
            expect_compile_error('from functions import library_vector_probe\nv = library_vector_probe(3)\noutput("v", v)', 'NFTest_flat_vector_function_scalar_rejected')
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
