"""Nested Functions structural inventory and validation regression tests."""

from pathlib import Path

import pytest

from NodeForge.package_function_index import scan_package_functions
from NodeForge.errors import CompileError
from NodeForge import catalog, packages


@pytest.fixture(autouse=True)
def _isolated_local_catalog(monkeypatch):
    """Avoid the Blender-managed Local folder for package-only unit fixtures."""
    from NodeForge import local_sources
    monkeypatch.setattr(local_sources, '_catalog_input_paths', lambda: ((), ()))


def _tree(root: Path, files: dict[str, str]) -> Path:
    """Create a physical Functions tree from relative fixture paths."""
    root.mkdir(parents=True, exist_ok=True)
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(data, encoding="utf-8")
    return root


def test_nested_source_modules_and_same_stem_directory_are_all_discovered(tmp_path):
    """A folder and module sharing a prefix are distinct owners."""
    root = _tree(tmp_path / "functions", {
        "mesh/primitives.nf": "def cube(x: Float):\n    return x\n",
        "mesh/primitives/detail.nf": "def cube(x: Float):\n    return x\n",
        "mesh/ops/modifiers/complex/merge.nf": "def merge(x: Float):\n    return x\n",
        "mesh/legacy/source.nf": "def old(x: Float):\n    return x\n",
        "mesh/native/interface.py": "EXTENSIONS = {}\n",
        "mesh/native/backend.py": "pass\n",
        "_private/hidden.nf": "def hidden():\n    return 1\n",
    })
    owners = scan_package_functions(root)
    assert [(owner.canonical_owner, owner.kind) for owner in owners] == [
        ("mesh.legacy", "script"),
        ("mesh.native", "extension"),
        ("mesh.ops.modifiers.complex.merge", "script"),
        ("mesh.primitives", "script"),
        ("mesh.primitives.detail", "script"),
    ]
    library_root = packages.LibraryRoot(
        path=root, package_id="vendor.test", package_name="Test", package_version="1.0",
        namespace="functions", origin="user",
    )
    discovered = catalog.candidate_records_from_inputs(
        "functions", package_roots=(library_root,), include_builtin_catalog=False,
    )
    assert len(discovered) == 5
    assert {item.folder_path for item in discovered} == {
        "mesh", "mesh/ops/modifiers/complex", "mesh/primitives",
    }


@pytest.mark.parametrize("invalid", ["mesh-bad/read.nf", "mesh/class.nf", "mesh/something-bad.nf"])
def test_public_namespace_components_are_validated_at_scan_boundary(tmp_path, invalid):
    """Publicly named invalid paths must fail before package publication."""
    root = _tree(tmp_path / "functions", {invalid: "def ok():\n    return 1\n"})
    with pytest.raises(CompileError, match="Invalid package Functions namespace component"):
        scan_package_functions(root)


def test_symlink_and_hybrid_owners_are_rejected(tmp_path):
    """Owner source paths cannot escape or combine native and source models."""
    root = _tree(tmp_path / "functions", {"mesh/ok.nf": "def ok():\n    return 1\n"})
    (root / "mesh" / "link.nf").symlink_to(root / "mesh" / "ok.nf")
    with pytest.raises(CompileError, match="symlink"):
        scan_package_functions(root)
    (root / "mesh" / "link.nf").unlink()
    _tree(root, {"hybrid/source.nf": "def ok():\n    return 1\n",
                 "hybrid/interface.py": "EXTENSIONS = {}\n"})
    with pytest.raises(CompileError, match="mixed source.nf and interface.py"):
        scan_package_functions(root)


def test_legacy_owner_backends_are_not_publicly_discovered(tmp_path):
    """Backend modules are implementation details, not extra package sources."""
    root = _tree(tmp_path / "functions", {
        "native/interface.py": "EXTENSIONS = {}\n",
        "native/backend/extra.nf": "def leaked():\n    return 1\n",
        "legacy/source.nf": "def old():\n    return 1\n",
        "legacy/hidden.nf": "def hidden():\n    return 1\n",
    })
    assert [owner.canonical_owner for owner in scan_package_functions(root)] == ["legacy", "native"]


