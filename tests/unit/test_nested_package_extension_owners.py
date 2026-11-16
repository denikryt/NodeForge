"""Nested Functions Extension API v2 owner identity and frozen dispatch tests."""

import json
import sys
import types
from pathlib import Path

import pytest

from NodeForge import packages, local_sources
from NodeForge.errors import CompileError
from NodeForge.environment_resolution import resolve_environment
from NodeForge.extensions.registry import library_owner_key


@pytest.fixture(autouse=True)
def _isolated_packages(tmp_path, monkeypatch):
    """Isolate package state and Blender-managed Local path for pure Python tests."""
    fake = types.ModuleType('bpy')
    fake.app = types.SimpleNamespace(driver_namespace={})
    fake.data = types.SimpleNamespace(node_groups=[])
    fake.utils = types.SimpleNamespace(user_resource=lambda *a, **k: str(tmp_path/'user'))
    monkeypatch.setitem(sys.modules, 'bpy', fake)
    monkeypatch.setattr(local_sources, '_catalog_input_paths', lambda: ((), ()))
    packages.set_packages_dir_for_tests(tmp_path / 'state')
    yield
    packages.set_packages_dir_for_tests(None)


def _owner(root: Path, member: str, *, offset: float = 0.0) -> None:
    """Create one native owner that returns an observable offset value."""
    root.mkdir(parents=True)
    (root/'interface.py').write_text('''from typing import Annotated
from NodeForge import EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {"'''+member+'''": ".backend:build"}
def '''+member+'''(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
''')
    (root/'backend.py').write_text(f'def build(context, value):\n    return value + {offset}\n')


def _install(root):
    """Install a package with colliding native owner leaves and one system."""
    pkg = root/'pkg'
    (pkg/'functions').mkdir(parents=True)
    (pkg/'systems').mkdir(parents=True)
    _owner(pkg/'functions/mesh/smooth', 'smooth', offset=1.0)
    _owner(pkg/'functions/curve/smooth', 'smooth', offset=2.0)
    _owner(pkg/'systems/ops', 'smooth', offset=3.0)
    (pkg/'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id':'vendor.nested', 'import_name':'nested',
        'name':'Nested', 'version':'1.0.0', 'author':'Tests',
        'description':'nested extension owners', 'contents': {'functions':'functions','systems':'systems'},
        'nodeforge_min_version':'0.65.0', 'permissions':{'python':True},
    }))
    return packages.install_package_directory(pkg)


def test_nested_native_owners_are_distinct_and_frozen(tmp_path):
    """Identical leaves do not reuse backend module state or ExtensionCallableIds."""
    installed = _install(tmp_path)
    env = resolve_environment()
    ns = env.package_by_import_name('nested')
    assert set(ns.qualified_exports) == {'mesh.smooth', 'curve.smooth', 'smooth'}
    assert ns.find('smooth') is ns.find_path('smooth')
    by_path = {path: ns.find_path(path).extension_callable_id for path in ns.qualified_exports}
    assert len(set(by_path.values())) == 3
    assert [env.extension_registry.invoke_implementation(by_path[path], None, 5.0)
            for path in ('mesh.smooth','curve.smooth','smooth')] == [6.0,7.0,8.0]
    root_key = library_owner_key(type('Root', (), {'namespace':'functions',
                                                   'package_id':'vendor.nested','name':'smooth',
                                                   'folder_path':''})())
    assert root_key == ('library','functions','vendor.nested','smooth')
    # Mutation after capture cannot alter the selected compilation revision.
    (installed.root/'functions/mesh/smooth/backend.py').write_text(
        'def build(context, value):\n    return value + 10.0\n')
    assert env.extension_registry.invoke_implementation(by_path['mesh.smooth'], None, 5.0) == 6.0
    fresh = resolve_environment()
    new_id = fresh.package_by_import_name('nested').find_path('mesh.smooth').extension_callable_id
    assert new_id == by_path['mesh.smooth']  # stable owner identity
    assert fresh.extension_registry.invoke_implementation(new_id, None, 5.0) == 15.0
    assert env.extension_registry.invoke_implementation(by_path['curve.smooth'], None, 5.0) == 7.0


