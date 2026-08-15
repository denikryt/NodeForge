from helpers import *

import json
import tempfile
from pathlib import Path

from NodeForge import packages


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _cleanup(path):
    if path.is_file():
        path.unlink(missing_ok=True)
    elif path.is_dir():
        for child in sorted(path.rglob('*'), reverse=True):
            if child.is_file():
                child.unlink(missing_ok=True)
            elif child.is_dir():
                child.rmdir()
        path.rmdir()


def test_catalog_discovery_and_example_imports():
    check(library.has_library_entry('functions', 'layout_circle'), 'layout_circle missing from functions')
    check(library.has_library_entry('examples', 'mandelbrot'), 'mandelbrot missing from examples')
    check(bool(library.backend_builtins_for_entry('examples', 'mandelbrot')), 'mandelbrot backend helpers missing')

    compile_group('from functions import layout_circle\ngeo = points(8)\ngeo = layout_circle(geo, count=8)\noutput("Geometry", geo)', 'NFTest_catalog_function_import')
    compile_group('from examples import *\ngeo = mandelbrot(resolution=12, max_iter=8)\noutput("Geometry", geo)', 'NFTest_catalog_example_star_import')

def test_local_recursive_catalog_duplicate_and_save_contracts():
    local = library.ensure_local_catalog_dir()
    flat = local / 'local_local_probe.nf'
    nested = local / 'math' / 'local_nested_probe.nf'
    ignored_native_folder = local / 'local_native_probe'
    unsupported_source_layout = local / 'local_source_layout_probe'
    duplicate_flat = local / 'local_duplicate_probe.nf'
    duplicate_nested = local / 'math' / 'local_duplicate_probe.nf'
    cross_folder = local / 'math' / 'local_cross_folder_duplicate.nf'
    cross_folder_root = local / 'local_cross_folder_duplicate.nf'
    saved = local / 'local_saved_text_probe.nf'
    nested_saved = local / 'math' / 'local_nested_saved_probe.nf'
    try:
        _write(flat, 'x = input_float("X", default=1.0)\noutput("x", x)\n')
        _write(nested, 'x = input_float("X", default=1.0)\noutput("x", x)\n')
        _write(ignored_native_folder / 'function.py', 'raise AssertionError("local function.py executed")\n')
        _write(ignored_native_folder / 'backend.py', 'raise AssertionError("local backend.py executed")\n')

        compile_group('from local import local_local_probe\nx = local_local_probe(3)\noutput("x", x)', 'NFTest_local_flat_import')
        compile_group('from local import local_nested_probe\nx = local_nested_probe(3)\noutput("x", x)', 'NFTest_local_nested_import')
        expect_compile_error('from local import local_native_probe\nx = local_native_probe(3)\noutput("x", x)', 'NFTest_local_native_files_ignored')
        expect_compile_error('from local.math import local_nested_probe\nx = 1\noutput("x", x)', 'NFTest_local_nested_import_rejected')
        expect_compile_error('import local.math\nx = 1\noutput("x", x)', 'NFTest_local_nested_plain_import_rejected')

        _write(duplicate_flat, 'x = input_float("X")\noutput("x", x)\n')
        _write(duplicate_nested, 'x = input_float("X")\noutput("x", x)\n')
        try:
            library.library_entry_names('local')
        except CompileError:
            pass
        else:
            raise AssertionError('duplicate local entries were accepted')
        duplicate_flat.unlink(missing_ok=True)
        _cleanup(duplicate_nested)

        path = library.save_local_source('local_saved_text_probe', 'x = input_float("X")\noutput("x", x)\n')
        check(path == saved and saved.exists(), 'flat local save failed')
        try:
            library.save_local_source('local_saved_text_probe', 'x = 2\noutput("x", x)\n', overwrite=False)
        except CompileError:
            pass
        else:
            raise AssertionError('duplicate local save was accepted')
        library.save_local_source('local_saved_text_probe', 'x = 2\noutput("x", x)\n', overwrite=True)
        check(saved.read_text(encoding='utf-8').startswith('x = 2'), 'overwrite did not replace exact flat file')

        library.create_local_folder('math')
        library.save_local_source('local_nested_saved_probe', 'x = input_float("X")\noutput("x", x)\n', folder_path='math')
        check(nested_saved.exists(), 'nested local save failed')
        compile_group('from local import local_nested_saved_probe\nx = local_nested_saved_probe(1)\noutput("x", x)', 'NFTest_local_nested_saved_import')

        _write(unsupported_source_layout / 'source.nf', 'x = input_float("X")\noutput("x", x)\n')
        try:
            library.library_entry_names('local')
        except CompileError:
            pass
        else:
            raise AssertionError('package-style local source.nf layout was accepted')
        _cleanup(unsupported_source_layout)

        _write(cross_folder, 'x = input_float("X")\noutput("x", x)\n')
        library.save_local_source('local_cross_folder_duplicate', 'x = 2\noutput("x", x)\n')
        check(cross_folder_root.exists(), 'path-addressed save was blocked by same-name managed source elsewhere')
        try:
            library.find_library_entry_record('local', 'local_cross_folder_duplicate')
        except CompileError:
            pass
        else:
            raise AssertionError('duplicate managed public names did not remain ambiguous at language resolution')

        for bad in ('../escape', '/abs', '_private', 'bad-name'):
            try:
                library.create_local_folder(bad)
            except CompileError:
                pass
            else:
                raise AssertionError(f'invalid local folder accepted: {bad}')
    finally:
        for path in (flat, nested, ignored_native_folder, unsupported_source_layout, duplicate_flat, duplicate_nested, cross_folder, cross_folder_root, saved, nested_saved):
            _cleanup(path)
        math = local / 'math'
        if math.exists() and not any(math.iterdir()):
            math.rmdir()


