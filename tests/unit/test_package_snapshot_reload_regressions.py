"""Admission, Examples registry and reload snapshot regressions for qualified package-callable."""

import json
import sys
import types
from pathlib import Path

import pytest

from NodeForge import packages
from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.extension_contracts import ExtensionCallableId
from NodeForge.extension_registry import library_owner_key
from NodeForge.resolved_environment import (
    PackageCallableExport, ResolvedCatalog, ResolvedEnvironment,
    ResolvedPackageNamespace,
)
from NodeForge.environment_resolution import resolve_environment
from NodeForge.semantic.group import analyze_group_source

pytestmark = pytest.mark.unit


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    fake = types.ModuleType('bpy')
    fake.app = types.SimpleNamespace(driver_namespace={})
    fake.data = types.SimpleNamespace(node_groups=[])
    fake.utils = types.SimpleNamespace(user_resource=lambda *a, **k: str(tmp_path / 'user'))
    monkeypatch.setitem(sys.modules, 'bpy', fake)
    packages.set_packages_dir_for_tests(tmp_path / 'inventory')
    yield
    packages.set_packages_dir_for_tests(None)


def write_package(root, package_id, *, import_name='shared', examples=False):
    root.mkdir(parents=True)
    catalog = 'examples' if examples else 'functions'
    (root / catalog).mkdir()
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': package_id, 'import_name': import_name,
        'name': package_id, 'version': '1.0.0', 'author': 'Tests',
        'description': 'qualified package-callable review regression', 'contents': {catalog: catalog},
        'nodeforge_min_version': '0.65.0', 'nodeforge_max_version': None,
        'permissions': {'python': examples},
    }))
    if not examples:
        (root / catalog / 'foo.nf').write_text('output(value=1)\n')
    return root


@pytest.mark.parametrize('replacement', [False, True])
def test_broken_callable_inventory_keeps_manifest_import_reservation(tmp_path, replacement):
    root = write_package(tmp_path / 'old', 'vendor.old')
    manifest_path = root / 'nodeforge_package.json'
    manifest = json.loads(manifest_path.read_text())
    manifest['contents']['systems'] = 'systems'
    manifest['permissions']['python'] = True
    manifest_path.write_text(json.dumps(manifest))
    owner = root / 'systems' / 'demo'
    owner.mkdir(parents=True)
    (owner / 'interface.py').write_text(
        'from typing import Annotated\nfrom NodeForge import Float, EvaluationMode\nEXTENSION_API = 2\n'
        'EXTENSIONS = {"system_value": ".backend:build"}\n'
        'def system_value(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'
    )
    (owner / 'backend.py').write_text('def build(context, value):\n    return value\n')
    installed = packages.install_package_directory(root, allow_python=True)
    # Invalid declarations leave the manifest structurally active.
    (installed.root / 'systems' / 'demo' / 'interface.py').write_text(
        'EXTENSION_API = 2\nEXTENSIONS = {}\n'
    )
    assert [m.package_id for m in packages.active_package_manifest_snapshot()] == ['vendor.old']
    environment = resolve_environment()
    assert environment.package_by_id('vendor.old') is None
    candidate = write_package(tmp_path / 'new', 'vendor.old' if replacement else 'vendor.new')
    if replacement:
        packages.install_package_directory(candidate, allow_python=False, replace=True)
        assert resolve_environment().package_by_import_name('shared').package_id == 'vendor.old'
    else:
        before = packages.load_package_state()
        with pytest.raises(packages.PackageError, match='already owned by vendor.old'):
            packages.install_package_directory(candidate, allow_python=False)
        assert packages.load_package_state() == before
        assert resolve_environment().package_by_id('vendor.new') is None


def write_example(root, name='review_example'):
    owner = root / 'examples' / name
    owner.mkdir()
    (owner / 'interface.py').write_text(
        'from typing import Annotated\n'
        'from NodeForge import Float, EvaluationMode\n'
        'EXTENSION_API = 2\n'
        f'EXTENSIONS = {{{name!r}: ".backend:build"}}\n'
        f'def {name}(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'
    )
    (owner / 'backend.py').write_text('def build(context, value):\n    return value\n')
    return owner


