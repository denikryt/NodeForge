"""Examples runtime trust, AST discovery, exact owner selection and isolation."""

import json
from pathlib import Path

import pytest

from NodeForge import catalog, environment_resolution, local_sources, packages
from NodeForge.errors import CompileError
from NodeForge.extensions.contracts import ExtensionCallableId
from NodeForge.local_runtime import local_runtime_owner_key
from NodeForge.tests.unit.test_local_mesh_compute_registry import _prepare

pytestmark = pytest.mark.unit


@pytest.fixture
def example_root(tmp_path, monkeypatch):
    """Replace the bundled catalog and persistent permission registry atomically."""
    root = tmp_path / 'bundled' / 'examples'
    root.mkdir(parents=True)
    original = catalog.catalog_dir
    monkeypatch.setattr(catalog, 'catalog_dir', lambda ns: root if ns == 'examples' else original(ns))
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'trust.json')
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: tmp_path / 'Local')
    return root


def write_python(root, relative='terrain/erosion.py', *, marker=None):
    """Write a declaration whose harmless side effect must not execute on scan."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    side_effect = f'open({str(marker)!r}, "w").write("BAD")\n' if marker else ''
    path.write_text(side_effect + 'from NodeForge import mesh_compute\n'
                    '@mesh_compute(reads=("height",), writes=("out_height",))\n'
                    f'def {path.stem}(mesh, amount: float = 2.0):\n'
                    '    return {"point_attributes": {"out_height": [amount] * mesh.vertex_count}}\n',
                    encoding='utf-8')
    return path


def root_code(namespace='examples.terrain'):
    return f'from {namespace} import erosion\ngeo = input_geometry("Geometry")\noutput(erosion(geo))\n'


def test_ast_only_discovery_executes_no_unselected_python(example_root):
    """Selected Examples Python sources need no grant and are not executed during discovery."""
    marker = example_root / 'executed'
    source = write_python(example_root, marker=marker)
    ordinary = example_root / 'terrain' / 'other.py'
    ordinary.write_text(f'open({str(marker)!r}, "w").write("BAD")\n', encoding='utf8')
    env = environment_resolution._make_compilation_environment()
    records = env.catalog('examples').records()
    assert [(entry.name, entry.kind) for entry in records] == [('erosion', 'runtime')]
    group = _prepare(root_code(), env)
    target = ExtensionCallableId(('examples-runtime', 'core', catalog._path_key(source)), 'erosion')
    assert env.extension_registry.contains(target)
    assert not marker.exists()


def test_nested_examples_dsl_and_python_share_one_directory_snapshot(example_root):
    """Keep nested DSL calls and explicit Python runtimes selectable in one catalog."""
    runtime = write_python(example_root)
    script = example_root / 'terrain' / 'offset.nf'
    script.write_text('x = input_float("X")\noutput(x + 1.0)\n', encoding='utf-8')

    view = environment_resolution._make_compilation_environment()
    examples = view.catalog('examples')
    assert examples.names() == frozenset()
    assert examples.select_import('examples.terrain', 'erosion').source_path == runtime.resolve()
    assert examples.select_import('examples.terrain', 'offset').source_path == script.resolve()
    _prepare('from examples import terrain\noutput(offset(1.0))\n', view)

    _prepare(root_code(), environment_resolution._make_compilation_environment())


def test_examples_owner_requires_real_core_root(example_root, tmp_path):
    """A runtime source outside the active Examples root is never authorized."""
    from NodeForge.local_runtime import runtime_source_active
    path = write_python(example_root)
    foreign = write_python(tmp_path / 'outside')
    assert runtime_source_active('examples', path)
    assert not runtime_source_active('examples', foreign)
    assert not runtime_source_active('examples', path, 'vendor.other')


def test_directory_alias_is_not_executable_and_nonroot_import_fails(example_root):
    """Only explicit from examples.<dir> import foo at root is executable."""
    write_python(example_root)

    with pytest.raises(CompileError, match='explicit from examples'):
        _prepare('from examples import terrain as t\ngeo = input_geometry("Geometry")\n'
                 'output(t.erosion(geo))\n', environment_resolution._make_compilation_environment())
    with pytest.raises(CompileError, match='explicit import'):
        _prepare('from examples.terrain import erosion\n'
                 'geo = input_geometry("Geometry")\n'
                 'def inner():\n    return erosion(geo)\n'
                 'output(inner())\n', environment_resolution._make_compilation_environment())
    env = environment_resolution._make_compilation_environment()
    _prepare('from examples import *\noutput(1.0)\n', env)
    assert not env.extension_registry._families


def test_python_symlinks_and_non_declarations_do_not_enter_catalog(example_root, tmp_path):
    """Disallow unreadable/escaping Python owners at filesystem discovery boundary."""
    write_python(example_root)
    foreign = write_python(tmp_path / 'foreign')
    (example_root / 'terrain' / 'external.py').symlink_to(foreign)
    with pytest.raises(CompileError, match='outside'):
        environment_resolution._make_compilation_environment().catalog('examples')._raise_if_failed()
    (example_root / 'terrain' / 'external.py').unlink()
    (example_root / 'terrain' / 'oversized.py').write_text(' ' * 1_048_577)
    names = {r.name for r in environment_resolution._make_compilation_environment().catalog('examples').records()}
    assert names == {'erosion'}


def test_source_edit_and_removal_use_fresh_root_snapshots(example_root):
    """A source edit changes its fingerprint and deletion invalidates owner membership."""
    from NodeForge.local_runtime import runtime_source_active
    file = write_python(example_root)
    first = environment_resolution._make_compilation_environment()
    _prepare(root_code(), first)
    owner = ('examples-runtime', 'core', catalog._path_key(file))
    fingerprint = first.extension_registry.owner_fingerprint(owner)
    file.write_text(file.read_text().replace('2.0', '3.0'))
    second = environment_resolution._make_compilation_environment()
    _prepare(root_code(), second)
    assert second.extension_registry.owner_fingerprint(owner) != fingerprint
    file.unlink()
    assert not runtime_source_active('examples', file)


def test_top_level_python_is_explicit_only_not_legacy_wildcard(example_root):
    """A root-level Python declaration never grows or shadows legacy star exports."""
    file = write_python(example_root, relative='erosion.py')
    (example_root / 'noise.nf').write_text('output(1.0)\n', encoding='utf8')
    view = environment_resolution._make_compilation_environment()
    selected = view.catalog('examples')
    assert selected.names() == frozenset({'noise'})
    assert {record.kind for record in selected.records()} == {'runtime', 'script'}

    _prepare(root_code('examples'), environment_resolution._make_compilation_environment())
    with pytest.raises(CompileError, match='Unknown|not importable|not defined|Unsupported'):
        _prepare('from examples import *\ngeo = input_geometry("Geometry")\noutput(erosion(geo))\n',
                 environment_resolution._make_compilation_environment())


def test_examples_owner_rechecked_for_live_stages(example_root):
    """Source availability is checked without any stored permission flags."""
    from NodeForge.local_runtime import runtime_source_active
    source = write_python(example_root)
    assert runtime_source_active('examples', source)
    assert not runtime_source_active('local', source)
    assert not runtime_source_active('examples', source, 'vendor.other')
    source.unlink()
    assert not runtime_source_active('examples', source)


@pytest.fixture
def isolated_package_root(tmp_path):
    packages.set_packages_dir_for_tests(tmp_path / 'packages')
    yield
    packages.set_packages_dir_for_tests(None)


def install_example_python(tmp_path, *, allow):
    """Install a declared Examples package under real install-state authorization."""
    root = tmp_path / 'candidate'
    write_python(root / 'examples')
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.erosion', 'import_name': 'erosionpkg',
        'name': 'Erosion', 'version': '1.0.0', 'author': 'Tests', 'description': '',
        'contents': {'examples': 'examples'}, 'permissions': {'python': True},
        'nodeforge_min_version': '0.68.0', 'nodeforge_max_version': None,
    }), encoding='utf8')
    return packages.install_package_directory(root)


def test_package_runtime_requires_active_install_owner(tmp_path, isolated_package_root, example_root):
    """Package Examples remain callable while installed, not after package removal."""
    from NodeForge.local_runtime import runtime_source_active
    installed = install_example_python(tmp_path, allow=False)
    view = environment_resolution._make_compilation_environment()
    record = next(r for r in view.catalog('examples').records()
                  if r.name == 'erosion' and r.package_id == installed.package_id)
    assert local_runtime_owner_key(record)[1] == 'vendor.erosion'
    assert runtime_source_active('examples', record.source_path, installed.package_id)
    packages.uninstall_package(installed.package_id)
    assert not runtime_source_active('examples', record.source_path, installed.package_id)
