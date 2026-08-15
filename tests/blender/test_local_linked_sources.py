from helpers import *

import tempfile
from pathlib import Path


def _write(path, text):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')


def _register_legacy_linked_file(path):
    """Seed one legacy file registration without using removed authoring behavior."""
    roots = library._read_local_source_registry()
    roots.append({"path": str(Path(path).resolve()), "label": Path(path).name, "kind": "file"})
    library._write_local_source_registry(roots)



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
        _register_legacy_linked_file(source)
        try:
            record = library.find_library_entry_record('local', 'linked_file_probe')
            check(record is not None and record.source_path == source.resolve(), 'linked file was not discovered directly')
            rows = library.local_browser_records('')
            row = next((r for r in rows if r.get('name') == 'linked_file_probe' and r.get('kind') == 'linked_script'), None)
            check(row is not None, 'linked file is not visible at Local browser root')
        finally:
            library.unlink_local_source_file(str(source))


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


def test_imported_root_remove_preserves_external_files_and_missing_registration():
    with tempfile.TemporaryDirectory(prefix='nodeforge_import_remove_') as temp:
        root = Path(temp) / 'external_root'
        root.mkdir()
        source = root / 'remove_probe.nf'
        _write(source, 'x = input_float("X")\noutput("x", x)\n')
        library.link_local_source_folder(str(root))
        library.unlink_local_source_folder(str(root))
        check(source.exists(), 'removing imported root deleted external source')

        library.link_local_source_folder(str(root))
        source.unlink()
        root.rmdir()
        rows = library.local_browser_records('')
        missing = next((r for r in rows if r.get('kind') == 'missing_linked_folder' and r.get('path') == str(root.resolve())), None)
        check(missing is not None, 'missing imported root is not visible for cleanup')
        library.unlink_local_source_folder(str(root))


def test_imported_roots_reject_managed_overlap_and_are_idempotent():
    managed = library.ensure_local_catalog_dir().resolve()
    child = managed / 'overlap_child_probe'
    child.mkdir(exist_ok=True)
    try:
        for candidate in (managed, child, managed.parent):
            try:
                library.link_local_source_folder(str(candidate))
            except CompileError:
                pass
            else:
                raise AssertionError(f'managed/imported root overlap was accepted: {candidate}')
    finally:
        child.rmdir()

    with tempfile.TemporaryDirectory(prefix='nodeforge_import_overlap_') as temp:
        root = Path(temp) / 'root'
        child = root / 'child'
        sibling = Path(temp) / 'sibling'
        child.mkdir(parents=True)
        sibling.mkdir()
        first = library.link_local_source_folder(str(root))
        try:
            second = library.link_local_source_folder(str(root))
            check(second == first, 're-adding exact imported root was not idempotent')
            try:
                library.link_local_source_folder(str(child))
            except CompileError:
                pass
            else:
                raise AssertionError('nested imported root was accepted')
            library.link_local_source_folder(str(sibling))
            library.unlink_local_source_folder(str(sibling))
        finally:
            library.unlink_local_source_folder(str(root))


def test_legacy_linked_file_can_be_removed_without_deleting_external_file():
    with tempfile.TemporaryDirectory(prefix='nodeforge_legacy_file_') as temp:
        source = Path(temp) / 'legacy_link_probe.nf'
        _write(source, 'x = input_float("X")\noutput("x", x)\n')
        _register_legacy_linked_file(source)
        rows = library.local_browser_records('')
        check(any(r.get('kind') == 'linked_script' and r.get('path') == str(source.resolve()) for r in rows), 'legacy linked file missing from cleanup UI model')
        library.unlink_local_source_file(str(source))
        check(source.exists(), 'legacy linked-file cleanup deleted external file')
        check(not any(r.get('path') == str(source.resolve()) for r in library.local_source_files(include_missing=True)), 'legacy linked-file registration remained after cleanup')


def test_managed_delete_and_overwrite_use_concrete_path_when_public_name_is_ambiguous():
    managed = library.ensure_local_catalog_dir()
    managed_dir = managed / 'path_identity_probe'
    managed_dir.mkdir(exist_ok=True)
    managed_source = managed_dir / 'same_leaf.nf'
    _write(managed_source, 'x = input_float("X")\noutput("x", x)\n')
    with tempfile.TemporaryDirectory(prefix='nodeforge_path_identity_') as temp:
        external = Path(temp)
        external_source = external / 'same_leaf.nf'
        _write(external_source, 'x = input_float("X")\noutput("x", x * 2.0)\n')
        library.link_local_source_folder(str(external))
        try:
            try:
                library.find_library_entry_record('local', 'same_leaf')
            except CompileError:
                pass
            else:
                raise AssertionError('same-name managed/imported sources were not ambiguous for language resolution')

            library.save_local_source('same_leaf', 'x = 3\noutput("x", x)\n', folder_path='path_identity_probe', overwrite=True)
            check(managed_source.read_text(encoding='utf-8').startswith('x = 3'), 'concrete managed overwrite failed under public-name ambiguity')
            check(external_source.read_text(encoding='utf-8').startswith('x = input_float'), 'managed overwrite modified external source')

            other = library.save_local_source('same_leaf', 'x = 4\noutput("x", x)\n', folder_path='path_identity_probe/other')
            check(other.exists(), 'same-name managed save at a distinct concrete path was rejected')
            library.delete_local_source(other)
            library.delete_local_source(managed_source)
            check(not managed_source.exists(), 'concrete managed delete failed under public-name ambiguity')
            check(external_source.exists(), 'managed delete removed external source')
        finally:
            library.unlink_local_source_folder(str(external))
            other_dir = managed_dir / 'other'
            if other_dir.exists():
                other_dir.rmdir()
            managed_source.unlink(missing_ok=True)
            managed_dir.rmdir()


