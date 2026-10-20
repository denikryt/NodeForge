"""Regression tests for plan 44 Examples directory discovery and semantic binding."""

from dataclasses import fields, is_dataclass
from pathlib import Path
from types import SimpleNamespace

import pytest

from NodeForge import catalog
from NodeForge.compiler_identities import GroupCompilationIdentity, library_function_id_for_record
from NodeForge.errors import CompileError
from NodeForge.local_index import ExamplesSourceIndex, LocalDirectoryCandidate
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.ir import IRCall, IRCallableKind
from NodeForge.semantic.source_callable_session import SourceCallableSession

pytestmark = pytest.mark.unit


def write(root, relative, value='x'):
    """Write an importable single-argument .nf example at a chosen path."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'x = input_float("X")\noutput({value})\n', encoding='utf8')
    return path


def selected(root, package_id=''):
    """Capture a complete, single-root immutable Examples view for frontend tests."""
    owner = SimpleNamespace(path=root, package_id=package_id, package_name='Test', package_version='1.0.0')
    records = catalog.candidate_records_from_inputs(
        'examples', package_roots=(owner,), include_builtin_catalog=False,
    )
    directories, positions = records.directories, records.source_positions
    legacy = catalog.unique_records_from_candidates('examples', (r for r in records if not r.folder_path))
    index = ExamplesSourceIndex.from_discovery(records, directories, positions)
    example = ResolvedCatalog('examples', legacy, examples_index=index)
    return ResolvedEnvironment({
        'examples': example,
        'functions': ResolvedCatalog('functions', {}),
        'local': ResolvedCatalog('local', {}),
    })


def compile_source(src, env):
    """Compile a root through production semantic and recursive callable sessions."""
    session = SourceCallableSession(resolved_environment=env)
    result = analyze_group_source(
        src, compilation_identity=GroupCompilationIdentity(None, 'ROOT', 'ROOT', 'ROOT'),
        resolved_environment=env, source_callable_session=session,
    )
    return result, session


def call_ids(value):
    """Collect exact canonical source identities from a detached IR tree."""
    identities = []
    def visit(item):
        """Walk nested semantic dataclasses and tuple members."""
        if isinstance(item, IRCall) and item.target.kind is IRCallableKind.SOURCE_FUNCTION:
            identities.append(item.target.function_id)
        if is_dataclass(item):
            for field in fields(item):
                visit(getattr(item, field.name))
        elif isinstance(item, (tuple, list)):
            for element in item:
                visit(element)
    visit(value)
    return identities


def test_qualified_flat_alias_and_deep_examples_share_exact_selection(tmp_path):
    """A nested source is accessible through flat, dotted and directory aliases."""
    write(tmp_path, 'terrain/erosion.nf')
    write(tmp_path, 'terrain/tools/slope.nf', 'x + 1')
    env = selected(tmp_path)
    result, session = compile_source('''from examples import erosion as erosion_alias
from examples.terrain import erosion as direct
from examples import terrain as t
from examples.terrain.tools import slope
output(erosion_alias(1.0) + direct(2.0) + t.erosion(3.0) + slope(4.0))
''', env)
    ids = call_ids(result.body)
    assert len(ids) == 4
    assert ids[0] == ids[1] == ids[2]
    assert ids[3] != ids[0]
    assert len(session.prepared) == 2
    assert env.catalog('examples').find('erosion') is None  # Legacy projection unchanged.
    assert set(env.catalog('examples').names()) == set()


def test_root_owner_precedence_and_nested_logical_identity(tmp_path):
    """Adding a nested basename cannot change the old direct owner or FunctionId."""
    write(tmp_path, 'foo.nf')
    write(tmp_path, 'terrain/foo.nf', 'x + 1')
    env = selected(tmp_path)
    root_owner = env.catalog('examples').entries['foo']
    assert library_function_id_for_record(root_owner).source_origin == ''
    result, _ = compile_source('''from examples import foo
from examples.terrain import foo as nested
output(foo(1.0) + nested(1.0))
''', env)
    ids = call_ids(result.body)
    assert len(ids) == 2 and ids[0] != ids[1]
    assert ids[0] == library_function_id_for_record(root_owner)
    assert ids[1].source_origin == 'terrain/foo.nf'


def test_nested_only_flat_ambiguity_and_qualified_disambiguation(tmp_path):
    """The shared index does not silently select the first basename."""
    write(tmp_path, 'a/foo.nf')
    write(tmp_path, 'b/foo.nf')
    env = selected(tmp_path)
    with pytest.raises(CompileError, match='Ambiguous callable.*foo'):
        compile_source('from examples import foo\noutput(foo(1.0))', env)
    group, _ = compile_source('''from examples.a import foo
from examples.b import foo as other
output(foo(1.0) + other(1.0))
''', env)
    assert {x.source_origin for x in call_ids(group.body)} == {'a/foo.nf', 'b/foo.nf'}


def test_legacy_star_names_and_owners_do_not_change(tmp_path):
    """Wildcard exports preserve direct .nf and directory source.nf owners only."""
    write(tmp_path, 'root.nf')
    write(tmp_path, 'legacy/source.nf')
    write(tmp_path, 'terrain/erosion.nf')
    write(tmp_path, 'terrain/root.nf')
    env = selected(tmp_path)
    view = env.catalog('examples')
    assert set(view.names()) == {'root', 'legacy'}
    assert view.select_import('examples', 'root', flat_only=True) is view.entries['root']
    assert view.select_import('examples', 'legacy', flat_only=True) is view.entries['legacy']
    assert view.select_import('examples', 'erosion', flat_only=True) is None
    assert view.select_import('examples', 'root') is view.entries['root']
    assert view.select_import('examples.legacy', 'source') is None
    assert isinstance(view.select_import('examples', 'terrain'), LocalDirectoryCandidate)
    group, _ = compile_source('from examples import *\noutput(root(1.0) + legacy(2.0))', env)
    assert {x.name for x in call_ids(group.body)} == {'root', 'legacy'}


def test_unaliased_directory_exposes_nf_bare_and_alias_does_not(tmp_path):
    """Directory bare-call admission is explicit and tracks alias status."""
    write(tmp_path, 'terrain/erosion.nf')
    env = selected(tmp_path)
    group, _ = compile_source('from examples import terrain\noutput(erosion(1.0))', env)
    assert call_ids(group.body)[0].source_origin == 'terrain/erosion.nf'
    with pytest.raises(CompileError):
        compile_source('from examples import terrain as t\noutput(erosion(1.0))', env)


def test_directory_member_missing_star_and_invalid_member(tmp_path):
    """Unknown directories/members and directory wildcards fail at import validation."""
    write(tmp_path, 'terrain/erosion.nf')
    env = selected(tmp_path)
    for source, error in (
        ('from examples.missing import erosion\noutput(1)', 'Unknown Examples directory'),
        ('from examples.terrain import missing\noutput(1)', 'Unknown examples.terrain import'),
        ('from examples.terrain import *\noutput(1)', 'star imports'),
        ('from examples import terrain as output\noutput(1)', 'reserved name'),
    ):
        with pytest.raises(CompileError, match=error):
            compile_source(source, env)


def test_physical_directory_leaf_collision(tmp_path):
    """File-versus-directory collision is not resolved by first-root ordering."""
    write(tmp_path, 'foo.nf')
    write(tmp_path, 'foo/child.nf')
    with pytest.raises(CompileError, match="Duplicate examples library entry 'foo'"):
        selected(tmp_path)


def test_identity_stable_under_relocated_package(tmp_path):
    """Package reinstall moves physical origins but keeps relative logical IDs."""
    identities = []
    for root in (tmp_path / 'first', tmp_path / 'second'):
        write(root, 'nested/foo.nf')
        record = selected(root, package_id='vendor.one').catalog('examples').records()[0]
        identities.append(library_function_id_for_record(record))
    assert identities[0] == identities[1]
    assert identities[0].source_origin == 'nested/foo.nf'


def test_no_source_from_disallowed_parent_or_symlink_escape(tmp_path):
    """Private paths are excluded and file symlinks escaping a root are rejected."""
    root = tmp_path / 'examples'
    write(root, '_hidden/foo.nf')
    write(root, 'not-valid/foo.nf')
    write(root, 'valid/ok.nf')
    assert [r.name for r in selected(root).catalog('examples').records()] == ['ok']
    outside = write(tmp_path, 'outside.nf')
    (root / 'valid' / 'escape.nf').symlink_to(outside)
    with pytest.raises(CompileError, match='outside'):
        selected(root)

@pytest.fixture
def isolated_packages(tmp_path):
    """Exercise real package admission without touching user-owned package state."""
    from NodeForge import packages
    packages.set_packages_dir_for_tests(tmp_path / 'installed')
    yield
    packages.set_packages_dir_for_tests(None)


def install_examples_package(tmp_path, package_id, entries, *, python=False):
    """Create and install a manifest-selected Examples owner with real installation checks."""
    import json
    from NodeForge import packages
    root = tmp_path / package_id.replace('.', '_')
    for filename, text in entries.items():
        file = root / 'examples' / filename
        file.parent.mkdir(parents=True, exist_ok=True)
        file.write_text(text, encoding='utf8')
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': package_id, 'import_name': package_id.split('.')[-1],
        'name': package_id, 'version': '1.0.0', 'author': 'Tests', 'description': 'Test',
        'contents': {'examples': 'examples'}, 'permissions': {'python': python},
        'nodeforge_min_version': '0.68.0', 'nodeforge_max_version': None,
    }), encoding='utf8')
    return packages.install_package_directory(root)


def test_real_installed_package_nested_examples_imports_and_collisions(tmp_path, isolated_packages):
    """Only admitted package inventories join the frozen Examples source index."""
    from NodeForge.environment_resolution import _make_compilation_environment
    text = 'x = input_float("X")\noutput(x)\n'
    install_examples_package(tmp_path, 'vendor.alpha', {'alpha/erosion.nf': text})
    install_examples_package(tmp_path, 'vendor.beta', {'beta/erosion.nf': text})
    env = _make_compilation_environment()
    group, _ = compile_source('''from examples.alpha import erosion
from examples.beta import erosion as second
output(erosion(1.0) + second(2.0))
''', env)
    assert len({id.source_origin for id in call_ids(group.body)}) == 2
    with pytest.raises(CompileError, match='Ambiguous callable'):
        compile_source('from examples import erosion\noutput(erosion(1.0))', env)


def test_real_installer_rejects_duplicate_top_level_examples_directories(tmp_path, isolated_packages):
    """Two packages cannot merge the same top-level Examples directory."""
    from NodeForge import packages
    text = 'output(1.0)\n'
    install_examples_package(tmp_path, 'vendor.alpha', {'shared/a.nf': text})
    with pytest.raises(packages.PackageError, match="Duplicate examples library entry 'shared'"):
        install_examples_package(tmp_path, 'vendor.beta', {'shared/b.nf': text})


def test_authorized_native_example_owner_and_nested_script_are_both_selected(tmp_path, isolated_packages):
    """Existing trusted interface owner retains its registry and dotted children."""
    from NodeForge.environment_resolution import _make_compilation_environment
    from NodeForge.extensions.registry import library_owner_key
    from NodeForge.extensions.contracts import ExtensionCallableId
    native = 'review_native'
    source = 'x = input_float("X")\noutput(x)\n'
    interface = (
        'from typing import Annotated\n'
        'from NodeForge import Float, EvaluationMode\n'
        'EXTENSION_API = 2\n'
        f'EXTENSIONS = {{{native!r}: ".backend:build"}}\n'
        f'def {native}(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'
    )
    install_examples_package(tmp_path, 'vendor.native', {
        f'{native}/interface.py': interface,
        f'{native}/backend.py': 'def build(context, value):\n    return value\n',
        f'{native}/child.nf': source,
    }, python=True)
    env = _make_compilation_environment()
    record = env.catalog('examples').find(native)
    assert record is not None
    assert env.extension_registry.contains(ExtensionCallableId(library_owner_key(record), native))
    assert env.catalog('examples').select_import('examples', native) is record
    child = env.catalog('examples').select_import(f'examples.{native}', 'child')
    assert child.folder_path == native and child.kind == 'script'
    result, _ = compile_source(f'from examples.{native} import child\noutput(child(1.0))', env)
    assert call_ids(result.body)[0].source_origin == f'{native}/child.nf'


def test_owner_symlink_escape_is_rejected_before_snapshot(tmp_path):
    """Special directory source owners cannot bypass root boundary via source.nf."""
    root = tmp_path / 'examples'
    legacy = root / 'legacy'
    legacy.mkdir(parents=True)
    other = write(tmp_path, 'untrusted.nf')
    (legacy / 'source.nf').symlink_to(other)
    with pytest.raises(CompileError, match='outside'):
        selected(root)


def test_mixed_suffixes_and_valid_directory_name_contract(tmp_path):
    """Dotted `.nodeforge` works while non-public identifiers cannot bind."""
    source = write(tmp_path, 'terrain/slope.nodeforge')
    write(tmp_path, 'terrain/class/foo.nf')
    env = selected(tmp_path)
    group, _ = compile_source('from examples.terrain import slope\noutput(slope(1.0))', env)
    assert call_ids(group.body)[0].source_origin == 'terrain/slope.nodeforge'
    with pytest.raises(CompileError):
        compile_source('from examples.terrain.class import foo\noutput(foo(1.0))', env)
    assert source.name == 'slope.nodeforge'
