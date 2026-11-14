"""Package Functions .nf modules export per-definition frozen source identities."""

import json
import sys
import types

import pytest

from NodeForge import packages
from NodeForge.environment_resolution import resolve_environment
from NodeForge.errors import CompileError
from NodeForge.semantic.call_resolution import (CallableEnvironment, CallableKind,
    resolve_package_callable)
from NodeForge.semantic.source_bindings import PackageNamespaceBinding


@pytest.fixture(autouse=True)
def _isolate_packages(tmp_path, monkeypatch):
    """Use a private package inventory; no Blender executable needed."""
    fake = types.ModuleType('bpy')
    fake.app = types.SimpleNamespace(driver_namespace={})
    fake.data = types.SimpleNamespace(node_groups=[])
    fake.utils = types.SimpleNamespace(user_resource=lambda *a, **k: str(tmp_path / 'user'))
    monkeypatch.setitem(sys.modules, 'bpy', fake)
    packages.set_packages_dir_for_tests(tmp_path / 'inventory')
    yield
    packages.set_packages_dir_for_tests(None)


def _install(root, files):
    """Create and register one package with a flat Functions directory."""
    root.mkdir(parents=True)
    functions = root / 'functions'
    functions.mkdir()
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.shapes', 'import_name': 'shapes',
        'name': 'Shapes', 'version': '1.0.0', 'author': 'Unit Tests',
        'description': 'Module export boundary regression fixture',
        'contents': {'functions': 'functions'},
        'nodeforge_min_version': '0.65.0', 'nodeforge_max_version': None,
        'permissions': {'python': False},
    }), encoding='utf-8')
    for name, content in files.items():
        path = functions / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    return packages.install_package_directory(root)


def _resolve_member(env, name):
    """Use the same package qualified-call resolver as semantic compilation."""
    namespace = env.package_by_import_name('shapes')
    assert namespace is not None
    environment = CallableEnvironment(frozenset(), {}, {},
        package_namespaces={'shapes': PackageNamespaceBinding('shapes', namespace)})
    return resolve_package_callable('shapes', name, environment)


def test_all_function_definitions_export_under_the_package_namespace(tmp_path):
    """The .nf filename is not the callable export and private prefixes are public."""
    _install(tmp_path / 'pkg', {
        'geometry.nf': '''def move(x: Float) -> Float:
    return x + 1

def resize(x: Float) -> Float:
    return x * 2

def _helper(x: Float) -> Float:
    return x

cube(1.0)''',
    })
    env = resolve_environment()
    ns = env.package_by_import_name('shapes')
    assert set(ns.exports) == {'move', 'resize', '_helper'}
    assert 'geometry' not in ns.exports
    assert {export.definition.name for export in ns.exports.values()} == {'move', 'resize', '_helper'}
    assert len({id(export.source_snapshot) for export in ns.exports.values()}) == 1
    resolved = _resolve_member(env, '_helper')
    assert resolved.kind is CallableKind.MODULE_DEFINITION
    assert resolved.target.definition == ns.find('_helper').definition


def test_package_exports_use_one_revision_even_if_source_changes_after_discovery(tmp_path):
    """File modifications never change the environment's published source revision."""
    installed = _install(tmp_path / 'pkg', {'geometry.nf': 'def move(x: Float):\n    return x + 1\n'})
    env = resolve_environment()
    old = env.package_by_import_name('shapes').find('move').source_snapshot
    (installed.root / 'functions' / 'geometry.nf').write_text('def resize(x: Float):\n    return x * 3\n')
    assert _resolve_member(env, 'move').target.snapshot is old
    assert 'move' in old.definitions and 'resize' not in old.definitions
    fresh = resolve_environment().package_by_import_name('shapes')
    assert 'resize' in fresh.exports and 'move' not in fresh.exports


@pytest.mark.parametrize('files, diagnostics', [
    ({'a.nf': 'def move(x: Float):\n    return x\n',
      'b.nf': 'def move(x: Float):\n    return x * 2\n'}, ('a.nf', 'b.nf')),
    ({'a.nf': 'def move(:\n    return 1\n'}, ('Syntax',)),
])
def test_invalid_package_exports_remain_visible_as_explicit_access_errors(tmp_path, files, diagnostics):
    """A malformed package is not silently mistaken for an unknown import."""
    _install(tmp_path / 'pkg', files)
    env = resolve_environment()
    ns = env.package_by_import_name('shapes')
    assert ns is not None
    assert not ns.exports
    with pytest.raises(CompileError) as raised:
        _resolve_member(env, 'move')
    text = str(raised.value)
    for fragment in diagnostics:
        assert fragment.lower() in text.lower()


