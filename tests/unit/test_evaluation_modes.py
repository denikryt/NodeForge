"""Pure tests for consumer-local compile-time/runtime representation selection."""

import ast
import inspect

import pytest

from NodeForge import evaluation_modes
from NodeForge.errors import CompileError
from NodeForge.evaluation_modes import (
    CompileTimeSelection,
    EvaluationMode,
    RuntimeRequired,
    resolve_argument_evaluation,
)
from NodeForge.consteval import ConstEvalUnavailable


pytestmark = pytest.mark.unit


def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def test_compile_time_only_returns_known_value():
    selection = resolve_argument_evaluation(
        _expr("known"), {"known": 7}, EvaluationMode.COMPILE_TIME_ONLY
    )
    assert selection == CompileTimeSelection(7)


def test_compile_time_only_propagates_unavailability():
    with pytest.raises(ConstEvalUnavailable):
        resolve_argument_evaluation(
            _expr("runtime_name"), {}, EvaluationMode.COMPILE_TIME_ONLY
        )


def test_compile_time_only_propagates_hard_compile_error():
    with pytest.raises(CompileError, match="not expects Bool"):
        resolve_argument_evaluation(
            _expr("not 1"), {}, EvaluationMode.COMPILE_TIME_ONLY
        )


def test_runtime_only_never_probes_consteval(monkeypatch):
    def fail_if_called(*_args, **_kwargs):
        raise AssertionError("runtime-only selection probed CTFE")

    monkeypatch.setattr(evaluation_modes, "_const_eval", fail_if_called)
    selection = resolve_argument_evaluation(
        _expr("anything"), {}, EvaluationMode.RUNTIME_ONLY
    )
    assert isinstance(selection, RuntimeRequired)


def test_mixed_mode_prefers_compile_time_when_available():
    selection = resolve_argument_evaluation(
        _expr("known"), {"known": "value"}, EvaluationMode.COMPILE_TIME_OR_RUNTIME
    )
    assert selection == CompileTimeSelection("value")


def test_mixed_mode_requests_runtime_only_on_consteval_unavailability():
    selection = resolve_argument_evaluation(
        _expr("runtime_name"), {}, EvaluationMode.COMPILE_TIME_OR_RUNTIME
    )
    assert isinstance(selection, RuntimeRequired)


def test_mixed_mode_does_not_turn_hard_error_into_runtime():
    with pytest.raises(CompileError, match="not expects Bool"):
        resolve_argument_evaluation(
            _expr("not 1"), {}, EvaluationMode.COMPILE_TIME_OR_RUNTIME
        )


def test_selector_has_no_runtime_callback_or_fold_policy_dependency():
    parameters = inspect.signature(resolve_argument_evaluation).parameters
    assert tuple(parameters) == ("expr", "consts", "mode")
    assert "try_runtime_fold" not in vars(evaluation_modes)


def test_invalid_mode_is_internal_type_error():
    with pytest.raises(TypeError, match="Unsupported evaluation mode"):
        resolve_argument_evaluation(_expr("1"), {}, object())
