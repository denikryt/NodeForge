"""Opt-in native GN versus selected Direct Mesh erosion benchmark in Blender 5.2.

Each process compiles the same original NumPy .nf and measures uncached source evaluation,
Python kernel, native Mesh preparation, pointer publication and full consumer
GN evaluation. Native GN uses the user original source and Math package.
No addon production dispatch or persistent settings are modified.
"""
from __future__ import annotations
from argparse import ArgumentParser
from array import array
from pathlib import Path
from time import perf_counter_ns
import json
import statistics
import sys
import tempfile
import shutil

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from NodeForge import compiler, local_sources, packages
from NodeForge.mesh_compute import normalize_mesh_result
from NodeForge.blender import mesh_compute_runtime as rt
from NodeForge.blender.mesh_compute_runtime import EvaluatedMeshCompute
from NodeForge.blender.mesh_compute_transports import direct_mesh_update
from NodeForge.tests.blender.compare_mesh_compute_original_erosion import (
    EXAMPLES, edited_source, create_owner, evaluated_output, LAYERS, parity_violations, compare_outputs,
)


def ms(start):
    """Elapsed milliseconds since a perf_counter_ns timestamp."""
    return (perf_counter_ns() - start) / 1e6



def sample_numpy(binding, *, side, check):
    """Time a fresh prefix→callback→publication→consumer evaluation."""
    stamps = {}
    start_total = perf_counter_ns()
    source_scene = binding.source_scene
    binding.source_object.update_tag(refresh={'OBJECT', 'DATA', 'TIME'})
    with bpy.context.temp_override(scene=source_scene, view_layer=source_scene.view_layers[0]):
        start = perf_counter_ns()
        source_scene.view_layers[0].update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        evaluated = binding.source_object.evaluated_get(depsgraph)
        snapshot = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
        stamps['prefix_ms'] = ms(start)
        try:
            start = perf_counter_ns()
            params = rt.runtime_parameters_from_evaluated(snapshot, binding.prepared.scalar_keys)
            inputs = rt.mesh_inputs_from_evaluated(snapshot, binding.prepared.reads)
            stamps['input_buffers_ms'] = ms(start)
            start = perf_counter_ns()
            result = binding.prepared.kernel(inputs, **params)
            stamps['kernel_ms'] = ms(start)
            start = perf_counter_ns()
            normalized = normalize_mesh_result(result, vertex_count=inputs.vertex_count,
                                               writes=binding.prepared.writes)
            stamps['normalize_ms'] = ms(start)
            candidate = EvaluatedMeshCompute(inputs, normalized)
            start = perf_counter_ns()
            new_mesh = direct_mesh_update.prepare_publication(evaluated, depsgraph, candidate,
                temporary_attribute_names=tuple(v[0] for v in binding.prepared.scalar_keys.values()))
            stamps['prepare_mesh_ms'] = ms(start)
        finally:
            evaluated.to_mesh_clear()
    start = perf_counter_ns()
    proxy = binding.prepared.result_proxy
    previous = proxy.data
    proxy.data = new_mesh
    binding.group.update_tag()
    stamps['publish_mesh_ms'] = ms(start)
    start = perf_counter_ns()
    output = evaluated_output(binding.owner)
    stamps['consumer_gn_ms'] = ms(start)
    stamps['total_ms'] = (perf_counter_ns() - start_total) / 1e6
    # benchmark retains old Mesh ID until after output is no longer in use
    if previous.users == 0:
        bpy.data.meshes.remove(previous)
    if check:
        for name in (*LAYERS, 'xyz'):
            if not np.isfinite(output[name]).all():
                raise AssertionError('Nonfinite output for ' + name)
        if output['height'].size != side * side:
            raise AssertionError('Wrong vertex count')
    return stamps, output


def native_sample(owner):
    """Force a native GN recomputation and time the final evaluated Mesh."""
    start = perf_counter_ns()
    owner.update_tag(refresh={'OBJECT', 'DATA', 'TIME'})
    output = evaluated_output(owner)
    return {'total_ms': ms(start)}, output


