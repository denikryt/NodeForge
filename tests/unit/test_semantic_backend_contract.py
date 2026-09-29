"""Exhaustive semantic-to-NodeForge-backend contract before Blender RNA."""

import ast
import importlib
import sys
from types import MappingProxyType, ModuleType, SimpleNamespace

import pytest

from NodeForge.compile_time import CompileTimeSnapshot, CompileTimeState

from NodeForge import blender_ir_lowering
from NodeForge.constants import (
    TYPE_BOOL,
    TYPE_BUNDLE,
    TYPE_FLOAT,
    TYPE_GEOMETRY,
    TYPE_INT,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.compiler_identities import BindingId
from NodeForge.call_resolution import CallableEnvironment
from NodeForge.builtin_call_semantics import IR_CAPABLE_BUILTIN_NAMES
from NodeForge.semantic_analysis import RuntimeBindingSymbol, SemanticEnvironment, analyze_expression, build_semantic_constant_snapshot
from NodeForge.semantic_values import ObjectInfoState, ObjectSemanticId, ObjectSemanticSnapshot
from NodeForge.semantic_lowering import lower_analyzed_expression
from NodeForge.values import ObjectValue, Value


pytestmark = pytest.mark.unit

def _empty_callable_environment():
    """Return an empty immutable callable namespace for non-call semantic tests."""
    return CallableEnvironment(frozenset(), {}, {}, {})


_TYPES = (
    TYPE_FLOAT,
    TYPE_INT,
    TYPE_VECTOR,
    TYPE_BOOL,
    TYPE_GEOMETRY,
    TYPE_MATERIAL,
    TYPE_OBJECT,
    TYPE_STRING,
    TYPE_BUNDLE,
)


class _FakeSocket:
    """Minimal Blender-facing socket accepted by the real NodeForge node helpers."""

    def __init__(self, node=None, index=0, name=""):
        self.node = node
        self.index = index
        self.name = name
        self.default_value = None


class _FakeSockets(list):
    """Socket collection supporting Blender-style integer and name lookup."""

    def __getitem__(self, key):
        if isinstance(key, str):
            for socket in self:
                if socket.name == key:
                    return socket
            raise KeyError(key)
        return super().__getitem__(key)


class _FakeNode:
    """Minimal mutable node object with generic sockets and RNA-like attributes."""

    def __init__(self, bl_idname):
        self.bl_idname = bl_idname
        self.location = (0, 0)
        self.label = ""
        self.operation = None
        self.data_type = None
        self.input_type = None
        self.integer = 0
        self.string = ""
        if bl_idname == "GeometryNodeObjectInfo":
            self.transform_space = "ORIGINAL"
            self.inputs = _FakeSockets([_FakeSocket(self, 0, "Object"), _FakeSocket(self, 1, "As Instance")])
            self.outputs = _FakeSockets([
                _FakeSocket(self, 0, "Geometry"),
                _FakeSocket(self, 1, "Location"),
                _FakeSocket(self, 2, "Rotation"),
                _FakeSocket(self, 3, "Scale"),
            ])
        else:
            self.inputs = _FakeSockets([_FakeSocket(self, i) for i in range(8)])
            self.outputs = _FakeSockets([_FakeSocket(self, i) for i in range(4)])


class _FakeNodes(list):
    """Node collection implementing the subset used by NodeForge.nodes helpers."""

    def new(self, bl_idname):
        node = _FakeNode(bl_idname)
        self.append(node)
        return node


class _FakeLinks(list):
    """Link collection recording connections made by real node helpers."""

    def new(self, from_socket, to_socket):
        self.append((from_socket, to_socket))
        return self[-1]


class _FakeGroup:
    """Small fake graph substrate below the real NodeForge helper layer."""

    def __init__(self):
        self.nodes = _FakeNodes()
        self.links = _FakeLinks()


def _expr(source):
    """Parse one expression fixture."""
    return ast.parse(source, mode="eval").body


def _environment(bindings, *, object_registry=True):
    """Build an immutable environment containing canonical runtime binding facts."""
    runtime_bindings = {
        name: RuntimeBindingSymbol(BindingId("backend-contract", index), typ)
        for index, (name, typ) in enumerate(bindings.items())
    }
    semantic_constants, const_eval_values = build_semantic_constant_snapshot(CompileTimeSnapshot({}))
    object_semantics = None
    if object_registry:
        object_ids = {}
        states = {}
        next_id = 0
        for symbol in runtime_bindings.values():
            if symbol.typ is TYPE_OBJECT:
                object_id = ObjectSemanticId(next_id)
                next_id += 1
                object_ids[symbol.binding_id] = object_id
                states[object_id] = ObjectInfoState()
        object_semantics = ObjectSemanticSnapshot(object_ids, states, next_id)
    return SemanticEnvironment(
        MappingProxyType(runtime_bindings),
        semantic_constants,
        const_eval_values,
        MappingProxyType({}),
        callable_environment=CallableEnvironment(frozenset(IR_CAPABLE_BUILTIN_NAMES), {}, {}, {}),
        object_semantics=object_semantics,
    )


def _accepted_program(source, bindings):
    """Return analyzer-owned IR, or ``None`` for rejected/legacy cases."""
    expr = _expr(source)
    try:
        analysis = analyze_expression(expr, _environment(bindings))
    except CompileError:
        return None
    if analysis is None:
        return None
    return lower_analyzed_expression(expr, analysis)


def _materialize(source, bindings):
    """Execute one analyzer-accepted program through the real NodeForge backend."""
    program = _accepted_program(source, bindings)
    if program is None:
        return False
    group = _FakeGroup()
    runtime_bindings = MappingProxyType({
        BindingId("backend-contract", index): (ObjectValue(_FakeSocket()) if typ == TYPE_OBJECT else Value(_FakeSocket(), typ))
        for index, (_, typ) in enumerate(bindings.items())
    })
    context = blender_ir_lowering.BlenderIRLoweringContext(group, runtime_bindings)
    blender_ir_lowering.lower_expression(context, program)
    return True


def test_exhaustive_analyzer_accepted_unary_shapes_reach_real_node_helpers():
    accepted = 0
    for operator in ("+", "-", "not "):
        for typ in _TYPES:
            if _materialize(f"{operator}a", {"a": typ}):
                accepted += 1
    assert accepted > 0


def test_exhaustive_analyzer_accepted_binary_shapes_reach_real_node_helpers():
    accepted = 0
    for operator in ("+", "-", "*", "/"):
        for left_typ in _TYPES:
            for right_typ in _TYPES:
                if _materialize(f"a {operator} b", {"a": left_typ, "b": right_typ}):
                    accepted += 1
    assert accepted > 0


def test_exhaustive_analyzer_accepted_boolean_shapes_reach_real_node_helpers():
    accepted = 0
    for operator in ("and", "or"):
        for left_typ in _TYPES:
            for right_typ in _TYPES:
                if _materialize(f"a {operator} b", {"a": left_typ, "b": right_typ}):
                    accepted += 1
    assert accepted == 2


def test_exhaustive_analyzer_accepted_comparison_shapes_reach_real_node_helpers():
    accepted = 0
    for operator in ("<", "<=", ">", ">=", "==", "!="):
        for left_typ in _TYPES:
            for right_typ in _TYPES:
                if _materialize(f"a {operator} b", {"a": left_typ, "b": right_typ}):
                    accepted += 1
    assert accepted > 0


def test_exhaustive_analyzer_accepted_conditional_shapes_reach_real_node_helpers():
    accepted = 0
    for condition_typ in _TYPES:
        for true_typ in _TYPES:
            for false_typ in _TYPES:
                if _materialize(
                    "a if flag else b",
                    {"flag": condition_typ, "a": true_typ, "b": false_typ},
                ):
                    accepted += 1
    assert accepted > 0


def test_all_analyzer_accepted_vector_components_reach_real_node_helpers():
    accepted = 0
    for typ in _TYPES:
        for component in ("x", "y", "z"):
            if _materialize(f"a.{component}", {"a": typ}):
                accepted += 1
    assert accepted == 3


def test_migrated_literal_realizations_reach_real_node_helpers():
    for source in ("1", "1.5", "True", '"name"'):
        assert _materialize(source, {})



def test_array_program_result_reconstructs_legacy_python_lists_with_exact_value_identity():
    from NodeForge.compiler_identities import BindingId
    from NodeForge.values import Value

    program = _accepted_program("[a, [b]]", {"a": TYPE_FLOAT, "b": TYPE_FLOAT})
    first = Value(_FakeSocket(), TYPE_FLOAT)
    second = Value(_FakeSocket(), TYPE_FLOAT)
    context = blender_ir_lowering.BlenderIRLoweringContext(
        _FakeGroup(),
        MappingProxyType({
            BindingId("backend-contract", 0): first,
            BindingId("backend-contract", 1): second,
        }),
    )
    result = blender_ir_lowering.lower_expression(context, program)
    assert result[0] is first
    assert result[1][0] is second
    assert blender_ir_lowering.lower_expression(
        blender_ir_lowering.BlenderIRLoweringContext(_FakeGroup(), MappingProxyType({})),
        _accepted_program("[]", {}),
    ) == []


def test_vector_literal_uses_real_combine_xyz_helper_contract():
    from NodeForge.semantic_analysis import build_semantic_constant_snapshot

    expr = _expr("vec")
    constants, const_eval_values = build_semantic_constant_snapshot(CompileTimeSnapshot({"vec": (1, 2, 3)}))
    environment = SemanticEnvironment(
        MappingProxyType({}), constants, const_eval_values, MappingProxyType({}),
        callable_environment=CallableEnvironment(frozenset(IR_CAPABLE_BUILTIN_NAMES), {}, {}, {}),
    )
    program = lower_analyzed_expression(expr, analyze_expression(expr, environment))
    group = _FakeGroup()
    result = blender_ir_lowering.lower_expression(
        blender_ir_lowering.BlenderIRLoweringContext(group, MappingProxyType({})), program
    )
    assert result.typ == TYPE_VECTOR
    combine = next(node for node in group.nodes if node.bl_idname == "ShaderNodeCombineXYZ")
    assert [socket.default_value for socket in combine.inputs[:3]] == [1.0, 2.0, 3.0]


def test_object_property_uses_exact_object_value_and_reuses_object_info_node():
    binding_id = BindingId("backend-contract", 0)
    obj = ObjectValue(_FakeSocket())
    group = _FakeGroup()
    context = blender_ir_lowering.BlenderIRLoweringContext(group, MappingProxyType({binding_id: obj}))
    environment = _environment({"obj": TYPE_OBJECT})

    for source in ("obj.location", "obj.scale"):
        expr = _expr(source)
        program = lower_analyzed_expression(expr, analyze_expression(expr, environment))
        blender_ir_lowering.lower_expression(context, program)

    nodes = [node for node in group.nodes if node.bl_idname == "GeometryNodeObjectInfo"]
    assert len(nodes) == 1
    assert obj._object_info_cache_config == ("ORIGINAL", True)
    assert obj._object_info_outputs is not None

    wrong = blender_ir_lowering.BlenderIRLoweringContext(
        _FakeGroup(), MappingProxyType({binding_id: Value(_FakeSocket(), TYPE_OBJECT)})
    )
    expr = _expr("obj.location")
    program = lower_analyzed_expression(expr, analyze_expression(expr, environment))
    with pytest.raises(CompileError, match="expected ObjectValue"):
        blender_ir_lowering.lower_expression(wrong, program)


def test_hand_constructed_unary_plus_is_rejected_by_backend():
    from NodeForge.semantic_ir import IRBinding, IRProgram, IRUnary, IRValue

    binding_id = BindingId("backend-contract", 0)
    operand = IRValue(0, TYPE_FLOAT)
    output = IRValue(1, TYPE_FLOAT)
    program = IRProgram((IRBinding(operand, 0, binding_id), IRUnary(output, 0, "+", operand)), output)
    context = blender_ir_lowering.BlenderIRLoweringContext(
        _FakeGroup(), MappingProxyType({binding_id: Value(_FakeSocket(), TYPE_FLOAT)})
    )
    with pytest.raises(CompileError, match="unsupported Semantic IR unary operation"):
        blender_ir_lowering.lower_expression(context, program)



def test_core_call_ir_reaches_ast_independent_backend_helpers():
    """Representative field/vector calls materialize from IR without Compiler or AST payloads."""
    assert _materialize("position()", {})
    assert _materialize("length(v)", {"v": TYPE_VECTOR})


def test_object_info_frontend_state_is_embedded_in_property_ir_without_mutating_legacy_state():
    """Migrated Object properties consume explicit frontend configuration, not ObjectValue state."""
    binding_id = BindingId("backend-contract", 0)
    obj = ObjectValue(_FakeSocket())
    group = _FakeGroup()
    environment = _environment({"obj": TYPE_OBJECT})
    expr = _expr('obj.info(transform_space="RELATIVE", as_instance=False).location')
    analysis = analyze_expression(expr, environment)
    program = lower_analyzed_expression(expr, analysis)
    result = blender_ir_lowering.lower_expression(
        blender_ir_lowering.BlenderIRLoweringContext(group, MappingProxyType({binding_id: obj})),
        program,
    )
    assert result.typ == TYPE_VECTOR
    info = next(node for node in group.nodes if node.bl_idname == "GeometryNodeObjectInfo")
    assert info.transform_space == "RELATIVE"
    assert info.inputs[1].default_value is False
    assert obj._object_info_cache_config == ("RELATIVE", False)
    assert obj._object_info_outputs is not None