def _install_namespace_package(tmp_path, files):
    """Install a source-only package with isolated temporary disk state."""
    import json
    packages.set_packages_dir_for_tests(tmp_path / 'state')
    root = tmp_path / 'pkg'
    functions = _tree(root / 'functions', files)
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.nested', 'import_name': 'nested',
        'name': 'Nested', 'version': '1.0.0', 'author': 'Test',
        'description': 'Nested namespace test', 'contents': {'functions': 'functions'},
        'nodeforge_min_version': '0.65.0',
    }), encoding='utf8')
    packages.install_package_directory(root)
    return functions


def test_canonical_and_short_indexes_handle_duplicate_leaves(tmp_path):
    """All canonical owners survive and an ambiguous shortcut stays invalid."""
    from NodeForge.environment_resolution import resolve_environment
    try:
        _install_namespace_package(tmp_path, {
            'mesh/primitives.nf': 'def cube(x: Float):\n    return x\n',
            'curve/primitives.nf': 'def cube(x: Float):\n    return x * 2\n',
            'geometry.nf': 'def smooth(x: Float):\n    return x\n',
        })
        namespace = resolve_environment().package_by_import_name('nested')
        assert set(namespace.qualified_exports) == {
            'mesh.primitives.cube', 'curve.primitives.cube', 'geometry.smooth',
        }
        assert namespace.find('smooth') is namespace.find_path('geometry.smooth')
        assert tuple(namespace.short_ambiguities['cube']) == (
            'curve.primitives.cube', 'mesh.primitives.cube',
        )
        with pytest.raises(CompileError, match='Ambiguous callable') as raised:
            namespace.find('cube')
        assert 'nested.curve.primitives.cube(...)' in str(raised.value)
        assert 'nested.mesh.primitives.cube(...)' in str(raised.value)
        assert namespace.find_path('mesh.primitives.cube') is not None
    finally:
        packages.set_packages_dir_for_tests(None)


def test_short_ambiguity_reports_all_root_and_nested_candidates(tmp_path):
    """Ambiguous shortcuts suggest every callable path, not only root candidates."""
    from NodeForge.environment_resolution import resolve_environment
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    from NodeForge.compiler_identities import GroupCompilationIdentity

    try:
        _install_namespace_package(tmp_path, {
            'root_A.nf': 'def shared(x: Float) -> Float:\n    return x\n',
            'root_B.nf': 'def shared(x: Float) -> Float:\n    return x * 2\n',
            'mesh/primitives.nf': 'def shared(x: Float) -> Float:\n    return x * 3\n',
            'curve/primitives.nf': 'def shared(x: Float) -> Float:\n    return x * 4\n',
        })
        env = resolve_environment()
        namespace = env.package_by_import_name('nested')
        expected = (
            'curve.primitives.shared', 'mesh.primitives.shared',
            'root_A.shared', 'root_B.shared',
        )
        assert tuple(namespace.short_ambiguities['shared']) == expected
        assert set(namespace.qualified_exports) == set(expected)
        identity = GroupCompilationIdentity(None, 'ROOT/NESTED', 'ROOT/NESTED', 'ROOT/NESTED')
        with pytest.raises(CompileError, match='Ambiguous callable') as raised:
            analyze_group_source(
                'from packages import nested as n\nx = n.shared(1.0)\n',
                compilation_identity=identity, resolved_environment=env,
                source_callable_session=SourceCallableSession(resolved_environment=env),
            )
        assert all(f'n.{member}(...)' in str(raised.value) for member in expected)
        assert all(namespace.find_path(member) is not None for member in expected)
    finally:
        packages.set_packages_dir_for_tests(None)


def test_root_source_short_name_precedes_nested_names(tmp_path):
    """A single directly rooted module definition stays the short winner."""
    from NodeForge.environment_resolution import resolve_environment
    try:
        _install_namespace_package(tmp_path, {
            'root.nf': 'def cube(x: Float):\n    return x\n',
            'mesh/primitives.nf': 'def cube(x: Float):\n    return x * 2\n',
        })
        namespace = resolve_environment().package_by_import_name('nested')
        assert namespace.find('cube') is namespace.find_path('root.cube')
        assert namespace.find_path('mesh.primitives.cube').qualified_name == 'mesh.primitives.cube'
    finally:
        packages.set_packages_dir_for_tests(None)


