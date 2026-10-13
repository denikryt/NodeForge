"""Canonical discovery, collision locality, names and storage contracts."""

import ast
import json
import os
from dataclasses import replace
from pathlib import Path

import pytest

from NodeForge import catalog, local_sources
from NodeForge.compiler_identities import library_function_id, library_function_id_for_record
from NodeForge.environment_resolution import resolve_environment
from NodeForge.errors import CompileError
from NodeForge.local_index import LocalSourceIndex
from NodeForge.function_instances import direct_library_owner_scope_for_id

pytestmark = pytest.mark.unit


def origin(path):
    """Return the production platform-normalized canonical test origin."""
    return catalog._path_key(path)


def write(root, relative, source='output(1.0)\n'):
    """Create an exact physical source, including invalid historical names."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding='utf-8')
    return path


def index(root, *linked, files=()):
    """Capture one production discovery traversal into a pure immutable index."""
    return LocalSourceIndex.from_discovery(*catalog.discover_local_sources((root, *linked), files))


@pytest.fixture
def storage(tmp_path, monkeypatch):
    """Route production managed paths and registry to isolated physical roots."""
    managed = tmp_path / 'Local'
    managed.mkdir()
    registry = tmp_path / 'local_sources.json'
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: managed)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: registry)
    return managed, registry


def test_duplicate_stems_are_local_and_suggestions_are_verified(tmp_path):
    """Unique flat sources survive while duplicate names offer working imports."""
    a = write(tmp_path, 'terrain/noise.nf')
    b = write(tmp_path, 'procedural/noise.nf')
    unique = write(tmp_path, 'unique_a.nf')
    captured = index(tmp_path)
    assert captured.find_flat_function('unique_a').path == unique
    assert len(captured.all_records()) == 3
    with pytest.raises(CompileError) as error:
        captured.find_flat_function('noise')
    message = str(error.value)
    assert "Ambiguous callable 'noise'." in message
    assert 'from local.terrain import noise' in message
    assert 'from local.procedural import noise' in message
    for origin_key in (origin(a), origin(b)):
        qualified = captured.qualified_imports(origin_key)[0]
        parsed = ast.parse(qualified[1]).body[0]
        parent = captured.resolve_directory(tuple(parsed.module.split('.')[1:]))
        assert captured.resolve_member(parent, 'noise').source_origin == origin_key


def test_same_position_extensions_have_no_broken_suggestions(tmp_path):
    """Two physical files in one member slot cannot be distinguished by qualification."""
    a = write(tmp_path, 'terrain/noise.nf')
    b = write(tmp_path, 'terrain/noise.nodeforge')
    captured = index(tmp_path)
    directory = captured.resolve_directory(('terrain',))
    assert captured.resolve_member(directory, 'noise', source_extensions=('.nf',)).source_origin == origin(a)
    write(tmp_path, 'terrain/only.nodeforge')
    with_only = index(tmp_path)
    assert with_only.resolve_member(directory, 'only', source_extensions=('.nf',)) is None
    assert with_only.resolve_member(directory, 'only').source_path.suffix == '.nodeforge'
    with pytest.raises(CompileError, match='Ambiguous'):
        captured.resolve_member(directory, 'noise')
    with pytest.raises(CompileError) as error:
        captured.find_flat_function('noise')
    assert origin(a) in str(error.value) and origin(b) in str(error.value)
    assert 'from local.terrain import noise' not in str(error.value)
    assert 'rename' in str(error.value)


def test_empty_directory_conflicts_after_registration_and_snapshot_isolation(storage, tmp_path):
    """Registration conflicts are access-local and empty directories share the snapshot."""
    managed, registry = storage
    linked = tmp_path / 'external/terrain'
    path = write(linked, 'noise.nf')
    local_sources.link_local_source_folder(str(linked), label='UI label')
    e0 = resolve_environment().catalog('local').local_index
    assert e0.resolve_member(e0.resolve_directory(('terrain',)), 'noise').path == path
    rows = json.loads(registry.read_text())
    assert rows['version'] == 1
    assert set(rows['roots'][0]) == {'path', 'label', 'kind'}
    file = write(managed, 'terrain.nf')
    unique = write(managed, 'unique_a.nf')
    e1 = resolve_environment().catalog('local').local_index
    with pytest.raises(CompileError, match='file-versus-directory'):
        e1.resolve_root_import('terrain')
    assert e1.find_flat_function('terrain').path == file
    assert e1.resolve_member(e1.resolve_directory(('terrain',)), 'noise').path == path
    file.unlink()
    (managed / 'terrain').mkdir()
    e2 = resolve_environment().catalog('local').local_index
    for select in (lambda: e2.resolve_directory(('terrain',)), lambda: e2.resolve_root_import('terrain')):
        with pytest.raises(CompileError) as error:
            select()
        assert origin(managed / 'terrain') in str(error.value)
        assert origin(linked) in str(error.value)
    assert e2.find_flat_function('unique_a').path == unique
    assert e0.resolve_member(e0.resolve_directory(('terrain',)), 'noise').path == path
    other = tmp_path / 'other/terrain'
    other.mkdir(parents=True)
    local_sources.link_local_source_folder(str(other))
    local_sources.link_local_source_folder(str(other))
    assert len(json.loads(registry.read_text())['roots']) == 2
    e3 = resolve_environment().catalog('local')
    assert e3.failure is None and e3.find('unique_a').path == unique
    with pytest.raises(CompileError, match='Ambiguous Local directory'):
        e3.local_index.resolve_directory(('terrain',))


@pytest.mark.parametrize('parent', ['My Geometry', 'terrain/Sub Folder', 'class', 'terrain\n'])
def test_invalid_physical_directories_keep_valid_flat_descendants(tmp_path, parent):
    """Historical invalid parent spellings neither disappear nor become namespaces."""
    if os.name == "nt" and "\n" in parent:
        pytest.skip("Windows disallows newline directory names; pure validation is covered separately")
    path = write(tmp_path, f'{parent}/noise.nf')
    write(tmp_path, 'procedural/noise.nf')
    captured = index(tmp_path)
    assert origin(path) in captured.by_origin
    assert captured.qualified_imports(origin(path)) == ()
    with pytest.raises(CompileError) as error:
        captured.find_flat_function('noise')
    assert origin(path) in str(error.value)
    assert f'from local.{parent.replace("/", ".")} import noise' not in str(error.value)
    (tmp_path / 'procedural/noise.nf').unlink()
    assert index(tmp_path).find_flat_function('noise').path == path


@pytest.mark.parametrize('name', ['my noise', 'for', 'noise\n', '_noise', 'éclair'])
def test_invalid_existing_file_stems_are_excluded_without_mutation(tmp_path, name):
    """Invalid file names remain on disk and never become importable records."""
    if os.name == "nt" and "\n" in name:
        pytest.skip("Windows disallows newline filenames; pure validation is covered separately")
    invalid = write(tmp_path, f'terrain/{name}.nf')
    valid = write(tmp_path, 'terrain/noise.nf')
    captured = index(tmp_path)
    assert invalid.exists()
    assert captured.flat_names() == {'noise'}
    assert tuple(captured.by_origin) == (origin(valid),)
    assert captured.find_flat_function(name) is None


@pytest.mark.parametrize('name', ['My Geometry', ' noise ', 'class', 'for', 'terrain\n', 'noise\n', '9name', 'éclair', '_hidden', '', '../escape'])
def test_new_invalid_leaves_fail_before_mutation(storage, name):
    """Managed creation refuses entire invalid identifiers without partial writes."""
    managed, _ = storage
    before = tuple(managed.rglob('*'))
    with pytest.raises(CompileError):
        local_sources.create_local_folder('valid_parent/' + name)
    assert tuple(managed.rglob('*')) == before
    with pytest.raises(CompileError):
        local_sources.save_local_source(name, 'output(1.0)', folder_path='valid_parent')
    assert tuple(managed.rglob('*')) == before


@pytest.mark.parametrize('name', ['noise', 'match', 'case', 'terrain_v2'])
def test_valid_names_and_preexisting_invalid_parents(storage, name):
    """New valid leaves preserve historical spaces and keyword parent paths."""
    managed, _ = storage
    for parent in ('My Geometry', 'class'):
        (managed / parent).mkdir(exist_ok=True)
        folder = local_sources.create_local_folder(f'{parent}/{name}')
        assert folder == managed / parent / name
        path = local_sources.save_local_source(name, 'output(1.0)', folder_path=parent)
        assert path == managed / parent / f'{name}.nf'
    assert catalog._is_public_function_name('noise\n')
    assert not catalog.is_public_local_name('noise\n')


def test_deduplication_symlinks_hardlinks_and_immutable_views(tmp_path):
    """Path identity deduplicates spelling/registration but preserves hardlink origins."""
    root = tmp_path / 'Local'
    a = write(root, 'terrain/noise.nf')
    alias = root / 'alias.nf'
    alias.symlink_to(a)
    hard = root / 'procedural/noise.nf'
    hard.parent.mkdir()
    os.link(a, hard)
    captured = index(root, files=(a, a))
    assert len(captured.all_records()) == 2
    assert len(captured.flat_candidates['noise']) == 2
    origin_key = origin(a)
    function = library_function_id_for_record(captured.by_origin[origin_key])
    other = library_function_id_for_record(captured.by_origin[origin(hard)])
    assert function != other
    assert direct_library_owner_scope_for_id(function) != direct_library_owner_scope_for_id(other)
    with pytest.raises(TypeError):
        captured.by_origin['new'] = captured.by_origin[origin_key]
    with pytest.raises(TypeError):
        captured.path_members[('terrain',)]['new'] = None
    with pytest.raises(ValueError):
        LocalSourceIndex(captured.by_origin, {}, {})
    with pytest.raises(ValueError):
        library_function_id_for_record(replace(captured.by_origin[origin_key], source_origin='wrong'))
    assert library_function_id('examples', '', 'demo').stable_key() == '{"kind":"LIBRARY","name":"demo","namespace":"examples","package_id":"__nodeforge_core__"}'


def test_no_lookup_reads_and_linked_single_files_are_flat_only(tmp_path, monkeypatch):
    """Captured lookups survive deletion without scanning or invented file namespaces."""
    root = tmp_path / 'Local'
    root.mkdir()
    linked = write(tmp_path, 'outside/demo.nf')
    captured = index(root, files=(linked, linked))
    linked.unlink()
    def forbidden(*args, **kwargs):
        """Fail if lookup attempts a filesystem query."""
        raise AssertionError('lookup rescanned filesystem')
    monkeypatch.setattr(Path, 'stat', forbidden)
    assert captured.find_flat_function('demo').path == linked
    assert captured.resolve_root_import('outside') is None
    assert captured.flat_names() == {'demo'}


def test_symlink_escape_and_permission_errors_are_not_partial_success(tmp_path, monkeypatch):
    """Unsafe roots or failed traversal never silently publish an incomplete index."""
    managed = tmp_path / 'Local'
    managed.mkdir()
    outside = write(tmp_path, 'outside/noise.nf')
    (managed / 'noise.nf').symlink_to(outside)
    with pytest.raises(CompileError, match='outside'):
        index(managed)
    (managed / 'noise.nf').unlink()
    (managed / 'external').symlink_to(outside.parent, target_is_directory=True)
    assert index(managed).all_records() == ()
    def failed_walk(*args, **kwargs):
        """Simulate an authoritative traversal permission error."""
        kwargs['onerror'](PermissionError('denied'))
    monkeypatch.setattr(os, 'walk', failed_walk)
    with pytest.raises(PermissionError):
        index(managed)


def test_storage_containment_read_only_and_path_specific_delete(storage, tmp_path):
    """Independent same-name files survive exact-path managed deletion and unlinking."""
    managed, registry = storage
    a = write(managed, 'a/noise.nf')
    b = write(managed, 'b/noise.nf')
    linked = write(tmp_path, 'external/noise.nf')
    local_sources.link_local_source_folder(str(linked.parent))
    local_sources.unlink_local_source_folder(linked.parent)
    assert linked.exists()
    local_sources.delete_local_source(a)
    assert b.exists() and not a.exists()
    (managed / 'escape').symlink_to(linked.parent, target_is_directory=True)
    with pytest.raises(CompileError, match='outside'):
        local_sources.save_local_source('valid', 'output(1.0)', folder_path='escape')
    assert not (linked.parent / 'valid.nf').exists()


def test_addon_execute_validates_exact_new_leaves(storage):
    """Run the real UI execute methods against storage, retaining invalid user input."""
    from types import SimpleNamespace
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / 'ui.py').read_text())
    managed, _ = storage
    (managed / 'My Geometry').mkdir()
    props = SimpleNamespace(text_block=SimpleNamespace(as_string=lambda: 'output(1.0)'), local_browser_path=str(managed / 'My Geometry'))
    context = SimpleNamespace(scene=SimpleNamespace(gn_script_mvp=props))
    refreshes = []
    namespace = {
        '_managed_local_browser_directory': lambda props: Path(props.local_browser_path),
        'ensure_local_catalog_dir': local_sources.ensure_local_catalog_dir,
        'validate_new_local_name': local_sources.validate_new_local_name,
        'create_local_folder': local_sources.create_local_folder,
        'save_local_source': local_sources.save_local_source,
        '_refresh_catalog_items': lambda *args: refreshes.append(args),
    }
    for class_name, attribute in [('NODEFORGE_OT_create_local_folder', 'folder_name'), ('NODEFORGE_OT_save_to_local', 'script_name')]:
        cls = next(x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == class_name)
        method = next(x for x in cls.body if isinstance(x, ast.FunctionDef) and x.name == 'execute')
        exec(compile(ast.Module(body=[method], type_ignores=[]), 'ui.execute', 'exec'), namespace)
        execute = namespace['execute']
        reports = []
        operator = SimpleNamespace(overwrite=False, report=lambda level, text: reports.append((level, text)))
        for name in ('My Geometry', 'class', 'for', 'noise\n', '../escape'):
            setattr(operator, attribute, name)
            before = tuple(managed.rglob('*'))
            assert execute(operator, context) == {'CANCELLED'}
            assert getattr(operator, attribute) == name
            assert tuple(managed.rglob('*')) == before
            assert reports[-1][0] == {'ERROR'}
        setattr(operator, attribute, 'valid_folder' if attribute == 'folder_name' else 'valid_source')
        assert execute(operator, context) == {'FINISHED'}
    assert (managed / 'My Geometry/valid_folder').is_dir()
    assert (managed / 'My Geometry/valid_source.nf').is_file()
    assert len(refreshes) == 2


def test_local_metadata_reload_uses_exact_origin_and_refuses_legacy(storage, tmp_path, monkeypatch):
    """Exercise production provenance helpers without creating Blender graphs."""
    import importlib
    import sys
    from types import SimpleNamespace
    managed, _ = storage
    a = write(managed, 'terrain/noise.nf')
    b = write(managed, 'procedural/noise.nf')
    env = resolve_environment()
    selected = env.catalog('local').local_index
    ra, rb = selected.find_origin(origin(a)), selected.find_origin(origin(b))
    monkeypatch.setitem(sys.modules, 'bpy', SimpleNamespace(data=SimpleNamespace(node_groups=[]), app=SimpleNamespace(driver_namespace={})))
    helpers = importlib.import_module('NodeForge.blender.library_groups')
    group = {}
    helpers._write_package_metadata(group, ra)
    before = dict(group)
    assert helpers.resolve_reloadable_library_entry(group, resolved_environment=env) is ra
    assert helpers._library_metadata_matches(group, ra, instance_key=None)
    assert not helpers._library_metadata_matches(group, rb, instance_key=None)
    with pytest.raises(CompileError, match='different physical source'):
        helpers.validate_reloadable_library_entry_record(group, rb)
    with pytest.raises(CompileError, match='FunctionId does not match'):
        helpers._validate_library_function_id(library_function_id_for_record(ra), rb)
    a.unlink()
    fresh = resolve_environment()
    with pytest.raises(CompileError, match='unavailable'):
        helpers.resolve_reloadable_library_entry(group, resolved_environment=fresh)
    assert group == before
    legacy = dict(group)
    del legacy['nodeforge_library_source_origin']
    legacy_before = dict(legacy)
    with pytest.raises(CompileError, match='no canonical source origin'):
        helpers.resolve_reloadable_library_entry(legacy, resolved_environment=env)
    with pytest.raises(CompileError, match='no canonical source origin'):
        helpers.validate_reloadable_library_entry_record(legacy, ra)
    assert legacy == legacy_before


def test_local_browser_and_selected_source_ui_keep_duplicate_paths(storage, monkeypatch):
    """Browser rows remain path-distinct and UI materialization routes by origin."""
    managed, _ = storage
    a = write(managed, 'noise.nf')
    b = write(managed, 'noise.nodeforge')
    rows = local_sources.local_browser_records('')
    assert {row['path'] for row in rows if row['name'] == 'noise'} == {str(a), str(b)}
    root = Path(__file__).resolve().parents[2]
    tree = ast.parse((root / 'compiler.py').read_text())
    method = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'create_local_source_group')
    env = resolve_environment()
    calls = []
    namespace = {'resolve_environment': lambda: env, 'CompileError': CompileError,
                 '_new_group_backend': lambda selected: selected,
                 'materialize_library_entry_group_for_record': lambda record, backend: calls.append((record, backend))}
    exec(compile(ast.Module(body=[method], type_ignores=[]), 'compiler.selected_source', 'exec'), namespace)
    namespace['create_local_source_group'](str(a))
    assert calls[0][0].source_origin == origin(a)
    assert calls[0][1] is env
    with pytest.raises(CompileError, match='unavailable'):
        namespace['create_local_source_group'](str(managed / 'missing.nf'))
    assert len(calls) == 1


@pytest.mark.parametrize('name', [
    'noise.nf', 'noise.nodeforge', 'match.nf', 'case.nodeforge', 'x.y.nf',
    '.nf', '..nf', 'noise.NF', 'noise.nf.', 'noise', 'class.nf', '_noise.nf',
    'éclair.nf', 'noise\n.nf', 'noise..nodeforge', '.hidden.nf', 'source.nf',
])
def test_string_candidate_filter_matches_path_eligibility(name):
    """The allocation-free filter preserves exact suffix/stem public-name rules."""
    path = Path(name)
    expected = path.stem if path.suffix in catalog._SOURCE_EXTENSIONS and catalog.is_public_local_name(path.stem) else None
    assert catalog._eligible_local_stem(name) == expected


def test_raw_discovery_order_pruning_and_linked_positions(tmp_path, monkeypatch):
    """Raw tuples retain per-root sorted walker order and linked-file registration order."""
    managed = tmp_path / 'Local'
    names = ('z.nodeforge', 'A.nf', 'a.nf', 'terrain/noise.nodeforge', 'terrain/noise.nf',
             'class/valid.nf', 'My Geometry/flat.nf')
    paths = {name: write(managed, name) for name in reversed(names)}
    for name in ('_cache/hidden.nf', '.git/hidden.nf', 'hidden.txt', 'class.nf', '.nf', '_secret.nf'):
        write(managed, name)
    (managed / 'empty').mkdir()
    external = tmp_path / 'Terrain'
    external_path = write(external, 'B.nf')
    linked_a = write(tmp_path, 'linked/a.nf')
    linked_z = write(tmp_path, 'linked/z.nf')
    original_walk = os.walk
    visited = []
    def reversed_files(*args, **kwargs):
        """Change scan order to prove candidates are deterministically ordered."""
        for current, children, files in original_walk(*args, **kwargs):
            visited.append(Path(current).name)
            yield current, children, list(reversed(files))
    monkeypatch.setattr(os, 'walk', reversed_files)
    expected_names = ('A.nf', 'a.nf', 'z.nodeforge', 'My Geometry/flat.nf',
                      'class/valid.nf', 'terrain/noise.nf', 'terrain/noise.nodeforge')
    for _ in range(2):
        records, directories, positions = catalog.discover_local_sources((managed, external), (linked_z, linked_a))
        expected = [paths[name] for name in expected_names] + [external_path, linked_z, linked_a]
        assert tuple((r.name, r.source_origin, r.folder_path) for r in records) == tuple(
            (p.stem, origin(p), str(p.parent.relative_to(managed)) if p in paths.values() and p.parent != managed else '')
            for p in expected)
        assert directories == tuple((prefix, origin(path)) for prefix, path in (
            (('My Geometry',), managed / 'My Geometry'), (('class',), managed / 'class'),
            (('empty',), managed / 'empty'), (('terrain',), managed / 'terrain'),
            (('Terrain',), external)))
        assert positions == tuple((prefix, path.stem, origin(path)) for prefix, path in (
            ((), paths['A.nf']), ((), paths['a.nf']), ((), paths['z.nodeforge']),
            (('terrain',), paths['terrain/noise.nf']), (('terrain',), paths['terrain/noise.nodeforge']),
            (('Terrain',), external_path)))
    assert '_cache' not in visited and '.git' not in visited


def test_rejected_filenames_are_never_sorted_or_materialized(tmp_path, monkeypatch):
    """Large unrelated filename lists cause neither source Path allocation nor status probes."""
    accepted = write(tmp_path, 'ok.nf')
    filenames = [f'unrelated_{i}.txt' for i in range(30000)] + ['noise.NF', 'class.nf', '.nf', '_hidden.nf', 'ok.nf']
    def walk(root, **kwargs):
        """Provide a populated directory at the production controlled-walker boundary."""
        yield str(root), [], filenames
    real_path, real_sorted = catalog.Path, sorted
    allocated, sorted_values = [], []
    def path(value):
        """Track catalog-owned Path allocation independently of os.walk internals."""
        allocated.append(str(value))
        return real_path(value)
    def candidate_sort(values, *args, **kwargs):
        """Reject any rejected filename entering an ordering operation."""
        values = list(values)
        sorted_values.extend(values)
        return real_sorted(values, *args, **kwargs)
    def file_probe(self):
        """Enforce that only accepted source candidates receive is_file checks."""
        assert self == accepted
        return True
    monkeypatch.setattr(os, 'walk', walk)
    monkeypatch.setattr(catalog, 'Path', path)
    monkeypatch.setattr(catalog, 'sorted', candidate_sort, raising=False)
    monkeypatch.setattr(Path, 'is_file', file_probe)
    records, _, _ = catalog.discover_local_sources((tmp_path,))
    assert len(records) == 1 and records[0].path == accepted
    assert sorted_values == ['ok.nf']
    assert not any('unrelated_' in value for value in allocated)


@pytest.mark.parametrize('linked', [False, True])
def test_reserved_layout_only_errors_for_existing_file(tmp_path, linked):
    """source.nf keeps its status-before-error behavior for traversed and linked entries."""
    reserved = tmp_path / 'source.nf'
    reserved.symlink_to(tmp_path / 'missing.nf')
    roots, files = ((tmp_path,), ()) if not linked else ((), (reserved,))
    assert catalog.discover_local_sources(roots, files)[0] == ()
    reserved.unlink()
    reserved.write_text('output(1.0)')
    with pytest.raises(CompileError) as error:
        catalog.discover_local_sources(roots, files)
    assert str(error.value) == f'Unsupported local source layout: {reserved}'


def test_file_status_error_never_publishes_partial_records(tmp_path, monkeypatch):
    """A late accepted-file status failure refuses an already partially visited tree."""
    write(tmp_path, 'a.nf')
    bad = write(tmp_path, 'z.nf')
    original = Path.is_file
    def probe(self):
        """Inject failure after the first candidate was accepted."""
        if self == bad:
            raise PermissionError(13, 'status denied', str(self))
        return original(self)
    monkeypatch.setattr(Path, 'is_file', probe)
    with pytest.raises(PermissionError, match='status denied'):
        catalog.discover_local_sources((tmp_path,))


def test_file_root_and_canonical_target_stem_validation(tmp_path):
    """Only eligible canonical target names survive; invalid roots fail explicitly."""
    root = tmp_path / 'Local'
    root.mkdir()
    invalid = write(root, 'class.nf')
    (root / 'valid.nf').symlink_to(invalid)
    valid = write(root, 'valid_target.nf')
    (root / 'alias.nf').symlink_to(valid)
    captured = index(root)
    assert captured.flat_names() == {'valid_target'}
    assert captured.find_flat_function('valid_target').source_origin == origin(valid)
    with pytest.raises(CompileError, match='not a directory'):
        catalog.discover_local_sources((valid,))
