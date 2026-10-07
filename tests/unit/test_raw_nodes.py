"""Unit coverage for raw-node semantic vocabulary after removal of the legacy AST adapter."""

import ast
import json
from types import SimpleNamespace

import pytest

from NodeForge.semantic.builtin_calls import IR_CAPABLE_BUILTIN_NAMES, analyze_builtin_call
from NodeForge.builtins import raw_nodes
from NodeForge.semantic.call_resolution import NamedOutputsCallResult, RuntimeCallResult
from NodeForge.semantic.constants import (
    TYPE_BOOL, TYPE_BUNDLE, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT,
    TYPE_MATERIAL, TYPE_OBJECT, TYPE_STRING, TYPE_TOKEN_NAMES, TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.semantic.parsing import _collect_inputs, _parse_source
from NodeForge.values import Value

pytestmark = pytest.mark.unit


class _FakeSocket:
    """Small Blender-socket stand-in for raw backend boundary tests."""

    _next_pointer = 1

    def __init__(
        self,
        name,
        identifier,
        *,
        bl_idname="NodeSocketFloat",
        is_output=False,
        is_unavailable=False,
        is_multi_input=False,
        default_value=0.0,
        pointer=None,
    ):
        self.name = name
        self.identifier = identifier
        self.bl_idname = bl_idname
        self.is_output = is_output
        self.is_unavailable = is_unavailable
        self.is_multi_input = is_multi_input
        self.default_value = default_value
        self.hide = False
        self.hide_value = False
        self.is_inactive = False
        if pointer is None:
            pointer = _FakeSocket._next_pointer
            _FakeSocket._next_pointer += 1
        self._pointer = pointer

    def as_pointer(self):
        return self._pointer


class _FakeNode(dict):
    """Minimal mapping/RNA stand-in for raw metadata and socket tests."""

    def __init__(self, bl_idname, inputs=(), outputs=()):
        super().__init__()
        self.bl_idname = bl_idname
        self.inputs = list(inputs)
        self.outputs = list(outputs)
        self.location = (0.0, 0.0)
        self.name = bl_idname
        self.label = ""
        self.bl_rna = SimpleNamespace(properties={})


class _FakeLinks(list):
    """Record link mutations made by the raw backend."""

    def new(self, from_socket, to_socket):
        self.append(SimpleNamespace(from_socket=from_socket, to_socket=to_socket, to_node=None))


class _FakeGroup:
    """Minimal raw-node group with observable link mutations."""

    def __init__(self):
        self.links = _FakeLinks()


def _write_v1_metadata(node, *, inputs=(), outputs=(), mode=None):
    """Attach one legacy name-based raw metadata record to a fake node."""
    node[raw_nodes.RAW_NODE_PROP] = True
    node[raw_nodes.RAW_BL_IDNAME_PROP] = node.bl_idname
    node[raw_nodes.RAW_PROPS_JSON_PROP] = "{}"
    node[raw_nodes.RAW_INPUTS_JSON_PROP] = json.dumps(list(inputs))
    node[raw_nodes.RAW_OUTPUTS_JSON_PROP] = json.dumps(list(outputs))
    node[raw_nodes.RAW_MODE_PROP] = mode or (
        raw_nodes.RAW_MULTI_MODE if len(tuple(outputs)) > 1 else raw_nodes.RAW_SINGLE_MODE
    )


def _write_v2_metadata(node, *, inputs=(), outputs=(), mode=None):
    """Attach one schema-v2 identifier-based raw metadata record to a fake node."""
    node[raw_nodes.RAW_NODE_PROP] = True
    node[raw_nodes.RAW_SCHEMA_VERSION_PROP] = raw_nodes.RAW_SCHEMA_VERSION
    node[raw_nodes.RAW_BL_IDNAME_PROP] = node.bl_idname
    node[raw_nodes.RAW_PROPS_JSON_PROP] = "{}"
    node[raw_nodes.RAW_INPUTS_JSON_PROP] = json.dumps(list(inputs))
    node[raw_nodes.RAW_OUTPUTS_JSON_PROP] = json.dumps(list(outputs))
    node[raw_nodes.RAW_MODE_PROP] = mode or (
        raw_nodes.RAW_MULTI_MODE if len(tuple(outputs)) > 1 else raw_nodes.RAW_SINGLE_MODE
    )


def test_type_token_names_are_authoritative_runtime_types():
    assert TYPE_TOKEN_NAMES == {
        "Float": TYPE_FLOAT,
        "Int": TYPE_INT,
        "Bool": TYPE_BOOL,
        "Vector": TYPE_VECTOR,
        "Geometry": TYPE_GEOMETRY,
        "Material": TYPE_MATERIAL,
        "Object": TYPE_OBJECT,
        "String": TYPE_STRING,
        "Bundle": TYPE_BUNDLE,
    }


@pytest.mark.parametrize(
    "source",
    [
        'Bool = 1\noutput("x", 1)',
        'Bool = 1\nBool += 1\noutput("x", Bool)',
        'for Bool in [1]:\n    x = Bool\noutput("x", 1)',
        'for x, Bool in [(1, 2)]:\n    y = x\noutput("x", 1)',
        'def Bool(x):\n    return x\noutput("x", 1)',
        'def f(Bool):\n    return Bool\noutput("x", 1)',
        'if True:\n    Geometry = 1\noutput("x", 1)',
    ],
)
def test_type_token_bindings_are_rejected(source):
    with pytest.raises(CompileError, match="Type token"):
        _parse_source(source)


def test_type_tokens_are_not_collected_as_implicit_inputs():
    stmts = _parse_source('mask = node("FunctionNodeCompare", output="Result", typ=Bool)\noutput("mask", mask)')
    assert "Bool" not in _collect_inputs(stmts, extra_builtin_names={"node"})


def test_direct_call_callee_is_not_collected_as_implicit_input():
    """Direct callable names are not runtime numeric inputs; call values still are."""
    stmts = _parse_source("out = foo(a, b=c)\noutput('out', out)")
    assert _collect_inputs(stmts, extra_builtin_names={"output"}) == ["a", "c"]


def test_attribute_call_receiver_remains_an_implicit_input_candidate():
    """Skipping direct callee names must not suppress attribute-call receivers."""
    stmts = _parse_source("out = obj.info()\noutput('out', out)")
    assert _collect_inputs(stmts, extra_builtin_names={"output"}) == ["obj"]


def test_bare_external_name_is_still_collected():
    """Only the direct-call callee role is excluded from implicit input discovery."""
    stmts = _parse_source("out = foo + 1\noutput('out', out)")
    assert _collect_inputs(stmts, extra_builtin_names={"output"}) == ["foo"]


@pytest.mark.parametrize(
    "source",
    [
        'ID = 42\noutput("x", ID)',
        'def ID(x):\n    return x\ny = ID(1)\noutput("y", y)',
        'def f(ID):\n    return ID\ny = f(1)\noutput("y", y)',
        'from local import thing as ID\ny = 1\noutput("y", y)',
    ],
)
def test_contextual_id_selector_does_not_globally_reserve_the_name(source):
    """ID is ordinary source vocabulary outside raw-node selector positions."""
    _parse_source(source)


def test_contextual_id_callee_is_covered_by_generic_direct_call_discovery_rule():
    """Input discovery skips ID by call role, not by a raw-node-specific name check."""
    stmts = _parse_source('out = ID(name)\noutput("out", out)')
    assert _collect_inputs(stmts, extra_builtin_names={"output"}) == ["name"]


def _runtime_type(_node, _name, _context):
    return TYPE_FLOAT


def test_raw_node_semantics_are_owned_by_typed_builtin_analysis():
    expr = ast.parse('node("ShaderNodeValue", output="Value", typ=Float)', mode="eval").body
    result = analyze_builtin_call("node", expr, {}, _runtime_type)
    assert isinstance(result.result, RuntimeCallResult)
    assert result.result.typ is TYPE_FLOAT
    assert "node" in IR_CAPABLE_BUILTIN_NAMES


def test_raw_node_named_outputs_are_semantic_shape_not_backend_container():
    expr = ast.parse(
        'node("ShaderNodeSeparateXYZ", outputs={"X": Float, "Y": Float})',
        mode="eval",
    ).body
    result = analyze_builtin_call("node", expr, {}, _runtime_type)
    assert isinstance(result.result, NamedOutputsCallResult)
    assert result.result.items == (("X", TYPE_FLOAT), ("Y", TYPE_FLOAT))


def test_raw_node_semantics_reject_malformed_call_before_backend():
    expr = ast.parse('node("FunctionNodeCompare")', mode="eval").body
    with pytest.raises(CompileError):
        analyze_builtin_call("node", expr, {}, _runtime_type)


def test_raw_node_backend_module_has_no_source_ast_adapter():
    assert not hasattr(raw_nodes, "compile_call")
    assert not hasattr(raw_nodes, "_parse_node_call")
    assert not hasattr(raw_nodes, "build_raw_node")


def test_raw_node_semantics_publish_complete_normalized_metadata_record():
    """Omitted raw-node collections become explicit normalized state before semantic lowering."""
    expr = ast.parse('node("ShaderNodeValue", output="Value", typ=Float)', mode="eval").body
    result = analyze_builtin_call("node", expr, {}, _runtime_type)
    options = dict(result.options)
    assert tuple(options) == (
        "bl_idname",
        "props",
        "inputs",
        "raw_output_mode",
        "output",
        "typ",
        "outputs",
    )
    assert options["props"] == ()
    assert options["inputs"] == ()
    assert options["raw_output_mode"] == "SINGLE_OUTPUT"
    assert options["output"] == "Value"
    assert options["typ"] is TYPE_FLOAT
    assert options["outputs"] is None


def test_raw_backend_helper_requires_complete_normalized_metadata_arguments():
    """Backend raw-node construction cannot recreate omitted source metadata containers."""
    from NodeForge.builtins.raw_nodes import build_materialized_raw_node

    with pytest.raises(TypeError, match="props"):
        build_materialized_raw_node(
            object(),
            bl_idname="ShaderNodeValue",
            inputs={},
            output="Value",
            typ=TYPE_FLOAT,
            outputs=None,
        )
    with pytest.raises(TypeError, match="inputs"):
        build_materialized_raw_node(
            object(),
            bl_idname="ShaderNodeValue",
            props={},
            output="Value",
            typ=TYPE_FLOAT,
            outputs=None,
        )


def test_addressable_socket_view_includes_only_ordinary_available_directional_sockets():
    visible = _FakeSocket("A", "A")
    hidden = _FakeSocket("B", "B")
    hidden.hide = True
    hidden.is_inactive = True
    unavailable = _FakeSocket("C", "C", is_unavailable=True)
    virtual = _FakeSocket("", "__extend__", bl_idname="NodeSocketVirtual")
    output = _FakeSocket("Out", "Out", is_output=True)

    assert raw_nodes._addressable_sockets(
        [visible, unavailable, virtual, hidden, output], direction="input"
    ) == (visible, hidden)
    assert raw_nodes._addressable_sockets([visible, output], direction="output") == (output,)


def test_source_socket_resolver_separates_name_position_and_identifier_semantics():
    first = _FakeSocket("Value", "Value")
    unavailable = _FakeSocket("Value", "Value_001", is_unavailable=True)
    scale = _FakeSocket("Scale", "Scale")
    sockets = [first, unavailable, scale]

    assert raw_nodes.resolve_socket(sockets, 1, direction="input", context="test") is scale
    assert raw_nodes.resolve_socket(
        sockets, ("identifier", "Value"), direction="input", context="test"
    ) is first
    assert raw_nodes.resolve_socket(sockets, "Scale", direction="input", context="test") is scale

    with pytest.raises(CompileError, match="unknown input socket name"):
        raw_nodes.resolve_socket(
            [_FakeSocket("Other", "Scale")], "Scale", direction="input", context="test"
        )
    with pytest.raises(CompileError, match="not addressable"):
        raw_nodes.resolve_socket(
            sockets, ("identifier", "Value_001"), direction="input", context="test"
        )
    with pytest.raises(CompileError, match="out of range"):
        raw_nodes.resolve_socket(sockets, 2, direction="input", context="test")


def test_ambiguous_name_fails_with_positional_guidance_instead_of_first_match():
    sockets = [_FakeSocket("Value", "A"), _FakeSocket("Value", "B")]
    with pytest.raises(CompileError, match=r"ambiguous.*positional input 0 or 1"):
        raw_nodes.resolve_socket(sockets, "Value", direction="input", context="test")


def test_identifier_selector_rejects_duplicate_or_virtual_identifier_matches():
    duplicates = [_FakeSocket("A", "same"), _FakeSocket("B", "same")]
    with pytest.raises(CompileError, match="duplicate input socket identifier"):
        raw_nodes.resolve_socket(
            duplicates, ("identifier", "same"), direction="input", context="test"
        )
    with pytest.raises(CompileError, match="not addressable"):
        raw_nodes.resolve_socket(
            [_FakeSocket("", "__extend__", bl_idname="NodeSocketVirtual")],
            ("identifier", "__extend__"),
            direction="input",
            context="test",
        )


def test_physical_socket_identity_uses_blender_proxy_semantics():
    left = _FakeSocket("A", "A", pointer=101)
    proxy = _FakeSocket("A", "A", pointer=101)
    other = _FakeSocket("A", "A", pointer=202)
    assert left is not proxy
    assert raw_nodes._same_blender_ref(left, proxy)
    assert not raw_nodes._same_blender_ref(left, other)


def test_capture_physical_ref_requires_non_empty_identifier():
    socket = _FakeSocket("Value", "Value")
    ref = raw_nodes._capture_physical_socket_ref(socket, direction="input", context="test")
    assert ref == raw_nodes.RawPhysicalSocketRef("Value", "NodeSocketFloat")

    with pytest.raises(CompileError, match="no non-empty identifier"):
        raw_nodes._capture_physical_socket_ref(
            _FakeSocket("Value", ""), direction="input", context="test"
        )


def test_identifierless_structural_coordinates_can_false_positive_but_are_never_identity():
    old = _FakeSocket("Value", "", pointer=11)
    replacement = _FakeSocket("Value", "", pointer=22)
    old_structural = (1, old.name, 0, old.bl_idname)
    replacement_structural = (1, replacement.name, 0, replacement.bl_idname)
    assert old_structural == replacement_structural
    assert not raw_nodes._same_blender_ref(old, replacement)
    with pytest.raises(CompileError, match="no non-empty identifier"):
        raw_nodes._capture_physical_socket_ref(replacement, direction="input", context="test")


def test_raw_build_preflights_all_selectors_before_defaults_links_or_metadata(monkeypatch):
    input_socket = _FakeSocket("Value", "Value", default_value=0.0)
    node = _FakeNode("ShaderNodeValue", inputs=[input_socket], outputs=[_FakeSocket("Value", "ValueOut", is_output=True)])
    group = _FakeGroup()
    monkeypatch.setattr(raw_nodes, "_new_node", lambda *_args, **_kwargs: node)

    with pytest.raises(CompileError, match="unknown output socket name"):
        raw_nodes.build_materialized_raw_node(
            group,
            bl_idname="ShaderNodeValue",
            props={},
            inputs=[("Value", 3.0)],
            output="Missing",
            typ=TYPE_FLOAT,
            outputs=None,
        )

    assert input_socket.default_value == 0.0
    assert group.links == []
    assert raw_nodes.RAW_NODE_PROP not in node


def test_raw_build_rejects_identifierless_declared_socket_before_mutation(monkeypatch):
    input_socket = _FakeSocket("Value", "", default_value=0.0)
    output_socket = _FakeSocket("Value", "Out", is_output=True)
    node = _FakeNode("ShaderNodeValue", inputs=[input_socket], outputs=[output_socket])
    group = _FakeGroup()
    monkeypatch.setattr(raw_nodes, "_new_node", lambda *_args, **_kwargs: node)

    with pytest.raises(CompileError, match="cannot be persisted safely"):
        raw_nodes.build_materialized_raw_node(
            group,
            bl_idname="ShaderNodeValue",
            props={},
            inputs=[(0, 2.0)],
            output=0,
            typ=TYPE_FLOAT,
            outputs=None,
        )

    assert input_socket.default_value == 0.0
    assert group.links == []
    assert raw_nodes.RAW_SCHEMA_VERSION_PROP not in node


def test_raw_build_writes_schema_v2_identifier_refs_only(monkeypatch):
    input_socket = _FakeSocket("Value", "InputValue", default_value=0.0)
    output_socket = _FakeSocket("Value", "OutputValue", is_output=True)
    node = _FakeNode("ShaderNodeValue", inputs=[input_socket], outputs=[output_socket])
    group = _FakeGroup()
    monkeypatch.setattr(raw_nodes, "_new_node", lambda *_args, **_kwargs: node)

    value = raw_nodes.build_materialized_raw_node(
        group,
        bl_idname="ShaderNodeValue",
        props={},
        inputs=[(0, 2.5)],
        output=("identifier", "OutputValue"),
        typ=TYPE_FLOAT,
        outputs=None,
    )

    assert isinstance(value, Value)
    assert input_socket.default_value == 2.5
    assert node[raw_nodes.RAW_SCHEMA_VERSION_PROP] == 2
    input_payload = json.loads(node[raw_nodes.RAW_INPUTS_JSON_PROP])
    output_payload = json.loads(node[raw_nodes.RAW_OUTPUTS_JSON_PROP])
    assert input_payload == [{
        "default": 2.5,
        "mode": raw_nodes.INPUT_LITERAL,
        "socket": {"identifier": "InputValue", "socket_type": "NodeSocketFloat"},
    }]
    assert output_payload == [{"identifier": "OutputValue", "socket_type": "NodeSocketFloat"}]
    assert "name" not in input_payload[0]["socket"]


def test_mixed_selectors_cannot_bind_one_physical_socket_twice(monkeypatch):
    shared = _FakeSocket("Value", "Value", default_value=0.0)
    output = _FakeSocket("Value", "Out", is_output=True)
    node = _FakeNode("ShaderNodeMath", inputs=[shared], outputs=[output])
    group = _FakeGroup()
    monkeypatch.setattr(raw_nodes, "_new_node", lambda *_args, **_kwargs: node)

    with pytest.raises(CompileError, match="two input selectors resolve to the same physical socket"):
        raw_nodes.build_materialized_raw_node(
            group,
            bl_idname="ShaderNodeMath",
            props={},
            inputs=[(0, 1.0), (("identifier", "Value"), 2.0)],
            output=0,
            typ=TYPE_FLOAT,
            outputs=None,
        )
    assert shared.default_value == 0.0


def test_v1_reader_keeps_name_locator_and_v2_reader_rejects_empty_identifier():
    v1 = _FakeNode("ShaderNodeValue")
    v1[raw_nodes.RAW_NODE_PROP] = True
    v1[raw_nodes.RAW_BL_IDNAME_PROP] = "ShaderNodeValue"
    v1[raw_nodes.RAW_PROPS_JSON_PROP] = "{}"
    v1[raw_nodes.RAW_INPUTS_JSON_PROP] = json.dumps([
        {"name": "Value", "mode": raw_nodes.INPUT_LITERAL, "default": 1.0}
    ])
    v1[raw_nodes.RAW_OUTPUTS_JSON_PROP] = json.dumps(["Value"])
    v1[raw_nodes.RAW_MODE_PROP] = raw_nodes.RAW_SINGLE_MODE
    spec = raw_nodes.raw_node_spec(v1)
    assert spec["schema_version"] == 1
    assert spec["inputs"][0]["name"] == "Value"
    assert isinstance(spec["outputs"][0], str)

    v2 = _FakeNode("ShaderNodeValue")
    v2.update(v1)
    v2[raw_nodes.RAW_SCHEMA_VERSION_PROP] = 2
    v2[raw_nodes.RAW_INPUTS_JSON_PROP] = json.dumps([
        {"socket": {"identifier": "", "socket_type": "NodeSocketFloat"}, "mode": raw_nodes.INPUT_LITERAL, "default": 1.0}
    ])
    v2[raw_nodes.RAW_OUTPUTS_JSON_PROP] = json.dumps([
        {"identifier": "Value", "socket_type": "NodeSocketFloat"}
    ])
    with pytest.raises(CompileError, match="non-empty string"):
        raw_nodes.raw_node_spec(v2)



def test_raw_schema_version_requires_exact_supported_integer():
    """Schema generation is fail-closed for value-equal but differently typed metadata."""
    node = _FakeNode("ShaderNodeValue")
    _write_v2_metadata(
        node,
        outputs=[{"identifier": "Value", "socket_type": "NodeSocketFloat"}],
    )
    assert raw_nodes.raw_node_spec(node)["schema_version"] == raw_nodes.RAW_SCHEMA_VERSION
    node[raw_nodes.RAW_SCHEMA_VERSION_PROP] = 2.0
    with pytest.raises(CompileError, match="unsupported raw node metadata schema version"):
        raw_nodes.raw_node_spec(node)

def test_v2_physical_ref_resolution_never_falls_back_to_name():
    ref = raw_nodes.RawPhysicalSocketRef("missing", "NodeSocketFloat")
    sockets = [_FakeSocket("missing", "different")]
    with pytest.raises(CompileError, match="identifier 'missing' is missing"):
        raw_nodes._resolve_physical_socket_ref(sockets, ref, direction="input", context="test")


def test_copy_raw_node_properties_preserves_v1_generation_and_name_locator_semantics():
    src_in = _FakeSocket("Value", "", default_value=1.25)
    src_out = _FakeSocket("Result", "", is_output=True)
    src = _FakeNode("ShaderNodeValue", inputs=[src_in], outputs=[src_out])
    _write_v1_metadata(
        src,
        inputs=[{"name": "Value", "mode": raw_nodes.INPUT_LITERAL, "default": 1.25}],
        outputs=["Result"],
    )

    dst_in = _FakeSocket("Value", "", default_value=0.0)
    dst_out = _FakeSocket("Result", "", is_output=True)
    dst = _FakeNode("ShaderNodeValue", inputs=[dst_in], outputs=[dst_out])
    dst[raw_nodes.RAW_SCHEMA_VERSION_PROP] = raw_nodes.RAW_SCHEMA_VERSION

    raw_nodes.copy_raw_node_properties(src, dst)

    assert dst_in.default_value == 1.25
    assert raw_nodes.RAW_SCHEMA_VERSION_PROP not in dst
    assert raw_nodes.raw_node_spec(dst)["schema_version"] == 1


def test_copy_raw_node_properties_preserves_v2_generation_and_identifier_resolution():
    src_in = _FakeSocket("Old Name", "InputID", default_value=2.0)
    src_out = _FakeSocket("Old Result", "OutputID", is_output=True)
    src = _FakeNode("ShaderNodeValue", inputs=[src_in], outputs=[src_out])
    _write_v2_metadata(
        src,
        inputs=[{
            "socket": {"identifier": "InputID", "socket_type": "NodeSocketFloat"},
            "mode": raw_nodes.INPUT_LITERAL,
            "default": 2.0,
        }],
        outputs=[{"identifier": "OutputID", "socket_type": "NodeSocketFloat"}],
    )

    dst_in = _FakeSocket("Renamed Input", "InputID", default_value=0.0)
    dst_out = _FakeSocket("Renamed Result", "OutputID", is_output=True)
    dst = _FakeNode("ShaderNodeValue", inputs=[dst_in], outputs=[dst_out])

    raw_nodes.copy_raw_node_properties(src, dst)

    assert dst_in.default_value == 2.0
    assert dst[raw_nodes.RAW_SCHEMA_VERSION_PROP] == 2
    spec = raw_nodes.raw_node_spec(dst)
    assert spec["schema_version"] == 2
    assert spec["outputs"][0].identifier == "OutputID"


def test_v2_declared_cutover_uses_identifier_and_never_name_fallback():
    src_out = _FakeSocket("Visible Name", "OutputID", is_output=True)
    src = _FakeNode("ShaderNodeValue", outputs=[src_out])
    _write_v2_metadata(
        src,
        outputs=[{"identifier": "OutputID", "socket_type": "NodeSocketFloat"}],
    )

    dst_exact = _FakeSocket("Renamed", "OutputID", is_output=True)
    dst = _FakeNode("ShaderNodeValue", outputs=[dst_exact])
    assert raw_nodes.resolve_cutover_socket(src, src_out, dst, direction="output") is dst_exact

    dst_wrong = _FakeNode(
        "ShaderNodeValue",
        outputs=[_FakeSocket("Visible Name", "DifferentID", is_output=True)],
    )
    with pytest.raises(CompileError, match="identifier 'OutputID' is missing"):
        raw_nodes.resolve_cutover_socket(src, src_out, dst_wrong, direction="output")


def test_v1_declared_cutover_stays_on_unique_name_compatibility_path():
    src_out = _FakeSocket("Result", "", is_output=True)
    src = _FakeNode("ShaderNodeValue", outputs=[src_out])
    _write_v1_metadata(src, outputs=["Result"])

    dst_out = _FakeSocket("Result", "new-id", is_output=True)
    dst = _FakeNode("ShaderNodeValue", outputs=[dst_out])
    assert raw_nodes.resolve_cutover_socket(src, src_out, dst, direction="output") is dst_out

    ambiguous = _FakeNode(
        "ShaderNodeValue",
        outputs=[
            _FakeSocket("Result", "A", is_output=True),
            _FakeSocket("Result", "B", is_output=True),
        ],
    )
    with pytest.raises(CompileError, match="output socket name 'Result' is ambiguous"):
        raw_nodes.resolve_cutover_socket(src, src_out, ambiguous, direction="output")


def test_undeclared_raw_cutover_socket_retains_generic_physical_position_behavior():
    declared = _FakeSocket("Declared", "DeclaredID", is_output=True)
    manual = _FakeSocket("Manual", "ManualID", is_output=True)
    src = _FakeNode("ShaderNodeValue", outputs=[declared, manual])
    _write_v2_metadata(
        src,
        outputs=[{"identifier": "DeclaredID", "socket_type": "NodeSocketFloat"}],
    )

    dst_declared = _FakeSocket("Declared New", "DeclaredID", is_output=True)
    dst_manual = _FakeSocket("Different Manual", "DifferentManualID", is_output=True)
    dst = _FakeNode("ShaderNodeValue", outputs=[dst_declared, dst_manual])

    assert raw_nodes.resolve_cutover_socket(src, manual, dst, direction="output") is dst_manual


def test_validate_raw_node_after_cutover_uses_schema_specific_locator_and_fails_closed():
    output = _FakeSocket("Renamed", "OutputID", is_output=True)
    node = _FakeNode("ShaderNodeValue", outputs=[output])
    _write_v2_metadata(
        node,
        outputs=[{"identifier": "OutputID", "socket_type": "NodeSocketFloat"}],
    )
    group = _FakeGroup()
    raw_nodes.validate_raw_node_after_cutover(node, group)

    output.identifier = "Corrupted"
    with pytest.raises(CompileError, match="identifier 'OutputID' is missing"):
        raw_nodes.validate_raw_node_after_cutover(node, group)
