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










@pytest.mark.parametrize('text', ['from local.My Geometry import noise', 'from local.class import noise', 'from local import for'])
def test_invalid_import_grammar_uses_existing_parser_error(text):
    """Invalid physical spellings remain Python AST syntax errors."""
    with pytest.raises(CompileError, match='[Ss]yntax'):
        _parse_source(text)








def test_missing_index_never_reinterprets_qualified_import_as_flat(tmp_path):
    """A malformed supplied environment cannot bypass qualified selection boundaries."""
    path = source(tmp_path, 'noise.nf')
    record = catalog.LibraryEntryRecord('local', 'noise', 'script', path, source_path=path, source_origin=catalog._path_key(path))
    env = ResolvedEnvironment({n: ResolvedCatalog(n, {'noise': record} if n == 'local' else {}) for n in ('functions', 'examples', 'local')})
    with pytest.raises(CompileError, match='captured Local source index'):
        compile_source('from local.missing import noise\nresult = noise(1.0)', env)
