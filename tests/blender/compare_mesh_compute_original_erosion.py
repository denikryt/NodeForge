"""Opt-in parity check: user's original GN erosion versus Local NumPy port.

Each invocation runs one configuration in a fresh Blender 5.2 process:
  blender --background --factory-startup --python \
    tests/blender/compare_mesh_compute_original_erosion.py -- \
    --math-package /path/to/nodeforge.math.zip \
    --reference-nf /path/to/hydraulic_grid_shape_cpu_fast.nf \
    --side 16 --steps 35 --preset outlet --output /tmp/parity.json

Inputs are external test artifacts and are *not* bundled in the addon.
Times are one-shot compilation and first evaluation, NOT median performance.
"""

from argparse import ArgumentParser
from array import array
from pathlib import Path
from time import perf_counter
import json
import re
import shutil
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from NodeForge import compiler, local_sources, packages
from NodeForge.blender import mesh_compute_runtime as runtime

EXAMPLES = Path(__file__).resolve().parents[2] / "examples" / "mesh_compute_erosion"
LAYERS = ("height", "water", "sediment", "initial_height", "height_change")
# Named arguments in both .nf sources; values intentionally include aggressive
# erosion so that passing zero-iteration/no-change fixtures cannot hide errors.
PRESETS = {
    "default": {},
    "closed": {
        "Open Edges": ("bool", "False"),
        "Thermal Rate": ("float", "6.0"),
        "Talus Slope": ("float", "0.04"),
        "Rain": ("float", "0.3"),
        "Erosion Rate": ("float", "1.5"),
        "Terrain Seed": ("int", "7"),
    },
    "outlet": {
        "Open Edges": ("bool", "True"),
        "Legacy Outlet": ("bool", "True"),
        "Thermal Rate": ("float", "8.0"),
        "Talus Slope": ("float", "0.02"),
        "Rain": ("float", "0.2"),
        "Erosion Rate": ("float", "1.8"),
        "Terrain Seed": ("int", "31"),
    },
}


def edited_source(source: str, side: int, steps: int, preset: str) -> str:
    """Change exact declared defaults, rejecting ambiguous source fixtures."""
    edits = {
        "Resolution": ("int", str(side)),
        "Steps": ("int", str(steps)),
        **PRESETS[preset],
    }
    for name, (kind, value) in edits.items():
        pattern = rf'(input_{kind}\("{re.escape(name)}", default=)[^)]+'
        source, count = re.subn(pattern, lambda match: match.group(1) + value, source)
        if count != 1:
            raise AssertionError(f"Expected one {name!r} declaration, found {count}")
    return source


def evaluated_output(owner):
    """Read the consumer's evaluated mesh, never a transient runtime proxy."""
    bpy.context.view_layer.update()
    graph = bpy.context.evaluated_depsgraph_get()
    evaluated = owner.evaluated_get(graph)
    mesh = evaluated.to_mesh(preserve_all_data_layers=True, depsgraph=graph)
    try:
        count = len(mesh.vertices)
        output = {}
        for name in LAYERS:
            attr = mesh.attributes.get(name)
            if attr is None or attr.domain != 'POINT' or attr.data_type != 'FLOAT':
                raise AssertionError(f"Missing POINT FLOAT {name!r}")
            buffer = array('f', [0.0]) * count
            attr.data.foreach_get('value', buffer)
            output[name] = np.frombuffer(buffer, dtype=np.float32).copy()
        pos = array('f', [0.0]) * (count * 3)
        mesh.attributes['position'].data.foreach_get('vector', pos)
        output['xyz'] = np.frombuffer(pos, dtype=np.float32).reshape((-1, 3)).copy()
        return output
    finally:
        evaluated.to_mesh_clear()


def create_owner(name):
    # Original and port generate their grid; owner source topology is incidental.
    mesh = bpy.data.meshes.new(name + "Input")
    mesh.from_pydata([(0, 0, 0), (1, 0, 0), (0, 1, 0)], [], [(0, 1, 2)])
    owner = bpy.data.objects.new(name, mesh)
    bpy.context.scene.collection.objects.link(owner)
    return owner


def evaluate_one(name, source, is_numpy):
    owner = create_owner(name)
    begin = perf_counter()
    if is_numpy:
        compiler.create_mesh_compute_modifier(source, owner)
    else:
        group = compiler.create_expression_group(source, "Original Hydraulic GN")
        modifier = owner.modifiers.new("Hydraulic Reference", 'NODES')
        modifier.node_group = group
    compiled = perf_counter()
    if is_numpy:
        runtime.evaluate_modifier_binding(owner)
    evaluated = perf_counter()
    output = evaluated_output(owner)
    done = perf_counter()
    return output, {
        "compile_ms": round((compiled - begin) * 1000, 3),
        "callback_and_publication_ms": round((evaluated - compiled) * 1000, 3),
        "consumer_mesh_ms": round((done - evaluated) * 1000, 3),
        "vertex_count": output['height'].size,
    }


