"""Demand-driven Local snapshots through real compiler and semantic boundaries."""

from dataclasses import FrozenInstanceError, fields, is_dataclass, replace
import json
import sys
from types import ModuleType, SimpleNamespace

import pytest

from NodeForge import catalog, environment_resolution as resolution, local_sources, packages
from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.local_index import LocalSourceIndex
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment, ResolvedPackageNamespace
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.ir import IRCall, IRCallableKind
from NodeForge.semantic.source_callable_session import SourceCallableSession

pytestmark = pytest.mark.unit


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    """Use real isolated package storage, registry and managed Local tree."""
    root = tmp_path / 'Local'
    root.mkdir()
    registry = tmp_path / 'local.json'
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: root)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: registry)
    packages.set_packages_dir_for_tests(tmp_path / 'inventory')
    yield root, registry
    packages.set_packages_dir_for_tests(None)


@pytest.fixture
def counters(monkeypatch):
    """Count the three authoritative Local input/capture boundaries."""
    counts = dict(inputs=0, discovery=0, index=0)
    def wrap(owner, name, key):
        """Retain production work behind an exact call counter."""
        original = getattr(owner, name)
        def counted(*args, **kwargs):
            """Observe and perform one boundary call."""
            counts[key] += 1
            return original(*args, **kwargs)
        monkeypatch.setattr(owner, name, counted)
    wrap(local_sources, '_catalog_input_paths', 'inputs')
    wrap(catalog, 'discover_local_sources', 'discovery')
    # getattr returns the bound classmethod, so preserve its binding explicitly.
    original = LocalSourceIndex.from_discovery
    def counted_index(cls, *args, **kwargs):
        """Count immutable index publication without replacing its implementation."""
        counts['index'] += 1
        return original(*args, **kwargs)
    monkeypatch.setattr(LocalSourceIndex, 'from_discovery', classmethod(counted_index))
    return counts


def write(root, name, text='x = input_float("X")\noutput(x)\n'):
    """Create an exact source under a real physical tree."""
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding='utf-8')
    return path


def install(tmp_path, functions, *, extension=False):
    """Install a real source package, optionally with one native extension owner."""
    root = tmp_path / 'package'
    root.mkdir()
    for name, text in functions.items():
        write(root / 'functions', name + '.nf', text)
    contents = {'functions': 'functions'}
    if extension:
        contents['systems'] = 'systems'
        owner = root / 'systems' / 'native'
        write(owner, 'interface.py',
              'from typing import Annotated\nfrom NodeForge import Float, EvaluationMode\n'
              'EXTENSION_API = 2\nEXTENSIONS = {"identity": ".backend:build"}\n'
              'def identity(x: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n')
        write(owner, 'backend.py', 'def build(context, x):\n    return x\n')
    write(root, 'nodeforge_package.json', json.dumps({
        'schema_version': 1, 'id': 'vendor.demo', 'import_name': 'p', 'name': 'Demo',
        'version': '1.0.0', 'author': 'Tests', 'description': 'Local demand regression',
        'contents': contents, 'permissions': {'python': extension},
        'nodeforge_min_version': '0.66.0', 'nodeforge_max_version': None,
    }))
    return packages.install_package_directory(root)


def prepare(text, view, session=None):
    """Run the production typed frontend with a root-scoped recursive session."""
    session = session or SourceCallableSession(resolved_environment=view)
    identity = GroupCompilationIdentity(None, 'ROOT/demand', 'ROOT/demand', 'ROOT/demand')
    group = analyze_group_source(text, compilation_identity=identity,
                                 resolved_environment=view, source_callable_session=session)
    return group, session


def source_calls(value):
    """Collect typed source-call identities from detached IR dataclasses."""
    result = []
    def visit(item):
        """Walk structural IR without dereferencing environments or the filesystem."""
        if isinstance(item, IRCall) and item.target.kind is IRCallableKind.SOURCE_FUNCTION:
            result.append(item)
        if is_dataclass(item):
            for field in fields(item):
                visit(getattr(item, field.name))
        elif isinstance(item, (tuple, list)):
            for child in item:
                visit(child)
    visit(value)
    return result