def test_same_canonical_source_declaration_rejects_package_atomically(tmp_path):
    """Different physical owners cannot both publish the same canonical member."""
    from NodeForge.environment_resolution import resolve_environment
    try:
        _install_namespace_package(tmp_path, {
            'mesh.nf': 'def cube(x: Float):\n    return x\n',
            'mesh/source.nf': 'def cube(x: Float):\n    return x * 2\n',
        })
        namespace = resolve_environment().package_by_import_name('nested')
        assert not namespace.exports and not namespace.qualified_exports
        with pytest.raises(CompileError, match='Duplicate package callable'):
            namespace.find('cube')
    finally:
        packages.set_packages_dir_for_tests(None)


def test_nested_dsl_root_alias_and_source_function_calls(tmp_path):
    """Dotted paths resolve into the same frozen module definition as short calls."""
    from NodeForge.environment_resolution import resolve_environment
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    from NodeForge.semantic.call_resolution import CallableEnvironment, resolve_package_callable
    from NodeForge.semantic.source_bindings import PackageNamespaceBinding
    try:
        _install_namespace_package(tmp_path, {
            'mesh/primitives.nf': 'def cube(x: Float) -> Float:\n    return x * 2\n',
        })
        env = resolve_environment()
        ns = env.package_by_import_name('nested')
        bindings = CallableEnvironment(
            frozenset(), {}, {}, package_namespaces={'n': PackageNamespaceBinding('n', ns)},
        )
        long = resolve_package_callable('n', 'mesh.primitives.cube', bindings)
        short = resolve_package_callable('n', 'cube', bindings)
        assert long.target.definition == short.target.definition
        identity = GroupCompilationIdentity(None, 'ROOT/NESTED', 'ROOT/NESTED', 'ROOT/NESTED')
        for source in [
            'from packages import nested as n\nx = n.mesh.primitives.cube(2.0)\n',
            'from packages import nested\nx = nested.cube(2.0)\n',
            'from packages import nested as n\n\ndef call(x: Float) -> Float:\n    return n.mesh.primitives.cube(x)\n\nv = call(1.0)\n',
        ]:
            group = analyze_group_source(source, compilation_identity=identity,
                                         resolved_environment=env,
                                         source_callable_session=SourceCallableSession(resolved_environment=env))
            assert group is not None
        for member, message in [('unknown.cube', 'no namespace'),
                                ('mesh.unknown.cube', 'no namespace'),
                                ('mesh.primitives.unknown', 'no callable member')]:
            with pytest.raises(CompileError, match=message):
                resolve_package_callable('n', member, bindings)
        with pytest.raises(CompileError, match='not callable'):
            resolve_package_callable('n', 'mesh.primitives', bindings)
    finally:
        packages.set_packages_dir_for_tests(None)


def test_ambiguous_short_dsl_call_has_explicit_working_alternatives(tmp_path):
    """Ambiguous aliases never bind to whichever module appears first on disk."""
    from NodeForge.environment_resolution import resolve_environment
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.semantic.source_callable_session import SourceCallableSession
    try:
        _install_namespace_package(tmp_path, {
            'mesh/primitives.nf': 'def cube(x: Float) -> Float:\n    return x\n',
            'curve/primitives.nf': 'def cube(x: Float) -> Float:\n    return x\n',
        })
        env = resolve_environment()
        identity = GroupCompilationIdentity(None, 'ROOT/NESTED', 'ROOT/NESTED', 'ROOT/NESTED')
        with pytest.raises(CompileError, match='Ambiguous callable') as raised:
            analyze_group_source('from packages import nested as n\ny = n.cube(1.0)\n',
                                 compilation_identity=identity, resolved_environment=env,
                                 source_callable_session=SourceCallableSession(resolved_environment=env))
        assert 'n.mesh.primitives.cube' in str(raised.value)
        assert 'n.curve.primitives.cube' in str(raised.value)
        analyze_group_source('from packages import nested as n\ny = n.mesh.primitives.cube(1.0)\n',
                             compilation_identity=identity, resolved_environment=env,
                             source_callable_session=SourceCallableSession(resolved_environment=env))
    finally:
        packages.set_packages_dir_for_tests(None)


def test_core_builtin_declarations_remain_reserved_in_root_sources(tmp_path):
    """Package-qualified names do not relax root-script DSL binding rules."""
    from NodeForge.compiler_identities import GroupCompilationIdentity
    from NodeForge.semantic.group import analyze_group_source
    from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
    env = ResolvedEnvironment({name: ResolvedCatalog(name, {})
                               for name in ('functions', 'examples', 'local')})
    identity = GroupCompilationIdentity(None, 'ROOT/NESTED', 'ROOT/NESTED', 'ROOT/NESTED')
    with pytest.raises(CompileError, match='reserved by DSL builtin'):
        analyze_group_source('def cube(x: Float) -> Float:\n    return x\n',
                             compilation_identity=identity, resolved_environment=env)


