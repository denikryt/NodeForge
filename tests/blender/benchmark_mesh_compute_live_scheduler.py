"""Measure complete automatic Mesh Compute updates with a real Blender modifier.

Run exactly one grid size per clean Blender process:

    blender --background --factory-startup --python benchmark_mesh_compute_live_scheduler.py -- \
        --side 96 --repeats 5 --warmups 2 --output /tmp/scheduler_96.json

The timed region includes the Blender update tag, dependency-graph handler,
Mesh Compute's evaluated prefix, numerical/publication fingerprints, optional
Python callback + Direct Mesh publication, and a final evaluated GN output.
Compilation, mesh creation, source installation and permission setup are
excluded. Source inputs are changed only before the measured update.
"""

from __future__ import annotations

import argparse
from array import array
from dataclasses import replace
import json
from pathlib import Path
import statistics
import sys
import tempfile
from time import perf_counter_ns

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from NodeForge import compiler, local_sources
from NodeForge.blender import mesh_compute_runtime as runtime

SOURCE = '''from local import shift
geo = input_geometry("Geometry")
amount = input_float("Amount", default=2.0)
geo = store_named_attribute(geo, "height", position().z, domain="POINT", type="FLOAT")
geo = shift(geo, amount=amount)
output("Geometry", geo)
'''
KERNEL = '''import numpy as np
from NodeForge import mesh_compute
@mesh_compute(writes=("height",))
def shift(mesh, amount: float = 2.0):
    xyz = np.frombuffer(mesh.positions, dtype=np.float32).reshape(-1, 3).copy()
    xyz[:, 2] += np.float32(amount)
    return {"positions": xyz, "point_attributes": {"height": xyz[:, 2].copy()}}
'''
PHASES = ('unchanged_owner_event', 'unrelated_object_event',
          'changed_scalar', 'changed_nonread_material')


def summarize(samples: list[float]) -> dict[str, float]:
    """Return repeatable descriptive statistics, retaining the raw samples."""
    if not samples:
        raise ValueError('at least one measurement required')
    if any(not np.isfinite(v) or v < 0 for v in samples):
        raise ValueError('measurements must be finite nonnegative milliseconds')
    return {'median_ms': round(statistics.median(samples), 4),
            'mean_ms': round(statistics.mean(samples), 4),
            'stdev_ms': round(statistics.stdev(samples) if len(samples) > 1 else 0.0, 4),
            'min_ms': round(min(samples), 4), 'max_ms': round(max(samples), 4),
            'samples_ms': [round(v, 4) for v in samples]}


def evaluated_output(owner, side):
    """Force the evaluated GN suffix and verify output size, layers and values."""
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = owner.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        if len(mesh.vertices) != side * side:
            raise AssertionError(f'wrong consumer vertex count: {len(mesh.vertices)}')
        result = array('f', [0]) * (side * side)
        mesh.attributes['height'].data.foreach_get('value', result)
        if not np.isfinite(np.frombuffer(result, dtype=np.float32)).all():
            raise AssertionError('nonfinite consumer heights')
        if len(mesh.polygons) == 0:
            raise AssertionError('missing consumer faces')
        return result, mesh.polygons[0].material_index
    finally:
        evaluated.to_mesh_clear()


def _event(owner, *, refresh=frozenset({'OBJECT'})):
    """Notify Blender of an RNA edit and run its registered pre-update hooks."""
    owner.update_tag(refresh=set(refresh))
    bpy.context.view_layer.update()


def _sample(owner, side):
    """Time a real tagged dependency-graph update through the evaluated suffix."""
    start = perf_counter_ns()
    _event(owner)
    result = evaluated_output(owner, side)
    return (perf_counter_ns() - start) / 1e6, result


