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
