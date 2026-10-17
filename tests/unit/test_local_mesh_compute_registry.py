"""Trusted Local runtime bridging into the existing immutable Extension API registry."""

import dataclasses
from pathlib import Path
from types import SimpleNamespace

import pytest

from NodeForge import catalog, environment_resolution, local_sources
from NodeForge.compiler_identities import GroupCompilationIdentity
from NodeForge.errors import CompileError
from NodeForge.extensions.contracts import ExtensionCallableId
from NodeForge.local_runtime import local_runtime_owner_key
from NodeForge.semantic.group import analyze_group_source
from NodeForge.semantic.ir import IRCall, IRCallableKind
from NodeForge.semantic.source_callable_session import SourceCallableSession


@pytest.fixture
def library(tmp_path, monkeypatch):
    """Create one Local root with safe and malicious examples."""
    managed = tmp_path / 'Local'
    managed.mkdir()
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: managed)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'registry.json')
    return managed


def _write(folder, name='erosion', marker=None):
    """Emit a callback with one defaulted float argument."""
    prefix = f'open({str(marker)!r}, "w").write("RUN")\n' if marker else ''
    path = folder / (name + '.py')
    path.write_text(prefix + 'from NodeForge import mesh_compute\n'
                    + '@mesh_compute(reads=("height",), writes=("out_height",))\n'
                    + f'def {name}(mesh, amount: float = 2.0):\n'
                    + '    return {"point_attributes": {"out_height": [amount] * mesh.vertex_count}}\n')
    return path


def _prepare(source, view):
    """Use the production frontend with one root-scoped compilation session."""
    session = SourceCallableSession(resolved_environment=view)
    identity = GroupCompilationIdentity(None, 'ROOT/compute', 'ROOT/compute', 'ROOT/compute')
    return analyze_group_source(source, compilation_identity=identity, resolved_environment=view, source_callable_session=session)


def _all_calls(value):
    """Walk detached semantic IR to find resolved call target identities."""
    found = []
    def visit(x):
        """Collect nested IR call records."""
        if isinstance(x, IRCall):
            found.append(x)
        if dataclasses.is_dataclass(x):
            for field in dataclasses.fields(x):
                visit(getattr(x, field.name))
        elif isinstance(x, (tuple, list)):
            for entry in x:
                visit(entry)
    visit(value)
    return found


def test_explicit_import_creates_canonical_extension_ir_without_user_execution(library):
    """Selected source creates one immutable owner overlay before semantic lowering."""
    marker = library / 'executed'
    source_file = _write(library, marker=marker)
    local_sources.set_local_python_permission(library, allowed=True)
    view = environment_resolution._make_compilation_environment()
    source = ('from local import erosion as erosion_alias\n'
              'geo = input_geometry("Geometry")\n'
              'geo = erosion_alias(geo, amount=2.0)\n'
              'output("Geometry", geo)\n')
    group = _prepare(source, view)
    candidates = [call for call in _all_calls(group.body) if call.target.kind is IRCallableKind.EXTENSION]
    assert len(candidates) == 1
    target = ExtensionCallableId(('local-runtime', catalog._path_key(source_file)), 'erosion')
    assert candidates[0].target.extension_callable_id == target
    assert view.extension_registry.contains(target)
    assert group.extension_dependencies == ((target.owner, view.extension_registry.owner_fingerprint(target.owner)),)
    assert not marker.exists()
    assert len(view.extension_registry._sessions) == 1


def test_captured_backend_invokes_exact_source_only_during_trusted_phase(library):
    """Owner implementation is lazily loaded and forwards to the shared backend method."""
    file = _write(library)
    local_sources.set_local_python_permission(library, allowed=True)
    view = environment_resolution._make_compilation_environment()
    group = _prepare('from local import erosion\nsource_geo = input_geometry("Geometry")\ngeo = erosion(source_geo)\noutput("Geometry", geo)', view)
    target = ExtensionCallableId(('local-runtime', catalog._path_key(file)), 'erosion')
    seen = []

    def compute(geometry, **kwargs):
        """Collect runtime builder call without importing bpy."""
        seen.append((geometry, kwargs))
        return 'SENTINEL'
    context = SimpleNamespace(mesh_compute=compute)
    value = view.extension_registry.invoke_implementation(target, context, 'SOURCE_SOCKET', 3.0)
    assert value == 'SENTINEL'
    assert seen[0][0] == 'SOURCE_SOCKET'
    assert seen[0][1]['parameters'] == {'amount': 3.0}
    assert seen[0][1]['reads'] == ('height',)
    assert seen[0][1]['writes'] == ('out_height',)
    assert seen[0][1]['kernel'].__name__ == 'erosion'