def test_new_catalog_materialized_group_ownership_metadata():
    local = library.ensure_local_catalog_dir()
    source = local / 'local_collision_probe.nf'
    user_group = None
    example_collision = None
    try:
        _write(source, 'x = input_float("X", default=1.0)\noutput("x", x)\n')
        record = library.find_library_entry_record('local', 'local_collision_probe')
        local_group_name = library._group_name_for_record(record)
        user_group = bpy.data.node_groups.new(local_group_name, 'GeometryNodeTree')
        collision_root = compile_group(
            'from local import local_collision_probe\nx = local_collision_probe(1)\noutput("x", x)',
            'NFTest_local_ownership_collision',
        )
        collision_backing = next(
            node.node_tree for node in collision_root.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_collision_probe'
        )
        check(bpy.data.node_groups.get(local_group_name) is user_group, 'Blender suffix allocation mutated the pre-existing user group')
        check(collision_backing is not user_group, 'Local compile reused a foreign group with the logical base name')
        check(collision_backing.name.startswith(local_group_name + '.'), f'Local collision did not receive a Blender suffix: {collision_backing.name}')
        bpy.data.node_groups.remove(user_group, do_unlink=True)
        user_group = None
        group = compile_group('from local import local_collision_probe\nx = local_collision_probe(1)\noutput("x", x)', 'NFTest_local_owned_group_created')
        backing = bpy.data.node_groups.get(local_group_name)
        check(backing is not None, 'local backing group not created')
        check(backing.get('nodeforge_library_namespace') == 'local', 'local backing namespace metadata missing')
        check(backing.get('nodeforge_library_name') == 'local_collision_probe', 'local backing name metadata missing')

        record = library.find_library_entry_record('examples', 'mandelbrot')
        example_group_name = library._group_name_for_record(record)
        existing_example = bpy.data.node_groups.get(example_group_name)
        if existing_example is not None:
            bpy.data.node_groups.remove(existing_example, do_unlink=True)
        example_collision = bpy.data.node_groups.new(example_group_name, 'GeometryNodeTree')
        example_collision['nodeforge_library_namespace'] = 'local'
        example_collision['nodeforge_library_name'] = 'mandelbrot'
        expect_compile_error('from examples import mandelbrot\ngeo = mandelbrot(resolution=12, max_iter=8)\noutput("Geometry", geo)', 'NFTest_example_ownership_collision')
    finally:
        source.unlink(missing_ok=True)
        for group in (user_group, example_collision):
            if group is not None and bpy.data.node_groups.get(group.name) is group:
                bpy.data.node_groups.remove(group, do_unlink=True)


