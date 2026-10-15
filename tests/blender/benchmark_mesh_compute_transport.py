"""Opt-in Blender 5.2 Direct Mesh physical benchmark for Local Mesh Compute.

The selected Direct Mesh backend is compiled into a GN consumer with an identical
isolated source-prefix group, deterministic NumPy kernel and native suffix.
This physical-pipeline benchmark does not exercise
Local source discovery, UI compile, precommit cutover or the live scheduler.
Lifecycle guarantees are tested separately in runtime regression tests.

Run, for example:
  blender -b --factory-startup --python tests/blender/benchmark_mesh_compute_transport.py -- \
      --sizes 96 512 --steps 0 100 500 --repeats 5
"""

from __future__ import annotations

from argparse import ArgumentParser
from array import array
from pathlib import Path
import cProfile
import gc
import json
import importlib
import pstats
import os
import resource
import statistics
import sys
import time

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from NodeForge.mesh_compute import normalize_mesh_result
from NodeForge.blender.mesh_compute_runtime import EvaluatedMeshCompute, mesh_inputs_from_evaluated
from NodeForge.blender.mesh_compute_transports import direct_mesh_update


def _now():
    """Return a monotonic high-resolution benchmark timestamp."""
    return time.perf_counter_ns()


def _ms(delta):
    """Convert elapsed nanoseconds to milliseconds."""
    return delta / 1_000_000


def _stock_mesh(size):
    """Create deterministic quads with height variation and native UVs in Blender."""
    if size < 3:
        raise ValueError('Benchmark grid side must contain at least 3 vertices')
    # Blender grid "subdivisions" uses interval count: S creates (S + 1)^2
    # vertices. Report regimes by actual vertex count, not operator arguments.
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=size - 1, y_subdivisions=size - 1, size=10)
    obj = bpy.context.object
    obj.name = 'NF benchmark geometry source'
    mesh = obj.data
    mesh.name = 'NF benchmark original quads'
    if len(mesh.vertices) != size * size:
        raise AssertionError(f'Benchmark grid expected {size ** 2} vertices, got {len(mesh.vertices)}')
    coords = array('f', [0.0]) * (len(mesh.vertices) * 3)
    mesh.vertices.foreach_get('co', coords)
    xyz = np.frombuffer(coords, dtype=np.float32).reshape(-1, 3)
    xyz[:, 2] = (np.sin(xyz[:, 0] * 1.1) * np.cos(xyz[:, 1] * .9) * .15).astype(np.float32)
    mesh.vertices.foreach_set('co', coords)
    if not mesh.uv_layers:
        mesh.uv_layers.new(name='Benchmark UV')
    mesh.update()
    return obj, mesh