def test_duplicate_managed_stems_are_independently_mutable_by_path():
    managed = library.ensure_local_catalog_dir()
    left = managed / 'managed_dup_left'
    right = managed / 'managed_dup_right'
    left.mkdir(exist_ok=True)
    right.mkdir(exist_ok=True)
    left_source = left / 'managed_dup_leaf.nf'
    right_source = right / 'managed_dup_leaf.nf'
    _write(left_source, 'x = 1\noutput("x", x)\n')
    _write(right_source, 'x = 2\noutput("x", x)\n')
    try:
        try:
            library.find_library_entry_record('local', 'managed_dup_leaf')
        except CompileError:
            pass
        else:
            raise AssertionError('duplicate managed stems did not remain ambiguous for language resolution')
        library.save_local_source('managed_dup_leaf', 'x = 3\noutput("x", x)\n', folder_path='managed_dup_left', overwrite=True)
        check(left_source.read_text(encoding='utf-8').startswith('x = 3'), 'left concrete overwrite failed')
        check(right_source.read_text(encoding='utf-8').startswith('x = 2'), 'left overwrite changed right concrete source')
        library.delete_local_source(right_source)
        check(left_source.exists() and not right_source.exists(), 'path-specific managed delete affected wrong duplicate')
    finally:
        left_source.unlink(missing_ok=True)
        right_source.unlink(missing_ok=True)
        left.rmdir()
        right.rmdir()


def test_managed_folder_delete_is_empty_only_and_rejects_external_paths():
    managed = library.ensure_local_catalog_dir()
    empty = managed / 'delete_empty_folder_probe'
    nonempty = managed / 'delete_nonempty_folder_probe'
    empty.mkdir(exist_ok=True)
    nonempty.mkdir(exist_ok=True)
    source = nonempty / 'child.nf'
    _write(source, 'x = 1\noutput("x", x)\n')
    try:
        deleted = library.delete_local_folder(empty)
        check(deleted == empty and not empty.exists(), 'empty managed folder was not deleted')
        try:
            library.delete_local_folder(nonempty)
        except CompileError:
            pass
        else:
            raise AssertionError('non-empty managed folder deletion succeeded')
        check(source.exists(), 'non-empty folder failure changed contents')
        try:
            library.delete_local_folder(managed)
        except CompileError:
            pass
        else:
            raise AssertionError('managed Local root deletion succeeded')
        with tempfile.TemporaryDirectory(prefix='nodeforge_external_delete_') as temp:
            external = Path(temp)
            try:
                library.delete_local_folder(external)
            except CompileError:
                pass
            else:
                raise AssertionError('external folder reached managed deletion')
    finally:
        source.unlink(missing_ok=True)
        nonempty.rmdir()



def test_independent_imported_roots_allow_same_public_name_until_resolution():
    with tempfile.TemporaryDirectory(prefix='nodeforge_import_same_name_') as temp:
        base = Path(temp)
        left = base / 'left'
        right = base / 'right'
        left.mkdir()
        right.mkdir()
        _write(left / 'shared_probe.nf', 'x = 1\noutput("x", x)\n')
        _write(right / 'shared_probe.nf', 'x = 2\noutput("x", x)\n')
        library.link_local_source_folder(str(left))
        library.link_local_source_folder(str(right))
        try:
            roots = {Path(item['path']) for item in library.local_source_roots()}
            check(left.resolve() in roots and right.resolve() in roots, 'independent imported roots with equal stems were rejected')
            try:
                library.find_library_entry_record('local', 'shared_probe')
            except CompileError:
                pass
            else:
                raise AssertionError('equal public names across imported roots were not ambiguous at resolution')
        finally:
            library.unlink_local_source_folder(str(left))
            library.unlink_local_source_folder(str(right))

def test_managed_symlink_escape_is_rejected_before_external_mutation():
    managed = library.ensure_local_catalog_dir()
    link = managed / 'symlink_escape_probe'
    with tempfile.TemporaryDirectory(prefix='nodeforge_symlink_escape_') as temp:
        external = Path(temp)
        try:
            link.symlink_to(external, target_is_directory=True)
        except (OSError, NotImplementedError):
            pytest.skip('native directory symlinks are unavailable')
        try:
            try:
                library.save_local_source('escaped', 'x = 1\noutput("x", x)\n', folder_path='symlink_escape_probe')
            except CompileError:
                pass
            else:
                raise AssertionError('save followed a managed symlink outside managed root')
            check(not (external / 'escaped.nf').exists(), 'symlink-escape save created an external source')
            check(not list(external.glob('.*.tmp')), 'symlink-escape save leaked an external temp file')

            try:
                library.create_local_folder('symlink_escape_probe/subdir')
            except CompileError:
                pass
            else:
                raise AssertionError('folder creation followed managed symlink outside managed root')
            check(not (external / 'subdir').exists(), 'symlink-escape folder creation mutated external storage')
        finally:
            link.unlink(missing_ok=True)