def test_local_save_ui_uses_selected_text_source_only():
    from types import SimpleNamespace
    from NodeForge import ui

    text_source = 'x = input_float("Text", default=1.0)\noutput("x", x)\n'
    text = SimpleNamespace(as_string=lambda: text_source)
    props = SimpleNamespace(text_block=text)
    check(ui._source_from_props(props) == text_source, 'Local Save did not use the selected Text datablock')
    check(ui._source_from_props(SimpleNamespace(text_block=None)) == '', 'Local Save source unexpectedly fell back without a Text datablock')


def test_local_materialization_uses_fresh_blender_suffixed_groups_per_compile():
    local = library.ensure_local_catalog_dir()
    leaf = local / 'local_suffix_leaf.nf'
    parent = local / 'local_suffix_parent.nf'
    root_source = (
        'from local import local_suffix_parent\n'
        'x = local_suffix_parent(2.0)\n'
        'y = local_suffix_parent(3.0)\n'
        'output("x", x)\n'
        'output("y", y)\n'
    )
    try:
        _write(leaf, 'x = input_float("X")\noutput("x", x * 2.0)\n')
        _write(parent, 'from local import local_suffix_leaf\nx = input_float("X")\ny = local_suffix_leaf(x)\noutput("y", y)\n')

        first = compile_group(root_source, 'NFTest_local_suffix_first')
        first_parent_nodes = [
            node for node in first.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_suffix_parent'
        ]
        check(len(first_parent_nodes) == 2, 'expected two calls to the Local parent')
        first_parent = first_parent_nodes[0].node_tree
        check(all(node.node_tree is first_parent for node in first_parent_nodes), 'same Local entry was materialized twice within one build')
        first_leaf = next(
            node.node_tree for node in first_parent.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_suffix_leaf'
        )

        second = compile_group(root_source, 'NFTest_local_suffix_second')
        second_parent = next(
            node.node_tree for node in second.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_suffix_parent'
        )
        second_leaf = next(
            node.node_tree for node in second_parent.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_suffix_leaf'
        )

        check(second_parent is not first_parent, 'new outer compile reused an older Local parent group')
        check(second_leaf is not first_leaf, 'new outer compile reused an older Local leaf group')
        check(first_parent.name == 'NodeForge.local.local_suffix_parent', f'unexpected first Local base name: {first_parent.name}')
        check(second_parent.name.startswith('NodeForge.local.local_suffix_parent.'), f'Blender suffix was not assigned: {second_parent.name}')
        check(first_leaf.name == 'NodeForge.local.local_suffix_leaf', f'unexpected first Local leaf base name: {first_leaf.name}')
        check(second_leaf.name.startswith('NodeForge.local.local_suffix_leaf.'), f'Blender suffix was not assigned to leaf: {second_leaf.name}')

        _write(leaf, 'x = input_float("X")\noutput("x", x * 3.0)\n')
        third = compile_group(root_source, 'NFTest_local_suffix_third')
        third_parent = next(
            node.node_tree for node in third.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_suffix_parent'
        )
        third_leaf = next(
            node.node_tree for node in third_parent.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_suffix_leaf'
        )
        check(third_parent is not second_parent, 'new compile after dependency edit reused previous Local parent')
        check(third_leaf is not second_leaf, 'new compile after dependency edit reused previous Local leaf')
        check(first_parent_nodes[0].node_tree is first_parent, 'older generated group dependency changed in place')
    finally:
        leaf.unlink(missing_ok=True)
        parent.unlink(missing_ok=True)