@pytest.mark.parametrize('bad', ['registry', 'layout', 'file_root', 'io'])
def test_local_free_frontend_ignores_local_failures(inputs, counters, monkeypatch, bad):
    """Local-free roots succeed; requesting Local reports the same deferred failure."""
    root, registry = inputs
    if bad == 'registry':
        registry.write_text('{"version":999,"roots":[]}')
        error, message = CompileError, 'registry format'
    elif bad == 'layout':
        write(root, 'source.nf')
        error, message = CompileError, 'Unsupported local source layout'
    elif bad == 'file_root':
        root.rmdir()
        root.write_text('not a directory')
        error, message = FileExistsError, 'File exists'
    else:
        def denied(*args, **kwargs):
            """Inject an authoritative inaccessible-tree failure."""
            kwargs['onerror'](PermissionError(13, 'denied', str(root)))
        monkeypatch.setattr(catalog.os, 'walk', denied)
        error, message = PermissionError, 'denied'
    view = resolution._make_compilation_environment()
    prepare('output("Value", 1.0)', view)
    assert counters == dict(inputs=0, discovery=0, index=0)
    with pytest.raises(error, match=message):
        prepare('from local import *\noutput(1.0)', view)
    captured = view.catalog('local')
    with pytest.raises(error, match=message):
        captured.names()
    assert view.catalog('local') is captured
    assert counters['inputs'] == 1 and counters['index'] == 0


@pytest.mark.parametrize('text', [
    'from local import noise\noutput(noise(1.0))',
    'from local.terrain import noise\noutput(noise(1.0))',
    'from local import terrain as t\noutput(t.noise(1.0))',
    'from local import terrain\noutput(noise(1.0))',
    'from local import *\noutput("Value", 1.0)',
    'from local import noise as a\nfrom local.terrain import noise as b\noutput(a(1.0) + b(2.0))',
    'from local import terrain as t\ndef helper(x: Float):\n    return t.noise(x)\noutput(helper(1.0))',
    'from local import terrain\ndef helper(x: Float):\n    return noise(x)\noutput(helper(1.0))',
])
def test_semantic_imports_capture_once_and_preserve_origin(inputs, counters, text):
    """Direct/qualified/star/inherited selections share one exact captured inventory."""
    root, _ = inputs
    path = write(root, 'terrain/noise.nf')
    view = resolution._make_compilation_environment()
    group, session = prepare(text, view)
    selected = view.catalog('local')
    assert view.catalog('local') is selected
    assert counters == dict(inputs=1, discovery=1, index=1)
    assert selected.find('noise').source_origin == catalog._path_key(path)
    for prepared in session.prepared.values():
        for call in source_calls(prepared.group.body):
            if call.target.function_id.namespace == 'local':
                assert call.target.function_id.source_origin == catalog._path_key(path)
    for call in source_calls(group.body):
        if call.target.function_id.namespace == 'local':
            assert call.target.function_id.source_origin == catalog._path_key(path)
    assert session.resolved_environment is view
    with pytest.raises(CompileError, match='Unknown library catalog: bogus'):
        view.catalog('bogus')
    assert counters == dict(inputs=1, discovery=1, index=1)


def test_star_collision_is_checked_even_when_unused(inputs, counters):
    """Unused star members remain validated; unique qualified imports still work."""
    root, _ = inputs
    write(root, 'terrain/noise.nf')
    write(root, 'procedural/noise.nf')
    view = resolution._make_compilation_environment()
    with pytest.raises(CompileError, match="Ambiguous callable 'noise'"):
        prepare('from local import *\noutput(1.0)', view)
    prepare('from local.terrain import noise\noutput(noise(1.0))', view)
    assert counters == dict(inputs=1, discovery=1, index=1)


@pytest.mark.parametrize('selected', [False, True])
def test_only_selected_package_source_demands_local(inputs, counters, tmp_path, selected):
    """Recursive selected source imports demand Local; untouched source/helpers do not."""
    root, registry = inputs
    write(root, 'noise.nf')
    uses_local = 'from local import noise\nx = input_float("X")\noutput(noise(x))\n'
    install(tmp_path, {'plain': 'x = input_float("X")\noutput(x)\n',
                       'first': uses_local, 'second': uses_local})
    if not selected:
        registry.write_text('{"version":999,"roots":[]}')
    view = resolution._make_compilation_environment()
    text = ('from packages import p\noutput(p.first(1.0) + p.second(2.0))' if selected else
            'from packages import p\ndef unused(x: Float):\n    return p.first(x)\noutput(p.plain(1.0))')
    group, session = prepare(text, view)
    assert source_calls(group.body)
    assert counters == {key: int(selected) for key in counters}
    if selected:
        local_ids = [call.target.function_id for item in session.prepared.values()
                     for call in source_calls(item.group.body)
                     if call.target.function_id.namespace == 'local']
        assert len(local_ids) == 2 and local_ids[0] == local_ids[1]
        assert len(session.source_snapshots) == 3
    else:
        assert len(session.source_snapshots) == 1
    assert session.resolved_environment is view


