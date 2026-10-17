"""Local Python discovery and trust regressions without importing callback code."""

import json
import os

import pytest

from NodeForge import catalog, local_sources
from NodeForge.errors import CompileError
from NodeForge.local_index import LocalSourceIndex


def _python(path, *, marker=None):
    """Write one runtime-marked file that would expose imports if executed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    prefix = f'open({str(marker)!r}, "w").write("executed")\n' if marker else ''
    path.write_text(prefix + 'from NodeForge import mesh_compute\n@mesh_compute(writes=("height",))\ndef erosion(mesh):\n    return {"point_attributes": {"height": []}}\n', encoding='utf-8')
    return path


@pytest.fixture
def local_storage(tmp_path, monkeypatch):
    """Redirect the Local registry and managed tree to user-isolated paths."""
    managed = tmp_path / 'Managed'
    managed.mkdir()
    registry = tmp_path / 'registry.json'
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: managed)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: registry)
    return managed, registry


def test_local_runtime_discovery_is_ast_only_and_helpers_stay_hidden(local_storage):
    """Qualified and flat runtime sources are indexed without executing imports."""
    managed, _ = local_storage
    marker = managed / 'executed'
    py = _python(managed / 'terrain' / 'erosion.py', marker=marker)
    (managed / 'terrain' / 'helper.py').write_text('open("bad", "w").write("bad")\n')
    index = LocalSourceIndex.from_discovery(*catalog.discover_local_sources((managed,)))
    candidate = index.find_flat_function('erosion')
    assert candidate.kind == 'runtime'
    assert candidate.source_path == py
    assert index.resolve_member(index.resolve_directory(('terrain',)), 'erosion') == candidate
    assert 'helper' not in index.flat_names()
    assert not marker.exists()


def test_runtime_name_collisions_reject_without_preferring_nf(local_storage):
    """Two different sources at one member slot remain ambiguous by identity."""
    managed, _ = local_storage
    _python(managed / 'erosion.py')
    (managed / 'erosion.nf').write_text('output(1)\n')
    index = LocalSourceIndex.from_discovery(*catalog.discover_local_sources((managed,)))
    with pytest.raises(CompileError, match='Ambiguous'):
        index.find_flat_function('erosion')
    with pytest.raises(CompileError, match='Ambiguous'):
        index.resolve_root_import('erosion')


def test_python_symlink_escape_is_rejected(local_storage, tmp_path):
    """Discovery cannot silently select a trusted Local path outside its root."""
    managed, _ = local_storage
    external = _python(tmp_path / 'outside' / 'erosion.py')
    try:
        (managed / 'erosion.py').symlink_to(external)
    except (OSError, NotImplementedError):
        pytest.skip('Symlink creation not available')
    with pytest.raises(CompileError, match='outside its registered root'):
        catalog.discover_local_sources((managed,))


def test_permission_is_default_deny_managed_and_external(local_storage, tmp_path):
    """Grants apply to one registered canonical root, not other linked roots."""
    managed, registry = local_storage
    local = _python(managed / 'erosion.py')
    external = tmp_path / 'External'
    linked = _python(external / 'erosion.py')
    another = tmp_path / 'Another'
    unlinked = _python(another / 'erosion.py')
    local_sources.link_local_source_folder(str(external))
    assert not local_sources.local_python_permission(local)
    assert not local_sources.local_python_permission(linked)
    assert not local_sources.local_python_permission(unlinked)
    local_sources.set_local_python_permission(managed, allowed=True)
    assert local_sources.local_python_permission(local)
    assert not local_sources.local_python_permission(linked)
    local_sources.set_local_python_permission(external, allowed=True)
    assert local_sources.local_python_permission(linked)
    assert not local_sources.local_python_permission(unlinked)
    payload = json.loads(registry.read_text())
    assert payload['managed_allow_python'] is True
    assert payload['roots'][0]['allow_python'] is True
    local_sources.set_local_python_permission(external, allowed=False)
    assert not local_sources.local_python_permission(linked)
    assert local_sources.local_python_permission(local)


def test_permission_survives_link_registry_mutation(local_storage, tmp_path):
    """Link, relabel and unlink paths do not silently lose other execution grants."""
    managed, registry = local_storage
    local_sources.set_local_python_permission(managed, allowed=True)
    root_a = tmp_path / 'A'
    root_b = tmp_path / 'B'
    root_a.mkdir()
    root_b.mkdir()
    local_sources.link_local_source_folder(str(root_a))
    local_sources.set_local_python_permission(root_a, allowed=True)
    local_sources.link_local_source_folder(str(root_b))
    local_sources.unlink_local_source_folder(root_b)
    payload = json.loads(registry.read_text())
    assert payload['managed_allow_python'] is True
    assert payload['roots'][0]['allow_python'] is True
    assert len(payload['roots']) == 1


def test_permission_rejects_unregistered_root_and_malformed_payload(local_storage, tmp_path):
    """Unapproved roots and invalid configuration never acquire implicit trust."""
    managed, registry = local_storage
    other = tmp_path / 'unregistered'
    other.mkdir()
    with pytest.raises(CompileError, match='registered'):
        local_sources.set_local_python_permission(other, allowed=True)
    with pytest.raises(CompileError, match='boolean'):
        local_sources.set_local_python_permission(managed, allowed=1)
    registry.write_text(json.dumps({'version': 1, 'roots': [], 'managed_allow_python': 'true'}))
    with pytest.raises(CompileError, match='permission'):
        local_sources.local_python_permission(_python(managed / 'erosion.py'))