def test_installed_zip_preserves_real_directories_and_short_aliases(tmp_path):
    """A release ZIP retains nested module ownership and module/directory overlap."""
    import json
    import zipfile
    from NodeForge.environment_resolution import resolve_environment

    packages.set_packages_dir_for_tests(tmp_path / 'installed')
    archive = tmp_path / 'nested.zip'
    manifest = {
        'schema_version': 1, 'id': 'vendor.nested', 'import_name': 'nested',
        'name': 'Nested', 'version': '1.0.0', 'author': 'Tests',
        'nodeforge_min_version': '0.91.0', 'contents': {'functions': 'functions'},
    }
    try:
        with zipfile.ZipFile(archive, 'w') as bundle:
            bundle.writestr('vendor.nested/nodeforge_package.json', json.dumps(manifest))
            bundle.writestr('vendor.nested/functions/utilities.nf',
                            'def identity(x: Float) -> Float:\n    return x\n')
            bundle.writestr('vendor.nested/functions/utilities/field.nf',
                            'def accumulate(x: Float) -> Float:\n    return x\n')
            bundle.writestr('vendor.nested/functions/mesh/primitives.nf',
                            'def cube(size: Float) -> Float:\n    return size\n')
        packages.install_package_zip(archive)
        namespace = resolve_environment().package_by_import_name('nested')
        assert set(namespace.qualified_exports) == {
            'utilities.identity', 'utilities.field.accumulate', 'mesh.primitives.cube',
        }
        assert 'utilities' in namespace.namespace_prefixes
        assert 'utilities.field' in namespace.namespace_prefixes
        assert namespace.find('identity') is namespace.find_path('utilities.identity')
        assert namespace.find('accumulate') is namespace.find_path('utilities.field.accumulate')
        assert namespace.find('cube') is namespace.find_path('mesh.primitives.cube')
        # The source origin is the selected physical module under the package.
        assert namespace.find('accumulate').record.source_path.parts[-3:] == (
            'functions', 'utilities', 'field.nf',
        )
    finally:
        packages.set_packages_dir_for_tests(None)


def test_incompatible_nested_package_replacement_preserves_active_owner(tmp_path):
    """An invalid minimum core version cannot replace a compatible package."""
    import json
    import zipfile
    from NodeForge.environment_resolution import resolve_environment
    from NodeForge import NODEFORGE_VERSION

    future_min = f'{NODEFORGE_VERSION[0]}.{NODEFORGE_VERSION[1] + 1}.0'
    packages.set_packages_dir_for_tests(tmp_path / 'installed')
    try:
        root = tmp_path / 'source'
        (root / 'functions').mkdir(parents=True)
        (root / 'functions' / 'entry.nf').write_text(
            'def identity(x: Float) -> Float:\n    return x\n', encoding='utf-8',
        )
        manifest = {
            'schema_version': 1, 'id': 'vendor.nested', 'import_name': 'nested',
            'name': 'Nested', 'version': '1.0.0', 'author': 'Tests',
            'nodeforge_min_version': '0.91.0', 'contents': {'functions': 'functions'},
        }
        (root / 'nodeforge_package.json').write_text(json.dumps(manifest))
        packages.install_package_directory(root)
        old = packages.active_package_records()[0]
        candidate = tmp_path / 'future.zip'
        with zipfile.ZipFile(candidate, 'w') as bundle:
            manifest.update(version='2.0.0', nodeforge_min_version=future_min)
            bundle.writestr('vendor.nested/nodeforge_package.json', json.dumps(manifest))
            bundle.writestr('vendor.nested/functions/entry.nf',
                            'def newer(x: Float) -> Float:\n    return x\n')
        with pytest.raises(packages.PackageError, match=f'requires NodeForge {future_min}'):
            packages.install_package_zip(candidate, replace=True)
        current = packages.active_package_records()[0]
        assert current.manifest.version == old.manifest.version
        assert current.state_record == old.state_record
        namespace = resolve_environment().package_by_import_name('nested')
        assert namespace.find('identity') is not None
        assert namespace.find('newer') is None
    finally:
        packages.set_packages_dir_for_tests(None)