def test_first_demand_freshness_inventory_freeze_and_separate_text_snapshot(inputs, counters):
    """Inventory freezes on demand; source text freezes only on first preparation."""
    root, _ = inputs
    view = resolution._make_compilation_environment()
    early = write(root, 'terrain/early.nf')
    selected = view.catalog('local')
    assert selected.names() == {'early'}
    # Contents have not been read by discovery: a first preparation sees new text.
    early.write_text('x = input_float("X")\noutput(x + 1.0)\n')
    _, session = prepare('from local.terrain import early\noutput(early(1.0))', view)
    snapshot = next(iter(session.source_snapshots.values()))
    assert 'x + 1.0' in snapshot
    early.write_text('invalid new contents')
    prepare('from local import early\noutput(early(2.0))', view, session)
    assert tuple(session.source_snapshots.values()) == (snapshot,)
    early.rename(root / 'renamed.nf')
    write(root, 'late.nf')
    (root / 'empty').mkdir()
    assert selected.names() == {'early'}
    assert selected.local_index.resolve_directory(('terrain',))
    with pytest.raises(CompileError, match='Unknown Local directory'):
        selected.local_index.resolve_directory(('empty',))
    next_view = resolution._make_compilation_environment()
    assert next_view.catalog('local').names() == {'renamed', 'late'}
    assert next_view.catalog('local') is not selected
    assert next_view.catalog('local').local_index.resolve_directory(('empty',))
    assert counters == dict(inputs=2, discovery=2, index=2)


def test_empty_and_missing_roots_are_real_captured_results(inputs, counters):
    """Empty success is memoized; a new attempt sees newly populated roots."""
    root, _ = inputs
    root.rmdir()
    view = resolution._make_compilation_environment()
    selected = view.catalog('local')
    assert selected.names() == frozenset()
    write(root, 'new.nf')
    assert view.catalog('local') is selected and not selected.names()
    assert resolution._make_compilation_environment().catalog('local').names() == {'new'}
    assert counters == dict(inputs=2, discovery=2, index=2)


@pytest.mark.parametrize('failure', [CompileError('bad Local'), PermissionError(13, 'denied', '/tree')])
def test_failed_capture_replays_fresh_exceptions_without_retry(inputs, counters, monkeypatch, failure):
    """No partial index or traceback-bearing exception is retained after a failure."""
    def fail(*args, **kwargs):
        """Inject a discovery failure after the input boundary."""
        raise failure
    monkeypatch.setattr(catalog, 'discover_local_sources', fail)
    view = resolution._make_compilation_environment()
    selected = view.catalog('local')
    errors = []
    for _ in range(2):
        assert view.catalog('local') is selected
        with pytest.raises(type(failure)) as error:
            selected.records()
        errors.append(error.value)
    assert errors[0] is not errors[1] and all(str(e) == str(failure) for e in errors)
    assert selected.local_index is None and not selected.entries
    assert counters['inputs'] == 1 and counters['index'] == 0


def test_public_environment_remains_complete_and_eager(inputs, counters):
    """Public snapshots enumerate on construction and exclude later filesystem changes."""
    root, _ = inputs
    first = resolution.resolve_environment()
    assert isinstance(first, ResolvedEnvironment)
    assert set(first.catalogs) == {'functions', 'examples', 'local'}
    assert counters == dict(inputs=1, discovery=1, index=1)
    write(root, 'new.nf')
    assert not first.catalog('local').names()
    assert resolution.resolve_environment().catalog('local').names() == {'new'}
    assert counters == dict(inputs=2, discovery=2, index=2)
    with pytest.raises(ValueError, match='requires functions, examples, and local'):
        ResolvedEnvironment({'examples': first.catalog('examples')})


def test_nonlocal_parts_freeze_and_reject_invalid_contracts(inputs):
    """Private carrier validates canonical owner/catalog contracts before use."""
    parts, local = resolution._resolve_environment_parts(capture_local=False)
    assert local is None
    with pytest.raises(FrozenInstanceError):
        parts.extension_registry = None
    with pytest.raises(TypeError):
        parts.package_namespaces['bad'] = object()
    with pytest.raises(ValueError, match='functions and examples'):
        replace(parts, functions_catalog=ResolvedCatalog('local', {}))
    namespace = ResolvedPackageNamespace('vendor.a', 'p', 'A', '1.0', {})
    with pytest.raises(ValueError, match='canonical package id'):
        replace(parts, package_namespaces={'wrong': namespace})
    other = ResolvedPackageNamespace('vendor.b', 'p', 'B', '1.0', {})
    with pytest.raises(ValueError, match='import names must be unique'):
        replace(parts, package_namespaces={'vendor.a': namespace, 'vendor.b': other})
    valid = replace(parts, package_namespaces={'vendor.a': namespace})
    assert valid.package_by_import_name('p') is namespace
    assert valid.package_by_import_name('absent') is None
    with pytest.raises(TypeError, match='ExtensionRegistry'):
        replace(parts, extension_registry=object())


