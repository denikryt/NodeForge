import pytest

from NodeForge.errors import CompileError
from NodeForge.systems.lsystem.analysis import analyze
from NodeForge.values import Value

pytestmark = pytest.mark.unit


def test_analysis_detects_static_branching_metrics():
    metrics = analyze("F[+F]F", angle=60.0, step=1.0)

    assert metrics.has_branches is True
    assert metrics.angle_is_runtime is False
    assert metrics.step_is_runtime is False
    assert metrics.segment_count == 3
    assert metrics.symbol_count == 6
    assert metrics.max_branch_depth == 1


def test_analysis_detects_runtime_inputs():
    runtime_value = Value(None, "FLOAT")
    metrics = analyze("F+F", angle=runtime_value, step=runtime_value)

    assert metrics.has_branches is False
    assert metrics.angle_is_runtime is True
    assert metrics.step_is_runtime is True


@pytest.mark.parametrize("stream", ["F]", "F[+F"])
def test_analysis_rejects_unbalanced_brackets(stream):
    with pytest.raises(CompileError):
        analyze(stream, angle=60.0, step=1.0)