def test_untrusted_import_fails_at_selection_without_running_source(library):
    """A default-denied root never invokes user code via extension normalization."""
    marker = library / 'executed'
    _write(library, marker=marker)
    view = environment_resolution._make_compilation_environment()
    with pytest.raises(CompileError, match='Allow executable Python'):
        _prepare('from local import erosion\nsource_geo = input_geometry("Geometry")\ngeo = erosion(source_geo)\noutput("Geometry", geo)', view)
    assert not marker.exists()
    assert not view.extension_registry._families


def test_star_import_excludes_python_and_inherited_explicit_import_is_rejected(library):
    """Legacy wildcard Local calls keep `.nf` behavior without leaking Python exports."""
    _write(library)
    local_sources.set_local_python_permission(library, allowed=True)
    view = environment_resolution._make_compilation_environment()
    _prepare('from local import *\noutput(1.0)', view)
    assert not view.extension_registry._families
    with pytest.raises(CompileError, match='explicit import'):
        _prepare('from local import erosion\n'
                 'source_geo = input_geometry("Geometry")\ndef nested():\n    return erosion(source_geo)\n'
                 'output(nested())', environment_resolution._make_compilation_environment())


def test_selected_owner_snapshots_do_not_import_other_python_sources(library):
    """The registry overlay imports neither unrelated nor unselected Local modules."""
    _write(library)
    marker = library / 'other-executed'
    _write(library, name='other', marker=marker)
    local_sources.set_local_python_permission(library, allowed=True)
    env = environment_resolution._make_compilation_environment()
    _prepare('from local import erosion\nsource_geo = input_geometry("Geometry")\noutput(erosion(source_geo))', env)
    assert len(env.extension_registry._sessions) == 1
    assert not marker.exists()
    assert not (library / 'other.py').with_suffix('.pyc').exists()


def test_recompile_captures_modified_source_without_reusing_implementation(library):
    """Separate root attempts preserve canonical owner ID but vary source fingerprint."""
    file = _write(library)
    local_sources.set_local_python_permission(library, allowed=True)
    before = environment_resolution._make_compilation_environment()
    _prepare('from local import erosion\nsource_geo = input_geometry("Geometry")\noutput(erosion(source_geo))', before)
    owner = ('local-runtime', catalog._path_key(file))
    old = before.extension_registry.owner_fingerprint(owner)
    file.write_text(file.read_text().replace('2.0', '3.0'))
    after = environment_resolution._make_compilation_environment()
    _prepare('from local import erosion\nsource_geo = input_geometry("Geometry")\noutput(erosion(source_geo))', after)
    assert after.extension_registry.owner_fingerprint(owner) != old
    assert before.extension_registry.owner_fingerprint(owner) == old


def test_qualified_local_python_import_uses_same_canonical_identity(library):
    """A qualified root import resolves the physical Local owner without alias drift."""
    directory = library / 'terrain'
    directory.mkdir()
    file = _write(directory)
    local_sources.set_local_python_permission(library, allowed=True)
    view = environment_resolution._make_compilation_environment()
    group = _prepare('from local.terrain import erosion as terrain_erosion\n'
                     'geo = input_geometry("Geometry")\n'
                     'output(terrain_erosion(geo))', view)
    owner = ('local-runtime', catalog._path_key(file))
    assert group.extension_dependencies[0][0] == owner
    assert view.extension_registry.contains(ExtensionCallableId(owner, 'erosion'))


def test_member_call_through_directory_alias_rejects_runtime_without_source_lowering(library):
    """Directory aliases may not downgrade a Python runtime record to a `.nf` source."""
    directory = library / 'terrain'
    directory.mkdir()
    _write(directory)
    local_sources.set_local_python_permission(library, allowed=True)
    with pytest.raises(CompileError, match='explicit from local'):
        _prepare('from local import terrain as t\ngeo = input_geometry("Geometry")\n'
                 'output(t.erosion(geo))', environment_resolution._make_compilation_environment())


def test_individual_linked_python_file_requires_its_own_grant(library, tmp_path):
    """Legacy individually linked files remain independent of folder permissions."""
    linked = _write(tmp_path)
    # Legacy file registrations are supported as a persisted v1 registry record.
    local_sources._write_local_source_registry([
        {'path': str(linked), 'label': 'External', 'kind': 'file'},
    ])
    view = environment_resolution._make_compilation_environment()
    with pytest.raises(CompileError, match='Allow executable Python'):
        _prepare('from local import erosion\ngeo = input_geometry("Geometry")\n'
                 'output(erosion(geo))', view)
    local_sources.set_local_python_permission(linked, allowed=True)
    trusted = environment_resolution._make_compilation_environment()
    group = _prepare('from local import erosion\ngeo = input_geometry("Geometry")\n'
                     'output(erosion(geo))', trusted)
    assert group.extension_dependencies[0][0] == ('local-runtime', catalog._path_key(linked))
    local_sources.set_local_python_permission(linked, allowed=False)
    with pytest.raises(CompileError, match='Allow executable Python'):
        _prepare('from local import erosion\ngeo = input_geometry("Geometry")\n'
                 'output(erosion(geo))', environment_resolution._make_compilation_environment())
