"""Unit contracts for the declarative extension data model."""

import inspect

import pytest

from NodeForge.blender_socket_types import socket_type_for_nf_type
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
