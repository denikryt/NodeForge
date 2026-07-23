from helpers import *


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
        try:
            library.save_local_source('local_cross_folder_duplicate', 'x = 2\noutput("x", x)\n')
        except CompileError:
            pass
        else:
            raise AssertionError('cross-folder logical duplicate save was accepted')

        for bad in ('../escape', '/abs', '_private', 'bad-name'):
            try:
                library.create_local_folder(bad)
            except CompileError:
                pass
            else:
                raise AssertionError(f'invalid local folder accepted: {bad}')
    finally:
        for path in (flat, nested, ignored_native_folder, unsupported_source_layout, duplicate_flat, duplicate_nested, cross_folder, saved, nested_saved):
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
        user_group = bpy.data.node_groups.new('NodeForge.local.local_collision_probe', 'GeometryNodeTree')
        expect_compile_error('from local import local_collision_probe\nx = local_collision_probe(1)\noutput("x", x)', 'NFTest_local_ownership_collision')
        check(bpy.data.node_groups.get('NodeForge.local.local_collision_probe') is user_group, 'ownership collision mutated user group')
        bpy.data.node_groups.remove(user_group, do_unlink=True)
        user_group = None
        group = compile_group('from local import local_collision_probe\nx = local_collision_probe(1)\noutput("x", x)', 'NFTest_local_owned_group_created')
        backing = bpy.data.node_groups.get('NodeForge.local.local_collision_probe')
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


def test_save_to_local_source_selection_is_explicit(monkeypatch):
    from types import SimpleNamespace
    from NodeForge import ui

    text_source = 'x = input_float("Text", default=1.0)\noutput("x", x)\n'
    group_source = 'x = input_float("Group", default=2.0)\noutput("x", x)\n'
    text = SimpleNamespace(as_string=lambda: text_source)
    props = SimpleNamespace(text_block=text)
    context = SimpleNamespace(scene=SimpleNamespace(gn_script_mvp=props))
    group = {"gn_script_mvp_source": group_source}
    node = SimpleNamespace(node_tree=group)
    monkeypatch.setattr(ui, '_selected_group_node', lambda context: node)

    check(ui._source_for_local_save(context, 'TEXT') == text_source, 'TEXT source_kind did not use Text datablock')
    check(ui._source_for_local_save(context, 'SELECTED_GROUP') == group_source, 'SELECTED_GROUP source_kind did not use selected group')


def test_save_to_local_source_selection_does_not_fallback(monkeypatch):
    from types import SimpleNamespace
    from NodeForge import ui

    props = SimpleNamespace(text_block=None)
    context = SimpleNamespace(scene=SimpleNamespace(gn_script_mvp=props))
    group = {"gn_script_mvp_source": 'x = input_float("Group")\noutput("x", x)\n'}
    node = SimpleNamespace(node_tree=group)
    monkeypatch.setattr(ui, '_selected_group_node', lambda context: node)

    try:
        ui._source_for_local_save(context, 'TEXT')
    except ValueError:
        pass
    else:
        raise AssertionError('TEXT source_kind fell back to selected group')

    props.text_block = SimpleNamespace(as_string=lambda: 'x = input_float("Text")\noutput("x", x)\n')
    monkeypatch.setattr(ui, '_selected_group_node', lambda context: None)
    try:
        ui._source_for_local_save(context, 'SELECTED_GROUP')
    except ValueError:
        pass
    else:
        raise AssertionError('SELECTED_GROUP source_kind fell back to Text datablock')
