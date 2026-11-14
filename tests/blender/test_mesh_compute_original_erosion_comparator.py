"""Numeric comparison regression tests, independent of the optional math package."""

import ast

import numpy as np
import pytest

from NodeForge.tests.blender.compare_mesh_compute_original_erosion import (
    compare_outputs, edited_source, parity_violations, typed_reference_source,
)

pytestmark = pytest.mark.blender

def _output(n):
    return {name: np.zeros(n*n, dtype=np.float32) for name in ('height','water','sediment','initial_height','height_change')} | {'xyz': np.zeros((n*n,3), dtype=np.float32)}


def test_identical_outputs_pass_all_channels():
    compare = compare_outputs
    assert all(row['max_abs'] == 0.0 for row in compare(_output(3),_output(3),3,1e-5).values())


def test_nonmatching_reference_fails_instead_of_masking_difference():
    compare = compare_outputs
    good=_output(3);bad=_output(3);bad['water'][4]=0.1
    with pytest.raises(AssertionError,match='water'):
        compare(good,bad,3,1e-5)


def test_mismatched_vertex_count_and_nan_fail():
    compare = compare_outputs
    good=_output(3)
    with pytest.raises(AssertionError,match='shape mismatch'):
        compare(good,_output(4),3,1e-5)
    bad=_output(3);bad['sediment'][2]=float('nan')
    with pytest.raises(AssertionError,match='non-finite'):
        compare(good,bad,3,1e-5)


def test_numeric_threshold_classifies_success_and_failure():
    metrics={'height':{'max_abs':1e-6},'water':{'max_abs':3e-4}}
    assert parity_violations(metrics,1e-5)=={'water':3e-4}
    assert parity_violations(metrics,1e-3)=={}


def test_parameter_editing_supports_typed_example_and_legacy_reference():
    from NodeForge.tests.blender.compare_mesh_compute_original_erosion import EXAMPLES

    source = (EXAMPLES / "erosion_numpy.nf").read_text()
    edited = edited_source(source, 12, 34, "closed")
    fn = next(node for node in ast.parse(edited).body if isinstance(node, ast.FunctionDef))
    defaults = {arg.arg: ast.literal_eval(default)
                for arg, default in zip(fn.args.args[1:], fn.args.defaults)}
    assert defaults["requested_n"] == 12
    assert defaults["requested_steps"] == 34
    assert defaults["open_edges"] is False
    assert defaults["parameter_7"] == 6.0
    assert defaults["seed"] == 7
    assert 'return geo' in edited

    original = ("n = input_int('Resolution', default=(48 + 48))\n"
                "steps = input_int('Steps', default=500)\n"
                "geo = cube(1)\noutput('Geometry', geo)\n")
    updated = edited_source(original, 8, 2, "default")
    parsed = ast.parse(updated).body
    assert ast.literal_eval(parsed[0].value.keywords[0].value) == 8
    assert ast.literal_eval(parsed[1].value.keywords[0].value) == 2
    assert "geo = cube(1)" in updated
    migrated = typed_reference_source(updated)
    assert "input_int" not in migrated and "output(" not in migrated
    assert "def original_reference(" in migrated
    assert "return geo" in migrated


def test_legacy_reference_migration_builds_a_node_group():
    from NodeForge import compiler

    source = ("n = input_int('Resolution', default=96)\n"
              "steps = input_int('Steps', default=500)\n"
              "geo = cube(1)\noutput('Geometry', geo)\n")
    group = compiler.create_expression_group(
        typed_reference_source(edited_source(source, 8, 2, "default")),
        "Converted original reference",
    )
    assert group.bl_idname == "GeometryNodeTree"
    assert any(node.bl_idname == "GeometryNodeMeshCube" for node in group.nodes)


def test_reference_migration_rejects_ambiguous_outputs():
    original = ("n = input_int('Resolution', default=96)\n"
                "output('Geometry', cube(1))\n"
                "output('Secondary', n)\n")
    with pytest.raises(ValueError, match='exactly one Geometry output'):
        typed_reference_source(original)
