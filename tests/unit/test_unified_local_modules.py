"""Local .nf import paths select file modules, then explicit function definitions."""

import pytest

from NodeForge import catalog
from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.local_index import LocalSourceIndex
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic.call_resolution import CallableEnvironment, CallableKind, resolve_simple_callable, resolve_local_callable
from NodeForge.semantic.group import _validate_import_bindings, analyze_group_source
from NodeForge.semantic.source_bindings import SourceModuleNamespaceBinding, SourceDefinitionBinding
from NodeForge.semantic.source_callable_session import SourceCallableSession
from NodeForge.semantic.builtin_calls import IR_CAPABLE_BUILTIN_NAMES
from NodeForge.semantic.parsing import FunctionImport


def _source(root, relative, source):
    """Write one actual physical Local file with definitions and executable code."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(source, encoding='utf-8')
    return path


def _env(root):
    """Freeze one index with physical source positions and directory candidates."""
    index = LocalSourceIndex.from_discovery(*catalog.discover_local_sources((root,)))
    return ResolvedEnvironment({n: ResolvedCatalog(n, {}, local_index=index if n == 'local' else None)
                                for n in ('functions', 'examples', 'local')})


def _resolve(env, session, *requests):
    """Call the production import resolver without materializing Blender nodes."""
    namespaces = {}
    imported = _validate_import_bindings(
        [FunctionImport(module, name, alias) for module, name, alias in requests], [], {}, env,
        directory_bindings=namespaces, source_callable_session=session,
    )
    return imported, namespaces


def test_module_and_direct_import_aliases_reach_one_source_definition(tmp_path):
    """Qualified and direct imports retain the same ModuleId and definition ref."""
    path = _source(tmp_path, 'geometry.nf', '''def move(geo: Geometry) -> Geometry:
    return geo
def resize(geo: Geometry) -> Geometry:
    return geo
unsafe = cube(1.0)''')
    env = _env(tmp_path)
    session = SourceCallableSession(resolved_environment=env)
    direct, modules = _resolve(env, session,
        ('local', 'geometry', 'geo'), ('local.geometry', 'move', 'translate'))
    module = modules['geo']
    member = direct['translate']
    assert isinstance(module, SourceModuleNamespaceBinding)
    assert isinstance(member, SourceDefinitionBinding)
    assert member.definition == module.member('move').definition
    assert member.snapshot is module.snapshot
    assert member.definition.module.origin == catalog._path_key(path)
    assert len(session.module_snapshots) == 1
    callable_env = CallableEnvironment(frozenset(IR_CAPABLE_BUILTIN_NAMES), {}, direct,
                                       local_namespaces=modules)
    assert resolve_simple_callable('translate', callable_env).kind is CallableKind.MODULE_DEFINITION
    assert resolve_local_callable('geo', 'move', callable_env).target.definition == member.definition


def test_nested_directory_paths_select_only_explicit_file_members(tmp_path):
    """Directory and file components are distinguished instead of invoked as functions."""
    _source(tmp_path, 'terrain/geometry.nf', 'def _hidden(x: Float):\n    return x\n')
    env = _env(tmp_path)
    session = SourceCallableSession(resolved_environment=env)
    direct, modules = _resolve(env, session,
        ('local.terrain', 'geometry', 'g'), ('local.terrain.geometry', '_hidden', 'h'))
    assert direct['h'].definition == modules['g'].member('_hidden').definition
    assert len(session.module_snapshots) == 1


def test_file_import_does_not_execute_executable_statements(tmp_path):
    """A declaration import does not generate root executable statements or additional groups."""
    _source(tmp_path, 'geometry.nf', '''def add(x: Float) -> Float:
    return x + 1
cube(1.0)''')
    env = _env(tmp_path)
    session = SourceCallableSession(resolved_environment=env)
    group = analyze_group_source('from local import geometry\noutput(2.0)',
        compilation_identity=GroupCompilationIdentity(None, 'ROOT', 'ROOT', 'ROOT'),
        resolved_environment=env, source_callable_session=session)
    assert len([module for module in session.module_snapshots if module.kind == "LOCAL"]) == 1
    assert not session.prepared
    assert len(group.interface.outputs) == 1


@pytest.mark.parametrize('import_request, error', [
    (('local.geometry', 'nonexistent', 'bad'), 'Unknown function'),
    (('local', 'missing', 'missing'), 'Unknown Local source path'),
    (('local', 'geometry', 'cube'), 'conflicts'),
])
def test_missing_members_or_conflicting_import_aliases_are_rejected(tmp_path, import_request, error):
    """Binding failures originate from explicit file-member and alias ownership."""
    _source(tmp_path, 'geometry.nf', 'def move(x: Float):\n    return x\n')
    env = _env(tmp_path)
    session = SourceCallableSession(resolved_environment=env)
    with pytest.raises(CompileError, match=error):
        _resolve(env, session, import_request)


def test_file_directory_collision_is_a_controlled_error(tmp_path):
    """Equal physical import names are never resolved by creation order."""
    _source(tmp_path, 'geometry.nf', 'def move(x: Float):\n    return x\n')
    _source(tmp_path, 'geometry/sample.nf', 'def f(x: Float):\n    return x\n')
    with pytest.raises(CompileError, match='file-versus-directory'):
        _resolve(_env(tmp_path), SourceCallableSession(resolved_environment=_env(tmp_path)),
                 ('local', 'geometry', 'geometry'))


def test_removed_file_as_callable_is_not_a_local_imported_function(tmp_path):
    """A module file has no direct callable binding and directories are not bare exports."""
    _source(tmp_path, 'geometry.nf', 'def move(x: Float):\n    return x\n')
    env = _env(tmp_path)
    session = SourceCallableSession(resolved_environment=env)
    imported, namespaces = _resolve(env, session, ('local', 'geometry', 'geo'))
    assert not imported
    assert isinstance(namespaces['geo'], SourceModuleNamespaceBinding)
    cenv = CallableEnvironment(frozenset(IR_CAPABLE_BUILTIN_NAMES), {}, imported,
                               local_namespaces=namespaces)
    with pytest.raises(CompileError, match='Unknown function'):
        resolve_local_callable('geo', 'absent', cenv)
    with pytest.raises(CompileError, match='conflicts'):
        _resolve(env, session, ('local', 'geometry', 'g'), ('local', 'geometry', 'g'))


def test_star_import_is_rejected_instead_of_exposing_legacy_file_functions(tmp_path):
    """Flat filename discovery is not an unqualified function export mechanism."""
    _source(tmp_path, 'geometry.nf', 'def move(x: Float):\n    return x\n')
    env = _env(tmp_path)
    session = SourceCallableSession(resolved_environment=env)
    with pytest.raises(CompileError, match='star imports are unsupported'):
        analyze_group_source('from local import *\noutput(1)',
            compilation_identity=GroupCompilationIdentity(None, 'ROOT', 'ROOT', 'ROOT'),
            resolved_environment=env, source_callable_session=session)


def test_calling_a_file_module_without_member_is_a_source_error(tmp_path):
    """A module path does not masquerade as a single implicitly callable script."""
    _source(tmp_path, 'geometry.nf', 'def move(x: Float):\n    return x\n')
    env = _env(tmp_path)
    session = SourceCallableSession(resolved_environment=env)
    with pytest.raises(CompileError, match='not callable'):
        analyze_group_source('from local import geometry\noutput(geometry(1))',
            compilation_identity=GroupCompilationIdentity(None, 'ROOT', 'ROOT', 'ROOT'),
            resolved_environment=env, source_callable_session=session)


def test_unselected_local_registry_failure_is_lazy_and_replayed(tmp_path, monkeypatch):
    """Local-free modules do not evaluate a broken registry; selected imports do."""
    from NodeForge import environment_resolution, local_sources
    registry = tmp_path / 'registry.json'
    registry.write_text('{"version":999,"roots":[]}', encoding='utf-8')
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: registry)
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: tmp_path / 'local')
    environment = environment_resolution._make_compilation_environment()
    session = SourceCallableSession(resolved_environment=environment)
    clean = analyze_group_source('value = cube(1.0)',
        compilation_identity=GroupCompilationIdentity(None, 'ROOT/lazy', 'ROOT/lazy', 'ROOT/lazy'),
        resolved_environment=environment, source_callable_session=session)
    assert clean.body.statements
    with pytest.raises(CompileError, match='registry format'):
        _resolve(environment, session, ('local', 'geometry', 'geometry'))
    registry.unlink()
    with pytest.raises(CompileError, match='registry format'):
        _resolve(environment, session, ('local', 'geometry', 'geometry'))


def test_imported_local_revision_is_frozen_until_next_attempt(tmp_path):
    """A single selected file is parsed once and new attempts see new revisions."""
    path = _source(tmp_path, 'geometry.nf', 'def move(x: Float) -> Float:\n    return x + 1\n')
    environment = _env(tmp_path)
    session = SourceCallableSession(resolved_environment=environment)
    direct, namespaces = _resolve(environment, session,
        ('local', 'geometry', 'geo'), ('local.geometry', 'move', 'move'))
    frozen = namespaces['geo'].snapshot
    path.write_text('def move(x: Float) -> Float:\n    return x + 2\n', encoding='utf-8')
    second, second_modules = _resolve(environment, session, ('local.geometry', 'move', 'another'))
    assert second['another'].snapshot is frozen
    assert direct['move'].snapshot is frozen
    assert '+ 1' in frozen.source and '+ 2' not in frozen.source
    next_session = SourceCallableSession(resolved_environment=environment)
    newer, _ = _resolve(environment, next_session, ('local.geometry', 'move', 'move'))
    assert newer['move'].snapshot is not frozen
    assert '+ 2' in newer['move'].snapshot.source
