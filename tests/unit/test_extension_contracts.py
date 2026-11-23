"""Unit contracts for the declarative extension data model."""

import inspect

import pytest

from NodeForge.blender.socket_types import socket_type_for_nf_type
from NodeForge.evaluation_modes import EvaluationMode
from NodeForge.extensions.contracts import (
    ExtensionCallableId,
    ExtensionCallableSpec,
    ExtensionImplementationRef,
    ExtensionParameterSpec,
    TypeSpec,
    validate_extension_argument_positions,
)
from NodeForge.nf_types import NFType

pytestmark = pytest.mark.unit


def test_extension_identity_and_physical_reference_are_distinct_minimal_records():
    callable_id = ExtensionCallableId(("system", "vendor.pkg", "math"), "select")
    ref = ExtensionImplementationRef(".operations", "select_node")
    assert callable_id.name == "select"
    assert ref.attribute == "select_node"
    assert not hasattr(callable_id, "module")
    assert not hasattr(ref, "owner")


def test_extension_spec_requires_direct_parameters_and_exact_results():
    callable_id = ExtensionCallableId(("system", "vendor.pkg", "math"), "foo")
    parameter = ExtensionParameterSpec(
        "value",
        inspect.Parameter.POSITIONAL_OR_KEYWORD,
        TypeSpec("NF_SET", frozenset({NFType.FLOAT, NFType.INT})),
        EvaluationMode.RUNTIME_ONLY,
    )
    spec = ExtensionCallableSpec(
        callable_id,
        (parameter,),
        TypeSpec("NF_SET", frozenset({NFType.FLOAT})),
    )
    assert spec.parameters == (parameter,)
    with pytest.raises(ValueError, match="exactly one"):
        ExtensionCallableSpec(
            callable_id,
            (parameter,),
            TypeSpec("NF_SET", frozenset({NFType.FLOAT, NFType.INT})),
        )


def test_blender_socket_authority_covers_rotation_and_every_nf_type():
    """Extension API v2 runtime types must all survive group-interface materialization."""
    mapping = {typ: socket_type_for_nf_type(typ) for typ in NFType}
    assert set(mapping) == set(NFType)
    assert mapping[NFType.ROTATION] == "NodeSocketRotation"


def test_extension_spec_derives_canonical_python_signature():
    callable_id = ExtensionCallableId(("system", "vendor.pkg", "math"), "mix")
    parameters = (
        ExtensionParameterSpec(
            "head",
            inspect.Parameter.POSITIONAL_ONLY,
            TypeSpec("NF_SET", frozenset({NFType.FLOAT})),
            EvaluationMode.RUNTIME_ONLY,
        ),
        ExtensionParameterSpec(
            "items",
            inspect.Parameter.VAR_POSITIONAL,
            TypeSpec("NF_SET", frozenset({NFType.FLOAT})),
            EvaluationMode.COMPILE_TIME_OR_RUNTIME,
        ),
        ExtensionParameterSpec(
            "scale",
            inspect.Parameter.KEYWORD_ONLY,
            TypeSpec("NF_SET", frozenset({NFType.FLOAT})),
            EvaluationMode.COMPILE_TIME_ONLY,
            2.0,
            NFType.FLOAT,
        ),
    )
    spec = ExtensionCallableSpec(
        callable_id,
        parameters,
        TypeSpec("NF_SET", frozenset({NFType.FLOAT})),
    )

    signature = spec.python_signature()

    assert tuple(signature.parameters) == ("head", "items", "scale")
    assert signature.parameters["head"].kind is inspect.Parameter.POSITIONAL_ONLY
    assert signature.parameters["items"].kind is inspect.Parameter.VAR_POSITIONAL
    assert signature.parameters["scale"].kind is inspect.Parameter.KEYWORD_ONLY
    assert signature.parameters["scale"].default == 2.0
    bound = signature.bind("runtime-head", 1.0, "runtime-item", scale=4.0)
    assert bound.args == ("runtime-head", 1.0, "runtime-item")
    assert bound.kwargs == {"scale": 4.0}


def test_extension_argument_position_validator_is_the_shared_transport_authority():
    validate_extension_argument_positions(((0, None), (1, 0), (1, 1), (2, None)))

    with pytest.raises(ValueError, match="require parameter positions"):
        validate_extension_argument_positions(((None, None),))
    with pytest.raises(ValueError, match="must be unique"):
        validate_extension_argument_positions(((0, None), (0, None)))
    with pytest.raises(ValueError, match="may appear only once"):
        validate_extension_argument_positions(((0, None), (0, 0)))
    with pytest.raises(ValueError, match="contiguous from zero"):
        validate_extension_argument_positions(((1, 0), (1, 2)))


@pytest.mark.parametrize("mode", list(EvaluationMode))
def test_optional_none_contract_keeps_python_signature_and_mode(mode):
    """An omitted direct argument has no NFType for every evaluation mode."""
    spec = ExtensionCallableSpec(
        ExtensionCallableId(("system", "optional", "contract"), "call"),
        (
            ExtensionParameterSpec("required", inspect.Parameter.POSITIONAL_OR_KEYWORD,
                                   TypeSpec("NF_SET", frozenset({NFType.FLOAT})), mode),
            ExtensionParameterSpec("optional", inspect.Parameter.KEYWORD_ONLY,
                                   TypeSpec("NF_SET", frozenset({NFType.FLOAT, NFType.INT})), mode, None),
        ),
        TypeSpec("NF_SET", frozenset({NFType.FLOAT})),
    )
    assert spec.parameters[1].default is None and spec.parameters[1].default_type is None
    signature = spec.python_signature()
    bound = signature.bind(1.5)
    bound.apply_defaults()
    assert bound.arguments == {"required": 1.5, "optional": None}
    with pytest.raises(TypeError, match="required"):
        signature.bind()


@pytest.mark.parametrize("default, default_type, mode, kind, message", [
    (inspect.Parameter.empty, NFType.FLOAT, EvaluationMode.COMPILE_TIME_ONLY,
     inspect.Parameter.KEYWORD_ONLY, "without concrete"),
    (None, NFType.FLOAT, EvaluationMode.RUNTIME_ONLY,
     inspect.Parameter.KEYWORD_ONLY, "without concrete"),
    (2.0, None, EvaluationMode.COMPILE_TIME_ONLY,
     inspect.Parameter.KEYWORD_ONLY, "canonical NFType"),
    (2.0, NFType.INT, EvaluationMode.COMPILE_TIME_ONLY,
     inspect.Parameter.KEYWORD_ONLY, "not declared"),
    (2.0, NFType.FLOAT, EvaluationMode.RUNTIME_ONLY,
     inspect.Parameter.KEYWORD_ONLY, "RUNTIME_ONLY"),
    (None, None, EvaluationMode.COMPILE_TIME_ONLY,
     inspect.Parameter.VAR_POSITIONAL, "variadic"),
    (None, None, EvaluationMode.COMPILE_TIME_ONLY,
     inspect.Parameter.VAR_KEYWORD, r"\*\*kwargs"),
])
def test_contract_rejects_invalid_default_states(default, default_type, mode, kind, message):
    """Malformed signatures fail at contract creation before call analysis."""
    with pytest.raises((ValueError, TypeError), match=message):
        ExtensionParameterSpec("value", kind, TypeSpec("NF_SET", frozenset({NFType.FLOAT})),
                               mode, default, default_type)
