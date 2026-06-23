import os
import json
import platform
import subprocess
import time

import pytest

from helpers import *

pytestmark = [
    pytest.mark.benchmark,
    pytest.mark.slow,
    pytest.mark.skipif(
        os.environ.get("NODEFORGE_LSYSTEM_BENCHMARK") != "1",
        reason="set NODEFORGE_LSYSTEM_BENCHMARK=1 to run L-system benchmarks",
    ),
]

def _git_commit_for_benchmark():
    """Return the current repository commit when the benchmark runs from a checkout."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=str(ROOT),
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except Exception:
        return "unknown"

def _benchmark_environment_row():
    """Describe the runtime used for optional L-system benchmark rows."""
    return {
        "blender_version": getattr(bpy.app, "version_string", "unknown"),
        "python_version": platform.python_version(),
        "nodeforge_version": ".".join(str(part) for part in NodeForge.bl_info.get("version", ())),
        "commit": _git_commit_for_benchmark(),
    }

def _benchmark_fixture_source(fixture):
    """Return the DSL source for one benchmark fixture."""
    return _lsystem_source(
        fixture["axiom"],
        rules=fixture.get("rules", ()),
        iterations=fixture.get("iterations", 0),
        runtime=fixture.get("runtime", False),
    )

def _benchmark_static_metrics(fixture):
    """Compute compile-time structural metrics for one benchmark fixture."""
    stream = expand_lsystem(fixture["axiom"], dict(fixture.get("rules", ())), fixture.get("iterations", 0))
    runtime_value = Value(None, "FLOAT")
    angle = runtime_value if fixture.get("runtime", False) else 60.0
    step = runtime_value if fixture.get("runtime", False) else 1.0
    metrics = analyze_lsystem(stream, angle=angle, step=step)
    return stream, metrics, lsystem_backends.select_backend_category(metrics)

def _benchmark_generated_topology(refs):
    """Summarize generated Curve or Mesh topology for benchmark output."""
    topology = {}
    for ref in refs:
        if ref.kind == "CURVE":
            curve = bpy.data.curves.get(ref.name)
            if curve is not None:
                topology["curve_splines"] = len(curve.splines)
                topology["curve_points"] = sum(len(spline.points) for spline in curve.splines)
        elif ref.kind == "MESH":
            mesh = bpy.data.meshes.get(ref.name)
            if mesh is not None:
                topology["mesh_vertices"] = len(mesh.vertices)
                topology["mesh_edges"] = len(mesh.edges)
    return topology

def _benchmark_runtime_evaluation(group, fixture_name):
    """Measure one evaluated depsgraph snapshot and verify runtime input stability."""
    before = generated_resources.read_group_manifest(group)
    wrapper, obj, mesh_data, mod = _attach_runtime_eval_modifier(group, "NFTest_lsystem_benchmark_eval_" + fixture_name)
    try:
        _set_modifier_input(mod, wrapper, "Angle", 45.0)
        _set_modifier_input(mod, wrapper, "Step", 0.5)
        obj.update_tag()
        start = time.perf_counter()
        bpy.context.view_layer.update()
        vertices, edges, polygons = _evaluated_mesh_snapshot(obj)
        elapsed = time.perf_counter() - start
        _set_modifier_input(mod, wrapper, "Angle", 30.0)
        _set_modifier_input(mod, wrapper, "Step", 1.25)
        obj.update_tag()
        bpy.context.view_layer.update()
        after = generated_resources.read_group_manifest(group)
        return {
            "depsgraph_snapshot_seconds": elapsed,
            "evaluated_vertices": len(vertices),
            "evaluated_edges": len(edges),
            "evaluated_polygons": polygons,
            "runtime_input_manifest_stable": before == after,
        }
    finally:
        _cleanup_runtime_eval_objects(wrapper, obj, mesh_data)

def _benchmark_cleanup(group, refs):
    """Remove a benchmark group and report generated-resource cleanup state."""
    names = [(ref.kind, ref.name) for ref in refs]
    try:
        bpy.data.node_groups.remove(group)
    except Exception:
        pass
    generated_resources.cleanup_restart_orphans()
    remaining = []
    for kind, name in names:
        if kind == "CURVE" and bpy.data.curves.get(name) is not None:
            remaining.append({"kind": kind, "name": name})
        elif kind == "MESH" and bpy.data.meshes.get(name) is not None:
            remaining.append({"kind": kind, "name": name})
        elif kind == "OBJECT" and bpy.data.objects.get(name) is not None:
            remaining.append({"kind": kind, "name": name})
    return {"removed_all_generated_ids": not remaining, "remaining": remaining}

def _run_lsystem_benchmark_fixture(fixture):
    """Compile one benchmark fixture and print a machine-readable metrics row."""
    row = {
        "fixture": fixture["name"],
        "fixture_category": fixture["category"],
        "expected_error": bool(fixture.get("expect_error", False)),
    }
    before_keys = _owned_generated_id_keys()
    try:
        stream, metrics, backend_category = _benchmark_static_metrics(fixture)
        row.update({
            "expanded_symbols": len(stream),
            "segment_count": metrics.segment_count,
            "max_branch_depth": metrics.max_branch_depth,
            "backend_category": backend_category,
        })
    except CompileError as exc:
        row.update({
            "expanded_symbols": None,
            "segment_count": None,
            "max_branch_depth": None,
            "backend_category": None,
            "analysis_error": type(exc).__name__,
            "analysis_message": str(exc),
        })
    source = _benchmark_fixture_source(fixture)
    group = None
    refs = []
    try:
        start = time.perf_counter()
        group = compile_group(source, "NFTest_lsystem_benchmark_" + fixture["name"])
        row["compile_seconds"] = time.perf_counter() - start
        manifest, refs = _manifest_refs(group)
        row["generated_ids"] = [{"kind": ref.kind, "role": ref.role} for ref in refs]
        row["topology"] = _benchmark_generated_topology(refs)
        row["node_count"] = len(group.nodes)
        start = time.perf_counter()
        compiler.update_expression_group(group, source)
        row["update_seconds"] = time.perf_counter() - start
        manifest, refs = _manifest_refs(group)
        row["post_update_generation_uuid"] = manifest["generation_uuid"]
        row["runtime_eval"] = None
        if row.get("backend_category") in {"branch_free_runtime", "branched_runtime"}:
            row["runtime_eval"] = _benchmark_runtime_evaluation(group, fixture["name"])
        row["cleanup"] = _benchmark_cleanup(group, refs)
        group = None
    except CompileError as exc:
        row["compile_error"] = type(exc).__name__
        row["compile_message"] = str(exc)
        row["cleanup"] = {"removed_all_generated_ids": _owned_generated_id_keys() == before_keys, "remaining": []}
        if not fixture.get("expect_error", False):
            raise
    finally:
        if group is not None:
            row["cleanup"] = _benchmark_cleanup(group, refs)
    if fixture.get("expect_error", False):
        check("compile_error" in row or "analysis_error" in row, f"benchmark limit fixture unexpectedly compiled: {fixture['name']}")
    else:
        check("compile_seconds" in row, f"benchmark fixture did not compile: {fixture['name']}")
        check(row.get("generated_ids"), f"benchmark fixture did not create generated resources: {fixture['name']}")
        check(row.get("cleanup", {}).get("removed_all_generated_ids"), f"benchmark cleanup left generated IDs: {fixture['name']}")
    print("LSYSTEM_BENCHMARK_ROW " + json.dumps(row, sort_keys=True))

def test_lsystem_benchmark_matrix_complete():
    required = {
        "static_straight",
        "static_branched",
        "branch_free_runtime_line",
        "branch_free_runtime_turns",
        "branched_runtime_shallow_wide",
        "branched_runtime_deep_narrow",
        "limit_failure",
    }
    assert required.issubset({fixture["category"] for fixture in LSYSTEM_BENCHMARK_FIXTURES})


@pytest.mark.parametrize("fixture", LSYSTEM_BENCHMARK_FIXTURES, ids=lambda item: item["name"])
def test_lsystem_benchmark_fixture(fixture):
    print("LSYSTEM_BENCHMARK_ENV " + json.dumps(_benchmark_environment_row(), sort_keys=True))
    _run_lsystem_benchmark_fixture(fixture)
