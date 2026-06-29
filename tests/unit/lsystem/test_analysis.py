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


def test_analysis_detects_runtime_parameterized_commands_and_marker_only_runtime_params():
    from NodeForge.systems.lsystem.model import LSystemModule, ModuleArg
    runtime_value = Value(None, "FLOAT")
    modules = (
        LSystemModule("F", (ModuleArg("length", runtime_value, True, "length"),), "F(length)"),
        LSystemModule("Leaf", (ModuleArg("size", runtime_value, True, "size"),), "Leaf(size)"),
    )
    metrics = analyze(modules, angle=60.0, step=1.0, markers={"Leaf": object()})
    assert metrics.step_is_runtime is True
    assert metrics.angle_is_runtime is False
    assert metrics.marker_count == 1
    assert metrics.marker_param_is_runtime is True


def test_analysis_marker_only_runtime_param_does_not_select_runtime_placement():
    from NodeForge.systems.lsystem.model import LSystemModule, ModuleArg
    runtime_value = Value(None, "FLOAT")
    modules = (LSystemModule("F", (), "F"), LSystemModule("Leaf", (ModuleArg("size", runtime_value, True, "size"),), "Leaf(size)"))
    metrics = analyze(modules, angle=60.0, step=1.0, markers={"Leaf": object()})
    assert metrics.step_is_runtime is False
    assert metrics.angle_is_runtime is False
    assert metrics.marker_param_is_runtime is True


def test_global_runtime_angle_selects_runtime_backend_even_without_turn_commands():
    runtime_value = Value(None, "FLOAT")
    metrics = analyze("F", angle=runtime_value, step=0.1)
    assert metrics.angle_is_runtime is True
    assert metrics.step_is_runtime is False


def test_global_runtime_step_selects_runtime_backend_even_without_explicit_parameterized_move():
    runtime_value = Value(None, "FLOAT")
    metrics = analyze("F", angle=60.0, step=runtime_value)
    assert metrics.step_is_runtime is True
    assert metrics.angle_is_runtime is False
