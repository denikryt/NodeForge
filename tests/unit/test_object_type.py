"""Unit coverage for the public Object semantic type and minimal backend carrier."""

import ast

from NodeForge.semantic.builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES
from NodeForge.semantic.constants import TYPE_OBJECT, TYPE_TOKEN_NAMES
from NodeForge.semantic.source_callables import input_call_for_type
from NodeForge.blender import raw_nodes
from NodeForge.blender.values import ObjectValue, make_value


class _Socket:
    """Minimal socket stand-in used by backend-carrier unit tests."""


def test_object_type_token_and_permanent_input_registration():
    assert TYPE_TOKEN_NAMES["Object"] == TYPE_OBJECT
    assert "input_object" in INPUT_DECLARATION_BUILTIN_NAMES
    assert TYPE_OBJECT in raw_nodes._SUPPORTED_TYPES


def test_local_function_object_parameter_source():
    assert input_call_for_type("source", TYPE_OBJECT) == "source = input_object('source')"


def test_raw_node_object_type_is_owned_by_type_token_vocabulary():
    expr = ast.parse('node("GeometryNodeInputObject", output="Object", typ=Object)', mode="eval").body
    assert isinstance(expr.keywords[-1].value, ast.Name)
    assert TYPE_TOKEN_NAMES[expr.keywords[-1].value.id] is TYPE_OBJECT


def test_make_value_wraps_object_sockets_with_minimal_object_value():
    socket = _Socket()
    value = make_value(socket, TYPE_OBJECT)
    assert isinstance(value, ObjectValue)
    assert value.socket is socket
    assert value.typ == TYPE_OBJECT
    assert value._object_info_outputs is None
    assert value._object_info_cache_config is None


def test_object_backend_carrier_has_no_frontend_configuration_api():
    value = ObjectValue(_Socket())
    assert not hasattr(value, "configure_info")
    assert not hasattr(value, "_info_transform_space")
    assert not hasattr(value, "_info_as_instance")
