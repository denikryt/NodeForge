"""Unit coverage for the public runtime String semantic type."""

import ast

from NodeForge.constants import TYPE_STRING, TYPE_TOKEN_NAMES
from NodeForge.builtins import io, raw_nodes
from NodeForge.local_functions import input_call_for_type, value_type_for_const
from NodeForge.library_calls import _const_arg_type, _argument_type_matches


def test_string_type_token_and_input_registration():
    assert TYPE_TOKEN_NAMES["String"] == TYPE_STRING
    assert "input_string" in io.NAMES
    assert TYPE_STRING in raw_nodes._SUPPORTED_TYPES


def test_local_function_string_parameter_and_constant_inference():
    assert input_call_for_type("name", TYPE_STRING) == "name = input_string('name')"
    assert value_type_for_const("Weight_A") == TYPE_STRING
    assert _const_arg_type("Weight_A") == TYPE_STRING
    assert _argument_type_matches(TYPE_STRING, TYPE_STRING)


def test_raw_node_string_type_token_parses():
    expr = ast.parse(
        'node("FunctionNodeInputString", output="String", typ=String)',
        mode="eval",
    ).body
    assert raw_nodes._optional_type_token(expr.keywords[-1].value, "typ=") == TYPE_STRING
