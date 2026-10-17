"""Benchmark native GN Repeat Zone against the NumPy four-neighbor stencil.

The algorithm is identical to benchmark_mesh_compute_transport._kernel:
periodic 2D four-neighbor Laplacian, in-place Euler updates of height and
water, followed by point Z replacement and a uniform +0.5 GN offset.

This is a physical GN evaluation benchmark, not a general erosion simulator.
"""
from __future__ import annotations

from argparse import ArgumentParser
from array import array
from pathlib import Path
import json
import resource
import statistics
import sys
import time

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from NodeForge.tests.blender.benchmark_mesh_compute_transport import _stock_mesh, _prefix_group, _kernel, _ms
from NodeForge.mesh_compute import MeshInputs, normalize_mesh_result


def build_native_stencil_group(side: int, steps: int):
    """Construct a repeated periodic four-neighbor stencil using only GN nodes."""
    group = bpy.data.node_groups.new(f'NF native stencil {side} {steps}', 'GeometryNodeTree')
    group.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    nodes, links = group.nodes, group.links
    src = nodes.new('NodeGroupInput')
    prefix = nodes.new('GeometryNodeGroup')
    prefix.node_tree = _prefix_group()
    links.new(src.outputs['Geometry'], prefix.inputs['Geometry'])

    def stored(geometry, name, value):
        """Write one POINT FLOAT layer into the current GN geometry."""
        node = nodes.new('GeometryNodeStoreNamedAttribute')
        node.domain, node.data_type = 'POINT', 'FLOAT'
        node.inputs['Name'].default_value = name
        links.new(geometry, node.inputs['Geometry'])
        if hasattr(value, 'is_output'):
            links.new(value, node.inputs['Value'])
        else:
            node.inputs['Value'].default_value = value
        return node.outputs['Geometry']

    def named(name):
        """Read a POINT FLOAT named attribute as a GN field."""
        n = nodes.new('GeometryNodeInputNamedAttribute')
        n.data_type = 'FLOAT'
        n.inputs['Name'].default_value = name
        return n.outputs['Attribute']

    def integer(op, left, right):
        """Build an index arithmetic expression with integer-only math."""
        node = nodes.new('FunctionNodeIntegerMath')
        node.operation = op
        for i, arg in enumerate((left, right)):
            if hasattr(arg, 'is_output'):
                links.new(arg, node.inputs[i])
            else:
                node.inputs[i].default_value = arg
        return node.outputs[0]

    def numeric(op, left, right=None):
        """Build a typed float arithmetic expression in the Repeat Zone."""
        node = nodes.new('ShaderNodeMath')
        node.operation = op
        for i, arg in enumerate((left, right)):
            if arg is None:
                continue
            if hasattr(arg, 'is_output'):
                links.new(arg, node.inputs[i])
            else:
                node.inputs[i].default_value = arg
        return node.outputs[0]

    init_water = stored(prefix.outputs['Geometry'], 'water', 0.0)
    repeat_input = nodes.new('GeometryNodeRepeatInput')
    repeat_output = nodes.new('GeometryNodeRepeatOutput')
    repeat_input.pair_with_output(repeat_output)
    repeat_input.inputs['Iterations'].default_value = steps
    links.new(init_water, repeat_input.inputs['Geometry'])
    iterate_geom = repeat_input.outputs['Geometry']

    idx = nodes.new('GeometryNodeInputIndex').outputs['Index']
    x = integer('MODULO', idx, side)
    y = integer('DIVIDE_FLOOR', idx, side)

    def wrapped(axis, increment):
        """Keep the chosen x/y component in 0..side-1 with periodic wrap."""
        return integer('MODULO', integer('ADD', axis, side + increment), side)

    def at(xval, yval):
        """Return a linear vertex index from periodic grid coordinates."""
        return integer('ADD', integer('MULTIPLY', yval, side), xval)

    height = named('height')
    current = height

    def sample(index):
        """Evaluate previous-iteration height at the specified neighbor index."""
        n = nodes.new('GeometryNodeSampleIndex')
        n.data_type, n.domain, n.clamp = 'FLOAT', 'POINT', False
        links.new(iterate_geom, n.inputs['Geometry'])
        links.new(height, n.inputs['Value'])
        links.new(index, n.inputs['Index'])
        return n.outputs['Value']

    north = sample(at(x, wrapped(y, -1)))
    south = sample(at(x, wrapped(y, 1)))
    east = sample(at(wrapped(x, 1), y))
    west = sample(at(wrapped(x, -1), y))
    neighbors = numeric('ADD', numeric('ADD', north, south), numeric('ADD', east, west))
    slope = numeric('SUBTRACT', neighbors, numeric('MULTIPLY', 4.0, current))
    updated_height = numeric('ADD', current, numeric('MULTIPLY', 0.004, slope))
    updated_water = numeric('ADD', named('water'), numeric('MULTIPLY', 0.00001, numeric('ABSOLUTE', slope)))
    height_geom = stored(iterate_geom, 'height', updated_height)
    water_geom = stored(height_geom, 'water', updated_water)
    links.new(water_geom, repeat_output.inputs['Geometry'])

    # The stencil updates attributes. Copy height into Z, matching NumPy kernel.
    pos = nodes.new('GeometryNodeInputPosition').outputs['Position']
    separate = nodes.new('ShaderNodeSeparateXYZ')
    links.new(pos, separate.inputs[0])
    combine = nodes.new('ShaderNodeCombineXYZ')
    links.new(separate.outputs['X'], combine.inputs['X'])
    links.new(separate.outputs['Y'], combine.inputs['Y'])
    links.new(named('height'), combine.inputs['Z'])
    set_z = nodes.new('GeometryNodeSetPosition')
    links.new(repeat_output.outputs['Geometry'], set_z.inputs['Geometry'])
    links.new(combine.outputs[0], set_z.inputs['Position'])
    suffix = nodes.new('GeometryNodeSetPosition')
    suffix.inputs['Offset'].default_value = (0, 0, .5)
    links.new(set_z.outputs['Geometry'], suffix.inputs['Geometry'])
    out = nodes.new('NodeGroupOutput')
    links.new(suffix.outputs['Geometry'], out.inputs['Geometry'])
    return group


