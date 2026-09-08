"""Unit contracts for the canonical NodeForge semantic type vocabulary."""

from types import SimpleNamespace

import pytest

from NodeForge.constants import OBJECT_PROPERTY_TYPES, TYPE_FLOAT, TYPE_TOKEN_NAMES, TYPE_VECTOR
from NodeForge.compiler_identities import BindingId, InputDeclarationId
from NodeForge.nf_types import NFType, deserialize_nf_type, serialize_nf_type
from NodeForge.semantic_analysis import ResolvedName, RuntimeBindingSymbol, SemanticConstant
from NodeForge.semantic_values import RuntimeResultShape
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


def test_input_default_metadata_uses_occurrence_aware_schema_and_reads_legacy(monkeypatch):
    """0.51 defaults distinguish duplicate labels while normalizing pre-0.51 metadata."""
    import importlib
    import sys
    import types

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace())
    sys.modules.pop("NodeForge.interface", None)
    interface = importlib.import_module("NodeForge.interface")

    group = {}
    interface._record_group_input_default(group, "Scale", NFType.FLOAT, 1.25)
    key = ("Scale", "NodeSocketFloat", 0)
    assert interface._get_group_input_defaults(group)[key] == {
        "name": "Scale",
        "nf_type": "FLOAT",
        "socket_type": "NodeSocketFloat",
        "occurrence": 0,
        "default": 1.25,
    }
    group[interface.INPUT_DEFAULTS_PROP] = {"Legacy": {"type": "VECTOR", "default": [1.0, 2.0, 3.0]}}
    assert interface._get_group_input_defaults(group)[("Legacy", "NodeSocketVector", 0)]["default"] == [1.0, 2.0, 3.0]
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



def test_duplicate_input_default_records_use_independent_occurrences(monkeypatch):
    """Duplicate display labels preserve independent defaults by physical occurrence."""
    import importlib
    import sys
    import types

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace())
    sys.modules.pop("NodeForge.interface", None)
    interface = importlib.import_module("NodeForge.interface")

    class Group(dict):
        """Minimal IDProperty-like group carrying a fake interface tree."""

    first = SimpleNamespace(
        item_type="SOCKET", in_out="INPUT", name="Scale", socket_type="NodeSocketFloat"
    )
    second = SimpleNamespace(
        item_type="SOCKET", in_out="INPUT", name="Scale", socket_type="NodeSocketFloat"
    )
    group = Group()
    group.interface = SimpleNamespace(items_tree=[first, second])
    interface._record_group_input_default(group, "Scale", NFType.FLOAT, 1.0, interface_item=first)
    interface._record_group_input_default(group, "Scale", NFType.FLOAT, 7.0, interface_item=second)

    defaults = interface._get_group_input_defaults(group)
    assert defaults[("Scale", "NodeSocketFloat", 0)]["default"] == 1.0
    assert defaults[("Scale", "NodeSocketFloat", 1)]["default"] == 7.0


def test_update_defaults_and_override_capture_distinguish_duplicate_input_labels(monkeypatch):
    """Update defaults and user overrides address duplicate sockets by structural occurrence."""
    import importlib
    import sys
    import types

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace())
    update = importlib.import_module("NodeForge.update")
    interface = importlib.import_module("NodeForge.interface")

    class Group(dict):
        """Minimal group with persisted input-default metadata."""

    class Socket:
        """Minimal group-node input socket with Blender-like physical identity fields."""

        _next_pointer = 1

        def __init__(self, name, default):
            self.name = name
            self.bl_idname = "NodeSocketFloat"
            self.default_value = default
            self._pointer = Socket._next_pointer
            Socket._next_pointer += 1

        def as_pointer(self):
            return self._pointer

    group = Group()
    group[interface.INPUT_DEFAULTS_PROP] = {
        "0": {"name": "Scale", "nf_type": "FLOAT", "socket_type": "NodeSocketFloat", "occurrence": 0, "default": 1.0},
        "1": {"name": "Scale", "nf_type": "FLOAT", "socket_type": "NodeSocketFloat", "occurrence": 1, "default": 7.0},
    }
    first = Socket("Scale", 0.0)
    second = Socket("Scale", 0.0)
    node = SimpleNamespace(node_tree=group, inputs=[first, second], outputs=[])

    update._apply_group_defaults_to_node(node)
    assert (first.default_value, second.default_value) == (1.0, 7.0)

    first.default_value = 3.0
    second.default_value = 7.0
    overrides = list(update._captured_node_input_overrides(node))
    assert overrides == [(first, 3.0)]

    keys = [key for _socket, key in update._socket_keys(node.inputs, "INPUT")]
    update._apply_group_defaults_to_node(node, preserve_existing={keys[1]: 11.0})
    assert (first.default_value, second.default_value) == (1.0, 11.0)


