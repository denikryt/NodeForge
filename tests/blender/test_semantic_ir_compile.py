"""Blender integration coverage for the initial Semantic IR expression slice."""

from helpers import *

import ast
from types import MappingProxyType, SimpleNamespace

from NodeForge.semantic import body as semantic_body
from NodeForge import compiler as compiler_module
from NodeForge.blender.ir_lowering import BlenderIRLoweringContext, lower_expression as lower_ir_program
from NodeForge.semantic.constants import (
    TYPE_BOOL, TYPE_BUNDLE, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_MATERIAL,
    TYPE_OBJECT, TYPE_STRING, TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.compiler_identities import BindingId
from NodeForge.blender.nodes import _socket_type_for
from NodeForge.semantic.call_resolution import CallableEnvironment
from NodeForge.semantic.compile_time import CompileTimeSnapshot
from NodeForge.semantic.analysis import RuntimeBindingSymbol, SemanticEnvironment, analyze_expression, build_semantic_constant_snapshot
from NodeForge.semantic.lowering import lower_analyzed_expression
from NodeForge.semantic.ir import (
    IRArray, IRBinary, IRBinding, IRBoolBinary, IRCall, IRCompare, IRConditional, IRLiteral, IRObjectProperty, IRUnary,
    IRVectorComponent, IRVectorLiteral,
)
from NodeForge.blender.values import Value, make_value
from NodeForge.blender import group_assembly as group_assembly_module



def _empty_callable_environment():
    """Return an empty immutable callable namespace for non-call semantic tests."""
    return CallableEnvironment(frozenset(), {}, {}, {})

def _nodes(group, bl_idname, operation=None):
    """Return nodes matching one Blender node type and optional operation enum."""
    result = [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]
    if operation is not None:
        result = [node for node in result if getattr(node, "operation", None) == operation]
    return result


def _linked_numeric_inputs(group, node):
    """Return numeric values feeding the first two inputs of one Math node."""
    values = []
    for socket in node.inputs[:2]:
        link = next((item for item in group.links if item.to_node == node and item.to_socket == socket), None)
        check(link is not None, f"Math input {socket.name!r} is unexpectedly unlinked")
        source = link.from_node
        if source.bl_idname == "FunctionNodeInputInt":
            values.append(float(source.integer))
        else:
            values.append(float(link.from_socket.default_value))
    return sorted(values)


def _pointer(value):
    """Return stable in-process identity for a Blender RNA wrapper."""
    try:
        return int(value.as_pointer())
    except Exception:
        return id(value)


def _body_programs(body):
    """Yield expression IR programs embedded in one straight-line IRBody."""
    for statement in body.statements:
        program = getattr(statement, "value", None)
        if hasattr(program, "operations"):
            yield program


def test_semantic_ir_route_materializes_representative_runtime_expressions(monkeypatch):
    calls = []
    original = group_assembly_module.lower_body

    def wrapped(context, body, initial_runtime_bindings, base_depth=1, *, group_input=None):
        for program in _body_programs(body):
            calls.append((base_depth, tuple(type(operation).__name__ for operation in program.operations)))
        return original(
            context, body, initial_runtime_bindings, base_depth, group_input=group_input
        )

    monkeypatch.setattr(group_assembly_module, "lower_body", wrapped)
    group = compile_group(
        '''
a = input_float("A", default=2.0)
b = input_float("B", default=3.0)
v = input_vector("V", default=(1, 2, 3))
flag = input_bool("Flag", default=True)
scalar = a * 2 + b
scaled = v * a
component = scaled.x
cmp = a < b
selected = scalar if flag else component
output("Scalar", scalar)
output("Selected", selected)
output("Comparison", cmp)
''',
        "NFTest_semantic_ir_representative",
    )

    check(calls, "supported expressions did not enter Blender Semantic IR lowering")
    operation_names = {name for _, names in calls for name in names}
    check("IRBinary" in operation_names, "representative arithmetic did not use value-based Semantic IR")
    check("IRVectorComponent" in operation_names, "Vector component did not use value-based Semantic IR")
    check("IRCompare" in operation_names, "comparison did not use value-based Semantic IR")
    check("IRConditional" in operation_names, "conditional did not use value-based Semantic IR")
    check(_nodes(group, "ShaderNodeMath", "MULTIPLY"), "scalar multiply node missing")
    check(_nodes(group, "ShaderNodeVectorMath", "SCALE"), "Vector scale node missing")
    check(_nodes(group, "ShaderNodeSeparateXYZ"), "Separate XYZ node missing")
    check(_nodes(group, "FunctionNodeCompare"), "Compare node missing")
    check(_nodes(group, "GeometryNodeSwitch"), "Switch node missing")



def test_supported_core_root_matrix_compiles_through_permanent_pipeline():
    """Representative supported core bodies compile through the permanent Semantic Body route."""
    cases = [
        (
            "expression",
            'a = input_float("A", default=2.0)\nresult = a * 2.0 + 1.0\noutput("Result", result)',
        ),
        (
            "runtime_if",
            'flag = input_bool("Flag", default=True)\n'
            'a = input_float("A", default=2.0)\n'
            'if flag:\n'
            '    result = a\n'
            'else:\n'
            '    result = a * 2.0\n'
            'output("Result", result)',
        ),
        (
            "literal_if",
            'a = input_float("A", default=2.0)\n'
            'if True:\n'
            '    result = a + 1.0\n'
            'else:\n'
            '    result = a + 2.0\n'
            'output("Result", result)',
        ),
        (
            "repeat",
            'value = input_float("Value", default=0.0)\n'
            'for i in repeat_range(2):\n'
            '    value = value + 1.0\n'
            'output("Value", value)',
        ),
        (
            "array",
            'value = input_float("Value", default=1.0)\n'
            'items = [value, value * 2.0]\n'
            'result = items[1]\n'
            'output("Result", result)',
        ),
        (
            "builder",
            'builder = geometry_builder()\n'
            'builder.add(cube(1.0))\n'
            'for i in repeat_range(2):\n'
            '    builder.add(cube(0.5))\n'
            'output("Geometry", builder.geometry)',
        ),
        (
            "contextual",
            'scale = input_float("Scale", default=2.0)\n'
            'geo = grid(scale, 3)\n'
            'uv = grid_uv()\n'
            'store("uv_x", uv.x)\n'
            'set_position(position() + vector(0, 0, 1))\n'
            'output("Grid", geo)\n'
            'output("UV", uv)',
        ),
        (
            "panel",
            'value = input_float("Value", default=1.0)\n'
            'panel([value], name="Inputs")\n'
            'output("Value", value)',
        ),
        (
            "object_bundle",
            'obj = input_object("Object")\n'
            'obj.info(as_instance=False)\n'
            'state = bundle(value=obj.location.x)\n'
            'value = bundle_get(state, "value", typ=Float)\n'
            'output("Value", value)',
        ),
    ]

    groups = []
    try:
        for suffix, source in cases:
            groups.append(compile_group(source, f"NFTest_core_root_matrix_{suffix}"))
    finally:
        for group in groups:
            if group.name in bpy.data.node_groups:
                bpy.data.node_groups.remove(group)


def test_structural_array_parent_uses_semantic_body_and_preserves_nested_expression_topology(monkeypatch):
    calls = []
    original = group_assembly_module.lower_body

    def wrapped(context, body, initial_runtime_bindings, base_depth=1, *, group_input=None):
        for program in _body_programs(body):
            calls.append((base_depth, tuple(type(operation).__name__ for operation in program.operations)))
        return original(
            context, body, initial_runtime_bindings, base_depth, group_input=group_input
        )

    monkeypatch.setattr(group_assembly_module, "lower_body", wrapped)
    group = compile_group(
        """
a = input_float("A", default=2.0)
items = [a * 2]
result = items[0]
output("Result", result)
""",
        "NFTest_semantic_ir_structural_array_parent",
    )

    semantic_calls = [entry for entry in calls if entry[0] > 0 and "IRBinary" in entry[1]]
    check(semantic_calls, "nested array expression did not reach Semantic Body lowering")
    multiplies = _nodes(group, "ShaderNodeMath", "MULTIPLY")
    check(len(multiplies) == 1, f"expected one nested MULTIPLY, got {len(multiplies)}")

def test_semantic_ir_unary_plus_preserves_socket_identity_and_adds_no_node():
    group = compile_group(
        '''
a = input_float("A", default=2.0)
result = +a
output("Result", result)
''',
        "NFTest_semantic_ir_unary_plus",
    )

    check(not _nodes(group, "ShaderNodeMath"), "unary + created an unexpected Math node")
    check(not _nodes(group, "ShaderNodeVectorMath"), "unary + created an unexpected Vector Math node")
    outputs = _nodes(group, "NodeGroupOutput")
    check(len(outputs) == 1, "expected one Group Output")
    result_links = [
        link
        for link in group.links
        if _pointer(link.to_node) == _pointer(outputs[0]) and link.to_socket.name == "Result"
    ]
    check(len(result_links) == 1, "Result output is not linked exactly once")
    check(
        getattr(result_links[0].from_node, "bl_idname", "") == "NodeGroupInput",
        "unary + did not preserve the input runtime socket identity",
    )


def test_semantic_ir_unary_minus_and_not_preserve_current_topology():
    group = compile_group(
        '''
a = input_float("A", default=2.0)
v = input_vector("V", default=(1, 2, 3))
flag = input_bool("Flag", default=True)
neg_scalar = -a
neg_vector = -v
inverted = not flag
output("Neg Scalar", neg_scalar)
output("Neg Vector", neg_vector)
output("Inverted", inverted)
''',
        "NFTest_semantic_ir_unary_topology",
    )

    subtract = _nodes(group, "ShaderNodeMath", "SUBTRACT")
    scale = _nodes(group, "ShaderNodeVectorMath", "SCALE")
    boolean_not = _nodes(group, "FunctionNodeBooleanMath", "NOT")
    check(len(subtract) == 1, f"expected one scalar SUBTRACT, got {len(subtract)}")
    check(len(scale) == 1, f"expected one Vector SCALE, got {len(scale)}")
    check(len(boolean_not) == 1, f"expected one Boolean NOT, got {len(boolean_not)}")

    helper_values = _nodes(group, "ShaderNodeValue")
    zero_helpers = [node for node in helper_values if abs(float(node.outputs[0].default_value) - 0.0) < 1e-8]
    minus_one_helpers = [node for node in helper_values if abs(float(node.outputs[0].default_value) + 1.0) < 1e-8]
    check(len(zero_helpers) == 1, f"expected one 0.0 unary helper, got {len(zero_helpers)}")
    check(len(minus_one_helpers) == 1, f"expected one -1.0 unary helper, got {len(minus_one_helpers)}")



def test_semantic_ir_comparison_chain_preserves_pairwise_rematerialization():
    group = compile_group(
        '''
a = input_float("A", default=1.0)
b = input_float("B", default=2.0)
c = input_float("C", default=3.0)
result = a < b * 2 < c
output("Result", result)
''',
        "NFTest_semantic_ir_compare_chain",
    )

    multiplies = _nodes(group, "ShaderNodeMath", "MULTIPLY")
    compares = _nodes(group, "FunctionNodeCompare", "LESS_THAN")
    boolean_ands = _nodes(group, "FunctionNodeBooleanMath", "AND")
    check(len(multiplies) == 2, f"expected two repeated MULTIPLY nodes, got {len(multiplies)}")
    check(len(compares) == 2, f"expected two LESS_THAN nodes, got {len(compares)}")
    check(len(boolean_ands) == 1, f"expected one Boolean AND, got {len(boolean_ands)}")

    compare_sources = {
        _pointer(link.from_node)
        for link in group.links
        if link.to_node in compares
    }
    check(
        {_pointer(node) for node in multiplies} <= compare_sources,
        "each repeated middle-expression MULTIPLY must feed a comparison",
    )
    and_sources = {
        _pointer(link.from_node)
        for link in group.links
        if _pointer(link.to_node) == _pointer(boolean_ands[0])
    }
    check(
        and_sources == {_pointer(node) for node in compares},
        "Boolean AND must combine exactly the two comparison results",
    )



def test_semantic_error_keeps_outer_fresh_build_cleanup_boundary():
    before = {_pointer(group) for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and INT"):
        compiler.create_expression_group(
            "flag = input_bool('Flag')\nresult = flag + 1\noutput('Result', result)",
            "NFTest_semantic_ir_failure_cleanup",
        )
    after = {_pointer(group) for group in bpy.data.node_groups}
    check(after == before, "semantic compile failure leaked a fresh node group")



def test_semantic_backend_failure_propagates_and_cleans_fresh_group(monkeypatch):
    """A permanent backend failure propagates while the partial fresh group is removed."""
    before = {_pointer(group) for group in bpy.data.node_groups}
    backend_error = CompileError("controlled Semantic IR backend failure")
    backend_calls = []

    def fail_backend(context, body, initial_runtime_bindings, base_depth=1, *, group_input=None):
        backend_calls.extend(_body_programs(body))
        context.group.nodes.new("ShaderNodeValue")
        raise backend_error

    monkeypatch.setattr(group_assembly_module, "lower_body", fail_backend)

    try:
        compiler.create_expression_group(
            "a = input_float('A')\nresult = a + 1\noutput('Result', result)",
            "NFTest_semantic_ir_backend_failure_cleanup",
        )
    except CompileError as exc:
        check(exc is backend_error, "backend failure was replaced instead of propagated")
    else:
        raise AssertionError("controlled Semantic IR backend failure did not propagate")

    after = {_pointer(group) for group in bpy.data.node_groups}
    check(backend_calls, "supported expression did not commit to Semantic IR backend")
    check(after == before, "semantic backend failure leaked a partially materialized fresh group")

_CONTRACT_TYPES = (
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

_CONTRACT_BINDING_IDS = {}


def _contract_binding_id(name):
    """Return one stable binding identity shared by contract analysis/materialization."""
    local_id = _CONTRACT_BINDING_IDS.setdefault(name, len(_CONTRACT_BINDING_IDS))
    return BindingId("semantic-ir-contract", local_id)


def _contract_environment(bindings):
    """Build one immutable runtime-only environment for backend contract discovery."""
    runtime_bindings = {
        name: RuntimeBindingSymbol(_contract_binding_id(name), typ)
        for name, typ in bindings.items()
    }
    semantic_constants, const_eval_values = build_semantic_constant_snapshot(CompileTimeSnapshot({}))
    return SemanticEnvironment(
        runtime_bindings=MappingProxyType(runtime_bindings),
        constants=semantic_constants,
        const_eval_values=const_eval_values,
        reserved_name_labels=MappingProxyType({}),
        callable_environment=_empty_callable_environment(),
    )


def _dispatch_operation_signature(operation):
    """Return the test-only Semantic-IR input shape seen by backend dispatch."""
    if isinstance(operation, IRLiteral):
        return ("IRLiteral", operation.result.typ)
    if isinstance(operation, IRUnary):
        return ("IRUnary", operation.op, operation.operand.typ, operation.result.typ)
    if isinstance(operation, IRBinary):
        return (
            "IRBinary", operation.op, operation.left.typ, operation.right.typ,
            operation.result.typ,
        )
    if isinstance(operation, IRCompare):
        return (
            "IRCompare", operation.op, operation.left.typ, operation.right.typ,
            operation.result.typ,
        )
    if isinstance(operation, IRBoolBinary):
        return (
            "IRBoolBinary", operation.op, operation.left.typ, operation.right.typ,
            operation.result.typ,
        )
    if isinstance(operation, IRConditional):
        return (
            "IRConditional", operation.condition.typ, operation.true_value.typ,
            operation.result.typ,
        )
    if isinstance(operation, IRVectorComponent):
        return (
            "IRVectorComponent", operation.value.typ, operation.component,
            operation.result.typ,
        )
    if isinstance(operation, IRVectorLiteral):
        return ("IRVectorLiteral", operation.result.typ, operation.components)
    if isinstance(operation, IRObjectProperty):
        return ("IRObjectProperty", operation.value.typ, operation.property_name, operation.result.typ)
    if isinstance(operation, IRBinding):
        return None
    raise AssertionError(f"unexpected contract operation: {type(operation).__name__}")


def _program_dispatch_signature(program):
    """Return only IR fields that are inputs to migrated backend decision logic."""
    result = []
    for operation in program.operations:
        signature = _dispatch_operation_signature(operation)
        if signature is not None:
            result.append(signature)
    return tuple(result)


def _accepted_contract_case(source, bindings):
    """Return emitted IR for an analyzer-accepted source case, else ``None``."""
    expr = ast.parse(source, mode="eval").body
    try:
        analysis = analyze_expression(expr, _contract_environment(bindings))
    except CompileError:
        return None
    if analysis is None:
        return None
    return lower_analyzed_expression(expr, analysis)


def _all_contract_source_cases():
    """Yield finite migrated frontend domains without encoding validity."""
    for source in ("1", "1.5", "True", '"name"'):
        yield source, {}
    for typ in _CONTRACT_TYPES:
        for operator in ("+", "-", "not "):
            yield f"{operator}a", {"a": typ}
    for left_typ in _CONTRACT_TYPES:
        for right_typ in _CONTRACT_TYPES:
            for operator in ("+", "-", "*", "/", "and", "or", "<", "<=", ">", ">=", "==", "!="):
                yield f"a {operator} b", {"a": left_typ, "b": right_typ}
    for condition_typ in _CONTRACT_TYPES:
        for true_typ in _CONTRACT_TYPES:
            for false_typ in _CONTRACT_TYPES:
                yield "a if flag else b", {
                    "flag": condition_typ,
                    "a": true_typ,
                    "b": false_typ,
                }
    for typ in _CONTRACT_TYPES:
        for component in ("x", "y", "z"):
            yield f"a.{component}", {"a": typ}
    for property_name in ("geometry", "location", "rotation", "scale"):
        yield f"a.{property_name}", {"a": TYPE_OBJECT}


def _lower_contract_program_on_real_blender(program, bindings, name):
    """Lower one analyzer-owned program through the explicit context on real Blender RNA."""
    group = bpy.data.node_groups.new(name, "GeometryNodeTree")
    for binding_name, typ in bindings.items():
        group.interface.new_socket(
            name=binding_name,
            in_out="INPUT",
            socket_type=_socket_type_for(typ),
        )
    group_input = group.nodes.new("NodeGroupInput")
    runtime_bindings = MappingProxyType({
        _contract_binding_id(binding_name): make_value(group_input.outputs[binding_name], typ)
        for binding_name, typ in bindings.items()
    })
    context = BlenderIRLoweringContext(group, runtime_bindings)
    result = lower_ir_program(context, program)
    return group, result


def _link_to_socket(group, socket):
    """Return the unique link targeting *socket* in one contract graph."""
    links = [link for link in group.links if _pointer(link.to_socket) == _pointer(socket)]
    check(len(links) == 1, f"expected one link to socket {socket.name!r}, got {len(links)}")
    return links[0]


def _result_link(group):
    """Return the unique link feeding the contract group's ``Result`` output."""
    outputs = _nodes(group, "NodeGroupOutput")
    check(len(outputs) == 1, f"expected one Group Output, got {len(outputs)}")
    result_inputs = [socket for socket in outputs[0].inputs if socket.name == "Result"]
    check(len(result_inputs) == 1, "contract group has no unique Result output socket")
    return _link_to_socket(group, result_inputs[0])


def _check_binding_source(link, binding_name):
    """Assert that one backend operand link comes from the expected group input."""
    check(
        getattr(link.from_node, "bl_idname", "") == "NodeGroupInput",
        f"operand {binding_name!r} is not sourced from the Group Input",
    )
    check(
        link.from_socket.name == binding_name,
        f"operand expected {binding_name!r}, got source socket {link.from_socket.name!r}",
    )


def _binding_name_by_value(program):
    """Map program-local binding value ids to test source labels for topology checks."""
    names_by_id = {_contract_binding_id(name): name for name in _CONTRACT_BINDING_IDS}
    return {
        operation.result.id: names_by_id[operation.binding_id]
        for operation in program.operations
        if isinstance(operation, IRBinding)
    }


def _check_operand_source(group, socket, value, binding_names):
    """Assert a backend operand socket is wired from the IR binding that produced *value*."""
    name = binding_names.get(value.id)
    check(name is not None, f"contract operand v{value.id} is not a direct runtime binding")
    _check_binding_source(_link_to_socket(group, socket), name)


def _expected_compare_data_type(left_type, right_type):
    """Return the Blender Compare mode required by the semantic operand types."""
    if left_type == right_type == TYPE_INT:
        return "INT"
    if left_type in {TYPE_FLOAT, TYPE_INT} and right_type in {TYPE_FLOAT, TYPE_INT}:
        return "FLOAT"
    if left_type == right_type == TYPE_BOOL:
        return "INT"
    if left_type == right_type == TYPE_VECTOR:
        return "VECTOR"
    raise AssertionError(f"unexpected analyzer-accepted comparison types: {left_type}, {right_type}")


def _expected_switch_input_type(result_type):
    """Return the Blender Switch mode corresponding to one Semantic IR result type."""
    return {
        TYPE_FLOAT: "FLOAT",
        TYPE_INT: "INT",
        TYPE_VECTOR: "VECTOR",
        TYPE_BOOL: "BOOLEAN",
        TYPE_GEOMETRY: "GEOMETRY",
        TYPE_STRING: "STRING",
        TYPE_BUNDLE: "BUNDLE",
    }[result_type]


def _check_literal_realization(group, operation, result_link):
    """Validate the concrete Blender realization of one migrated runtime literal."""
    node = result_link.from_node
    if operation.result.typ == TYPE_FLOAT:
        check(node.bl_idname == "ShaderNodeValue", "Float literal did not realize as ShaderNodeValue")
        check(
            abs(float(result_link.from_socket.default_value) - float(operation.value)) < 1e-8,
            "Float literal value changed at the Blender boundary",
        )
        return
    if operation.result.typ == TYPE_INT:
        check(node.bl_idname == "FunctionNodeInputInt", "Int literal did not realize as FunctionNodeInputInt")
        check(int(node.integer) == int(operation.value), "Int literal value changed at the Blender boundary")
        return
    if operation.result.typ == TYPE_STRING:
        check(node.bl_idname == "FunctionNodeInputString", "String literal did not realize as FunctionNodeInputString")
        check(node.string == operation.value, "String literal value changed at the Blender boundary")
        return
    if operation.result.typ == TYPE_BOOL:
        check(node.bl_idname == "FunctionNodeCompare", "Bool literal did not realize through Compare")
        check(node.operation == "NOT_EQUAL", "Bool literal Compare operation changed")
        check(node.data_type == "FLOAT", "Bool literal Compare data_type changed")
        inputs = [_link_to_socket(group, node.inputs[index]) for index in (0, 1)]
        values = [float(link.from_socket.default_value) for link in inputs]
        expected = [1.0 if operation.value else 0.0, 0.0]
        check(values == expected, f"Bool literal helper values changed: {values!r} != {expected!r}")
        return
    raise AssertionError(f"unexpected literal type {operation.result.typ}")


def _check_unary_realization(group, operation, result_link, binding_names):
    """Validate operation, output and operand wiring for one unary IR operation."""
    node = result_link.from_node
    if operation.op == "+":
        _check_binding_source(result_link, binding_names[operation.operand.id])
        return
    if operation.op == "not":
        check(node.bl_idname == "FunctionNodeBooleanMath", "not did not realize as Boolean Math")
        check(node.operation == "NOT", "Boolean NOT operation enum changed")
        _check_operand_source(group, node.inputs[0], operation.operand, binding_names)
        return
    check(operation.op == "-", f"unexpected unary op {operation.op!r}")
    if operation.operand.typ == TYPE_INT:
        check(node.bl_idname == "FunctionNodeIntegerMath", "Int unary - did not realize as Integer Math")
        check(node.operation == "NEGATE", "Int unary - operation enum changed")
        _check_operand_source(group, node.inputs[0], operation.operand, binding_names)
        return
    if operation.operand.typ == TYPE_FLOAT:
        check(node.bl_idname == "ShaderNodeMath", "Float unary - did not realize as Math")
        check(node.operation == "SUBTRACT", "Float unary - operation enum changed")
        zero_link = _link_to_socket(group, node.inputs[0])
        check(zero_link.from_node.bl_idname == "ShaderNodeValue", "Float unary - lost its zero helper")
        check(abs(float(zero_link.from_socket.default_value)) < 1e-8, "Float unary - zero helper changed")
        _check_operand_source(group, node.inputs[1], operation.operand, binding_names)
        return
    check(operation.operand.typ == TYPE_VECTOR, "unexpected analyzer-accepted unary - type")
    check(node.bl_idname == "ShaderNodeVectorMath", "Vector unary - did not realize as Vector Math")
    check(node.operation == "SCALE", "Vector unary - operation enum changed")
    _check_operand_source(group, node.inputs[0], operation.operand, binding_names)
    scale_link = _link_to_socket(group, node.inputs[3])
    check(scale_link.from_node.bl_idname == "ShaderNodeValue", "Vector unary - lost its scale helper")
    check(abs(float(scale_link.from_socket.default_value) + 1.0) < 1e-8, "Vector unary - scale helper changed")


def _check_binary_realization(group, operation, result_link, binding_names):
    """Validate type-directed backend realization and ordered binary operands."""
    node = result_link.from_node
    left_type = operation.left.typ
    right_type = operation.right.typ
    numeric_types = {TYPE_FLOAT, TYPE_INT}
    if left_type in numeric_types and right_type in numeric_types:
        if operation.result.typ == TYPE_INT:
            check(node.bl_idname == "FunctionNodeIntegerMath", "Int binary op did not realize as Integer Math")
            expected_operation = {
                "ADD": "ADD",
                "SUBTRACT": "SUBTRACT",
                "MULTIPLY": "MULTIPLY",
                "FLOOR_DIVIDE": "DIVIDE_FLOOR",
                "MODULO": "FLOORED_MODULO",
            }[operation.op]
            check(node.operation == expected_operation, "Integer Math operation enum differs from Semantic IR")
            _check_operand_source(group, node.inputs[0], operation.left, binding_names)
            _check_operand_source(group, node.inputs[1], operation.right, binding_names)
            return
        check(operation.result.typ == TYPE_FLOAT, "scalar numeric operation produced an unexpected result type")
        if operation.op == "FLOOR_DIVIDE":
            check(node.bl_idname == "ShaderNodeMath" and node.operation == "FLOOR", "Float // final node is not Math FLOOR")
            quotient_link = _link_to_socket(group, node.inputs[0])
            quotient = quotient_link.from_node
            check(quotient.bl_idname == "ShaderNodeMath" and quotient.operation == "DIVIDE", "Float // lost DIVIDE -> FLOOR topology")
            _check_operand_source(group, quotient.inputs[0], operation.left, binding_names)
            _check_operand_source(group, quotient.inputs[1], operation.right, binding_names)
            return
        check(node.bl_idname == "ShaderNodeMath", "Float/mixed binary op did not realize as Math")
        expected_operation = "FLOORED_MODULO" if operation.op == "MODULO" else operation.op
        check(node.operation == expected_operation, "Float/mixed Math operation enum differs from Semantic IR")
        _check_operand_source(group, node.inputs[0], operation.left, binding_names)
        _check_operand_source(group, node.inputs[1], operation.right, binding_names)
        return
    check(operation.result.typ == TYPE_VECTOR, "non-numeric IRBinary produced an unexpected type")
    check(node.bl_idname == "ShaderNodeVectorMath", "Vector binary op did not realize as Vector Math")
    if operation.op in {"ADD", "SUBTRACT"} or (operation.op == "MULTIPLY" and left_type == right_type == TYPE_VECTOR):
        check(node.operation == operation.op, "Vector Math operation enum differs from Semantic IR")
        _check_operand_source(group, node.inputs[0], operation.left, binding_names)
        _check_operand_source(group, node.inputs[1], operation.right, binding_names)
        return
    if operation.op == "MULTIPLY":
        check(node.operation == "SCALE", "scalar/Vector multiplication did not realize as SCALE")
        if left_type == TYPE_VECTOR:
            vector_value, scalar_value = operation.left, operation.right
        else:
            check(right_type == TYPE_VECTOR, "unexpected analyzer-accepted MULTIPLY shape")
            vector_value, scalar_value = operation.right, operation.left
        _check_operand_source(group, node.inputs[0], vector_value, binding_names)
        _check_operand_source(group, node.inputs[3], scalar_value, binding_names)
        return
    check(
        operation.op == "DIVIDE" and left_type == TYPE_VECTOR and right_type in numeric_types,
        "unexpected Vector binary realization",
    )
    check(node.operation == "SCALE", "Vector / scalar final node is not SCALE")
    _check_operand_source(group, node.inputs[0], operation.left, binding_names)
    reciprocal_link = _link_to_socket(group, node.inputs[3])
    reciprocal = reciprocal_link.from_node
    check(reciprocal.bl_idname == "ShaderNodeMath", "Vector / scalar reciprocal did not use Math")
    check(reciprocal.operation == "DIVIDE", "Vector / scalar reciprocal operation changed")
    one_link = _link_to_socket(group, reciprocal.inputs[0])
    check(one_link.from_node.bl_idname == "ShaderNodeValue", "Vector / scalar reciprocal lost its 1.0 helper")
    check(abs(float(one_link.from_socket.default_value) - 1.0) < 1e-8, "Vector / scalar reciprocal helper changed")
    _check_operand_source(group, reciprocal.inputs[1], operation.right, binding_names)


def _check_compare_realization(group, operation, result_link, binding_names):
    """Validate Compare operation, type mode and ordered operand wiring."""
    node = result_link.from_node
    check(node.bl_idname == "FunctionNodeCompare", "IRCompare did not realize as FunctionNodeCompare")
    check(node.operation == operation.op, "Compare operation enum differs from Semantic IR")
    check(
        node.data_type == _expected_compare_data_type(operation.left.typ, operation.right.typ),
        "Compare data_type does not match Semantic IR operand types",
    )
    _check_operand_source(group, node.inputs[0], operation.left, binding_names)
    _check_operand_source(group, node.inputs[1], operation.right, binding_names)


def _check_bool_binary_realization(group, operation, result_link, binding_names):
    """Validate Boolean operation and operand wiring from the complete IR signature."""
    node = result_link.from_node
    check(operation.left.typ == operation.right.typ == operation.result.typ == TYPE_BOOL, "invalid IRBoolBinary types")
    check(node.bl_idname == "FunctionNodeBooleanMath", "IRBoolBinary did not realize as Boolean Math")
    check(node.operation == operation.op, "Boolean Math operation enum differs from Semantic IR")
    _check_operand_source(group, node.inputs[0], operation.left, binding_names)
    _check_operand_source(group, node.inputs[1], operation.right, binding_names)


def _check_conditional_realization(group, operation, result_link, binding_names):
    """Validate Switch mode plus condition/false/true socket ordering."""
    node = result_link.from_node
    check(node.bl_idname == "GeometryNodeSwitch", "IRConditional did not realize as GeometryNodeSwitch")
    check(node.input_type == _expected_switch_input_type(operation.result.typ), "Switch input_type differs from Semantic IR result type")
    _check_operand_source(group, node.inputs[0], operation.condition, binding_names)
    _check_operand_source(group, node.inputs[1], operation.false_value, binding_names)
    _check_operand_source(group, node.inputs[2], operation.true_value, binding_names)


def _check_vector_component_realization(group, operation, result_link, binding_names):
    """Validate Separate XYZ input and the exact component output selected by lowering."""
    node = result_link.from_node
    check(node.bl_idname == "ShaderNodeSeparateXYZ", "IRVectorComponent did not realize as Separate XYZ")
    _check_operand_source(group, node.inputs[0], operation.value, binding_names)
    expected_index = {"x": 0, "y": 1, "z": 2}[operation.component]
    check(
        _pointer(result_link.from_socket) == _pointer(node.outputs[expected_index]),
        f"Separate XYZ selected the wrong output for .{operation.component}",
    )


def _check_program_realization(group, program, result):
    """Assert that the final analyzer-owned IR operation is realized exactly on Blender RNA."""
    semantic_operations = [operation for operation in program.operations if not isinstance(operation, IRBinding)]
    check(semantic_operations, "contract representative has no semantic operation")
    operation = semantic_operations[-1]
    check(operation.result.id == program.result.id, "contract representative final IR operation does not produce program result")
    result_link = SimpleNamespace(from_node=result.socket.node, from_socket=result.socket)
    binding_names = _binding_name_by_value(program)

    if isinstance(operation, IRLiteral):
        _check_literal_realization(group, operation, result_link)
    elif isinstance(operation, IRUnary):
        _check_unary_realization(group, operation, result_link, binding_names)
    elif isinstance(operation, IRBinary):
        _check_binary_realization(group, operation, result_link, binding_names)
    elif isinstance(operation, IRCompare):
        _check_compare_realization(group, operation, result_link, binding_names)
    elif isinstance(operation, IRBoolBinary):
        _check_bool_binary_realization(group, operation, result_link, binding_names)
    elif isinstance(operation, IRConditional):
        _check_conditional_realization(group, operation, result_link, binding_names)
    elif isinstance(operation, IRVectorComponent):
        _check_vector_component_realization(group, operation, result_link, binding_names)
    elif isinstance(operation, IRObjectProperty):
        node = result_link.from_node
        check(node.bl_idname == "GeometryNodeObjectInfo", "IRObjectProperty did not realize as Object Info")
        expected_socket = {"geometry": "Geometry", "location": "Location", "rotation": "Rotation", "scale": "Scale"}[operation.property_name]
        check(result_link.from_socket.name == expected_socket, "Object Info property output changed")
    else:
        raise AssertionError(f"unexpected final contract operation: {type(operation).__name__}")


def test_semantic_backend_dispatch_signatures_realize_on_blender_rna():
    representatives = {}
    for source, bindings in _all_contract_source_cases():
        program = _accepted_contract_case(source, bindings)
        if program is None:
            continue
        signature = _program_dispatch_signature(program)
        if not signature:
            continue
        representatives.setdefault(signature, (source, bindings, program))

    check(representatives, "semantic/backend contract produced no analyzer-accepted representatives")
    for index, (signature, (source, bindings, program)) in enumerate(representatives.items()):
        group, result = _lower_contract_program_on_real_blender(
            program,
            bindings,
            f"NFTest_semantic_backend_rna_{index}",
        )
        _check_program_realization(group, program, result)
        bpy.data.node_groups.remove(group)





def test_structural_arrays_flat_unpack_append_loop_preserves_graph():
    """The semantic frontend realizes the existing array-append graph contract."""
    from expression_characterization.harness import load_manifest, load_case_baseline, run_case, assert_case_result
    case = next(case for case in load_manifest() if case["id"] == "for_unpack_append_compat")
    before = set(bpy.data.node_groups)
    assert_case_result(case["id"], run_case(case), load_case_baseline(case["id"]))
    check(set(bpy.data.node_groups) == before, "array-unpack case cleanup leaked a group")


def test_structural_arrays_runtime_dependent_flat_unpack_preserves_graph():
    """Runtime inputs retain their original connections through flat loop targets."""
    from expression_characterization.harness import load_manifest, load_case_baseline, run_case, assert_case_result
    case = next(case for case in load_manifest() if case["id"] == "for_unpack_runtime_array_compat")
    before = set(bpy.data.node_groups)
    assert_case_result(case["id"], run_case(case), load_case_baseline(case["id"]))
    check(set(bpy.data.node_groups) == before, "runtime-unpack case cleanup leaked a group")


def test_frontend_geometry_builder_core_route_preserves_permanent_topology():
    """Accepted core builder bodies lower through the permanent Semantic Body route."""
    group = compile_group(
        'builder = geometry_builder()\n'
        'items = [cube(1.0), cube(2.0)]\n'
        'for item in items:\n'
        '    builder.add(item)\n'
        'for i in repeat_range(2):\n'
        '    builder.add(cube(0.5))\n'
        'output("Geometry", builder.geometry)',
        "NFTest_frontend_builder_core_route",
    )
    check(len(_nodes(group, "GeometryNodeJoinGeometry")) >= 1, "frontend builder route lost Join Geometry topology")
    check(len(_nodes(group, "GeometryNodeRepeatInput")) == 1, "frontend builder Repeat did not create one Repeat Input")
    bpy.data.node_groups.remove(group)


def test_geometry_builder_and_grid_share_semantic_body_route():
    """Grid and GeometryBuilder share the permanent Semantic Body route."""
    group = compile_group(
        'builder = geometry_builder()\n'
        'builder.add(cube(1.0))\n'
        'grid_geo = grid(2, 2)\n'
        'uv = grid_uv()\n'
        'output("Geometry", builder.geometry)\n'
        'output("UV", uv)',
        "NFTest_builder_grid_semantic_route",
    )
    check(len(_nodes(group, "GeometryNodeMeshCube")) == 1, "builder + grid route changed cube topology")
    check(len(_nodes(group, "GeometryNodeMeshGrid")) == 1, "builder + grid route duplicated Mesh Grid")
    bpy.data.node_groups.remove(group)


def test_semantic_environment_exports_frontend_geometry_builder_binding(monkeypatch):
    """Accepted builder expressions see only frontend hidden BindingId metadata, never a backend builder object."""
    environments = []
    original = semantic_body.analyze_expression

    def wrapped(expr, environment):
        environments.append(environment)
        return original(expr, environment)

    monkeypatch.setattr(semantic_body, "analyze_expression", wrapped)
    group = compile_group(
        """
builder = geometry_builder()
builder.add(cube(1.0))
result = builder.geometry
output("Geometry", result)
""",
        "NFTest_semantic_frontend_builder_binding",
    )
    check(_nodes(group, "GeometryNodeMeshCube"), "GeometryBuilder semantic expression behavior changed")
    builder_envs = [env for env in environments if "builder" in env.builder_bindings]
    check(builder_envs, "GeometryBuilder hidden BindingId was not exported to semantic expression analysis")
    from NodeForge.semantic.runtime_bindings import RuntimeBindingSymbol

    for environment in builder_envs:
        check("builder" not in environment.runtime_bindings, "GeometryBuilder leaked into ordinary runtime binding names")
        check(isinstance(environment.builder_bindings["builder"], BindingId), "builder semantic environment lacks BindingId identity")
        check(
            all(isinstance(symbol, RuntimeBindingSymbol) for symbol in environment.runtime_bindings.values()),
            "semantic runtime environment carried a backend Value instead of frontend binding metadata",
        )
        check(
            all(not hasattr(symbol, "socket") for symbol in environment.runtime_bindings.values()),
            "semantic runtime binding metadata leaked a Blender socket",
        )



def test_permanent_expression_routing_keeps_structures_frontend_owned_and_runtime_forms_in_ir(monkeypatch):
    """Body-owned structure stays frontend-side while runtime expression forms reach typed IR."""
    programs = []
    original_body = group_assembly_module.lower_body

    def wrapped_body(context, body, initial_runtime_bindings, base_depth=1, *, group_input=None):
        programs.extend(_body_programs(body))
        return original_body(
            context, body, initial_runtime_bindings, base_depth, group_input=group_input
        )

    monkeypatch.setattr(group_assembly_module, "lower_body", wrapped_body)

    def compile_and_remove(source, name):
        group = compile_group(source, name)
        bpy.data.node_groups.remove(group)

    compile_and_remove('items = [1, 2]\noutput("Result", items[0])', "NFTest_complete_ir_route_array")
    check(
        not any(isinstance(program.result, IRArray) for program in programs),
        "body-owned structural array leaked through the Blender expression-result boundary",
    )
    check(programs, "indexed structural array produced no permanent runtime expression program")

    programs.clear()
    compile_and_remove('v = vector(1, 2, 3)\noutput("Result", v)', "NFTest_complete_ir_route_vector_literal")
    vector_calls = [
        op
        for program in programs
        for op in program.operations
        if isinstance(op, IRCall) and op.target.name == "vector"
    ]
    check(vector_calls, "known vector() assignment did not remain on the runtime Semantic Call IR route")

    programs.clear()
    compile_and_remove('obj = input_object("Source")\noutput("Result", obj.location)', "NFTest_complete_ir_route_object_property")
    check(any(any(isinstance(op, IRObjectProperty) for op in program.operations) for program in programs), "Object property did not reach IR backend")

    programs.clear()
    compile_and_remove('v = input_vector("V")\noutput("Result", v[1])', "NFTest_complete_ir_route_vector_subscript")
    components = [
        op
        for program in programs
        for op in program.operations
        if isinstance(op, IRVectorComponent)
    ]
    check(any(op.component == "y" for op in components), "Vector subscript did not normalize to IRVectorComponent")

    programs.clear()
    compile_and_remove(
        'v = input_vector("V")\noutput("Result", length(v) + 1)',
        "NFTest_complete_ir_route_call_fallback",
    )
    check(any(any(isinstance(op, IRBinary) for op in program.operations) for program in programs), "Call IR parent did not remain inside Semantic IR as IRBinary")


def test_unused_cyclic_compile_time_constant_does_not_break_expression_compilation():
    """Detached constant snapshots must tolerate unused cyclic compile-time structures."""
    group = compile_group(
        '''
xs = [1]
xs.append(xs)
output("Result", 1)
''',
        "NFTest_complete_ir_unused_cyclic_const",
    )
    check(_nodes(group, "FunctionNodeInputInt"), "unrelated Int output did not compile with unused cyclic constant")
    bpy.data.node_groups.remove(group)

def test_complete_expression_ir_vector_subscript_and_named_vector_topology():
    group = compile_group(
        '''
v = input_vector("V", default=(1, 2, 3))
output("X", v[0])
output("Y", v[1])
output("Z", v[2])
''',
        "NFTest_complete_ir_vector_subscript",
    )
    separates = _nodes(group, "ShaderNodeSeparateXYZ")
    check(len(separates) == 3, "Vector subscripts did not realize through Separate XYZ")
    bpy.data.node_groups.remove(group)

    group = compile_group(
        '''
v = vector(1, 2, 3)
output("Result", v[1])
''',
        "NFTest_complete_ir_named_vector",
    )
    combines = _nodes(group, "ShaderNodeCombineXYZ")
    separates = _nodes(group, "ShaderNodeSeparateXYZ")
    check(len(combines) == 1, "named Vector constant did not use one Combine XYZ")
    check(not _nodes(group, "ShaderNodeValue"), "named Vector constant created extra Value nodes")
    check(len(separates) == 1, "named Vector subscript did not use one Separate XYZ")
    bpy.data.node_groups.remove(group)


def test_complete_expression_ir_arrays_and_unary_plus_preserve_selected_socket_identity():
    group = compile_group(
        '''
a = input_float("A")
b = input_float("B")
output("Selected", (+[a, b])[0])
''',
        "NFTest_complete_ir_array_identity",
    )
    output = _nodes(group, "NodeGroupOutput")[0]
    selected = next(socket for socket in output.inputs if socket.name == "Selected")
    link = _link_to_socket(group, selected)
    check(link.from_node.bl_idname == "NodeGroupInput", "array selection no longer returns the existing binding socket")
    check(link.from_socket.name == "A", "unary-plus array selection changed selected socket identity")
    bpy.data.node_groups.remove(group)


def test_complete_expression_ir_object_properties_reuse_and_info_configuration():
    group = compile_group(
        '''
obj = input_object("Source")
output("Geometry", obj.geometry)
output("Location", obj.location)
output("Rotation", obj.rotation)
output("Scale", obj.scale)
''',
        "NFTest_complete_ir_object_reuse",
    )
    infos = _nodes(group, "GeometryNodeObjectInfo")
    check(len(infos) == 1, "Object properties did not reuse one Object Info node")
    bpy.data.node_groups.remove(group)

    group = compile_group(
        '''
obj = input_object("Source")
info = obj.info(transform_space="RELATIVE", as_instance=False)
output("Location", info.location)
''',
        "NFTest_complete_ir_object_configured",
    )
    info = _nodes(group, "GeometryNodeObjectInfo")[0]
    check(info.transform_space == "RELATIVE", "Object.info transform_space was not honored by IR property lowering")
    as_instance = next(socket for socket in info.inputs if socket.name == "As Instance")
    check(as_instance.default_value is False, "Object.info as_instance was not honored by IR property lowering")
    bpy.data.node_groups.remove(group)


def test_semantic_call_ir_materializes_core_calls_with_existing_layout(monkeypatch):
    """Stateless core calls enter body IR and retain the existing depth-derived placement."""
    calls = []
    original = group_assembly_module.lower_body

    def wrapped(context, body, initial_runtime_bindings, base_depth=1, *, group_input=None):
        for program in _body_programs(body):
            calls.extend(
                operation for operation in program.operations if type(operation).__name__ == "IRCall"
            )
        return original(
            context, body, initial_runtime_bindings, base_depth, group_input=group_input
        )

    monkeypatch.setattr(group_assembly_module, "lower_body", wrapped)
    group = compile_group(
        '''
v = input_vector("V", default=(1, 2, 3))
scale = input_float("Scale", default=2.0)
length_value = length(v)
geo = cube(scale * 2.0)
output("Length", length_value)
output("Geometry", geo)
''',
        "NFTest_semantic_call_ir_core",
    )
    check(calls, "core calls did not emit IRCall operations")
    check({call.target.name for call in calls} >= {"length", "cube"}, "expected length/cube Call IR targets")
    length_nodes = _nodes(group, "ShaderNodeVectorMath", "LENGTH")
    cube_nodes = _nodes(group, "GeometryNodeMeshCube")
    multiply_nodes = _nodes(group, "ShaderNodeMath", "MULTIPLY")
    check(len(length_nodes) == 1, "length() did not preserve one Vector Math node")
    check(len(cube_nodes) == 1, "cube() did not preserve one Mesh Cube node")
    check(len(multiply_nodes) == 1, "cube runtime size expression did not preserve multiply topology")
    check(tuple(length_nodes[0].location) == (240.0, -90.0), f"length() location changed: {tuple(length_nodes[0].location)}")
    check(tuple(cube_nodes[0].location) == (240.0, -90.0), f"cube() location changed: {tuple(cube_nodes[0].location)}")
    check(tuple(multiply_nodes[0].location) == (480.0, -180.0), f"cube() operand depth changed: {tuple(multiply_nodes[0].location)}")
    bpy.data.node_groups.remove(group)


def test_semantic_call_ir_raw_named_outputs_preserve_one_entry_structure_and_selection():
    """outputs= remains structurally named for one output and supports both selectors on the IR path."""
    group = compile_group(
        '''
a = node("ShaderNodeSeparateXYZ", inputs={"Vector": (1, 2, 3)}, outputs={"X": Float}).X
b = node("ShaderNodeSeparateXYZ", inputs={"Vector": (4, 5, 6)}, outputs={"X": Float})["X"]
output("A", a)
output("B", b)
''',
        "NFTest_semantic_call_ir_raw_named_one",
    )
    raw = _nodes(group, "ShaderNodeSeparateXYZ")
    check(len(raw) == 2, f"expected two raw Separate XYZ nodes, got {len(raw)}")
    for node in raw:
        from NodeForge.blender import raw_nodes
        check(raw_nodes.is_raw_node(node), "one-entry outputs= lost raw-node metadata")
    bpy.data.node_groups.remove(group)

    expect_compile_error(
        '''
r = node("ShaderNodeSeparateXYZ", inputs={"Vector": (1, 2, 3)}, outputs={"X": Float})
output("X", r)
''',
        "NFTest_semantic_call_ir_raw_named_unselected",
    )


def test_semantic_call_ir_raw_runtime_operand_is_created_before_raw_node_by_authorized_exception():
    """Raw-node insertion order may follow dependency-ordered IR while graph semantics stay unchanged."""
    group = compile_group(
        '''
mask = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "GREATER_THAN"},
    inputs={"A": position().z, "B": 0.5},
    output="Result",
    typ=Bool,
)
output("Mask", mask)
''',
        "NFTest_semantic_call_ir_raw_order",
    )
    nodes = list(group.nodes)
    pos_index = next(i for i, node in enumerate(nodes) if node.bl_idname == "GeometryNodeInputPosition")
    sep_index = next(i for i, node in enumerate(nodes) if node.bl_idname == "ShaderNodeSeparateXYZ")
    raw_index = next(i for i, node in enumerate(nodes) if node.bl_idname == "FunctionNodeCompare")
    check(pos_index < raw_index and sep_index < raw_index, "authorized dependency-first raw-node order was not realized")
    raw = nodes[raw_index]
    check(raw.operation == "GREATER_THAN", "raw-node property changed under insertion-order exception")
    bpy.data.node_groups.remove(group)


def test_semantic_call_ir_raw_mixed_invalid_prefers_frontend_operand_error():
    """Semantic Call IR permits operand diagnostics before independent Blender raw-node errors."""
    try:
        compile_group(
            '''
x = node("NoSuchNode", inputs={"A": no_such_function()}, output="Result", typ=Bool)
output("X", x)
''',
            "NFTest_semantic_call_ir_raw_mixed_invalid",
        )
    except CompileError as exc:
        check(str(exc) == "Unsupported function: no_such_function", f"unexpected mixed-invalid precedence: {exc}")
    else:
        raise AssertionError("mixed-invalid raw node unexpectedly compiled")


def test_semantic_call_ir_grid_context_preserves_shared_mesh_and_fresh_input_sockets():
    """Semantic grid context keeps one Mesh Grid while explicit input labels remain independent."""
    group = compile_group(
        '''
geo = grid(4, 3)
uv = grid_uv()
a = input_float("Scale", default=2.0)
b = input_float("Scale", default=2.0)
output("Geometry", geo)
output("UV", uv)
output("Sum", a + b)
''',
        "NFTest_semantic_call_ir_grid_context",
    )
    check(len(_nodes(group, "GeometryNodeMeshGrid")) == 1, "grid/grid_uv Semantic IR lost shared grid state")
    scale_items = [item for item in group.interface.items_tree if getattr(item, "item_type", "") == "SOCKET" and item.name == "Scale"]
    check(len(scale_items) == 2, f"repeated input_float did not create two Scale sockets: {len(scale_items)}")
    bpy.data.node_groups.remove(group)


def test_semantic_call_ir_tuple_result_indexing_reaches_blender_without_legacy_result_coercion():
    """capture_attribute tuple selection maps one declared IR result back to its exact backend Value."""
    group = compile_group(
        '''
geo = cube(1.0)
value = position().x
captured = capture_attribute(geo, value)[1]
output("Captured", captured)
''',
        "NFTest_semantic_call_ir_capture_tuple",
    )
    check(len(_nodes(group, "GeometryNodeCaptureAttribute")) == 1, "capture_attribute Call IR node missing")
    bpy.data.node_groups.remove(group)


def test_contextual_group_semantic_route_preserves_store_set_position_and_grid_topology():
    """Contextual core syntax preserves physical topology on the permanent route."""
    group = compile_group(
        'scale = input_float("Scale")\n'
        'geo = grid(scale, 3)\n'
        'uv = grid_uv()\n'
        'store("first", uv.x)\n'
        'set_position(position() + vector(0, 0, 1))\n'
        'store("second", uv.y)\n'
        'output("Grid", geo)\n'
        'output("UV", uv)',
        "NFTest_contextual_group_semantic_route",
    )
    grids = _nodes(group, "GeometryNodeMeshGrid")
    stores = _nodes(group, "GeometryNodeStoreNamedAttribute")
    positions = _nodes(group, "GeometryNodeSetPosition")
    check(len(grids) == 1, f"expected one Mesh Grid, got {len(grids)}")
    check(len(stores) == 2, f"expected two Store Named Attribute nodes, got {len(stores)}")
    check(len(positions) == 1, f"expected one Set Position node, got {len(positions)}")
    for store in stores:
        check(store.domain == "POINT", "omitted store domain did not remain POINT")
        check(not store.inputs["Selection"].is_linked, "omitted store selection unexpectedly linked")
        check(bool(store.inputs["Selection"].default_value) is True, "omitted store selection was not explicitly True")
    position = positions[0]
    check(not position.inputs["Selection"].is_linked, "omitted set_position selection unexpectedly linked")
    check(bool(position.inputs["Selection"].default_value) is True, "omitted set_position selection was not explicitly True")
    check(grids[0].inputs["Vertices Y"].default_value == 3, "constant grid height was not written as a socket default")
    value_nodes = _nodes(group, "ShaderNodeValue") + _nodes(group, "FunctionNodeInputInt")
    check(not any(getattr(node, "label", "") in {"3", "3.0"} for node in value_nodes), "grid constant created a standalone Value node")
    bpy.data.node_groups.remove(group)


def test_source_ordered_ct_handoff_keeps_future_assignment_out_of_earlier_runtime_rhs():
    """A later erased CT assignment must not change an earlier residual Math input."""
    group = compile_group(
        'a = 1.0\n'
        'b = a + 1.0\n'
        'a = 10.0\n'
        'output("B", b)\n',
        "NFTest_source_order_future_fact",
    )
    adds = _nodes(group, "ShaderNodeMath", "ADD")
    check(len(adds) == 1, "expected one ADD node for b = a + 1")
    values = _linked_numeric_inputs(group, adds[0])
    check(values == [1.0, 1.0], f"earlier b RHS observed a future CT value: {values!r}")


def test_source_ordered_ct_handoff_runtime_if_materializes_erased_seed_value():
    """Runtime branches use the erased source-order seed rather than a residual seed assignment."""
    group = compile_group(
        'x = 0.0\n'
        'flag = input_bool("Flag")\n'
        'if flag:\n'
        '    x = x + 1.0\n'
        'else:\n'
        '    x = x + 2.0\n'
        'output("X", x)\n',
        "NFTest_source_order_runtime_if_seed",
    )
    adds = _nodes(group, "ShaderNodeMath", "ADD")
    check(len(adds) == 2, "expected one ADD node per runtime-if branch")
    defaults = sorted(
        tuple(_linked_numeric_inputs(group, node))
        for node in adds
    )
    check(defaults == [(0.0, 1.0), (0.0, 2.0)], f"runtime-if branches lost the erased seed: {defaults!r}")
    check(len(_nodes(group, "GeometryNodeSwitch")) == 1, "runtime if did not materialize one Switch")


def test_source_ordered_ct_handoff_self_reference_uses_old_target_value():
    """Runtime RHS lowering happens before the assignment publishes its new CT target fact."""
    group = compile_group(
        'x = 1.0\n'
        'x = x + 1.0\n'
        'output("X", x)\n',
        "NFTest_source_order_self_reference",
    )
    adds = _nodes(group, "ShaderNodeMath", "ADD")
    check(len(adds) == 1, "expected one ADD node for self-referential assignment")
    values = _linked_numeric_inputs(group, adds[0])
    check(values == [1.0, 1.0], f"self-reference observed the post-assignment value: {values!r}")


def test_source_ordered_ct_handoff_mutable_state_does_not_mutate_earlier_input_default():
    """A later erased append cannot change an earlier input declaration default."""
    group = compile_group(
        'items = [1]\n'
        'x = input_float("X", default=len(items))\n'
        'items.append(2)\n'
        'output("X", x)\n',
        "NFTest_source_order_mutable_default",
    )
    defaults = [
        item.default_value
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET"
        and getattr(item, "in_out", None) == "INPUT"
        and getattr(item, "name", None) == "X"
    ]
    check(defaults == [1.0], f"later append changed earlier input default: {defaults!r}")


def test_source_ordered_ct_handoff_compile_time_owned_seed_reaches_runtime_if_branches():
    """An erased len() seed is materialized as 3 in both runtime-if branches."""
    group = compile_group(
        'x = len([1, 2, 3])\n'
        'flag = input_bool("Flag")\n'
        'if flag:\n'
        '    x = x + 1.0\n'
        'else:\n'
        '    x = x + 2.0\n'
        'output("X", x)\n',
        "NFTest_source_order_ct_owned_seed",
    )
    adds = _nodes(group, "ShaderNodeMath", "ADD")
    check(len(adds) == 2, "expected one ADD node per runtime-if branch")
    defaults = sorted(tuple(_linked_numeric_inputs(group, node)) for node in adds)
    check(defaults == [(1.0, 3.0), (2.0, 3.0)], f"len() seed was not replayed in source order: {defaults!r}")


def test_source_ordered_ct_handoff_literal_ordinary_if_stays_runtime():
    """Source-ordered compile-time knowledge does not prune an ordinary literal-condition if."""
    group = compile_group(
        'x = 1.0\n'
        'if True:\n'
        '    x = x + 1.0\n'
        'else:\n'
        '    x = x + 2.0\n'
        'output("X", x)\n',
        "NFTest_source_order_literal_if_runtime",
    )
    adds = _nodes(group, "ShaderNodeMath", "ADD")
    switches = _nodes(group, "GeometryNodeSwitch")
    check(len(adds) == 2, f"expected both literal-if branch ADD nodes, found {len(adds)}")
    check(len(switches) == 1, f"expected one literal-if runtime Switch, found {len(switches)}")
    defaults = sorted(tuple(_linked_numeric_inputs(group, node)) for node in adds)
    check(defaults == [(1.0, 1.0), (1.0, 2.0)], f"source-order literal-if seed changed: {defaults!r}")


def test_evaluation_modes_instance_on_points_static_and_runtime_mixed_options_preserve_backend_shape():
    static_group = compile_group(
        '''
geo = instance_on_points(
    cube(0.5),
    points(2),
    scale=0.5,
    rotation=vector(0.0, 0.0, 0.25),
    realize=False,
)
output("Geometry", geo)
''',
        "NFTest_evaluation_modes_instance_static",
    )
    static_nodes = _nodes(static_group, "GeometryNodeInstanceOnPoints")
    check(len(static_nodes) == 1, "expected one static Instance on Points node")
    static_node = static_nodes[0]
    check(not static_node.inputs["Scale"].is_linked, "static scale unexpectedly became runtime")
    check(
        tuple(round(float(v), 6) for v in static_node.inputs["Scale"].default_value) == (0.5, 0.5, 0.5),
        "static scale default changed",
    )
    check(not static_node.inputs["Rotation"].is_linked, "static rotation unexpectedly became runtime")
    bpy.data.node_groups.remove(static_group)

    runtime_group = compile_group(
        '''
scale = input_float("Scale", default=0.5)
rotation = input_vector("Rotation", default=(0.0, 0.0, 0.25))
geo = instance_on_points(cube(0.5), points(2), scale=scale, rotation=rotation, realize=False)
output("Geometry", geo)
''',
        "NFTest_evaluation_modes_instance_runtime",
    )
    runtime_nodes = _nodes(runtime_group, "GeometryNodeInstanceOnPoints")
    check(len(runtime_nodes) == 1, "expected one runtime Instance on Points node")
    runtime_node = runtime_nodes[0]
    check(runtime_node.inputs["Scale"].is_linked, "runtime scale was not linked")
    check(runtime_node.inputs["Rotation"].is_linked, "runtime rotation was not linked")
    bpy.data.node_groups.remove(runtime_group)


def test_evaluation_modes_transform_static_and_runtime_mixed_options_preserve_backend_shape():
    static_group = compile_group(
        '''
geo = transform(
    cube(1.0),
    translation=vector(1.0, 2.0, 3.0),
    scale=2.0,
    rotation=vector(0.0, 0.0, 0.25),
)
output("Geometry", geo)
''',
        "NFTest_evaluation_modes_transform_static",
    )
    static_nodes = _nodes(static_group, "GeometryNodeTransform")
    check(len(static_nodes) == 1, "expected one static Transform Geometry node")
    static_node = static_nodes[0]
    check(not static_node.inputs["Translation"].is_linked, "static translation unexpectedly became runtime")
    check(not static_node.inputs["Scale"].is_linked, "static transform scale unexpectedly became runtime")
    check(not static_node.inputs["Rotation"].is_linked, "static transform rotation unexpectedly became runtime")
    bpy.data.node_groups.remove(static_group)

    runtime_group = compile_group(
        '''
translation = input_vector("Translation", default=(1.0, 2.0, 3.0))
scale = input_float("Scale", default=2.0)
rotation = input_vector("Rotation", default=(0.0, 0.0, 0.25))
geo = transform(cube(1.0), translation=translation, scale=scale, rotation=rotation)
output("Geometry", geo)
''',
        "NFTest_evaluation_modes_transform_runtime",
    )
    runtime_nodes = _nodes(runtime_group, "GeometryNodeTransform")
    check(len(runtime_nodes) == 1, "expected one runtime Transform Geometry node")
    runtime_node = runtime_nodes[0]
    check(runtime_node.inputs["Translation"].is_linked, "runtime translation was not linked")
    check(runtime_node.inputs["Scale"].is_linked, "runtime transform scale was not linked")
    check(runtime_node.inputs["Rotation"].is_linked, "runtime transform rotation was not linked")
    bpy.data.node_groups.remove(runtime_group)


def test_transform_omitted_overrides_remain_explicit_semantic_omissions():
    """Omitted transform options reach Blender as untouched native identity sockets."""
    group = compile_group(
        'geo = transform(cube(1.0))\noutput("Geometry", geo)',
        "NFTest_transform_omitted_overrides",
    )
    nodes = _nodes(group, "GeometryNodeTransform")
    check(len(nodes) == 1, "expected one Transform Geometry node")
    node = nodes[0]
    for name in ("Translation", "Rotation", "Scale"):
        check(not node.inputs[name].is_linked, f"omitted {name} unexpectedly linked")
    bpy.data.node_groups.remove(group)


def test_evaluation_modes_invalid_static_mixed_options_fail_without_publishing_group():
    cases = [
        (
            'geo = instance_on_points(cube(0.5), points(2), scale="bad", realize=False)\noutput("Geometry", geo)',
            "NFTest_evaluation_modes_invalid_instance_scale",
            "instance_on_points scale= expects Float/Int or Vector",
        ),
        (
            'geo = transform(cube(1.0), rotation="bad")\noutput("Geometry", geo)',
            "NFTest_evaluation_modes_invalid_transform_rotation",
            "rotation= must be Vector in radians",
        ),
    ]
    for source, name, message in cases:
        old = bpy.data.node_groups.get(name)
        if old is not None:
            bpy.data.node_groups.remove(old)
        try:
            compile_group(source, name)
        except CompileError as exc:
            check(str(exc) == message, f"unexpected declarative evaluation-mode semantic diagnostic: {exc}")
        else:
            raise AssertionError(f"{name} unexpectedly compiled")
        check(bpy.data.node_groups.get(name) is None, f"failed declarative evaluation-mode compile published {name}")


def test_set_position_omitted_selection_is_explicit_select_all():
    group = compile_group('''
geo = grid(2, 2)
geo = set_position(geo, position() + vector(0.0, 0.0, 1.0))
output("Geometry", geo)
''', "NFTest_set_position_selection_default")
    nodes = [node for node in group.nodes if node.bl_idname == "GeometryNodeSetPosition"]
    check(len(nodes) == 1, f"expected one Set Position node, got {len(nodes)}")
    selection = nodes[0].inputs.get("Selection")
    check(selection is not None, "Set Position Selection input missing")
    check(not selection.is_linked, "omitted Set Position selection unexpectedly linked")
    check(bool(selection.default_value) is True, "omitted Set Position selection is not explicitly True")


def test_runtime_control_flow_terrain_erosion_repeat_local_delta_merges_without_becoming_repeat_state():
    """Evaluate a terrain-like Repeat where only height is carried and delta converges inside the iteration."""
    group = compile_group(
        '''
height = 10.0
flag = input_bool("Flag", default=True)
for i in repeat_range(2):
    capacity = height * 0.1
    if flag:
        delta = capacity * 0.5
    else:
        delta = -capacity * 0.25
    height = height + delta
output("Geometry", point(vector(height, 0.0, 0.0)))
''',
        "NFTest_runtime_control_flow_terrain_erosion",
    )

    repeat_outputs = _nodes(group, "GeometryNodeRepeatOutput")
    check(len(repeat_outputs) == 1, f"expected one Repeat Output, found {len(repeat_outputs)}")
    repeat_names = [item.name for item in repeat_outputs[0].repeat_items]
    check("height" in repeat_names, f"height is not Repeat-carried: {repeat_names!r}")
    check("capacity" not in repeat_names, f"capacity unexpectedly became Repeat state: {repeat_names!r}")
    check("delta" not in repeat_names, f"delta unexpectedly became Repeat state: {repeat_names!r}")

    switches = _nodes(group, "GeometryNodeSwitch")
    check(len(switches) == 1, f"expected exactly one nested delta Switch, found {len(switches)}")
    check(switches[0].input_type == "FLOAT", f"delta Switch type changed: {switches[0].input_type!r}")

    mesh = bpy.data.meshes.new("NFTest_runtime_control_flow_terrain_erosion_Mesh")
    obj = bpy.data.objects.new("NFTest_runtime_control_flow_terrain_erosion_Object", mesh)
    bpy.context.collection.objects.link(obj)
    modifier = obj.modifiers.new("NodeForge", "NODES")
    modifier.node_group = group

    def evaluated_x(flag_value):
        """Evaluate the generated point position for one runtime branch selection."""
        _set_modifier_input(modifier, group, "Flag", flag_value)
        obj.update_tag()
        bpy.context.view_layer.update()
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        evaluated = obj.evaluated_get(depsgraph)
        evaluated_mesh = evaluated.to_mesh()
        try:
            check(len(evaluated_mesh.vertices) == 1, "terrain regression must evaluate to one point")
            return float(evaluated_mesh.vertices[0].co.x)
        finally:
            evaluated.to_mesh_clear()

    try:
        true_x = evaluated_x(True)
        false_x = evaluated_x(False)
        check(abs(true_x - 11.025) < 1e-4, f"true branch erosion result changed: {true_x!r}")
        check(abs(false_x - 9.50625) < 1e-4, f"false branch erosion result changed: {false_x!r}")
    finally:
        if bpy.data.objects.get(obj.name) is obj:
            bpy.data.objects.remove(obj, do_unlink=True)
        if bpy.data.meshes.get(mesh.name) is mesh:
            bpy.data.meshes.remove(mesh, do_unlink=True)
        if bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)