def test_update_expression_group_keeps_selected_group_identity_with_fresh_local_dependencies():
    local = library.ensure_local_catalog_dir()
    helper = local / 'local_update_identity_probe.nf'
    root_source = (
        'from local import local_update_identity_probe\n'
        'x = local_update_identity_probe(2.0)\n'
        'output("x", x)\n'
    )
    try:
        _write(helper, 'x = input_float("X")\noutput("x", x * 2.0)\n')
        group = compile_group(root_source, 'NFTest_local_update_identity')
        group_pointer = group.as_pointer()
        group_name = group.name
        first_backing = next(
            node.node_tree for node in group.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_update_identity_probe'
        )

        _write(helper, 'x = input_float("X")\noutput("x", x * 3.0)\n')
        compiler.update_expression_group(group, root_source)
        second_backing = next(
            node.node_tree for node in group.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_update_identity_probe'
        )

        check(group.as_pointer() == group_pointer, 'Update Selected semantics replaced the selected group datablock')
        check(group.name == group_name, 'Update Selected semantics changed the selected group name')
        check(second_backing is not first_backing, 'updated selected group did not move to a fresh current Local dependency')
    finally:
        helper.unlink(missing_ok=True)


def test_local_logical_name_collision_uses_blender_suffix_without_mutating_foreign_group():
    local = library.ensure_local_catalog_dir()
    source = local / 'local_suffix_collision_probe.nf'
    foreign = None
    try:
        _write(source, 'x = input_float("X", default=1.0)\noutput("x", x)\n')
        record = library.find_library_entry_record('local', 'local_suffix_collision_probe')
        base_name = library._group_name_for_record(record)
        foreign = bpy.data.node_groups.new(base_name, 'GeometryNodeTree')
        root = compile_group(
            'from local import local_suffix_collision_probe\n'
            'x = local_suffix_collision_probe(1)\n'
            'output("x", x)\n',
            'NFTest_local_suffix_collision',
        )
        backing = next(
            node.node_tree for node in root.nodes
            if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'local_suffix_collision_probe'
        )
        check(bpy.data.node_groups.get(base_name) is foreign, 'fresh Local compile mutated the foreign base-name group')
        check(backing is not foreign, 'fresh Local compile reused the foreign base-name group')
        check(backing.name.startswith(base_name + '.'), f'Blender did not suffix the fresh Local group: {backing.name}')
    finally:
        source.unlink(missing_ok=True)
        if foreign is not None and bpy.data.node_groups.get(foreign.name) is foreign:
            bpy.data.node_groups.remove(foreign, do_unlink=True)


def _library_backing(group, namespace, name):
    """Return the imported catalog backing group used by *group*."""
    return next(
        node.node_tree
        for node in group.nodes
        if getattr(getattr(node, "node_tree", None), "get", lambda *args: None)("nodeforge_library_namespace") == namespace
        and node.node_tree.get("nodeforge_library_name") == name
    )


def test_reload_local_catalog_group_keeps_root_identity_and_refreshes_dependencies():
    """Reload must rebuild Local dependencies even when the root source is unchanged."""
    local = library.ensure_local_catalog_dir()
    helper = local / "local_reload_helper.nf"
    root = local / "local_reload_root.nf"
    group = None
    try:
        _write(helper, 'x = input_float("X", default=1.0)\noutput("Value", x + 1)\n')
        _write(root, 'from local import local_reload_helper\nx = input_float("X", default=1.0)\ny = local_reload_helper(x)\noutput("Value", y)\n')
        group = compiler.create_library_catalog_group("local", "local_reload_root")
        pointer = group.as_pointer()
        old_name = group.name
        first_backing = _library_backing(group, "local", "local_reload_helper")

        _write(helper, 'x = input_float("X", default=1.0)\noutput("Value", x + 2)\n')
        compiler.update_library_catalog_group(group, "local", "local_reload_root")

        second_backing = _library_backing(group, "local", "local_reload_helper")
        check(group.as_pointer() == pointer, "library reload replaced the selected root datablock")
        check(group.name == old_name, "library reload changed the selected root datablock name")
        check(second_backing is not first_backing, "library reload reused the old Local dependency snapshot")
        check("x + 2" in second_backing.get("nodeforge_library_source", ""), "library reload did not use the current helper source")
        check(group.get("nodeforge_library_namespace") == "local", "reload lost Local namespace provenance")
        check(group.get("nodeforge_library_name") == "local_reload_root", "reload lost Local entry provenance")
    finally:
        helper.unlink(missing_ok=True)
        root.unlink(missing_ok=True)
        if group is not None and bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)