@pytest.mark.parametrize('corrupt', ['missing', 'extra', 'wrong'])
def test_nested_native_interface_requires_exact_single_owner_export(tmp_path, corrupt):
    """Malformed native declarations fail package activation as one unit."""
    installed = _install(tmp_path)
    owner = installed.root/'functions/mesh/smooth/interface.py'
    text = owner.read_text()
    if corrupt == 'missing':
        text = text.replace('EXTENSIONS = {"smooth": ".backend:build"}', 'EXTENSIONS = {}')
    elif corrupt == 'extra':
        text = text.replace('EXTENSIONS = {"smooth": ".backend:build"}',
                            'EXTENSIONS = {"smooth": ".backend:build", "extra": ".backend:build"}')
    else:
        text = text.replace('"smooth": ".backend:build"', '"other": ".backend:build"')
    owner.write_text(text)
    namespace = resolve_environment().package_by_import_name('nested')
    assert namespace is not None and not namespace.exports and not namespace.qualified_exports
    with pytest.raises(CompileError):
        namespace.find_path('mesh.smooth')


def test_nested_native_backend_symlink_is_rejected_before_execution(tmp_path):
    """Owner snapshots refuse symlinked Python files and retain no partial members."""
    installed = _install(tmp_path)
    backend = installed.root/'functions/mesh/smooth/backend.py'
    backend.unlink()
    external=tmp_path/'external.py';external.write_text('def build(context,value): return 999\n')
    backend.symlink_to(external)
    ns=resolve_environment().package_by_import_name('nested')
    assert not ns.qualified_exports
    with pytest.raises(CompileError, match='symlink'):
        ns.find('smooth')


def test_same_stem_source_module_cannot_hide_invalid_native_owner(tmp_path):
    """Every physical owner is validated even when canonical owner stems coincide."""
    pkg = tmp_path / 'same_stem'; functions = pkg / 'functions'
    _owner(functions / 'primitives', 'primitives')
    (functions / 'primitives.nf').write_text('def cube(x: Float) -> Float:\n    return x\n')
    interface = functions / 'primitives' / 'interface.py'
    interface.write_text(interface.read_text().replace('"primitives":', '"other":').replace('def primitives(', 'def other('))
    (pkg / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.same_stem', 'import_name': 'same_stem',
        'name': 'Same Stem', 'version': '1.0.0', 'author': 'Review',
        'description': 'owner collision', 'nodeforge_min_version': '0.65.0',
        'contents': {'functions': 'functions'}, 'permissions': {'python': True},
    }))
    with pytest.raises(packages.PackageError, match='must declare exactly one EXTENSIONS'):
        packages.install_package_directory(pkg)


def test_system_terminal_and_source_namespace_prefix_can_coexist(tmp_path):
    """A source namespace may share a prefix with a root Systems callable."""
    pkg = tmp_path / 'system_prefix'; functions = pkg / 'functions'
    source = functions / 'primitives'; source.mkdir(parents=True)
    (source / 'source.nf').write_text('def cube(x: Float) -> Float:\n    return x\n')
    _owner(pkg / 'systems' / 'owner', 'primitives')
    (pkg / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.prefix', 'import_name': 'prefix',
        'name': 'Prefix', 'version': '1.0.0', 'author': 'Review',
        'description': 'owner prefix', 'nodeforge_min_version': '0.65.0',
        'contents': {'functions': 'functions', 'systems': 'systems'},
        'permissions': {'python': True},
    }))
    packages.install_package_directory(pkg)
    namespace = resolve_environment().package_by_import_name('prefix')
    assert namespace.find('primitives').extension_callable_id is not None
    assert namespace.find_path('primitives.cube').definition is not None
