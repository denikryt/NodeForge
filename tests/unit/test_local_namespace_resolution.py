"""End-to-end Local frontend namespaces, canonical call IR and capture regressions."""

import ast
from dataclasses import fields, is_dataclass

import pytest

from NodeForge import catalog
from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.local_index import LocalSourceIndex
from NodeForge.resolved_environment import ResolvedCatalog, ResolvedEnvironment
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.ir import IRCall, IRCallableKind
from NodeForge.semantic.source_callable_session import SourceCallableSession
from NodeForge.semantic.parsing import _parse_source

pytestmark = pytest.mark.unit


def environment(root):
    """Capture exact source/empty-directory descriptors without Blender."""
    index = LocalSourceIndex.from_discovery(*catalog.discover_local_sources((root,)))
    return ResolvedEnvironment({n: ResolvedCatalog(n, {}, local_index=index if n == 'local' else None) for n in ('functions', 'examples', 'local')})


def source(root, relative, expression='x'):
    """Create a reusable single-input source callable."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'x = input_float("X")\noutput({expression})\n')
    return path


def compile_source(text, env):
    """Analyze through the production root/source callable session and typed IR."""
    session = SourceCallableSession(resolved_environment=env)
    identity = GroupCompilationIdentity(None, 'ROOT', 'ROOT', 'ROOT')
    group = analyze_group_source(text, compilation_identity=identity, resolved_environment=env, source_callable_session=session)
    return group, session


def calls(value):
    """Find source call IR in detached nested compilation artifacts."""
    result = []
    def visit(item):
        """Traverse IR dataclasses and their structural sequences."""
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


def test_flat_direct_and_alias_share_identity_and_one_preparation(tmp_path):
    """Spelling changes preserve canonical FunctionId, cache and owner scope."""
    path = source(tmp_path, 'terrain/noise.nf')
    env = environment(tmp_path)
    group, session = compile_source('''from local import noise as flat
from local.terrain import noise as direct
from local import terrain as t
a = flat(1.0)
b = direct(2.0)
c = t.noise(3.0)
output(a + b + c)
''', env)
    resolved = calls(group.body)
    assert len(resolved) == 3
    ids = [item.target.function_id for item in resolved]
    assert ids[0] == ids[1] == ids[2]
    assert ids[0].source_origin == catalog._path_key(path)
    assert all(item.materialization is None for item in resolved)
    assert len(session.source_snapshots) == len(session.prepared) == 1
    assert all(item.source_name != 't' for item in group.interface.inputs)


def test_two_same_name_sources_and_nested_directory_aliases(tmp_path):
    """Qualified paths select distinct origins including an explicit nested alias."""
    a = source(tmp_path, 'terrain/noise.nf')
    b = source(tmp_path, 'procedural/noise.nf', 'x + 1.0')
    c = source(tmp_path, 'terrain/filters/blur.nf')
    group, session = compile_source('''from local.terrain import noise
from local.procedural import noise as other
from local.terrain import filters as f
output(noise(1.0) + other(1.0) + f.blur(1.0))
''', environment(tmp_path))
    resolved = calls(group.body)
    assert {item.target.function_id.source_origin for item in resolved} == {catalog._path_key(x) for x in (a, b, c)}
    assert len(session.prepared) == 3
    assert len({item.group.identity.owner_scope for item in session.prepared.values()}) == 3


def test_directory_alias_inherits_without_capture_and_local_shadowing_is_normal(tmp_path):
    """Unshadowed aliases propagate to helpers; helper parameter shadows remain values."""
    path = source(tmp_path, 'terrain/noise.nf')
    env = environment(tmp_path)
    group, session = compile_source('''from local import terrain as t
def helper(x: Float):
    return t.noise(x)
output(helper(2.0))
''', env)
    local = next(item for key, item in session.prepared.items() if key.function_id.kind == 'LOCAL_DEF')
    assert [item.source_name for item in local.contract.parameters] == ['x']
    assert calls(local.group.body)[0].target.function_id.source_origin == catalog._path_key(path)
    group, _ = compile_source('''from local import terrain as t
def helper(t: Float):
    return t + 1.0
output(helper(2.0))
''', env)
    assert group.interface.outputs
    with pytest.raises(CompileError, match='Object|info'):
        compile_source('''from local import terrain as t
def helper(t: Float):
    return t.noise(1.0)
output(helper(2.0))
''', env)


@pytest.mark.parametrize('text, diagnostic', [
    ('from local import terrain as t\noutput(t)', 'cannot be used as a value'),
    ('from local import terrain as t\ndef helper(x: Float):\n    return t\noutput(helper(1.0))', 'cannot be used as a value'),
    ('from local import terrain as t\noutput(t.missing(1.0))', 'has no callable member'),
    ('from local import terrain as t\noutput(t.filters.blur(1.0))', 'nested import|directory alias'),
    ('from local.missing import noise\noutput(noise(1.0))', 'Unknown Local directory'),
    ('from local.terrain import missing\noutput(1.0)', 'Unknown local.terrain import'),
    ('from local.terrain import *\noutput(1.0)', 'star imports'),
    ('from local import terrain as output\noutput(1.0)', 'reserved name'),
    ('from local import terrain as t\nt = 2.0\noutput(t)', 'conflicts with local binding'),
    ('from local import terrain as t\nfrom local import terrain as t\noutput(1.0)', 'Duplicate'),
])
def test_invalid_namespace_uses_are_frontend_errors(tmp_path, text, diagnostic):
    """Malformed selections and runtime namespace use fail before backend mutation."""
    source(tmp_path, 'terrain/noise.nf')
    source(tmp_path, 'terrain/filters/blur.nf')
    with pytest.raises(CompileError, match=diagnostic):
        compile_source(text, environment(tmp_path))


@pytest.mark.parametrize('text', ['from local.My Geometry import noise', 'from local.class import noise', 'from local import for'])
def test_invalid_import_grammar_uses_existing_parser_error(text):
    """Invalid physical spellings remain Python AST syntax errors."""
    with pytest.raises(CompileError, match='[Ss]yntax'):
        _parse_source(text)


def test_file_directory_conflicts_and_star_flat_only(tmp_path):
    """Module prefixes select directories while imported members reject both kinds."""
    source(tmp_path, 'terrain.nf')
    source(tmp_path, 'terrain/noise.nf')
    source(tmp_path, 'terrain/filters.nf')
    source(tmp_path, 'terrain/filters/blur.nf')
    env = environment(tmp_path)
    group, _ = compile_source('from local.terrain import noise\noutput(noise(1.0))', env)
    assert calls(group.body)
    group, _ = compile_source('from local import *\noutput(terrain(1.0))', env)
    assert calls(group.body)[0].target.function_id.name == 'terrain'
    for text in ('from local import terrain\noutput(1.0)', 'from local.terrain import filters\noutput(1.0)'):
        with pytest.raises(CompileError, match='file-versus-directory'):
            compile_source(text, env)
    source(tmp_path, 'procedural/noise.nf')
    env = environment(tmp_path)
    for text in ('from local import noise\noutput(1.0)', 'from local import noise as n\noutput(1.0)', 'from local import *\noutput(1.0)'):
        with pytest.raises(CompileError, match="Ambiguous callable 'noise'"):
            compile_source(text, env)


def test_source_snapshot_stays_coherent_and_next_session_observes_changes(tmp_path):
    """Root-attempt source caches preserve exact origin and textual snapshot."""
    path = source(tmp_path, 'terrain/noise.nf')
    env = environment(tmp_path)
    _, session = compile_source('from local.terrain import noise\noutput(noise(1.0))', env)
    original = dict(session.source_snapshots)
    path.write_text('output(7.0)')
    assert session.source_snapshots == original
    _, next_session = compile_source('from local.terrain import noise\noutput(noise())', env)
    assert set(next_session.source_snapshots) == set(original)
    assert next(iter(next_session.source_snapshots.values())) == 'output(7.0)'


def test_inherited_callable_directory_alias_collision_is_controlled(tmp_path):
    """Inherited source imports cannot overwrite a locally imported directory alias."""
    from NodeForge.semantic.group import _validate_import_bindings
    from NodeForge.semantic.parsing import FunctionImport
    from NodeForge.semantic.source_bindings import LibraryBinding
    source(tmp_path, 'terrain/noise.nf')
    env = environment(tmp_path)
    record = env.catalog('local').find('noise')
    inherited = {'t': LibraryBinding('local', 'noise', record)}
    with pytest.raises(CompileError, match='Duplicate function import name'):
        _validate_import_bindings((FunctionImport('local', 'terrain', 't'),), (), {}, env, inherited_imports=inherited)
    inherited = {'noise': LibraryBinding('local', 'noise', record)}
    bindings = _validate_import_bindings((), (), {}, env, inherited_imports=inherited)
    assert bindings['noise'].record is record


def test_missing_index_never_reinterprets_qualified_import_as_flat(tmp_path):
    """A malformed supplied environment cannot bypass qualified selection boundaries."""
    path = source(tmp_path, 'noise.nf')
    record = catalog.LibraryEntryRecord('local', 'noise', 'script', path, source_path=path, source_origin=catalog._path_key(path))
    env = ResolvedEnvironment({n: ResolvedCatalog(n, {'noise': record} if n == 'local' else {}) for n in ('functions', 'examples', 'local')})
    with pytest.raises(CompileError, match='captured Local source index'):
        compile_source('from local.missing import noise\noutput(noise(1.0))', env)


def test_non_ascii_existing_directory_reports_invalid_segment(tmp_path):
    """AST-valid physical names outside the public ASCII vocabulary stay flat-only."""
    path = source(tmp_path, 'éclair/noise.nf')
    env = environment(tmp_path)
    group, _ = compile_source('from local import noise\noutput(noise(1.0))', env)
    assert calls(group.body)[0].target.function_id.source_origin == catalog._path_key(path)
    with pytest.raises(CompileError, match='Invalid Local directory name'):
        compile_source('from local.éclair import noise\noutput(noise(1.0))', env)


def test_directory_bare_scope_identity_and_lazy_preparation(tmp_path):
    """Bare/qualified/explicit spellings share identity while excluded sources stay lazy."""
    path = source(tmp_path, 'terrain/noise.nf')
    source(tmp_path, 'procedural/noise.nodeforge')
    source(tmp_path, 'terrain/_hidden.nf')
    source(tmp_path, 'terrain/filters/blur.nf')
    source(tmp_path, 'unrelated/other.nf')
    bad = source(tmp_path, 'terrain/unused.nf')
    bad.write_text('invalid syntax ???')
    env = environment(tmp_path)
    group, session = compile_source('''from local import terrain, procedural
from local.terrain import noise
a = noise(1.0)
b = terrain.noise(2.0)
c = noise(3.0, __unique__=True)
output(a+b+c)
''', env)
    resolved = calls(group.body)
    assert len(resolved) == 3
    assert {call.target.function_id.source_origin for call in resolved} == {catalog._path_key(path)}
    assert len(session.source_snapshots) == 1
    assert {key.function_id for key in session.prepared} == set(session.source_snapshots)
    assert resolved[0].materialization is resolved[1].materialization is None
    assert resolved[2].materialization is not None
    for name in ('_hidden', 'blur', 'other'):
        with pytest.raises(CompileError, match='Unsupported function'):
            compile_source(f'from local import terrain\noutput({name}(1.0))', env)
    nested, _ = compile_source('from local.terrain import filters\noutput(blur(1.0))', env)
    assert calls(nested.body)[0].target.function_id.source_origin == catalog._path_key(tmp_path / 'terrain/filters/blur.nf')
    for alias in ('t', 'terrain'):
        prefix = f'from local import terrain as {alias}\n'
        assert calls(compile_source(prefix + f'output({alias}.noise(1.0))', env)[0].body)
        with pytest.raises(CompileError, match='Unsupported function'):
            compile_source(prefix + 'output(noise(1.0))', env)
    with pytest.raises(CompileError, match='[Ss]yntax'):
        compile_source('from local import terrain\noutput(unused())', env)
    from NodeForge.semantic.source_bindings import LocalDirectoryNamespaceBinding
    index = env.catalog('local').local_index
    with pytest.raises(TypeError, match='capability must be Bool'):
        LocalDirectoryNamespaceBinding('terrain', index, index.resolve_directory(('terrain',)), 1)


def test_directory_extension_filter_precedes_ambiguity_and_keeps_qualified_lookup(tmp_path):
    """Only .nf auto-exports while explicit/qualified calls retain both extensions."""
    nf = source(tmp_path, 'terrain/noise.nf')
    long = source(tmp_path, 'procedural/noise.nodeforge')
    source(tmp_path, 'terrain/unique.nf')
    env = environment(tmp_path)
    group, _ = compile_source('''from local import terrain, procedural
from local.procedural import noise as explicit
output(noise(1.0) + procedural.noise(1.0) + explicit(1.0))
''', env)
    assert [call.target.function_id.source_origin for call in calls(group.body)] == [
        catalog._path_key(nf), catalog._path_key(long), catalog._path_key(long),
    ]
    with pytest.raises(CompileError, match='Unsupported function'):
        compile_source('from local import procedural\noutput(noise(1.0))', env)
    source(tmp_path, 'terrain/noise.nodeforge')
    env = environment(tmp_path)
    assert calls(compile_source('from local import terrain\noutput(noise(1.0))', env)[0].body)[0].target.function_id.source_origin == catalog._path_key(nf)
    for text in ('from local import terrain\noutput(terrain.noise(1.0))',
                 'from local.terrain import noise\noutput(noise(1.0))'):
        with pytest.raises(CompileError, match='Ambiguous'):
            compile_source(text, env)
    source(tmp_path, 'procedural/noise.nf')
    # Keep procedural qualification valid so only the terrain source lacks an alternative.
    long.unlink()
    env = environment(tmp_path)
    assert calls(compile_source('from local import terrain, procedural\noutput(unique(1.0))', env)[0].body)
    with pytest.raises(CompileError) as error:
        compile_source('from local import terrain, procedural\noutput(noise(1.0))', env)
    message = str(error.value)
    assert 'Ambiguous callable' in message and catalog._path_key(nf) in message
    assert 'Use a qualified call: procedural.noise(...)' in message
    assert 'terrain.noise(...)' not in message


def test_directory_bare_bindings_and_helper_inheritance(tmp_path):
    """Namespace capability reaches helpers without bypassing lexical value ownership."""
    path = source(tmp_path, 'terrain/noise.nf')
    source(tmp_path, 'terrain/points.nf')
    source(tmp_path, 'procedural/noise.nf')
    env = environment(tmp_path)
    group, session = compile_source('''from local import terrain
offset = input_float("Offset")
def helper(x: Float):
    return noise(x + offset)
output(helper(2.0))
''', env)
    helper = next(item for key, item in session.prepared.items() if key.function_id.kind == 'LOCAL_DEF')
    assert [p.source_name for p in helper.contract.parameters] == ['x', 'offset']
    assert calls(helper.group.body)[0].target.function_id.source_origin == catalog._path_key(path)
    assert compile_source('from local import terrain\noutput(points(2))', env)[0].interface.outputs
    assert calls(compile_source('from local import terrain\noutput(terrain.points(1.0))', env)[0].body)
    bad_sources = (
        'noise=2.0\noutput(noise(1.0))',
        'noise=[1.0, 2.0]\noutput(noise(1.0))',
        'noise=input_float("Noise")\ndef helper(x: Float):\n    return noise(x)\noutput(helper(1.0))',
        'def helper(noise: Float):\n    return noise(1.0)\noutput(helper(1.0))',
    )
    for text in bad_sources:
        with pytest.raises(CompileError, match="Cannot call 'noise'.*source value.*terrain.noise"):
            compile_source('from local import terrain\n'+text, env)
    assert calls(compile_source('from local import terrain\nnoise=2.0\noutput(terrain.noise(noise))', env)[0].body)
    implicit, _ = compile_source('from local import terrain\noutput(noise + terrain.noise(1.0))', env)
    assert [item.source_name for item in implicit.interface.inputs] == ['noise']
    with pytest.raises(CompileError, match='Unsupported function'):
        compile_source('from local import terrain\ndef helper(terrain: Float):\n    return noise(1.0)\noutput(helper(1.0))', env)
    with pytest.raises(CompileError, match='Ambiguous callable'):
        compile_source('from local import terrain\ndef noise(x: Float):\n    return x\noutput(noise(1.0))', env)
    with pytest.raises(CompileError, match='Ambiguous callable'):
        compile_source('from local import terrain\nfrom local.procedural import noise\noutput(noise(1.0))', env)