def validate_native_output(obj, expected, *, tolerance=2e-4):
    """Compare GN positions and both stored attributes to NumPy stencil."""
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
    try:
        n = len(mesh.vertices)
        if n != len(expected.point_attributes['height']):
            raise AssertionError(f'Native GN vertex count {n} differs from NumPy {len(expected.point_attributes["height"])}')
        actual_positions = array('f', [0]) * (3*n)
        mesh.vertices.foreach_get('co', actual_positions)
        xyz = np.frombuffer(actual_positions, dtype=np.float32).reshape(-1, 3)
        exp = np.frombuffer(expected.positions, dtype=np.float32).reshape(-1, 3).copy()
        exp[:, 2] += np.float32(.5)
        max_pos_err = float(np.max(np.abs(xyz - exp)))
        if not np.allclose(xyz, exp, rtol=0, atol=tolerance):
            raise AssertionError(f'Native GN position error max={max_pos_err}')
        errs = {'positions': max_pos_err}
        for name in ('height','water'):
            attr = mesh.attributes.get(name)
            if attr is None or attr.data_type != 'FLOAT' or attr.domain != 'POINT':
                raise AssertionError(f'Native GN output missing POINT FLOAT {name}')
            actual = array('f',[0])*n
            attr.data.foreach_get('value', actual)
            expected_values = np.frombuffer(expected.point_attributes[name], dtype=np.float32)
            err = float(np.max(np.abs(np.frombuffer(actual, dtype=np.float32)-expected_values)))
            errs[name] = err
            if not np.allclose(np.frombuffer(actual, dtype=np.float32), expected_values, rtol=0, atol=tolerance):
                raise AssertionError(f'Native GN {name} error max={err}')
        return errs
    finally:
        evaluated.to_mesh_clear()


def measure_native(side, steps, *, repeats, warmups):
    """Measure full consumer reevaluation after marking the source dirty."""
    obj, mesh = _stock_mesh(side)
    group = build_native_stencil_group(side, steps)
    modifier = obj.modifiers.new('Native stencil', 'NODES')
    modifier.node_group = group
    try:
        coords = array('f', [0]) * (len(mesh.vertices)*3)
        mesh.vertices.foreach_get('co', coords)
        heights = array('f', (coords[i] for i in range(2, len(coords), 3)))
        expected = normalize_mesh_result(_kernel(MeshInputs(coords, {'height': heights}, side*side), steps),
                                         vertex_count=side*side, writes=('height','water'))
        errors = validate_native_output(obj, expected)
        samples = []
        for i in range(warmups+repeats):
            # Force a new GN evaluation, not an unchanged cached-output read.
            mesh.update()
            obj.update_tag(refresh={'DATA', 'OBJECT'})
            group.update_tag()
            start = time.perf_counter_ns()
            bpy.context.view_layer.update()
            after_update = time.perf_counter_ns()
            depsgraph = bpy.context.evaluated_depsgraph_get()
            evaluated = obj.evaluated_get(depsgraph)
            result = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=depsgraph)
            after_mesh = time.perf_counter_ns()
            count = len(result.vertices)
            evaluated.to_mesh_clear()
            if count != side*side:
                raise AssertionError('Native GN result has incorrect vertex count')
            print(f'NATIVE SAMPLE {side}^2/{steps} iteration={i} measured={i >= warmups} '
                  f'eval_ms={_ms(after_mesh - start):.2f} rss_kb={resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}',
                  flush=True)
            if i >= warmups:
                samples.append({'update_ms': _ms(after_update - start),
                                'to_mesh_ms': _ms(after_mesh - after_update),
                                'total_ms': _ms(after_mesh - start)})
        return {'size': side, 'steps': steps, 'repeats': repeats, 'warmups': warmups,
                'median_ms': {key: statistics.median(s[key] for s in samples)
                              for key in samples[0]},
                'samples': samples, 'max_abs_errors': errors,
                'peak_rss_kb': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
    finally:
        # One process per benchmark run is recommended; cleanup source and graph.
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.node_groups.remove(group, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)


def main():
    """Run the native GN counterpart using the same sizes/steps protocol."""
    parser = ArgumentParser()
    parser.add_argument('--sizes', type=int, nargs='+', default=[96, 512])
    parser.add_argument('--steps', type=int, nargs='+', default=[0, 100, 500])
    parser.add_argument('--repeats', type=int, default=5)
    parser.add_argument('--warmups', type=int, default=2)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    report = {'method': 'native Geometry Nodes Repeat Zone periodic 4-neighbor stencil',
              'blender_version': bpy.app.version_string, 'numpy_reference_only': np.__version__,
              'note': 'Source tagged before every run; wall time includes GN reevaluation + evaluated mesh materialization, excludes graph construction and reference checks.',
              'results': []}
    for size in args.sizes:
        for steps in args.steps:
            row = measure_native(size, steps, repeats=args.repeats, warmups=args.warmups)
            report['results'].append(row)
            print(f'NATIVE {size}^2 {steps} steps: {row["median_ms"]["total_ms"]:.3f} ms errors={row["max_abs_errors"]}', flush=True)
    args.output.write_text(json.dumps(report, indent=2))


if __name__ == '__main__':
    main()
