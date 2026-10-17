"""Positive and negative pure-Python regression tests for Mesh Compute contracts."""

from array import array
import math

import pytest

from NodeForge import mesh_compute
from NodeForge.errors import CompileError
from NodeForge.mesh_compute import (
    MeshInputs, has_mesh_compute_declaration, normalize_mesh_result, parse_mesh_compute_source,
)


def _source(header='@mesh_compute(reads=("height",), writes=("new_height",))', signature='mesh, amount: float = 1.0'):
    """Return valid Local authoring syntax independently of Blender."""
    return f'from NodeForge import mesh_compute\n{header}\ndef erosion({signature}):\n    return {{}}\n'


def test_static_parse_accepts_public_function_and_literals_without_execution(tmp_path):
    """Import-time side effects are not run during candidate inspection or validation."""
    marker = tmp_path / 'executed'
    source = f'open({str(marker)!r}, "w").write("bad")\n' + _source()
    assert has_mesh_compute_declaration(source)
    spec = parse_mesh_compute_source(source, stem='erosion')
    assert spec.name == 'erosion'
    assert spec.reads == ('height',)
    assert spec.writes == ('new_height',)
    assert spec.parameters == (('amount', 'float', 1.0),)
    assert not marker.exists()


@pytest.mark.parametrize('source', [
    _source(header='@mesh_compute(reads=("height", "height"))'),
    _source(header='@mesh_compute(reads=get_inputs())'),
    _source(header='@mesh_compute("height")'),
    _source(signature='mesh: Geometry, amount: float = 1.0'),
    _source(signature='mesh, amount: str = "x"'),
    _source(signature='mesh, *args'),
    _source(signature='mesh, amount: float = None'),
    _source().replace('def erosion', 'async def erosion'),
    _source().replace('def erosion', 'def other'),
    _source() + '\n@mesh_compute()\ndef other(mesh): pass\n',
])
def test_static_parse_rejects_unsafe_or_invalid_declarations(source):
    """Reject inconsistent callable contracts at discovery/selection boundary."""
    with pytest.raises(CompileError):
        parse_mesh_compute_source(source, stem='erosion')


def test_discovery_ignores_undecorated_helpers_and_invalid_python():
    """Only an actual top-level @mesh_compute candidate is exposed in Local."""
    assert not has_mesh_compute_declaration('def erosion(mesh): return {}')
    assert not has_mesh_compute_declaration('import subprocess\n' 'def bad(:\n')
    assert not has_mesh_compute_declaration('def other(mesh):\n @mesh_compute()\n def erosion(mesh): return {}')


def test_public_decorator_attaches_metadata_without_invoking_function():
    """The decorator stores authoring intent without computing mesh data."""
    called = []

    @mesh_compute(reads=['a'], writes=('b',))
    def erosion(mesh, amount: float = 1.0):
        called.append(1)
        return {}

    assert erosion.__nodeforge_mesh_compute__ == (('a',), ('b',))
    assert not called
    with pytest.raises(CompileError):
        mesh_compute(writes=('a', 'a'))


def test_inputs_and_results_positive_positions_and_attributes():
    """All allowed result combinations preserve a detached scalar float32 representation."""
    mesh = MeshInputs(array('f', [0, 1, 2, 3, 4, 5]), {'height': array('f', [2, 4])}, 2)
    assert mesh.vertex_count == 2
    positions = normalize_mesh_result({'positions': [0, 1, 5, 3, 4, 8]}, vertex_count=2, writes=())
    assert list(positions.positions) == [0, 1, 5, 3, 4, 8]
    assert not positions.point_attributes
    attrs = normalize_mesh_result({'point_attributes': {'height': [4.5, 5.5]}}, vertex_count=2, writes=('height',))
    assert attrs.positions is None and list(attrs.point_attributes['height']) == [4.5, 5.5]
    both = normalize_mesh_result({'positions': mesh.positions, 'point_attributes': {'height': mesh.point_attributes['height']}}, vertex_count=2, writes=('height',))
    assert both.positions is not mesh.positions and both.point_attributes['height'] is not mesh.point_attributes['height']


@pytest.mark.parametrize('raw,writes', [
    ({}, ()),
    ({'positions': [0.0, 1.0]}, ()),
    ({'positions': [0] * 6, 'extra': True}, ()),
    ({'positions': [float('nan')] * 6}, ()),
    ({'positions': [float('inf')] * 6}, ()),
    ({'positions': [1e400] * 6}, ()),
    ({'positions': ['1'] * 6}, ()),
    ({'positions': [True] * 6}, ()),
    ({'point_attributes': {'height': [1]}}, ('height',)),
    ({'point_attributes': {'wrong': [1, 2]}}, ('height',)),
    ({'point_attributes': {'height': [1, 2]}}, ()),
    ({'point_attributes': {}}, ()),
])
def test_invalid_results_are_rejected_before_blender_publication(raw, writes):
    """Reject stale or ambiguous array outputs before any Blender ID is created."""
    with pytest.raises(ValueError):
        normalize_mesh_result(raw, vertex_count=2, writes=writes)


def test_invalid_mesh_buffers_rejected_at_callback_boundary():
    """A zero-vertex or malformed input cannot reach numerical execution."""
    with pytest.raises(ValueError):
        MeshInputs(array('f'), {}, 0)
    with pytest.raises(ValueError):
        MeshInputs(array('f', [1, 2]), {}, 1)
    with pytest.raises(ValueError):
        MeshInputs(array('f', [0, 1, math.inf]), {}, 1)
    with pytest.raises(ValueError):
        MeshInputs(array('f', [0, 1, 2]), {'height': array('f', [1, 2])}, 1)


def test_numerical_key_ignores_unobservable_topology_but_sees_declared_inputs():
    """A connectivity-only update may reuse compute while republishing native geometry."""
    from NodeForge.mesh_compute import fingerprint_mesh_inputs
    xyz = array('f', [0, 0, 0, 1, 0, 0, 0, 1, 0, 1, 1, 0])
    mesh = MeshInputs(xyz, {'height': array('f', [0, 1, 2, 3])}, 4)
    # Edges/faces intentionally remain outside MeshInputs; the callback never sees them.
    topology_a = [(0, 1), (1, 3), (3, 2)]
    topology_b = [(0, 2), (2, 3), (3, 1)]
    assert topology_a != topology_b
    def fingerprint(data, amount=2.0, generation='a'):
        """Construct one production cache fingerprint from an observable payload."""
        return fingerprint_mesh_inputs(data, declared_reads=('height',),
                                       parameters={'amount': amount}, implementation_fingerprint=generation)
    assert fingerprint(mesh) == fingerprint(mesh)
    assert fingerprint(mesh) != fingerprint(mesh, amount=3.0)
    assert fingerprint(mesh) != fingerprint(mesh, generation='b')
    other = MeshInputs(xyz, {'height': array('f', [0, 1, 2, 4])}, 4)
    assert fingerprint(mesh) != fingerprint(other)
    with pytest.raises(ValueError, match='declared reads'):
        fingerprint_mesh_inputs(mesh, declared_reads=(), parameters={}, implementation_fingerprint='a')