def compiler_with_fake_bpy(monkeypatch):
    """Load the production compiler facade without constructing Blender datablocks."""
    fake = ModuleType('bpy')
    fake.app = SimpleNamespace(driver_namespace={})
    fake.data = SimpleNamespace(node_groups=[])
    monkeypatch.setitem(sys.modules, 'bpy', fake)
    from NodeForge import compiler
    return compiler


def test_compiler_root_create_update_and_callback_lifetimes(inputs, counters, monkeypatch):
    """Real facade paths preserve sessions and create fresh views for each root attempt."""
    compiler = compiler_with_fake_bpy(monkeypatch)
    from NodeForge.blender.group_backend import BlenderGroupBackend
    published = []
    def publish(self, request):
        """Observe the actual build request without simulating native Blender lowering."""
        published.append(request)
        return request
    monkeypatch.setattr(BlenderGroupBackend, 'create_or_update', publish)
    monkeypatch.setattr(BlenderGroupBackend, 'resolve_root_compilation_identity',
                        lambda *args: GroupCompilationIdentity(None, 'ROOT', 'ROOT', 'ROOT'))
    compiler.create_expression_group('output(1.0)')
    compiler.update_expression_group(SimpleNamespace(name='existing'), 'output(2.0)')
    backend = compiler._new_group_backend()
    backend.compile_group_callback('output(3.0)')
    backend.compile_group_callback('output(4.0)', source_callable_session=None)
    assert counters == dict(inputs=0, discovery=0, index=0)
    views = [r.source_callable_session.resolved_environment for r in published]
    assert len({id(v) for v in views}) == 4
    supplied = backend.new_source_callable_session()
    backend.compile_group_callback('output(5.0)', source_callable_session=supplied)
    assert published[-1].source_callable_session is supplied
    write(inputs[0], 'noise.nf')
    backend.compile_group_callback('from local import noise\noutput(noise(1.0))', source_callable_session=supplied)
    first = supplied.resolved_environment.catalog('local')
    write(inputs[0], 'new.nf')
    backend.compile_group_callback('from local import *\noutput(1.0)')
    assert 'new' not in first.names()
    assert 'new' in published[-1].source_callable_session.resolved_environment.catalog('local').names()
    assert counters == dict(inputs=2, discovery=2, index=2)
    eager = resolution.resolve_environment()
    seeded = compiler._new_group_backend(eager)
    assert seeded.new_source_callable_session().resolved_environment is eager
    assert seeded.new_source_callable_session().resolved_environment is eager
    with pytest.raises(CompileError, match='does not match supplied resolved environment'):
        seeded.compile_group_callback('output(1.0)', source_callable_session=supplied)
    seeded.compile_group_callback('output(1.0)')
    assert published[-1].source_callable_session.resolved_environment is eager
    assert counters == dict(inputs=3, discovery=3, index=3)


@pytest.mark.parametrize('eager', [False, True])
def test_package_extension_lifecycle_and_capture_phase(inputs, counters, tmp_path, monkeypatch, eager):
    """Both resolver paths retain one owner snapshot/normalization and package read sequence."""
    from NodeForge.extensions import registry
    install(tmp_path, {'plain': 'x=input_float("X")\noutput(x)\n'}, extension=True)
    write(inputs[0], 'noise.nf')
    events = []
    def wrap(owner, name, event):
        """Observe production lifecycle operations after installation has completed."""
        original = getattr(owner, name)
        def observed(*args, **kwargs):
            """Record one lifecycle event and retain production behavior."""
            events.append(event)
            return original(*args, **kwargs)
        monkeypatch.setattr(owner, name, observed)
    wrap(packages, 'active_package_manifest_snapshot', 'manifests')
    wrap(packages, 'validate_package_root', 'manifest_read')
    wrap(registry, 'capture_owner_code_snapshot', 'owner')
    wrap(registry, 'normalize_interface_module', 'normalize')
    wrap(resolution, '_capture_local_catalog', 'local')
    view = resolution.resolve_environment() if eager else resolution._make_compilation_environment()
    assert events == (['manifests', 'manifest_read', 'local', 'owner', 'normalize'] if eager else
                      ['manifests', 'manifest_read', 'owner', 'normalize'])
    selected_registry = view.extension_registry
    namespace = view.package_by_import_name('p')
    native_id = namespace.find('identity').extension_callable_id
    assert selected_registry.contains(native_id)
    # Avoid a runtime call to the native owner: preparation of package source is sufficient.
    prepare('from packages import p\noutput(p.plain(1.0))', view)
    before_demand = list(events)
    prepare('from local import noise\noutput(noise(1.0))', view)
    assert view.extension_registry is selected_registry
    assert view.package_by_import_name('p') is namespace
    assert events == before_demand + ([] if eager else ['local'])
    assert counters == dict(inputs=1, discovery=1, index=1)