def compile_example(environment):
    return analyze_group_source(
        'from examples import review_example\nx = input_float("X")\noutput(review_example(x))\n',
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/review', 'definition', 'decl'),
        resolved_environment=environment,
    )


def test_python_example_import_uses_captured_registry_and_is_not_package_export(tmp_path):
    root = write_package(tmp_path / 'source', 'vendor.examples', examples=True)
    write_example(root)
    installed = packages.install_package_directory(root, allow_python=True)
    environment = resolve_environment()
    record = environment.catalog('examples').find('review_example')
    callable_id = ExtensionCallableId(library_owner_key(record), record.name)
    assert environment.extension_registry.contains(callable_id)
    compile_example(environment)
    assert 'review_example' not in environment.package_by_id('vendor.examples').exports
    (installed.root / 'examples' / record.name / 'backend.py').write_text(
        'def build(context, value):\n    raise RuntimeError("live mutation")\n'
    )
    assert environment.extension_registry.invoke_implementation(callable_id, None, 7) == 7


@pytest.mark.parametrize('corruption', ['syntax', 'wrong_name', 'snapshot'])
def test_invalid_python_example_is_deferred_catalog_failure(tmp_path, corruption):
    root = write_package(tmp_path / 'source', 'vendor.examples', examples=True)
    write_example(root)
    installed = packages.install_package_directory(root, allow_python=True)
    owner = installed.root / 'examples' / 'review_example'
    if corruption == 'syntax':
        (owner / 'interface.py').write_text('not valid python!\n')
    elif corruption == 'wrong_name':
        path = owner / 'interface.py'
        path.write_text(path.read_text().replace('review_example', 'other_name'))
    else:
        outside = tmp_path / 'outside.py'
        outside.write_text('value = 1\n')
        (owner / 'escape.py').symlink_to(outside)
    environment = resolve_environment()
    assert environment.package_by_id('vendor.examples') is not None
    assert not environment.catalog('examples').entries
    with pytest.raises(CompileError):
        compile_example(environment)
    callable_id = ExtensionCallableId(('library', 'examples', 'vendor.examples', 'review_example'), 'review_example')
    assert not environment.extension_registry.contains(callable_id)


def reload_environment(tmp_path, namespace, *, owner_present=True):
    from NodeForge.catalog import LibraryEntryRecord
    path = tmp_path / 'foo.nf'
    path.write_text('output(value=1)\n')
    record = LibraryEntryRecord(namespace, 'foo', 'source', path, source_path=path,
                                package_id='vendor.old', package_version='1.0.0')
    catalogs = {n: ResolvedCatalog(n, {'foo': record} if n == namespace and n != 'functions' and owner_present else {})
                for n in ('functions', 'examples', 'local')}
    owners = {}
    if namespace == 'functions' and owner_present:
        owners['vendor.old'] = ResolvedPackageNamespace('vendor.old', 'old', 'Old', '1.0.0', {
            'foo': PackageCallableExport('vendor.old', 'foo', record=record),
        })
    return ResolvedEnvironment(catalogs, package_namespaces=owners), record


@pytest.mark.parametrize('namespace', ['functions', 'examples', 'local'])
@pytest.mark.parametrize('owner_present', [True, False])
def test_reload_record_and_backend_share_one_snapshot(tmp_path, monkeypatch, namespace, owner_present):
    from NodeForge import compiler
    environment, record = reload_environment(tmp_path, namespace, owner_present=owner_present)
    group = {'nodeforge_library_namespace': namespace, 'nodeforge_library_name': 'foo',
             'nodeforge_package_id': 'vendor.old'}
    resolved = []
    def resolve_once():
        resolved.append(environment)
        assert len(resolved) == 1
        return environment
    monkeypatch.setattr(compiler, 'resolve_environment', resolve_once)
    backend = object()
    def new_backend(snapshot):
        assert snapshot is environment
        return backend
    monkeypatch.setattr(compiler, '_new_group_backend', new_backend)
    calls = []
    monkeypatch.setattr(compiler, 'update_materialized_library_entry_group_for_record',
                        lambda r, g, b: calls.append((r, g, b)))
    if owner_present:
        compiler.update_library_catalog_group(group, namespace, 'foo')
        assert calls == [(record, group, backend)]
    else:
        with pytest.raises(CompileError, match='unavailable'):
            compiler.update_library_catalog_group(group, namespace, 'foo')
        assert calls == []
    assert resolved == [environment]