def test_reload_local_catalog_group_fails_before_mutation_when_source_disappears():
    """A missing current catalog source must leave the materialized group untouched."""
    local = library.ensure_local_catalog_dir()
    source = local / "local_reload_missing.nf"
    group = None
    try:
        _write(source, 'x = input_float("X", default=1.0)\noutput("Value", x)\n')
        group = compiler.create_library_catalog_group("local", "local_reload_missing")
        pointer = group.as_pointer()
        embedded = group.get("nodeforge_library_source")
        node_count = len(group.nodes)
        source.unlink()
        try:
            compiler.update_library_catalog_group(group, "local", "local_reload_missing")
        except CompileError:
            pass
        else:
            raise AssertionError("reload accepted a missing current Local source")
        check(group.as_pointer() == pointer, "missing-source reload replaced the root datablock")
        check(group.get("nodeforge_library_source") == embedded, "missing-source reload changed provenance source")
        check(len(group.nodes) == node_count, "missing-source reload mutated the root graph")
    finally:
        source.unlink(missing_ok=True)
        if group is not None and bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)


def test_reload_local_catalog_group_rolls_back_invalid_current_source_without_leaks():
    """Compilation failure during reload must preserve the root and clean transaction groups."""
    local = library.ensure_local_catalog_dir()
    source = local / "local_reload_invalid.nf"
    group = None
    try:
        original = 'x = input_float("X", default=1.0)\noutput("Value", x)\n'
        _write(source, original)
        group = compiler.create_library_catalog_group("local", "local_reload_invalid")
        pointer = group.as_pointer()
        node_names = [node.name for node in group.nodes]
        _write(source, 'x = missing_reload_function(1)\noutput("Value", x)\n')
        try:
            compiler.update_library_catalog_group(group, "local", "local_reload_invalid")
        except Exception:
            pass
        else:
            raise AssertionError("reload accepted invalid current source")
        check(group.as_pointer() == pointer, "failed reload replaced the root datablock")
        check([node.name for node in group.nodes] == node_names, "failed reload did not preserve the root graph")
        leaked = [
            g.name for g in bpy.data.node_groups
            if g.name.startswith("NodeForge.replacement.") or g.name.startswith("NodeForge.rollback.") or g.name.startswith("NodeForge.preflight.")
        ]
        check(not leaked, f"failed library reload leaked transaction groups: {leaked}")
    finally:
        source.unlink(missing_ok=True)
        if group is not None and bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)


def _make_reload_package(root: Path, *, package_id: str, version: str, value: float) -> None:
    """Create a source-only functions package used by reload ownership tests."""
    functions = root / "functions"
    functions.mkdir(parents=True)
    (functions / "reload_value.nf").write_text(
        f'value = input_float("Value", default={value})\noutput("Value", value)\n',
        encoding="utf-8",
    )
    (root / "nodeforge_package.json").write_text(
        json.dumps({
            "schema_version": 1,
            "id": package_id,
            "name": package_id,
            "version": version,
            "author": "Tests",
            "description": "Reload ownership fixture",
            "nodeforge_min_version": "0.49.47",
            "nodeforge_max_version": None,
            "contents": {"functions": "functions"},
            "permissions": {"python": False},
        }),
        encoding="utf-8",
    )


