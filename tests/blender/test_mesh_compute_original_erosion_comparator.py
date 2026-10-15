"""Numeric comparison regression tests, independent of the optional math package."""

import numpy as np
import pytest

from NodeForge.tests.blender.compare_mesh_compute_original_erosion import compare_outputs, parity_violations

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
