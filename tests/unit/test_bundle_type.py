"""Unit coverage for the public runtime Bundle semantic type."""

import ast

import pytest

from NodeForge.builtins import bundle, io, raw_nodes
from NodeForge.constants import TYPE_BUNDLE, TYPE_TOKEN_NAMES, TYPE_VECTOR
from NodeForge.errors import CompileError
from NodeForge.local_functions import input_call_for_type, resolve_local_parameter_annotation, value_type_for_const
from NodeForge.nodes import _socket_type_for
from NodeForge.library_calls import _argument_type_matches, _const_arg_type
from NodeForge.library import _socket_type_to_value_type

pytestmark = pytest.mark.unit


def test_bundle_type_token_and_input_registration():
    assert TYPE_TOKEN_NAMES["Bundle"] == TYPE_BUNDLE
    assert _socket_type_for(TYPE_BUNDLE) == "NodeSocketBundle"
    assert "input_bundle" in io.NAMES
    assert TYPE_BUNDLE in raw_nodes._SUPPORTED_TYPES
    assert {"bundle", "bundle_get", "bundle_set"} <= bundle.NAMES


def test_local_function_bundle_parameter_is_runtime_only():
    assert input_call_for_type("state", TYPE_BUNDLE) == "state = input_bundle('state')"
    annotation = ast.parse("Bundle", mode="eval").body
    assert resolve_local_parameter_annotation(annotation) == TYPE_BUNDLE
    assert _argument_type_matches(TYPE_BUNDLE, TYPE_BUNDLE)
    assert _const_arg_type({"x": 1}) is None
    with pytest.raises(CompileError):
        value_type_for_const({"x": 1})


def test_bundle_type_token_parser_accepts_bundle():
    expr = ast.parse("Bundle", mode="eval").body
    assert bundle._parse_type_token(expr, "typ=") == TYPE_BUNDLE
    assert bundle._bundle_socket_type(TYPE_BUNDLE, "item") == "BUNDLE"
    assert bundle._bundle_socket_type(TYPE_VECTOR, "item") == "VECTOR"


def test_library_socket_mapping_recognizes_bundle():
    class FakeBundleSocket:
        bl_idname = "NodeSocketBundle"
        socket_type = "NodeSocketBundle"
        bl_socket_idname = "NodeSocketBundle"

    assert _socket_type_to_value_type(FakeBundleSocket()) == TYPE_BUNDLE


def test_unknown_group_socket_does_not_fall_back_to_float():
    class FakeSocket:
        bl_idname = "NodeSocketUnsupportedThing"
        socket_type = ""
        bl_socket_idname = ""

    with pytest.raises(CompileError, match="Unsupported Blender group socket type"):
        _socket_type_to_value_type(FakeSocket())