def test_library_reload_allows_same_package_upgrade_and_rejects_package_takeover():
    """Package version may move on reload while package identity remains stable."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        packages.set_packages_dir_for_tests(tmp / "inventory")
        group = None
        try:
            v1 = tmp / "v1"
            _make_reload_package(v1, package_id="vendor.reload", version="1.0.0", value=1.0)
            packages.install_package_directory(v1, allow_python=False)
            group = compiler.create_library_catalog_group("functions", "reload_value")
            pointer = group.as_pointer()
            check(group.get("nodeforge_package_id") == "vendor.reload", "initial package id metadata missing")
            check(group.get("nodeforge_package_version") == "1.0.0", "initial package version metadata missing")

            v2 = tmp / "v2"
            _make_reload_package(v2, package_id="vendor.reload", version="2.0.0", value=2.0)
            packages.install_package_directory(v2, allow_python=False, replace=True)
            compiler.update_library_catalog_group(group, "functions", "reload_value")
            check(group.as_pointer() == pointer, "same-package reload replaced the root datablock")
            check(group.get("nodeforge_package_id") == "vendor.reload", "same-package reload changed package identity")
            check(group.get("nodeforge_package_version") == "2.0.0", "same-package reload did not stamp new package version")
            check("default=2.0" in group.get("nodeforge_library_source", ""), "same-package reload did not use current source")

            packages.uninstall_package("vendor.reload")
            other = tmp / "other"
            _make_reload_package(other, package_id="vendor.other", version="1.0.0", value=3.0)
            packages.install_package_directory(other, allow_python=False)
            before_source = group.get("nodeforge_library_source")
            try:
                compiler.update_library_catalog_group(group, "functions", "reload_value")
            except Exception as exc:
                check("different package" in str(exc), f"unexpected cross-package reload error: {exc}")
            else:
                raise AssertionError("different package took over an existing library group")
            check(group.as_pointer() == pointer, "rejected package takeover replaced the root datablock")
            check(group.get("nodeforge_package_id") == "vendor.reload", "rejected package takeover changed ownership")
            check(group.get("nodeforge_library_source") == before_source, "rejected package takeover changed stored source")
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()


def test_native_only_library_entry_is_not_reloadable():
    """Native-only catalog entries stay outside the in-place source reload path."""
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        packages.set_packages_dir_for_tests(tmp / "inventory")
        group = None
        try:
            source = tmp / "native_only"
            entry = source / "functions" / "reload_native"
            entry.mkdir(parents=True)
            (entry / "function.py").write_text(
                "def materialize_group(compile_group_callback):\n    raise AssertionError('reload resolver executed native materializer')\n",
                encoding="utf-8",
            )
            (source / "nodeforge_package.json").write_text(
                json.dumps({
                    "schema_version": 1,
                    "id": "vendor.native_reload",
                    "name": "Vendor Native Reload",
                    "version": "1.0.0",
                    "author": "Tests",
                    "description": "Native-only reload fixture",
                    "nodeforge_min_version": "0.49.47",
                    "nodeforge_max_version": None,
                    "contents": {"functions": "functions"},
                    "permissions": {"python": True},
                }),
                encoding="utf-8",
            )
            packages.install_package_directory(source, allow_python=True)
            group = bpy.data.node_groups.new("NFTest_native_only_reload", "GeometryNodeTree")
            group["nodeforge_library_namespace"] = "functions"
            group["nodeforge_library_name"] = "reload_native"
            group["nodeforge_package_id"] = "vendor.native_reload"
            try:
                library.resolve_reloadable_library_entry(group)
            except CompileError as exc:
                check("no reloadable .nf source" in str(exc), f"unexpected native-only reload error: {exc}")
            else:
                raise AssertionError("native-only library entry was treated as reloadable")
        finally:
            if group is not None and bpy.data.node_groups.get(group.name) is group:
                bpy.data.node_groups.remove(group, do_unlink=True)
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()
