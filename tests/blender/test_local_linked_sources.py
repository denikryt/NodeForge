from helpers import *

import tempfile
from pathlib import Path


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def test_linked_local_folder_is_live_and_does_not_copy_sources():
    with tempfile.TemporaryDirectory(prefix='nodeforge_linked_local_') as temp:
        root = Path(temp) / 'project_scripts'
        nested = root / 'plants'
        nested.mkdir(parents=True)
        source = nested / 'linked_live_probe.nf'
        _write(source, 'x = input_float("X")\noutput("x", x * 2.0)\n')

        linked = library.link_local_source_folder(str(root))
        try:
            check(linked == root.resolve(), 'linked source root path changed unexpectedly')
            check(library.has_library_entry('local', 'linked_live_probe'), 'linked Local script was not discovered')
            record = library.find_library_entry_record('local', 'linked_live_probe')
            check(record.source_path == source.resolve(), 'linked Local source was copied instead of read in place')

            first = compile_group(
                'from local import linked_live_probe\n'
                'x = linked_live_probe(2.0)\n'
                'output("x", x)\n',
                'NFTest_linked_local_first',
            )
            first_backing = next(
                node.node_tree for node in first.nodes
                if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'linked_live_probe'
            )

            _write(source, 'x = input_float("X")\noutput("x", x * 3.0)\n')
            second = compile_group(
                'from local import linked_live_probe\n'
                'x = linked_live_probe(2.0)\n'
                'output("x", x)\n',
                'NFTest_linked_local_second',
            )
            second_backing = next(
                node.node_tree for node in second.nodes
                if getattr(getattr(node, 'node_tree', None), 'get', lambda *args: None)('nodeforge_library_name') == 'linked_live_probe'
            )
            check(second_backing is not first_backing, 'changed linked source reused the old snapshot')
        finally:
            library.unlink_local_source_folder(str(root))

        check(source.exists(), 'unlink deleted an external source file')
        check(not library.has_library_entry('local', 'linked_live_probe'), 'unlinked source remained discoverable')


def test_local_browser_shows_direct_managed_folders_and_linked_roots():
    managed = library.ensure_local_catalog_dir()
    empty = managed / 'linked_ui_empty_folder_probe'
    empty.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='nodeforge_linked_browser_') as temp:
        root = Path(temp) / 'external_root'
        (root / 'empty_external').mkdir(parents=True)
        library.link_local_source_folder(str(root))
        try:
            rows = library.local_browser_records('')
            managed_folder = next((r for r in rows if r.get('kind') == 'folder' and r.get('path') == str(empty)), None)
            check(managed_folder is not None, 'empty managed Local folder is invisible in browser root')
            source_root = next((r for r in rows if r.get('kind') == 'linked_folder' and r.get('path') == str(root.resolve())), None)
            check(source_root is not None, 'linked source root is invisible in browser root')
            external_rows = library.local_browser_records(str(root))
            external_folder = next((r for r in external_rows if r.get('kind') == 'folder' and r.get('name') == 'empty_external'), None)
            check(external_folder is not None, 'empty folder inside linked source root is invisible after entering it')
        finally:
            library.unlink_local_source_folder(str(root))
            empty.rmdir()


def test_duplicate_names_across_managed_and_linked_roots_remain_rejected():
    managed = library.ensure_local_catalog_dir()
    managed_source = managed / 'linked_duplicate_probe.nf'
    _write(managed_source, 'x = input_float("X")\noutput("x", x)\n')
    with tempfile.TemporaryDirectory(prefix='nodeforge_linked_duplicate_') as temp:
        root = Path(temp)
        _write(root / 'linked_duplicate_probe.nf', 'x = input_float("X")\noutput("x", x * 2.0)\n')
        library.link_local_source_folder(str(root))
        try:
            try:
                library.library_entry_names('local')
            except CompileError:
                pass
            else:
                raise AssertionError('duplicate public Local name across source roots was accepted')
        finally:
            library.unlink_local_source_folder(str(root))
            managed_source.unlink(missing_ok=True)


def test_linked_source_roots_reject_overlapping_registrations():
    with tempfile.TemporaryDirectory(prefix='nodeforge_linked_overlap_') as temp:
        root = Path(temp) / 'root'
        child = root / 'child'
        child.mkdir(parents=True)
        library.link_local_source_folder(str(root))
        try:
            try:
                library.link_local_source_folder(str(child))
            except CompileError:
                pass
            else:
                raise AssertionError('overlapping linked Local source roots were accepted')
        finally:
            library.unlink_local_source_folder(str(root))


def test_individually_linked_local_file_is_live_and_browser_visible():
    with tempfile.TemporaryDirectory(prefix='nodeforge_linked_file_') as temp:
        source = Path(temp) / 'linked_file_probe.nf'
        _write(source, 'x = input_float("X")\noutput("x", x * 2.0)\n')
        linked = library.link_local_source_file(str(source))
        try:
            check(linked == source.resolve(), 'linked file path changed unexpectedly')
            record = library.find_library_entry_record('local', 'linked_file_probe')
            check(record is not None and record.source_path == source.resolve(), 'linked file was not discovered directly')
            rows = library.local_browser_records('')
            row = next((r for r in rows if r.get('name') == 'linked_file_probe' and r.get('kind') == 'linked_script'), None)
            check(row is not None, 'linked file is not visible at Local browser root')
        finally:
            library.unlink_local_source_folder(str(source))


def test_local_browser_returns_only_current_directory_children():
    managed = library.ensure_local_catalog_dir()
    root = managed / 'browser_nav_probe'
    child = root / 'child'
    child.mkdir(parents=True, exist_ok=True)
    _write(root / 'root_probe.nf', 'x = input_float("X")\noutput("x", x)\n')
    _write(child / 'child_probe.nf', 'x = input_float("X")\noutput("x", x)\n')
    try:
        top = library.local_browser_records(str(root))
        check(any(r.get('kind') == 'folder' and r.get('name') == 'child' for r in top), 'child folder missing from current directory')
        check(any(r.get('name') == 'root_probe' for r in top), 'current-directory script missing')
        check(not any(r.get('name') == 'child_probe' for r in top), 'nested script leaked into parent directory listing')
        nested = library.local_browser_records(str(child))
        check(any(r.get('name') == 'child_probe' for r in nested), 'nested script missing after entering folder')
        check(not any(r.get('name') == 'root_probe' for r in nested), 'parent script leaked into nested directory listing')
    finally:
        for path in sorted(root.rglob('*'), reverse=True):
            if path.is_file():
                path.unlink()
            elif path.is_dir():
                path.rmdir()
        root.rmdir()
