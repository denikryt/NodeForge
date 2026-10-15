"""Blender 5.2 opt-in benchmark of Mesh Compute fingerprint and unchanged runtime evaluation."""
import json
import os
import statistics
import sys
import tempfile
import time
from pathlib import Path

import bpy
sys.path.insert(0, os.environ.get("NODEFORGE_PARENT", str(Path(__file__).resolve().parents[3])))
from NodeForge.blender import mesh_compute_runtime as rt
from NodeForge import local_sources
from NodeForge.compiler import create_mesh_compute_modifier

SIZES = (96, 256, 512)
REPEATS = 7
WARMUPS = 2


def timings(fn):
    """Return repeated wall-clock timings after warming Blender's caches."""
    for _ in range(WARMUPS):
        fn()
    results = []
    for _ in range(REPEATS):
        t0 = time.perf_counter_ns()
        fn()
        results.append((time.perf_counter_ns() - t0) / 1_000_000)
    return {"median_ms": round(statistics.median(results), 4),
            "mean_ms": round(statistics.mean(results), 4),
            "stdev_ms": round(statistics.stdev(results), 4),
            "trials_ms": [round(v, 4) for v in results]}


def grid_with_layers(side):
    """Make a representative exact-size mesh with UV and mixed face material indices."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=side - 1, y_subdivisions=side - 1, size=3)
    owner = bpy.context.object
    assert len(owner.data.vertices) == side**2
    material = bpy.data.materials.new(f"TimingMat{side}")
    owner.data.materials.append(material)
    if not owner.data.uv_layers:
        owner.data.uv_layers.new(name="UVMap")
    owner.data.polygons[0].material_index = 1
    assert owner.data.attributes.get("material_index") is not None
    return owner


results = {"blender": bpy.app.version_string, "repeats": REPEATS, "warmups": WARMUPS,
           "sizes": {}}

with tempfile.TemporaryDirectory(prefix="mesh_compute_bench23_") as directory:
    root = Path(directory)
    local = root / "Local"
    local.mkdir()
    (local / "passthrough.py").write_text('''import numpy as np\nfrom NodeForge import mesh_compute\n@mesh_compute(writes=("height",))\ndef passthrough(mesh):\n    coords=np.frombuffer(mesh.positions,dtype=np.float32).reshape((-1,3)).copy()\n    return {"positions":coords,"point_attributes":{"height":coords[:,2].copy()}}\n''')
    local_sources._default_local_catalog_dir = lambda: local
    local_sources._local_sources_registry_path = lambda: root / "registry.json"
    local_sources.set_local_python_permission(local, allowed=True)
    source = 'from local import passthrough\ngeo = input_geometry("Geometry")\ngeo = passthrough(geo)\noutput("Geometry", geo)\n'
    owners = []
    for side in SIZES:
        owner = grid_with_layers(side)
        owners.append(owner)
        mesh = owner.data
        old_key = rt._publication_fingerprint(mesh)
        original = timings(lambda: rt._publication_fingerprint(mesh))
        assert rt._publication_fingerprint(mesh) == old_key
        # Full scheduler-equivalent path: build a real first GN modifier, then
        # evaluate its unchanged input without re-running its Python callback.
        create_mesh_compute_modifier(source, owner)
        rt.unregister_runtime_handlers()
        rt.evaluate_modifier_binding(owner)
        binding = rt.get_modifier_binding(owner)
        published = binding.prepared.result_proxy.data
        callback = binding.prepared.kernel
        from dataclasses import replace
        called = [0]
        def counted(*args, **kwargs):
            called[0] += 1
            return callback(*args, **kwargs)
        binding.prepared = replace(binding.prepared, kernel=counted)
        binding.numerical_key = None
        rt.evaluate_modifier_binding(owner, skip_unchanged=True)
        assert called == [1]
        # Blender can publish once more while its GN depsgraph stabilizes.
        for _ in range(5):
            published = rt.evaluate_modifier_binding(owner, skip_unchanged=True)
        def noop():
            assert rt.evaluate_modifier_binding(owner, skip_unchanged=True) is published
        runtime = timings(noop)
        assert called == [1], called
        assert binding.prepared.result_proxy.data is published
        results["sizes"][str(side)] = {
            "vertices": side*side,
            "fingerprint": original,
            "full_cache_hit": runtime,
            "attributes": len(mesh.attributes),
            "uv_layers": len(mesh.uv_layers),
            "native_material_index": True,
        }
        print("BENCH23", side, json.dumps(results["sizes"][str(side)]), flush=True)

output = os.environ.get('NODEFORGE_BENCHMARK_RESULT')
if output:
    Path(output).write_text(json.dumps(results, indent=2))
print("BENCH23_RESULT=" + json.dumps(results), flush=True)
