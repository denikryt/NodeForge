"""Unit contracts for the canonical NodeForge semantic type vocabulary."""

from types import SimpleNamespace

import pytest

from NodeForge.constants import OBJECT_PROPERTY_TYPES, TYPE_FLOAT, TYPE_TOKEN_NAMES, TYPE_VECTOR
from NodeForge.compiler_identities import BindingId
from NodeForge.nf_types import NFType, deserialize_nf_type, serialize_nf_type
from NodeForge.semantic_analysis import ResolvedName, RuntimeBindingSymbol, RuntimeResultShape, SemanticConstant
from NodeForge.semantic_ir import IRValue
from NodeForge.values import Value

pytestmark = pytest.mark.unit


_PUBLIC_TYPE_TOKENS = {
    "Float",
    "Int",
    "Bool",
    "Vector",
    "Geometry",
    "Material",
    "Object",
    "String",
    "Bundle",
}


def test_canonical_types_are_plain_enum_members_with_stable_rendering():
    """Canonical types retain old display tokens without string identity."""
    assert TYPE_FLOAT is NFType.FLOAT
    assert TYPE_VECTOR is NFType.VECTOR
    assert NFType.FLOAT != "FLOAT"
    assert not isinstance(NFType.FLOAT, str)
    assert str(NFType.FLOAT) == "FLOAT"
    assert repr(NFType.FLOAT) == "'FLOAT'"


def test_explicit_serialization_is_strict_and_stable():
    """External type tokens cross only the explicit serialization boundary."""
    assert serialize_nf_type(NFType.FLOAT) == "FLOAT"
    assert deserialize_nf_type("VECTOR") is NFType.VECTOR
    with pytest.raises(TypeError, match="typ must be an NFType"):
        serialize_nf_type("FLOAT")
    with pytest.raises(TypeError, match="token must be a string"):
        deserialize_nf_type(NFType.FLOAT)
    with pytest.raises(ValueError, match="Unknown NodeForge type token"):
        deserialize_nf_type("COLOR")


def test_source_type_tokens_remain_exact_and_rotation_stays_internal_only():
    """Public source tokens map explicitly to canonical runtime types."""
    assert set(TYPE_TOKEN_NAMES) == _PUBLIC_TYPE_TOKENS
    assert TYPE_TOKEN_NAMES["Float"] is NFType.FLOAT
    assert "Rotation" not in TYPE_TOKEN_NAMES
    assert NFType.ROTATION.value == "ROTATION"
    assert OBJECT_PROPERTY_TYPES == {
        "geometry": NFType.GEOMETRY,
        "location": NFType.VECTOR,
        "rotation": NFType.VECTOR,
        "scale": NFType.VECTOR,
    }


def test_type_bearing_runtime_records_reject_raw_strings():
    """Semantic and backend runtime values require canonical NFType objects."""
    assert IRValue(0, NFType.FLOAT).typ is NFType.FLOAT
    with pytest.raises(TypeError, match="typ must be an NFType"):
        IRValue(0, "FLOAT")

    binding = RuntimeBindingSymbol(BindingId("owner", 0), NFType.FLOAT)
    assert binding.typ is NFType.FLOAT
    with pytest.raises(TypeError, match="typ must be an NFType"):
        RuntimeBindingSymbol(BindingId("owner", 1), "FLOAT")
    with pytest.raises(TypeError, match="typ must be an NFType"):
        RuntimeResultShape("FLOAT")
    with pytest.raises(TypeError, match="typ must be an NFType or None"):
        SemanticConstant("scalar", "FLOAT", 1.0)
    with pytest.raises(TypeError, match="typ must be an NFType or None"):
        ResolvedName("runtime", "FLOAT")

    socket = SimpleNamespace()
    assert Value(socket, NFType.FLOAT).typ is NFType.FLOAT
    with pytest.raises(TypeError, match="typ must be an NFType"):
        Value(socket, "FLOAT")
    with pytest.raises(TypeError, match="typ must be an NFType"):
        Value(socket, "UNKNOWN")


def test_input_default_metadata_serializes_historical_plain_type_token(monkeypatch):
    """Persisted group-input metadata stays byte-compatible with previous files."""
    import importlib
    import sys
    import types

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace())
    sys.modules.pop("NodeForge.storage", None)
    sys.modules.pop("NodeForge.interface", None)
    interface = importlib.import_module("NodeForge.interface")

    group = {}
    interface._record_group_input_default(group, "Scale", NFType.FLOAT, 1.25)
    assert interface._get_group_input_defaults(group)["Scale"] == {"type": "FLOAT", "default": 1.25}
    with pytest.raises(TypeError, match="typ must be an NFType"):
        interface._record_group_input_default(group, "Legacy", "FLOAT", 1.0)


def test_euler_to_rotation_uses_internal_canonical_rotation_type(monkeypatch):
    """The existing internal Rotation socket value is canonical but not public."""
    from NodeForge import geometry

    input_socket = object()
    output_socket = object()
    node = SimpleNamespace(inputs=[input_socket], outputs=[output_socket])
    links = SimpleNamespace(new=lambda source, target: None)
    group = SimpleNamespace(links=links)
    monkeypatch.setattr(geometry, "_new_node", lambda *args, **kwargs: node)

    result = geometry._euler_to_rotation(group, Value(object(), NFType.VECTOR))
    assert result.socket is output_socket
    assert result.typ is NFType.ROTATION