def test_unknown_package_function_reports_selected_member_name(tmp_path):
    """Only exact members of the selected package namespace resolve."""
    _install(tmp_path / 'pkg', {'geometry.nf': 'def move(x: Float):\n    return x\n'})
    with pytest.raises(CompileError, match="no callable member 'resize'"):
        _resolve_member(resolve_environment(), 'resize')


def test_source_definition_collides_with_extension_api_export(tmp_path):
    """Function declarations cannot shadow physical Extension API v2 members."""
    _install(tmp_path / 'pkg', {
        'geometry.nf': 'def move(x: Float):\n    return x\n',
    })
    # Install a second, complete version of the same package containing a
    # systems extension with the identical namespace export name.
    root = tmp_path / 'replacement'
    root.mkdir()
    (root / 'functions').mkdir()
    (root / 'functions' / 'geometry.nf').write_text(
        'def move(x: Float):\n    return x\n', encoding='utf-8')
    owner = root / 'systems' / 'move'
    owner.mkdir(parents=True)
    (owner / 'interface.py').write_text(
        'from typing import Annotated\n'
        'from NodeForge import Float, EvaluationMode\n'
        'EXTENSION_API = 2\n'
        'EXTENSIONS = {"move": ".backend:build"}\n'
        'def move(x: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n', encoding='utf-8')
    (owner / 'backend.py').write_text(
        'def build(context, x):\n    return x\n', encoding='utf-8')
    manifest = tmp_path / 'pkg' / 'nodeforge_package.json'
    metadata = json.loads(manifest.read_text(encoding='utf-8'))
    metadata['contents']['systems'] = 'systems'
    metadata['permissions']['python'] = True
    (root / 'nodeforge_package.json').write_text(json.dumps(metadata), encoding='utf-8')
    packages.install_package_directory(root, replace=True)
    namespace = resolve_environment().package_by_import_name('shapes')
    assert namespace is not None
    assert not namespace.exports
    with pytest.raises(CompileError, match='move'):
        _resolve_member(resolve_environment(), 'move')


def test_imported_package_definition_compiles_from_captured_snapshot(tmp_path):
    """Top-level composition in a module is never lowered when a member is called."""
    installed = _install(tmp_path / 'pkg', {'geometry.nf': '''def leaf(x: Float):
    return x + 1

def move(x: Float):
    return leaf(x)

cube(1)
'''})
    env = resolve_environment()
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    session = SourceCallableSession(resolved_environment=env)
    group = analyze_group_source('from packages import shapes\nv = shapes.move(5)',
        compilation_identity=GroupCompilationIdentity(None, 'PACKAGE/CALL', 'PACKAGE/CALL', 'PACKAGE/CALL'),
        resolved_environment=env, source_callable_session=session)
    assert {key.function_id.name for key in session.prepared} == {'move', 'leaf'}
    assert all(key.function_id.kind == 'MODULE_DEF' for key in session.prepared)
    assert not any('cube(' in item.group.source for item in session.prepared.values())
    (installed.root / 'functions' / 'geometry.nf').write_text('not valid python!\n', encoding='utf-8')
    assert all(item.group.source for item in session.prepared.values())


def test_imported_package_definition_bad_call_rejects_before_materialization(tmp_path):
    _install(tmp_path / 'pkg', {'geometry.nf': 'def move(x: Float):\n    return x\n'})
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    env = resolve_environment()
    session = SourceCallableSession(resolved_environment=env)
    with pytest.raises(CompileError, match='missing required'):
        analyze_group_source('from packages import shapes\nv = shapes.move()',
            compilation_identity=GroupCompilationIdentity(None, 'PACKAGE/CALL', 'PACKAGE/CALL', 'PACKAGE/CALL'),
            resolved_environment=env, source_callable_session=session)
    assert not session.prepared
