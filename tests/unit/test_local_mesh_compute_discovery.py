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


def test_local_source_membership_follows_registered_roots(local_storage, tmp_path):
    """A managed Python source is active, an external source needs a registered owner."""
    managed, _registry = local_storage
    inside = _python(managed / 'erosion.py')
    external = _python(tmp_path / 'outside' / 'erosion.py')
    assert local_sources.is_registered_local_python_source(inside)
    assert not local_sources.is_registered_local_python_source(external)
    local_sources.link_local_source_folder(str(external.parent))
    assert local_sources.is_registered_local_python_source(external)
    local_sources.unlink_local_source_folder(external.parent)
    assert not local_sources.is_registered_local_python_source(external)


def test_registry_writes_drop_obsolete_permission_fields(local_storage, tmp_path):
    """Legacy grants are ignored and never reintroduced on ordinary root mutations."""
    _managed, registry = local_storage
    root = tmp_path / 'linked'
    root.mkdir()
    registry.write_text(json.dumps({'version': 1, 'roots': [], 'managed_allow_python': False,
                                   'allow_examples_python': True}))
    local_sources.link_local_source_folder(str(root))
    data = json.loads(registry.read_text())
    assert data['roots'][0]['path'] == str(root)
    assert 'managed_allow_python' not in data
    assert 'allow_examples_python' not in data


def test_invalid_registry_rejected_without_permission_gate(local_storage, tmp_path):
    """Owner paths still require valid registry shape; obsolete grants are ignored."""
    managed, registry = local_storage
    path = _python(managed / 'erosion.py')
    registry.write_text(json.dumps({'version': 1, 'roots': [], 'managed_allow_python': 'old'}))
    assert local_sources.is_registered_local_python_source(path)
    registry.write_text(json.dumps({'version': 1, 'roots': 'wrong'}))
    with pytest.raises(CompileError, match='roots'):
        local_sources.local_source_roots()