def compare_outputs(port, reference, side: int, atol: float):
    """Check every requested channel and reject wrong shapes/inputs."""
    result = {}
    for name in (*LAYERS, 'xyz'):
        left, right = port[name], reference[name]
        if left.shape != right.shape or left.size != (3 if name == 'xyz' else 1) * side * side:
            raise AssertionError(f"{name}: shape mismatch {left.shape} vs {right.shape}")
        if not np.isfinite(left).all() or not np.isfinite(right).all():
            raise AssertionError(f"{name}: non-finite output")
        difference = np.abs(left.astype(np.float64) - right.astype(np.float64))
        maximum = float(difference.max(initial=0.0))
        result[name] = {"max_abs": maximum, "rmse": float(np.sqrt(np.mean(difference ** 2)))}
        if maximum > atol:
            raise AssertionError(f"{name}: max absolute difference {maximum} > {atol}")
    return result


def parity_violations(comparisons, atol):
    """Only declared absolute-tolerance differences are acceptable."""
    return {name: stat['max_abs'] for name, stat in comparisons.items()
            if stat['max_abs'] > atol}


def run(args):
    if args.side < 3 or args.steps < 0:
        raise ValueError('Require side >=3, steps >=0')
    with tempfile.TemporaryDirectory(prefix='nodeforge-gn-erosion-parity-') as tmp:
        root = Path(tmp)
        packages.set_packages_dir_for_tests(root / 'packages')
        installed = packages.install_package_zip(args.math_package)
        assert installed.package_id == 'nodeforge.math'
        local = root / 'Local'
        local.mkdir()
        shutil.copy2(EXAMPLES / 'erosion.py', local / 'erosion.py')
        old_dir = local_sources._default_local_catalog_dir
        old_registry = local_sources._local_sources_registry_path
        local_sources._default_local_catalog_dir = lambda: local
        local_sources._local_sources_registry_path = lambda: root / 'local_sources.json'
        try:

            bpy.data.materials.new('Erosion Terrain')
            source_gn = edited_source(args.reference_nf.read_text(), args.side, args.steps, args.preset)
            source_numpy = edited_source((EXAMPLES / 'erosion_numpy.nf').read_text(), args.side, args.steps, args.preset)
            num, num_t = evaluate_one('NumPy', source_numpy, True)
            ref, ref_t = evaluate_one('NativeGN', source_gn, False)
            comparisons = compare_outputs(num, ref, args.side, float('inf'))
            failures = parity_violations(comparisons, args.atol)
            return {
                'blender': bpy.app.version_string,
                'package': f'{installed.package_id} {installed.version}',
                'side': args.side,
                'steps': args.steps,
                'preset': args.preset,
                'max_abs_tolerance': args.atol,
                'numpy': num_t,
                'native_gn': ref_t,
                'comparisons': comparisons,
                'parity_passed': not failures,
                'parity_violations': failures,
                'reference_height_change_range': [float(ref['height_change'].min()), float(ref['height_change'].max())],
            }
        finally:
            local_sources._default_local_catalog_dir = old_dir
            local_sources._local_sources_registry_path = old_registry
            packages.set_packages_dir_for_tests(None)


def main():
    parser = ArgumentParser()
    parser.add_argument('--math-package', type=Path, required=True)
    parser.add_argument('--reference-nf', type=Path, required=True)
    parser.add_argument('--side', type=int, default=16)
    parser.add_argument('--steps', type=int, default=20)
    parser.add_argument('--preset', choices=tuple(PRESETS), default='default')
    parser.add_argument('--atol', type=float, default=1e-5)
    parser.add_argument('--output', type=Path, required=True)
    argv = sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []
    args = parser.parse_args(argv)
    result = run(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print('PARITY_PASS' if result['parity_passed'] else 'PARITY_FAIL',args.preset,args.side,args.steps,
          'height maxabs',result['comparisons']['height']['max_abs'],
          'num_ms',result['numpy']['callback_and_publication_ms']+result['numpy']['consumer_mesh_ms'],
          'gn_ms',result['native_gn']['consumer_mesh_ms'],flush=True)
    if not result['parity_passed']:
        raise SystemExit(2)


if __name__ == '__main__':
    main()