def test_invalid_package_and_broken_examples_keep_independent_diagnostics(inputs, counters, tmp_path, monkeypatch):
    """Deferred Local does not change rejected packages or mandatory Examples errors."""
    installed = install(tmp_path, {'plain': 'output(1.0)\n'}, extension=True)
    (installed.root / 'systems/native/interface.py').write_text('EXTENSION_API=2\nEXTENSIONS={}\n')
    view = resolution._make_compilation_environment()
    assert view.package_by_import_name('p') is None
    with pytest.raises(CompileError, match='Unknown package'):
        prepare('from packages import p\noutput(1.0)', view)
    assert counters == dict(inputs=0, discovery=0, index=0)
    original = catalog.candidate_records_from_inputs
    def broken_examples(namespace, **kwargs):
        """Fail Examples discovery independently of Local state."""
        if namespace == 'examples':
            raise CompileError('broken examples')
        return original(namespace, **kwargs)
    monkeypatch.setattr(catalog, 'candidate_records_from_inputs', broken_examples)
    broken_view = resolution._make_compilation_environment()
    with pytest.raises(CompileError, match='broken examples'):
        prepare('output(1.0)', broken_view)
    assert counters == dict(inputs=0, discovery=0, index=0)


def test_inherited_alias_uses_exact_lazy_index_and_rejects_foreign_snapshot(inputs, counters, monkeypatch):
    """Inherited directory binding validation uses the root slot's canonical index identity."""
    from NodeForge.semantic import group as frontend
    root, _ = inputs
    write(root, 'terrain/noise.nf')
    view = resolution._make_compilation_environment()
    original = frontend._validate_import_bindings
    seen = []
    def observe(*args, **kwargs):
        """Retain inherited bindings observed at the authoritative semantic boundary."""
        result = original(*args, **kwargs)
        seen.extend((kwargs.get('directory_bindings') or {}).values())
        return result
    monkeypatch.setattr(frontend, '_validate_import_bindings', observe)
    prepare('from local import terrain as t\ndef helper(x: Float):\n    return t.noise(x)\noutput(helper(1.0))', view)
    captured = view.catalog('local')
    assert seen and all(binding.index_snapshot is captured.local_index for binding in seen)
    assert counters == dict(inputs=1, discovery=1, index=1)
    other = resolution._make_compilation_environment()
    binding = seen[0]
    with pytest.raises(CompileError, match='inherited Local namespace'):
        frontend._validate_import_bindings(
            (), (), {}, other, inherited_local_namespaces={binding.source_name: binding})
    assert counters == dict(inputs=2, discovery=2, index=2)


def test_registry_changes_before_demand_and_frozen_failure_after_repair(inputs, counters, tmp_path):
    """Registry selection is live before demand and immutable after success or failure."""
    root, registry = inputs
    first = resolution._make_compilation_environment()
    external = tmp_path / 'external' / 'terrain'
    write(external, 'noise.nf')
    local_sources.link_local_source_folder(str(external))
    assert first.catalog('local').names() == {'noise'}
    local_sources.unlink_local_source_folder(external)
    assert first.catalog('local').names() == {'noise'}
    assert not resolution._make_compilation_environment().catalog('local').names()
    registry.write_text('{"version":999,"roots":[]}')
    failed = resolution._make_compilation_environment()
    selected = failed.catalog('local')
    registry.write_text('{"version":1,"roots":[],"files":[]}')
    for _ in range(2):
        assert failed.catalog('local') is selected
        with pytest.raises(CompileError, match='registry format'):
            selected.names()
    assert not resolution._make_compilation_environment().catalog('local').names()
    assert counters == dict(inputs=4, discovery=3, index=3)
