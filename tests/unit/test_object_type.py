"""Unit coverage for the public Object semantic type and unresolved state."""

import ast

import pytest

from NodeForge.constants import TYPE_OBJECT, TYPE_TOKEN_NAMES
from NodeForge.errors import CompileError
from NodeForge.local_functions import input_call_for_type
from NodeForge.builtins import io, raw_nodes
from NodeForge.values import ObjectValue, make_value


class _Socket:
    """Minimal socket stand-in used by value-state unit tests."""


def test_object_type_token_and_input_registration():
    assert TYPE_TOKEN_NAMES["Object"] == TYPE_OBJECT
    assert "input_object" in io.NAMES
    assert TYPE_OBJECT in raw_nodes._SUPPORTED_TYPES


def test_local_function_object_parameter_source():
    assert input_call_for_type("source", TYPE_OBJECT) == "source = input_object('source')"


def test_raw_node_object_type_token_parses():
    expr = ast.parse('node("GeometryNodeInputObject", output="Object", typ=Object)', mode="eval").body
    assert raw_nodes._optional_type_token(expr.keywords[-1].value, "typ=") == TYPE_OBJECT


def test_make_value_wraps_object_sockets_with_object_value():
    socket = _Socket()
    value = make_value(socket, TYPE_OBJECT)
    assert isinstance(value, ObjectValue)
    assert value.socket is socket
    assert value.typ == TYPE_OBJECT


def test_object_info_defaults_and_no_argument_configuration_identity():
    value = ObjectValue(_Socket())
    returned = value.configure_info()
    assert returned is value
    assert value._info_transform_space == "ORIGINAL"
    assert value._info_as_instance is True
    assert value._info_resolved is False


def test_object_info_unresolved_partial_updates_merge_and_return_identity():
    value = ObjectValue(_Socket())
    assert value.configure_info(transform_space="RELATIVE") is value
    assert value._info_transform_space == "RELATIVE"
    assert value._info_as_instance is True
    assert value.configure_info(as_instance=False) is value
    assert value._info_transform_space == "RELATIVE"
    assert value._info_as_instance is False


def test_object_info_resolution_locks_even_identical_configuration():
    value = ObjectValue(_Socket())
    value.configure_info(transform_space="RELATIVE", as_instance=False)
    value._info_resolved = True
    with pytest.raises(CompileError, match="cannot be changed after Object Info has been resolved"):
        value.configure_info(transform_space="RELATIVE", as_instance=False)
