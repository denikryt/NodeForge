"""Unit coverage for the public runtime Bundle semantic type."""

import pytest

from NodeForge.semantic.builtin_calls import INPUT_DECLARATION_BUILTIN_NAMES, IR_CAPABLE_BUILTIN_NAMES
from NodeForge.blender import bundle, raw_nodes
from NodeForge.semantic.constants import TYPE_BUNDLE, TYPE_TOKEN_NAMES, TYPE_VECTOR
from NodeForge.nf_types import NFType
from NodeForge.errors import CompileError
from NodeForge.blender.nodes import _socket_type_for
from NodeForge.semantic.callable_contracts import source_argument_type_matches

pytestmark = pytest.mark.unit


def test_bundle_type_token_and_permanent_builtin_registration():
    assert TYPE_TOKEN_NAMES["Bundle"] == TYPE_BUNDLE
    assert _socket_type_for(TYPE_BUNDLE) == "NodeSocketBundle"
    assert "input_bundle" in INPUT_DECLARATION_BUILTIN_NAMES
    assert TYPE_BUNDLE in raw_nodes._SUPPORTED_TYPES
    assert {"bundle", "bundle_get", "bundle_set"} <= IR_CAPABLE_BUILTIN_NAMES


def test_bundle_is_not_registered_through_deleted_executable_builtin_modules():
    from NodeForge.semantic import builtin_registry as registry

    assert not hasattr(registry, "compile_call")
    assert not hasattr(registry, "_HANDLERS")
    assert "bundle" in registry.CALLABLE_BUILTIN_NAMES


def test_bundle_backend_socket_mapping_is_explicit_and_rejects_unknown_type():
    assert bundle._bundle_socket_type(TYPE_BUNDLE, "item") == "BUNDLE"
    assert bundle._bundle_socket_type(TYPE_VECTOR, "item") == "VECTOR"
    with pytest.raises(CompileError, match="unsupported Bundle item type"):
        bundle._bundle_socket_type(object(), "item")


def test_source_callable_bundle_type_matching_is_semantic():
    assert source_argument_type_matches(TYPE_BUNDLE, TYPE_BUNDLE)
    assert not source_argument_type_matches(TYPE_BUNDLE, TYPE_VECTOR)


def test_bundle_remains_one_runtime_nftype_without_structural_schema_types():
    assert TYPE_BUNDLE is NFType.BUNDLE
    assert not hasattr(NFType, "TUPLE")
    assert not hasattr(NFType, "NODE_RESULT")
    assert not hasattr(NFType, "NAMED_OUTPUTS")
