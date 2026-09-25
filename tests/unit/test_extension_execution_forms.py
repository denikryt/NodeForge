"""Package semantic-value extension execution-form classification tests."""

from __future__ import annotations

import inspect

import pytest

from NodeForge.errors import CompileError
from NodeForge.evaluation_modes import EvaluationMode
from NodeForge.extension_contracts import (
    ExtensionCallableId,
    ExtensionCallableSpec,
    ExtensionParameterSpec,
    ExtensionTypeId,
    PythonScalarKind,
    TypeSpec,
)
from NodeForge.extension_semantics import ExtensionExecutionForm, classify_extension_execution
from NodeForge.nf_types import NFType

pytestmark = pytest.mark.unit
OWNER = ("system", "vendor.exec", "test")
CALLABLE = ExtensionCallableId(OWNER, "foo")
PART = ExtensionTypeId(OWNER, "Part")
PRIVATE = ExtensionTypeId(OWNER, "_State")
NF_FLOAT = TypeSpec("NF_SET", frozenset({NFType.FLOAT}))
RECORD_PART = TypeSpec("RECORD", record_type=PART)
RECORD_PRIVATE = TypeSpec("RECORD", record_type=PRIVATE)
PY_INT = TypeSpec("PY_SCALAR", python_scalar_kind=PythonScalarKind.INT)


def _spec(parameter, result):
    return ExtensionCallableSpec(CALLABLE, (parameter,), result)


def test_backend_only_and_semantic_only_forms_are_exact():
    """Backend-only and frontend-semantic public results stay disjoint execution forms."""
    runtime_param = ExtensionParameterSpec("x", inspect.Parameter.POSITIONAL_OR_KEYWORD, NF_FLOAT, EvaluationMode.RUNTIME_ONLY)
    backend = _spec(runtime_param, NF_FLOAT)
    assert classify_extension_execution(backend, None, has_implementation=True) is ExtensionExecutionForm.BACKEND_ONLY

    semantic = _spec(runtime_param, RECORD_PART)
    assert classify_extension_execution(semantic, RECORD_PART, has_implementation=False) is ExtensionExecutionForm.SEMANTIC_ONLY
    with pytest.raises(CompileError, match="cannot also declare"):
        classify_extension_execution(semantic, RECORD_PART, has_implementation=True)


def test_semantic_then_backend_requires_distinct_private_state_and_backend():
    """Executable public calls may use semantic normalization only through distinct private state."""
    record_param = ExtensionParameterSpec("part", inspect.Parameter.POSITIONAL_OR_KEYWORD, RECORD_PART, None)
    spec = _spec(record_param, NF_FLOAT)
    assert classify_extension_execution(spec, RECORD_PRIVATE, has_implementation=True) is ExtensionExecutionForm.SEMANTIC_THEN_BACKEND
    assert classify_extension_execution(spec, PY_INT, has_implementation=True) is ExtensionExecutionForm.SEMANTIC_THEN_BACKEND
    with pytest.raises(CompileError, match="requires a physical"):
        classify_extension_execution(spec, RECORD_PRIVATE, has_implementation=False)
    with pytest.raises(CompileError, match="requires semantic.py"):
        classify_extension_execution(spec, None, has_implementation=True)


def test_executable_public_result_cannot_be_same_semantic_result():
    """A same-type semantic return cannot silently turn an executable result into frontend state."""
    runtime_param = ExtensionParameterSpec("x", inspect.Parameter.POSITIONAL_OR_KEYWORD, NF_FLOAT, EvaluationMode.RUNTIME_ONLY)
    spec = _spec(runtime_param, NF_FLOAT)
    with pytest.raises(CompileError, match="cannot use its executable public result"):
        classify_extension_execution(spec, NF_FLOAT, has_implementation=True)