def run(args):
    """Create isolated trusted sources and benchmark exactly one Blender configuration."""
    with tempfile.TemporaryDirectory(prefix='nf-erosion-direct-vs-gn-') as temp:
        root = Path(temp)
        packages.set_packages_dir_for_tests(root / 'packages')
        installed = packages.install_package_zip(args.math_package, allow_python=True)
        assert installed.package_id == 'nodeforge.math'
        local = root / 'Local'; local.mkdir()
        shutil.copy2(EXAMPLES / 'erosion.py', local / 'erosion.py')
        old_local = local_sources._default_local_catalog_dir
        old_registry = local_sources._local_sources_registry_path
        local_sources._default_local_catalog_dir = lambda: local
        local_sources._local_sources_registry_path = lambda: root / 'local_sources.json'
        try:
            local_sources.set_local_python_permission(local, allowed=True)
            bpy.data.materials.new('Erosion Terrain')
            source = edited_source((EXAMPLES / 'erosion_numpy.nf').read_text(), args.side, args.steps, args.preset)
            reference_source = edited_source(args.reference_nf.read_text(), args.side, args.steps, args.preset)
            if args.mode == 'native_gn':
                owner = create_owner('Original GN erosion')
                compile_start = perf_counter_ns()
                group = compiler.create_expression_group(reference_source, 'Original GN erosion')
                modifier = owner.modifiers.new('Original GN erosion', 'NODES')
                modifier.node_group = group
                compile_ms = ms(compile_start)
                samples = []; last = None
                for _ in range(args.warmups + args.repeats):
                    record, last = native_sample(owner)
                    samples.append(record)
            else:
                owner = create_owner('NumPy erosion ' + args.mode)
                compile_start = perf_counter_ns()
                compiler.create_mesh_compute_modifier(source, owner)
                binding = rt.get_modifier_binding(owner)
                assert binding is not None
                compile_ms = ms(compile_start)
                # This benchmark controls all publications, not live scheduling.
                for handlers, hook in ((bpy.app.handlers.depsgraph_update_pre, rt._on_depsgraph_update_pre),
                                       (bpy.app.handlers.frame_change_pre, rt._on_frame_change_pre),
                                       (bpy.app.handlers.render_pre, rt._on_render_pre)):
                    if hook in handlers: handlers.remove(hook)
                samples = []; last = None
                for i in range(args.warmups + args.repeats):
                    record, last = sample_numpy(binding, side=args.side, check=i == 0)
                    samples.append(record)
            measured = samples[args.warmups:]
            response = dict(mode=args.mode, side=args.side, steps=args.steps,
                            preset=args.preset, samples=measured, warmups=args.warmups,
                            repeats=args.repeats, compile_ms=compile_ms,
                            median_ms={key: statistics.median(sample[key] for sample in measured)
                                       for key in measured[0]},
                            mean_ms={key: statistics.mean(sample[key] for sample in measured)
                                     for key in measured[0]},
                            stdev_total_ms=statistics.stdev([s['total_ms'] for s in measured])
                                if args.repeats > 1 else 0.0,
                            verified_output={k: {'sum': float(np.sum(v.astype(np.float64))),
                                                 'min': float(v.min()), 'max': float(v.max())}
                                             for k, v in last.items()})
            if args.compare_reference and args.mode != 'native_gn':
                reference = create_owner('Erosion parity reference')
                group = compiler.create_expression_group(reference_source, 'GN parity group')
                mod = reference.modifiers.new('GN parity', 'NODES'); mod.node_group = group
                ref_output = evaluated_output(reference)
                compare = compare_outputs(last, ref_output, args.side, float('inf'))
                response['parity'] = compare
                response['parity_atol'] = args.atol
                response['parity_violations'] = parity_violations(compare, args.atol)
            return response
        finally:
            local_sources._default_local_catalog_dir = old_local
            local_sources._local_sources_registry_path = old_registry
            packages.set_packages_dir_for_tests(None)


def main():
    """Parse a single reproducible scenario and write raw samples to JSON."""
    parser = ArgumentParser()
    parser.add_argument('--mode', required=True, choices=['native_gn', 'direct_mesh_update'])
    parser.add_argument('--math-package', required=True, type=Path)
    parser.add_argument('--reference-nf', required=True, type=Path)
    parser.add_argument('--side', type=int, default=32)
    parser.add_argument('--steps', type=int, default=100)
    parser.add_argument('--preset', choices=['default', 'closed', 'outlet'], default='default')
    parser.add_argument('--warmups', type=int, default=1)
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--atol', type=float, default=1e-5)
    parser.add_argument('--compare-reference', action='store_true')
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if args.side < 3 or args.steps < 0 or args.repeats <= 0:
        parser.error('Side >=3, steps >=0 and repeats >0 required')
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    print('EROSION_NATIVE_VS_DIRECT', args.mode, args.side, args.steps,
          'median_ms', result['median_ms']['total_ms'],
          'parity', result.get('parity_violations'), flush=True)
    if result.get('parity_violations'):
        raise SystemExit(2)

if __name__ == '__main__': main()
