import pytest

from NodeForge.systems.lsystem import backends as lsystem_backends
from NodeForge.systems.lsystem.model import LSystemAnalysis

pytestmark = pytest.mark.unit


def metrics(*, has_branches, angle_runtime, step_runtime, depth=0):
    return LSystemAnalysis(
        has_branches=has_branches,
        angle_is_runtime=angle_runtime,
        step_is_runtime=step_runtime,
        segment_count=1,
        symbol_count=1,
        max_branch_depth=depth,
    )


def test_backend_selector_categories():
    assert lsystem_backends.select_backend_category(metrics(has_branches=True, angle_runtime=False, step_runtime=False)) == "static"
    assert lsystem_backends.select_backend_category(metrics(has_branches=False, angle_runtime=True, step_runtime=False)) == "branch_free_runtime"
    assert lsystem_backends.select_backend_category(metrics(has_branches=True, angle_runtime=False, step_runtime=True)) == "branched_runtime"