def test_bundled_python_example_is_registered_without_package_namespace(tmp_path, monkeypatch):
    from NodeForge import catalog
    root = write_package(tmp_path / 'bundled', 'fixture.bundled', examples=True)
    write_example(root)
    original = catalog.catalog_dir
    monkeypatch.setattr(catalog, 'catalog_dir', lambda n: root / 'examples' if n == 'examples' else original(n))
    environment = resolve_environment()
    record = environment.catalog('examples').find('review_example')
    assert record.package_id == ''
    compile_example(environment)
    assert not environment.package_namespaces
    callable_id = ExtensionCallableId(library_owner_key(record), record.name)
    assert environment.extension_registry.invoke_implementation(callable_id, None, 5) == 5


def test_example_catalog_failure_does_not_publish_partial_sessions(tmp_path):
    root = write_package(tmp_path / 'source', 'vendor.examples', examples=True)
    write_example(root, 'a_good')
    write_example(root, 'z_bad')
    installed = packages.install_package_directory(root, allow_python=True)
    path = installed.root / 'examples' / 'z_bad' / 'interface.py'
    path.write_text(path.read_text().replace('z_bad', 'wrong_name'))
    environment = resolve_environment()
    with pytest.raises(CompileError, match='exactly one EXTENSIONS key'):
        environment.catalog('examples').names()
    assert not environment.catalog('examples').entries
    for name in ('a_good', 'z_bad'):
        callable_id = ExtensionCallableId(('library', 'examples', 'vendor.examples', name), name)
        assert not environment.extension_registry.contains(callable_id)


def test_examples_are_not_exposed_as_package_members(tmp_path):
    root = write_package(tmp_path / 'source', 'vendor.examples', examples=True)
    write_example(root)
    packages.install_package_directory(root, allow_python=True)
    environment = resolve_environment()
    with pytest.raises(CompileError, match="has no callable member 'review_example'"):
        analyze_group_source(
            'from packages import shared\nx = input_float("X")\noutput(shared.review_example(x))\n',
            compilation_identity=GroupCompilationIdentity(None, 'ROOT/review', 'definition', 'decl'),
            resolved_environment=environment,
        )


@pytest.mark.parametrize('namespace', ['functions', 'examples', 'local'])
def test_reload_rejects_wrong_provenance_before_backend_creation(tmp_path, monkeypatch, namespace):
    from NodeForge import compiler
    environment, _ = reload_environment(tmp_path, namespace)
    group = {'nodeforge_library_namespace': namespace, 'nodeforge_library_name': 'other',
             'nodeforge_package_id': 'vendor.old'}
    monkeypatch.setattr(compiler, 'resolve_environment', lambda: environment)
    def forbidden(*a, **k):
        pytest.fail('Invalid reload provenance reached backend creation')
    monkeypatch.setattr(compiler, '_new_group_backend', forbidden)
    with pytest.raises(CompileError, match='belongs to'):
        compiler.update_library_catalog_group(group, namespace, 'foo')


@pytest.mark.parametrize('owner_present', [True, False])
def test_library_reload_adapter_uses_backend_session_snapshot(tmp_path, monkeypatch, owner_present):
    from NodeForge.blender import library_groups
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    environment, record = reload_environment(tmp_path, 'functions', owner_present=owner_present)
    session = SourceCallableSession(resolved_environment=environment)
    group = {'nodeforge_library_namespace': 'functions', 'nodeforge_library_name': 'foo',
             'nodeforge_package_id': 'vendor.old'}
    sessions = []
    def new_session():
        sessions.append(session)
        return session
    backend = types.SimpleNamespace(new_source_callable_session=new_session)
    updates = []
    monkeypatch.setattr(library_groups, 'update_materialized_library_entry_group_for_record',
                        lambda r, g, b, **k: updates.append((r, g, b, k)))
    if owner_present:
        library_groups.update_materialized_library_entry_group('functions', 'foo', group, backend)
        assert updates == [(record, group, backend, {'source_callable_session': session})]
    else:
        with pytest.raises(CompileError, match='unavailable'):
            library_groups.update_materialized_library_entry_group('functions', 'foo', group, backend)
        assert updates == []
    assert sessions == [session]
