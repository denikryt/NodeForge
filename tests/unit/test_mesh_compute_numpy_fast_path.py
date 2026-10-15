"""Optimized NumPy buffer normalization must retain validation and ownership."""

from array import array
import math
import pytest

from NodeForge.mesh_compute import MeshInputs, normalize_mesh_result


def test_numpy_fast_float32_path_copies_and_retains_values():
    """C-contiguous coordinates and attributes detach from user callback buffers."""
    np = pytest.importorskip('numpy')
    coords = np.arange(15, dtype=np.float32).reshape(5, 3)
    attrs = np.array([1, 2, 3, 4, 5], dtype=np.float32)
    result = normalize_mesh_result({'positions': coords, 'point_attributes': {'height': attrs}},
                                   vertex_count=5, writes=('height',))
    assert result.positions == array('f', range(15))
    assert result.point_attributes['height'] == array('f', range(1, 6))
    coords[:] = -1
    attrs[:] = -1
    assert result.positions[0] == 0 and result.point_attributes['height'][0] == 1


def test_numpy_strided_and_float64_buffers_supported():
    """Strided and float64 numeric arrays are normalized as detached float32."""
    np = pytest.importorskip('numpy')
    xyz = np.arange(36, dtype=np.float64).reshape(12, 3)[::2]
    attr = np.arange(12, dtype=np.int64)[::2]
    result = normalize_mesh_result({'positions': xyz, 'point_attributes': {'a': attr}},
                                   vertex_count=6, writes=('a',))
    assert list(result.positions) == list(xyz.astype(np.float32).flat)
    assert list(result.point_attributes['a']) == list(attr.astype(np.float32))


@pytest.mark.parametrize('dtype,bad', [
    ('bool', [True, False]), ('object', [1, 2]), ('complex64', [1j, 2j]),
    ('float64', [1e100, 2.0]), ('float32', [math.nan, 2.0]),
    ('float32', [math.inf, 2.0]),
])
def test_numpy_invalid_buffers_rejected(dtype, bad):
    """Wrong dtypes and out-of-range numbers cannot cross the Blender boundary."""
    np = pytest.importorskip('numpy')
    with pytest.raises(ValueError, match='Mesh Compute a'):
        normalize_mesh_result({'point_attributes': {'a': np.asarray(bad, dtype=dtype)}},
                              vertex_count=2, writes=('a',))


def test_numpy_shape_mismatch_rejected():
    """Only flat or N×3 positions and flat point attributes are accepted."""
    np = pytest.importorskip('numpy')
    with pytest.raises(ValueError, match='positions'):
        normalize_mesh_result({'positions': np.zeros((2, 6), np.float32)}, vertex_count=4, writes=())
    with pytest.raises(ValueError, match='a'):
        normalize_mesh_result({'point_attributes': {'a': np.ones((2, 2), np.float32)}},
                              vertex_count=4, writes=('a',))


def test_input_fast_path_rejects_nonfinite_and_accepts_valid():
    """Input validation remains finite after opportunistic NumPy acceleration."""
    pytest.importorskip('numpy')
    coords = array('f', [0, 0, 0, 1, 1, 1])
    m = MeshInputs(coords, {'height': array('f', [1, 2])}, 2)
    assert m.vertex_count == 2
    coords[1] = float('nan')
    with pytest.raises(ValueError, match='finite'):
        MeshInputs(coords, {}, 2)


def test_without_numpy_import_still_validates_native_arrays(monkeypatch):
    """The public array('f') contract works without a NumPy dependency."""
    import sys
    monkeypatch.setitem(sys.modules, 'numpy', None)
    source = array('f', [0, 1, 2, 3, 4, 5])
    mesh = MeshInputs(source, {}, 2)
    result = normalize_mesh_result({'positions': source}, vertex_count=2, writes=())
    assert list(result.positions) == list(mesh.positions)
    source[0] = float('nan')
    with pytest.raises(ValueError, match='finite'):
        MeshInputs(source, {}, 2)
