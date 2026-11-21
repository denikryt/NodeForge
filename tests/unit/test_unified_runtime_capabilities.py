"""Semantic ROOT/NESTED roles and physical level-zero capability boundaries."""

import pytest

from NodeForge import environment_resolution, local_sources
from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.runtime_capabilities import RuntimeRole
from NodeForge.semantic.source_callable_session import SourceCallableSession


def _prepare(source, view, role):
    """Prepare one detached compilation with an explicitly supplied role."""
    return analyze_group_source(
        source, compilation_identity=GroupCompilationIdentity(None, 'role-test', 'role-test', 'role-test'),
        resolved_environment=view, source_callable_session=SourceCallableSession(resolved_environment=view),
        runtime_role=role,
    )


def test_nested_role_rejects_used_root_only_mesh_compute_but_root_allows_it(tmp_path, monkeypatch):
    """Runtime callbacks are checked at their resolved call, not at import time."""
    folder = tmp_path / 'Local'
    folder.mkdir()
    (folder / 'erosion.py').write_text('from NodeForge import mesh_compute\n'
        '@mesh_compute(reads=("height",), writes=("out_height",))\n'
        'def erosion(mesh, amount: float = 2.0):\n'
        '    return {"point_attributes": {"out_height": [amount] * mesh.vertex_count}}\n')
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: folder)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'registry.json')
    source = 'from local import erosion\ngeo = cube(1)\nresult = erosion(geo)\n'
    with pytest.raises(CompileError, match='root-only Mesh Compute'):
        _prepare(source, environment_resolution._make_compilation_environment(), RuntimeRole.NESTED)
    group = _prepare(source, environment_resolution._make_compilation_environment(), RuntimeRole.ROOT)
    assert group.extension_dependencies
    unused = _prepare('from local import erosion\nvalue = cube(1)\n',
                      environment_resolution._make_compilation_environment(), RuntimeRole.NESTED)
    assert not unused.extension_dependencies


def test_invalid_runtime_role_fails_during_semantic_environment_construction():
    """Unknown semantic roles cannot silently acquire ROOT privileges."""
    with pytest.raises(ValueError):
        _prepare('x = cube(1)\n', environment_resolution._make_compilation_environment(), 'UNKNOWN_ROLE')