def run(side: int, repeats: int, warmups: int) -> dict:
    """Benchmark state-preserving, foreign, scalar and non-read mesh changes."""
    if side < 3 or repeats < 1 or warmups < 0:
        raise ValueError('side >= 3; repeats >= 1; warmups >= 0 required')
    with tempfile.TemporaryDirectory(prefix='nf-live-runtime-') as dirname:
        root = Path(dirname)
        local = root / 'Local'
        local.mkdir()
        (local / 'shift.py').write_text(KERNEL)
        orig_root = local_sources._default_local_catalog_dir
        orig_registry = local_sources._local_sources_registry_path
        local_sources._default_local_catalog_dir = lambda: local
        local_sources._local_sources_registry_path = lambda: root / 'registry.json'
        try:

            bpy.ops.mesh.primitive_grid_add(x_subdivisions=side - 1, y_subdivisions=side - 1)
            owner = bpy.context.object
            if len(owner.data.vertices) != side * side:
                raise AssertionError('grid subdivisions do not produce requested vertices')
            mesh = owner.data
            if len(mesh.polygons) == 0:
                raise AssertionError('expected grid with faces')
            material_a = bpy.data.materials.new('LiveBenchmarkMaterialA')
            material_b = bpy.data.materials.new('LiveBenchmarkMaterialB')
            mesh.materials.append(material_a)
            mesh.materials.append(material_b)
            foreign_mesh = bpy.data.meshes.new('LiveBenchmarkForeignMesh')
            foreign_mesh.from_pydata([(0, 0, 0)], [], [])
            foreign = bpy.data.objects.new('LiveBenchmarkUnrelated', foreign_mesh)
            bpy.context.scene.collection.objects.link(foreign)

            compiler.create_mesh_compute_modifier(SOURCE, owner)
            binding = runtime.get_modifier_binding(owner)
            calls = [0]
            original = binding.prepared.kernel

            def counted(*args, **kwargs):
                """Count actual kernel calls independently of NumPy caching."""
                calls[0] += 1
                return original(*args, **kwargs)
            binding.prepared = replace(binding.prepared, kernel=counted)
            runtime.evaluate_modifier_binding(owner)
            assert binding.status == 'READY'
            socket = next(item for item in binding.group.interface.items_tree
                          if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == 'Amount')
            input_value = getattr(binding.modifier.properties.inputs, socket.identifier)
            base = 2.0

            # Let one-time depsgraph propagation settle without including it in the series.
            for _ in range(3):
                _event(owner)
            if binding.status != 'READY':
                raise AssertionError(f'initial automatic scheduler status: {binding.status}')
            result = {'side': side, 'vertices': side * side,
                      'repeats': repeats, 'warmups': warmups,
                      'blender': bpy.app.version_string, 'phases': {}}
            for phase in PHASES:
                samples = []
                changes = []
                for i in range(warmups + repeats):
                    old_mesh = binding.prepared.result_proxy.data
                    old_calls = calls[0]
                    if phase == 'unchanged_owner_event':
                        measured, (heights, material_index) = _sample(owner, side)
                        expected_calls = 0
                        expected_republication = False
                    elif phase == 'unrelated_object_event':
                        start = perf_counter_ns()
                        _event(foreign)
                        heights, material_index = evaluated_output(owner, side)
                        measured = (perf_counter_ns() - start) / 1e6
                        expected_calls = 0
                        expected_republication = False
                    elif phase == 'changed_scalar':
                        input_value.value = base + (i + 1) * 0.125
                        measured, (heights, material_index) = _sample(owner, side)
                        expected_calls = 1
                        expected_republication = True
                    else:
                        mesh.polygons[0].material_index = (i + 1) % 2
                        mesh.update()
                        measured, (heights, material_index) = _sample(owner, side)
                        expected_calls = 0
                        expected_republication = True
                    delta = calls[0] - old_calls
                    republished = binding.prepared.result_proxy.data is not old_mesh
                    if delta != expected_calls or republished != expected_republication:
                        raise AssertionError(f'{phase}: calls={delta}, republished={republished}; '
                                             f'expected {expected_calls}, {expected_republication}')
                    if binding.status != 'READY':
                        raise AssertionError(f'{phase}: binding status {binding.status}, {binding.last_error}')
                    # Verify *actual evaluated output* after the timed region.
                    # A correct call/mesh-pointer count alone is not enough.
                    expected_z = float(input_value.value)
                    if not np.allclose(np.frombuffer(heights, dtype=np.float32), expected_z,
                                       rtol=0, atol=2e-5):
                        raise AssertionError(f'{phase}: GN suffix did not publish current height {expected_z}')
                    expected_material = (i + 1) % 2 if phase == 'changed_nonread_material' else 0
                    if material_index != expected_material:
                        raise AssertionError(f'{phase}: output material index {material_index} != {expected_material}')
                    if i >= warmups:
                        samples.append(measured)
                        changes.append({'kernel_calls': delta, 'mesh_republished': republished})
                result['phases'][phase] = {**summarize(samples), 'checks': changes,
                                            'expected_kernel_calls': expected_calls,
                                            'expected_republication': expected_republication}
                print('LIVE_RUNTIME', side, phase, result['phases'][phase]['median_ms'], flush=True)
            return result
        finally:
            local_sources._default_local_catalog_dir = orig_root
            local_sources._local_sources_registry_path = orig_registry
            # Standalone Blender process is discarded after one configuration.


def main():
    """Run one isolated configuration and write its diagnostic report."""
    parser = argparse.ArgumentParser()
    parser.add_argument('--side', type=int, default=96)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--output', type=Path)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    result = run(args.side, args.repeats, args.warmups)
    print('LIVE_RUNTIME_RESULT=' + json.dumps(result), flush=True)
    if args.output:
        args.output.write_text(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
