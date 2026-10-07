"""Unit coverage for the public runtime String semantic type."""

import ast

from NodeForge.builtin_call_semantics import INPUT_DECLARATION_BUILTIN_NAMES
from NodeForge.constants import TYPE_STRING, TYPE_TOKEN_NAMES
from NodeForge.builtins import raw_nodes
from NodeForge.semantic.source_callables import input_call_for_type, value_type_for_const
from NodeForge.callable_contracts import source_argument_type_matches


def test_string_type_token_and_permanent_input_registration():
    assert TYPE_TOKEN_NAMES["String"] == TYPE_STRING
    assert "input_string" in INPUT_DECLARATION_BUILTIN_NAMES
    assert TYPE_STRING in raw_nodes._SUPPORTED_TYPES


def test_local_function_string_parameter_and_constant_inference():
    assert input_call_for_type("name", TYPE_STRING) == "name = input_string('name')"
    assert value_type_for_const("Weight_A") == TYPE_STRING
    assert source_argument_type_matches(TYPE_STRING, TYPE_STRING)


def test_raw_node_string_type_is_owned_by_type_token_vocabulary():
    expr = ast.parse('node("FunctionNodeInputString", output="String", typ=String)', mode="eval").body
    assert isinstance(expr.keywords[-1].value, ast.Name)
    assert TYPE_TOKEN_NAMES[expr.keywords[-1].value.id] is TYPE_STRING