def test_same_label_different_socket_types_have_distinct_default_keys(monkeypatch):
    """Occurrence identity includes physical socket type as required by update matching."""
    import importlib
    import sys
    import types

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace())
    interface = importlib.import_module("NodeForge.interface")

    class Group(dict):
        """Minimal group with a fake ordered interface."""

    float_item = SimpleNamespace(item_type="SOCKET", in_out="INPUT", name="Same", socket_type="NodeSocketFloat")
    vector_item = SimpleNamespace(item_type="SOCKET", in_out="INPUT", name="Same", socket_type="NodeSocketVector")
    group = Group()
    group.interface = SimpleNamespace(items_tree=[float_item, vector_item])
    interface._record_group_input_default(group, "Same", NFType.FLOAT, 1.0, interface_item=float_item)
    interface._record_group_input_default(group, "Same", NFType.VECTOR, (1.0, 2.0, 3.0), interface_item=vector_item)
    defaults = interface._get_group_input_defaults(group)
    assert ("Same", "NodeSocketFloat", 0) in defaults
    assert ("Same", "NodeSocketVector", 0) in defaults


def test_explicit_declaration_defaults_merge_with_implicit_defaults_for_override_capture(monkeypatch):
    """Explicit declaration metadata must not hide ordinary implicit-input script defaults."""
    import importlib
    import sys
    import types

    monkeypatch.setitem(sys.modules, "bpy", types.SimpleNamespace())
    interface = importlib.import_module("NodeForge.interface")
    update = importlib.import_module("NodeForge.update")

    class Group(dict):
        """Minimal group carrying both persisted input-default metadata stores."""

    class Socket:
        """Minimal group-node input socket used by override capture."""

        _next_pointer = 1

        def __init__(self, name, socket_type, default):
            self.name = name
            self.bl_idname = socket_type
            self.default_value = default
            self._pointer = Socket._next_pointer
            Socket._next_pointer += 1

        def as_pointer(self):
            return self._pointer

    group = Group()
    group[interface.INPUT_DEFAULTS_PROP] = {
        "0": {
            "name": "iterations",
            "nf_type": "INT",
            "socket_type": "NodeSocketInt",
            "occurrence": 0,
            "default": 1,
        }
    }
    interface._write_group_input_declarations(
        group,
        [{
            "declaration_id": InputDeclarationId("owner", "scale", 0),
            "display_name": "Scale",
            "nf_type": "FLOAT",
            "socket_type": "NodeSocketFloat",
            "occurrence": 0,
            "has_default": True,
            "default": 1.0,
        }],
    )

    defaults = interface._get_group_input_defaults(group)
    assert defaults[("iterations", "NodeSocketInt", 0)]["default"] == 1
    assert defaults[("Scale", "NodeSocketFloat", 0)]["default"] == 1.0

    iterations = Socket("iterations", "NodeSocketInt", 0)
    scale = Socket("Scale", "NodeSocketFloat", 1.0)
    node = SimpleNamespace(node_tree=group, inputs=[iterations, scale], outputs=[])
    assert list(update._captured_node_input_overrides(node)) == [(iterations, 0)]