def _prefix_group():
    """Generate the same height-storing GN prefix for source and consumer."""
    group = bpy.data.node_groups.new('NF benchmark prefix', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    inp = group.nodes.new('NodeGroupInput')
    pos = group.nodes.new('GeometryNodeInputPosition')
    xyz = group.nodes.new('ShaderNodeSeparateXYZ')
    group.links.new(pos.outputs['Position'], xyz.inputs[0])
    store = group.nodes.new('GeometryNodeStoreNamedAttribute')
    store.domain = 'POINT'
    store.data_type = 'FLOAT'
    store.inputs['Name'].default_value = 'height'
    group.links.new(inp.outputs['Geometry'], store.inputs['Geometry'])
    group.links.new(xyz.outputs['Z'], store.inputs['Value'])
    out = group.nodes.new('NodeGroupOutput')
    group.links.new(store.outputs['Geometry'], out.inputs['Geometry'])
    return group


def _consumer_group(prefix, proxy):
    """Build the selected Direct Mesh GN boundary with its standard suffix."""
    group = bpy.data.node_groups.new('NF benchmark consumer', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    inp = group.nodes.new('NodeGroupInput')
    boundary = direct_mesh_update.build_boundary(group, proxy)
    suffix = group.nodes.new('GeometryNodeSetPosition')
    suffix.inputs['Offset'].default_value = (0.0, 0.0, 0.5)
    group.links.new(boundary, suffix.inputs['Geometry'])
    output = group.nodes.new('NodeGroupOutput')
    group.links.new(suffix.outputs['Geometry'], output.inputs['Geometry'])
    return group


def _kernel(inputs, steps):
    """Deterministic grid-stencil workload using copied NumPy float32 input arrays.

    This is *not* a physical hydraulic erosion model. It isolates transfer
    cost while providing 0/100/500 iterations of nontrivial point arithmetic.
    """
    side = int(np.sqrt(inputs.vertex_count))
    if side * side != inputs.vertex_count:
        raise ValueError('Benchmark requires a square grid')
    height = np.frombuffer(inputs.point_attributes['height'], dtype=np.float32).copy().reshape(side, side)
    water = np.zeros_like(height)
    for _ in range(steps):
        north = np.roll(height, 1, axis=0)
        south = np.roll(height, -1, axis=0)
        east = np.roll(height, 1, axis=1)
        west = np.roll(height, -1, axis=1)
        slope = (north + south + east + west - np.float32(4) * height)
        height += np.float32(.004) * slope
        water += np.float32(.00001) * np.abs(slope)
    positions = np.frombuffer(inputs.positions, dtype=np.float32).copy().reshape(-1, 3)
    positions[:, 2] = height.reshape(-1)
    return {'positions': positions, 'point_attributes': {
        'height': height.reshape(-1), 'water': water.reshape(-1)}}


def _setup(size):
    """Build fresh source, isolated prefix and consumer/proxies per experiment."""
    consumer_scene = bpy.context.scene
    source, original = _stock_mesh(size)
    scene = bpy.data.scenes.new('NF benchmark isolated prefix Scene')
    isolated = bpy.data.objects.new('NF benchmark isolated source', original)
    scene.collection.objects.link(isolated)
    prefix = _prefix_group()
    source_mod = isolated.modifiers.new('NF benchmark prefix', 'NODES')
    source_mod.node_group = prefix
    empty = bpy.data.meshes.new('NF benchmark empty result')
    proxy = bpy.data.objects.new('NF benchmark result proxy', empty)
    consumer_group = _consumer_group(prefix, proxy)
    consumer_mod = source.modifiers.new('NF benchmark GN consumer', 'NODES')
    consumer_mod.node_group = consumer_group
    return (consumer_scene, scene, source, isolated, original, prefix, consumer_group,
            proxy, empty)


def _source_mesh(prefix_scene, isolated, *, detail=None):
    """Evaluate the isolated prefix with all native data layers available."""
    with bpy.context.temp_override(scene=prefix_scene, view_layer=prefix_scene.view_layers[0]):
        t0 = _now()
        prefix_scene.view_layers[0].update()
        t1 = _now()
        dg = bpy.context.evaluated_depsgraph_get()
        t2 = _now()
        evaluated = isolated.evaluated_get(dg)
        t3 = _now()
        temp = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
        t4 = _now()
        if detail is not None:
            detail.update(source_view_update_ms=_ms(t1 - t0),
                          source_depsgraph_ms=_ms(t2 - t1),
                          source_evaluated_get_ms=_ms(t3 - t2),
                          source_to_mesh_ms=_ms(t4 - t3))
        return evaluated, temp, dg


def _consumer_snapshot(consumer_scene, obj, expected_count, *, detail=None):
    """Force full GN suffix evaluation and assert native output observables."""
    with bpy.context.temp_override(scene=consumer_scene, view_layer=consumer_scene.view_layers[0]):
        t0 = _now()
        consumer_scene.view_layers[0].update()
        t1 = _now()
        dg = bpy.context.evaluated_depsgraph_get()
        t2 = _now()
        evaluated = obj.evaluated_get(dg)
        t3 = _now()
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=dg)
        t4 = _now()
        try:
            if len(mesh.vertices) != expected_count or len(mesh.uv_layers) < 1:
                raise AssertionError(f'GN result lost vertices/UV: {len(mesh.vertices)} / {len(mesh.uv_layers)}')
            height = mesh.attributes.get('height')
            water = mesh.attributes.get('water')
            if height is None or water is None or height.domain != 'POINT' or water.domain != 'POINT':
                raise AssertionError('GN result lacks declared height/water attributes')
            return (len(mesh.vertices), len(mesh.polygons), len(mesh.uv_layers), len(mesh.materials))
        finally:
            t5 = _now()
            evaluated.to_mesh_clear()
            t6 = _now()
            if detail is not None:
                detail.update(consumer_view_update_ms=_ms(t1 - t0),
                              consumer_depsgraph_ms=_ms(t2 - t1),
                              consumer_evaluated_get_ms=_ms(t3 - t2),
                              consumer_to_mesh_ms=_ms(t4 - t3),
                              consumer_assertions_ms=_ms(t5 - t4),
                              consumer_mesh_clear_ms=_ms(t6 - t5))


def _remove_id(collection, obj):
    """Delete one exact test-owned Blender datablock without relying on labels."""
    if obj is not None:
        try:
            if obj.as_pointer() in {x.as_pointer() for x in collection}:
                collection.remove(obj, do_unlink=True)
        except ReferenceError:
            pass


def _teardown(state, created):
    """Free physical benchmark scene/Object/Mesh/NodeTree IDs between cases."""
    _, prefix_scene, source, isolated, original, prefix, consumer_group, proxy, empty = state
    for obj in (source, isolated, proxy):
        _remove_id(bpy.data.objects, obj)
    _remove_id(bpy.data.scenes, prefix_scene)
    for mesh in created:
        _remove_id(bpy.data.meshes, mesh)
    for mesh in (empty, original):
        _remove_id(bpy.data.meshes, mesh)
    for group in (consumer_group, prefix):
        _remove_id(bpy.data.node_groups, group)
    gc.collect()


def _check_output_values(scene, consumer, normalized):
    """Check all visible floats against the callback buffers outside timed phases."""
    with bpy.context.temp_override(scene=scene, view_layer=scene.view_layers[0]):
        scene.view_layers[0].update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = consumer.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        try:
            count = len(mesh.vertices)
            positions = array('f', [0.0]) * (count * 3)
            mesh.vertices.foreach_get('co', positions)
            expected = np.frombuffer(normalized.positions, dtype=np.float32).reshape(-1, 3)
            actual = np.frombuffer(positions, dtype=np.float32).reshape(-1, 3)
            if not np.allclose(actual[:, 2], expected[:, 2] + np.float32(.5), rtol=0, atol=1e-4):
                raise AssertionError('Consumer GN coordinates differ from normalized callback result')
            for name, expected_values in normalized.point_attributes.items():
                values = array('f', [0.0]) * count
                mesh.attributes[name].data.foreach_get('value', values)
                if not np.allclose(np.frombuffer(values, dtype=np.float32),
                                   np.frombuffer(expected_values, dtype=np.float32), rtol=0, atol=1e-4):
                    raise AssertionError(f'Consumer GN {name!r} values differ from callback')
        finally:
            evaluated.to_mesh_clear()


def _profiled_call(callback):
    """One opt-in diagnostic sample; never mix profiler-inflated time with timing medians."""
    profiler = cProfile.Profile()
    profiler.enable()
    try:
        return callback(), profiler
    finally:
        profiler.disable()


def _profile_rows(profiler, *, limit=18):
    """Exclusive Python/C-function cost and cumulative cost, in descending self time."""
    profile = pstats.Stats(profiler)
    rows = []
    for (file, line, name), (_primitive, count, self_sec, cumulative_sec, _callers) in profile.stats.items():
        if self_sec < 0.00001:
            continue
        label = (f'{Path(file).name}:{line}:{name}' if file and not file.startswith('~')
                 else name)
        rows.append({'call': label, 'calls': count,
                     'self_ms': self_sec * 1000, 'cumulative_ms': cumulative_sec * 1000})
    return sorted(rows, key=lambda r: r['self_ms'], reverse=True)[:limit]


def _time_normalization_outputs(callback, *, profile=False):
    """Time each declared result buffer without a Python per-element profiler.

    The test-only function intercepts exactly three scalar-buffer normalizations.
    Full cProfile is optional and happens on a separate unmeasured iteration.
    """
    module = importlib.import_module('NodeForge.mesh_compute')
    original = module._normalized_floats
    per_output = {}
    def wrapped(value, count, label, *, xyz=False):
        start = _now()
        try:
            return original(value, count, label, xyz=xyz)
        finally:
            per_output[label] = _ms(_now() - start)
    module._normalized_floats = wrapped
    try:
        if profile:
            result, profiler = _profiled_call(callback)
            diagnostics = {'per_output_profiled_ms': per_output, 'top_calls': _profile_rows(profiler)}
        else:
            result = callback()
            diagnostics = None
    finally:
        module._normalized_floats = original
    return result, per_output, diagnostics


_PHASE_KEYS = ('prefix_ms', 'input_copy_ms', 'kernel_ms', 'normalize_ms',
               'prepare_mesh_ms', 'publish_pointer_ms', 'gn_suffix_ms')


def assert_phase_accounting(metrics):
    """Guard against omitted or double-counted operations in the physical pass."""
    if abs(sum(metrics[name] for name in _PHASE_KEYS) - metrics['total_ms']) > 0.001:
        raise AssertionError('Benchmark phase accounting must exactly cover full physical pass')


def _one_run(state, steps, last_meshes, *, validate=False, profile=False):
    """Measure one complete source → buffers → NumPy → transport → GN suffix run."""
    consumer_scene, prefix_scene, consumer, isolated, original, prefix, group, proxy, empty = state
    metrics = {}
    created = []
    prefix_detail, consumer_detail, cache_detail = {}, {}, {}
    start = _now()
    evaluated, source_mesh, dg = _source_mesh(prefix_scene, isolated, detail=prefix_detail)
    try:
        t1 = _now()
        if profile:
            inputs, input_profiler = _profiled_call(lambda: mesh_inputs_from_evaluated(source_mesh, ('height',)))
            input_profile = {'top_calls': _profile_rows(input_profiler)}
        else:
            inputs = mesh_inputs_from_evaluated(source_mesh, ('height',))
        t2 = _now()
        result = _kernel(inputs, steps)
        t3 = _now()
        normalizer = lambda: normalize_mesh_result(result, vertex_count=inputs.vertex_count,
                                                     writes=('height', 'water'))
        normalized, normalized_outputs, normalize_profile = _time_normalization_outputs(
            normalizer, profile=profile)
        t4 = _now()
        payload = EvaluatedMeshCompute(inputs, normalized)
        publication = lambda: direct_mesh_update.prepare_publication(evaluated, dg, payload)
        if profile:
            published, prep_profiler = _profiled_call(publication)
            prepare_profile = {'top_calls': _profile_rows(prep_profiler)}
        else:
            published = publication()
        t5 = _now()
    finally:
        evaluated.to_mesh_clear()
    created.append(published)
    pub0 = pub1 = _now()  # Direct Mesh needs no multi-proxy gate/coordination.
    proxy.data = published
    pub2 = _now()
    proxy.update_tag(refresh={'DATA', 'OBJECT'})
    group.update_tag()
    t6 = _now()
    result_shape = _consumer_snapshot(consumer_scene, consumer, inputs.vertex_count, detail=consumer_detail)
    t7 = _now()
    # Reused proxies: consumer-only GN eval; this is NOT a scheduler numerical cache hit.
    _consumer_snapshot(consumer_scene, consumer, inputs.vertex_count, detail=cache_detail)
    t8 = _now()
    if validate:
        _check_output_values(consumer_scene, consumer, normalized)
    for mesh in last_meshes:
        if mesh.users == 0:
            _remove_id(bpy.data.meshes, mesh)
    metrics.update({
        'prefix_ms': _ms(t1 - start), 'input_copy_ms': _ms(t2 - t1),
        'kernel_ms': _ms(t3 - t2), 'normalize_ms': _ms(t4 - t3),
        'prepare_mesh_ms': _ms(t5 - t4), 'publish_pointer_ms': _ms(t6 - t5),
        'gn_suffix_ms': _ms(t7 - t6), 'unchanged_suffix_ms': _ms(t8 - t7),
        'total_ms': _ms(t7 - start), 'output': result_shape,
        'peak_rss_kb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        'meshes_allocated': len(created),
        'nested_ms': {
            **prefix_detail, **consumer_detail,
            **{f'normalize_{name}_ms': duration for name, duration in normalized_outputs.items()},
            'publication_gate_close_ms': _ms(pub1 - pub0),
            'publication_pointer_swap_ms': _ms(pub2 - pub1),
            'publication_update_tags_ms': _ms(t6 - pub2),
            **{f'cache_{name}': value for name, value in cache_detail.items()},
        },
    })
    # Main reported total excludes the separate consumer-only reused-proxy pass.
    assert_phase_accounting(metrics)
    if profile:
        metrics['diagnostic_profiles'] = {'input_copy': input_profile,
                                          'normalize': normalize_profile, 'prepare_mesh': prepare_profile}
    return metrics, created


def _bench(size, steps, repeats, warmups, *, diagnostic=False):
    """Benchmark freshly compiled candidate GN root across warmups and repeats."""
    state = _setup(size)
    latest = []
    records = []
    try:
        for index in range(warmups + repeats):
            record, prepared = _one_run(state, steps, latest, validate=index == 0)
            latest = prepared
            if index >= warmups:
                records.append(record)
        keys = ('prefix_ms', 'input_copy_ms', 'kernel_ms', 'normalize_ms', 'prepare_mesh_ms',
                'publish_pointer_ms', 'gn_suffix_ms', 'unchanged_suffix_ms', 'total_ms')
        all_keys = sorted(records[0]['nested_ms'])
        details = {key: statistics.median(r['nested_ms'][key] for r in records) for key in all_keys}
        # Diagnostic probes
        # are separate to avoid contaminating ordinary samples.
        diagnostic_data = None
        if diagnostic:
            diagnostic_record, probed = _one_run(state, steps, latest, profile=True)
            latest = probed
            diagnostic_data = {
                'profiled_phase_wall_ms': {k: diagnostic_record[k] for k in ('input_copy_ms', 'normalize_ms', 'prepare_mesh_ms')},
                'profiles': diagnostic_record['diagnostic_profiles'],
            }
        return {
            'size': size, 'steps': steps, 'repeats': repeats, 'warmups': warmups,
            'median_ms': {key: statistics.median(r[key] for r in records) for key in keys},
            'range_total_ms': [min(r['total_ms'] for r in records), max(r['total_ms'] for r in records)],
            'peak_rss_kb': max(r['peak_rss_kb'] for r in records),
            'output': records[-1]['output'], 'meshes_allocated': records[-1]['meshes_allocated'],
            'samples': records,
            'median_nested_ms': details,
            'median_percent_total': {key: 100 * statistics.median(r[key] / r['total_ms'] for r in records)
                                     for key in keys if key not in ('unchanged_suffix_ms', 'total_ms')},
            'mad_total_ms': statistics.median(abs(r['total_ms'] - statistics.median(s['total_ms'] for s in records))
                                              for r in records),
            'diagnostic': diagnostic_data,
        }
    finally:
        _teardown(state, latest)


def main():
    """Run selected production transport in a fresh Blender process and emit JSON."""
    parser = ArgumentParser()
    parser.add_argument('--sizes', type=int, nargs='+', default=[96, 512])
    parser.add_argument('--steps', type=int, nargs='+', default=[0, 100, 500])
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--output', type=Path, default=None)
    parser.add_argument('--diagnostic-profiles', action='store_true',
                        help='Collect one additional cProfile pass per case AFTER unprofiled measurements')
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    if args.repeats < 1 or args.warmups < 0 or any(n < 0 for n in args.steps) or any(n < 3 for n in args.sizes):
        parser.error('repeats >=1, warmups >=0, sizes >=3 and steps >=0 are required')
    report = {'transport': 'direct_mesh_update', 'blender_version': bpy.app.version_string,
              'numpy_version': np.__version__, 'method': 'isolated-prefix physical GN evaluation',
              'note': 'Standalone physical transport pipeline; does not time Local registry/compile or live scheduler.',
              'metric_contract': {
                  'total_ms': 'sum(prefix, input_copy, kernel, normalize, prepare_mesh, publish_pointer, gn_suffix) for ONE sample',
                  'prefix_ms': 'isolated Scene view update + evaluated object + to_mesh copy (not compiled source)',
                  'input_copy_ms': 'MeshInputs POINT float32 foreach_get plus input validation',
                  'kernel_ms': 'pure NumPy stencil callback including output assembly',
                  'normalize_ms': 'complete validation and float32 detached output array conversion',
                  'prepare_mesh_ms': 'direct native Mesh copy and writes, no live pointer mutation',
                  'publish_pointer_ms': 'candidate Mesh pointer assignments plus Blender update tags, NOT full transaction commit/manifest',
                  'gn_suffix_ms': 'consumer Scene update/evaluation/to_mesh INCLUDING transport GN nodes and common suffix',
                  'unchanged_suffix_ms': 'same proxy consumer-only reevaluation; NOT a cache hit in full runtime',
                  'diagnostic_profiles': 'separately profiled, overhead-distorted; do NOT compare wall times with medians',
              },
              'results': []}
    for size in args.sizes:
        for steps in args.steps:
            record = _bench(size, steps, args.repeats, args.warmups, diagnostic=args.diagnostic_profiles)
            report['results'].append(record)
            med = record['median_ms']
            print(f'BENCH direct_mesh_update {size}^2 / {steps} steps: total {med["total_ms"]:.2f} ms '
                  f'(kernel {med["kernel_ms"]:.2f}, mesh {med["prepare_mesh_ms"]:.2f}, '
                  f'GN {med["gn_suffix_ms"]:.2f})', flush=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2))
        print('SAVED', args.output, flush=True)
    else:
        print(json.dumps(report))


if __name__ == '__main__':
    main()
