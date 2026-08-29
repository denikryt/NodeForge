"""Blender integration coverage for the initial Semantic IR expression slice."""

from helpers import *

import ast
from types import MappingProxyType, SimpleNamespace

from NodeForge import expression_compiler
from NodeForge.blender_ir_lowering import BlenderIRLoweringContext, lower_expression as lower_ir_program
from NodeForge.constants import (
    TYPE_BOOL, TYPE_BUNDLE, TYPE_FLOAT, TYPE_GEOMETRY, TYPE_INT, TYPE_MATERIAL,
    TYPE_OBJECT, TYPE_STRING, TYPE_VECTOR,
)
from NodeForge.errors import CompileError
from NodeForge.compiler_identities import BindingId
from NodeForge.nodes import _socket_type_for
from NodeForge.semantic_analysis import RuntimeBindingSymbol, SemanticEnvironment, analyze_expression
from NodeForge.semantic_lowering import lower_analyzed_expression
from NodeForge.semantic_ir import (
    IRBinary, IRBinding, IRBoolBinary, IRCompare, IRConditional, IRLiteral, IRUnary,
    IRVectorComponent,
)
from NodeForge.values import Value


def _nodes(group, bl_idname, operation=None):
    """Return nodes matching one Blender node type and optional operation enum."""
    result = [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]
    if operation is not None:
        result = [node for node in result if getattr(node, "operation", None) == operation]
    return result


def _pointer(value):
    """Return stable in-process identity for a Blender RNA wrapper."""
    try:
        return int(value.as_pointer())
    except Exception:
        return id(value)


def test_semantic_ir_route_materializes_representative_runtime_expressions(monkeypatch):
    calls = []
    original = expression_compiler.lower_ir_expression

    def wrapped(comp, program, base_depth=0):
        calls.append((base_depth, tuple(type(operation).__name__ for operation in program.operations)))
        return original(comp, program, base_depth)

    monkeypatch.setattr(expression_compiler, "lower_ir_expression", wrapped)
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



def test_semantic_ir_child_reached_through_legacy_parent_preserves_nonzero_base_depth(monkeypatch):
    calls = []
    original = expression_compiler.lower_ir_expression

    def wrapped(comp, program, base_depth=0):
        calls.append((base_depth, tuple(type(operation).__name__ for operation in program.operations)))
        return original(comp, program, base_depth)

    monkeypatch.setattr(expression_compiler, "lower_ir_expression", wrapped)
    group = compile_group(
        """
a = input_float("A", default=2.0)
items = [a * 2]
result = items[0]
output("Result", result)
""",
        "NFTest_semantic_ir_legacy_parent_depth",
    )

    nested_calls = [entry for entry in calls if entry[0] > 0 and "IRBinary" in entry[1]]
    check(nested_calls, "IR-owned child under a legacy list parent did not enter with non-zero base depth")
    multiplies = _nodes(group, "ShaderNodeMath", "MULTIPLY")
    check(len(multiplies) == 1, f"expected one nested MULTIPLY, got {len(multiplies)}")
    check(abs(float(multiplies[0].location.x) - 240.0) < 1e-6, "nested IR child x placement changed")
    check(abs(float(multiplies[0].location.y) + 90.0) < 1e-6, "nested IR child y placement changed")

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


def test_semantic_ir_unary_minus_and_not_preserve_current_topology_and_placement():
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

    expected_x, expected_y = 240.0, -90.0
    for node in (subtract[0], scale[0], boolean_not[0]):
        check(abs(float(node.location.x) - expected_x) < 1e-6, "unary operator x placement changed")
        check(abs(float(node.location.y) - expected_y) < 1e-6, "unary operator y placement changed")
    for node in (zero_helpers[0], minus_one_helpers[0]):
        check(abs(float(node.location.x) - expected_x) < 1e-6, "unary helper x placement changed")
        check(abs(float(node.location.y) - (expected_y - 40.0)) < 1e-6, "unary helper y placement changed")


def test_semantic_ir_comparison_chain_preserves_pairwise_rematerialization_and_layout():
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

    for node in compares + boolean_ands:
        check(abs(float(node.location.x) - 240.0) < 1e-6, "comparison-chain operator x placement changed")
        check(abs(float(node.location.y) + 90.0) < 1e-6, "comparison-chain operator y placement changed")
    for node in multiplies:
        check(abs(float(node.location.x) - 480.0) < 1e-6, "middle expression was not lowered at depth + 1")
        check(abs(float(node.location.y) + 180.0) < 1e-6, "middle expression y placement changed")


def test_semantic_error_keeps_outer_fresh_build_cleanup_boundary():
    before = {_pointer(group) for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match="Unsupported operation between BOOL and FLOAT"):
        compiler._make_group(
            "flag = input_bool('Flag')\nresult = flag + 1\noutput('Result', result)",
            "NFTest_semantic_ir_failure_cleanup",
        )
    after = {_pointer(group) for group in bpy.data.node_groups}
    check(after == before, "semantic compile failure leaked a fresh node group")



def test_semantic_backend_failure_does_not_retry_legacy_and_cleans_fresh_group(monkeypatch):
    """Keep backend failure committed to IR while removing the partial fresh group."""
    before = {_pointer(group) for group in bpy.data.node_groups}
    backend_error = CompileError("controlled Semantic IR backend failure")
    legacy_calls = []
    backend_calls = []
    original_analyze = expression_compiler.analyze_expression

    def checked_analyze(expr, environment):
        analysis = original_analyze(expr, environment)
        if isinstance(expr, ast.BinOp):
            check(analysis is not None, "backend-failure fixture did not pass semantic analysis")
        return analysis

    def fail_backend(context, program, base_depth=0):
        backend_calls.append(program)
        context.group.nodes.new("ShaderNodeValue")
        raise backend_error

    def legacy_math(*args, **kwargs):
        legacy_calls.append("math")
        raise AssertionError("legacy AST binary lowering ran after semantic backend failure")

    monkeypatch.setattr(expression_compiler, "analyze_expression", checked_analyze)
    monkeypatch.setattr(expression_compiler, "lower_ir_expression", fail_backend)
    monkeypatch.setattr(expression_compiler, "_math", legacy_math)

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
    check(legacy_calls == [], "semantic backend failure retried the legacy AST dispatcher")
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
    return SemanticEnvironment(
        MappingProxyType(runtime_bindings),
        frozenset(),
        MappingProxyType({}),
        frozenset(),
        MappingProxyType({}),
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
        _contract_binding_id(binding_name): Value(group_input.outputs[binding_name], typ)
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
    """Return the Blender Compare mode required by the existing semantic operand types."""
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
    if operation.operand.typ in {TYPE_FLOAT, TYPE_INT}:
        check(node.bl_idname == "ShaderNodeMath", "numeric unary - did not realize as Math")
        check(node.operation == "SUBTRACT", "numeric unary - operation enum changed")
        zero_link = _link_to_socket(group, node.inputs[0])
        check(zero_link.from_node.bl_idname == "ShaderNodeValue", "numeric unary - lost its zero helper")
        check(abs(float(zero_link.from_socket.default_value)) < 1e-8, "numeric unary - zero helper changed")
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
    """Validate exact backend operation and operand ordering for one binary IR operation."""
    node = result_link.from_node
    left_type = operation.left.typ
    right_type = operation.right.typ
    if left_type in {TYPE_FLOAT, TYPE_INT} and right_type in {TYPE_FLOAT, TYPE_INT}:
        check(node.bl_idname == "ShaderNodeMath", "numeric binary op did not realize as Math")
        check(node.operation == operation.op, "numeric Math operation enum differs from Semantic IR")
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
    check(operation.op == "DIVIDE" and left_type == TYPE_VECTOR and right_type == TYPE_FLOAT, "unexpected Vector binary realization")
    check(node.operation == "SCALE", "Vector / Float final node is not SCALE")
    _check_operand_source(group, node.inputs[0], operation.left, binding_names)
    reciprocal_link = _link_to_socket(group, node.inputs[3])
    reciprocal = reciprocal_link.from_node
    check(reciprocal.bl_idname == "ShaderNodeMath", "Vector / Float reciprocal did not use Math")
    check(reciprocal.operation == "DIVIDE", "Vector / Float reciprocal operation changed")
    one_link = _link_to_socket(group, reciprocal.inputs[0])
    check(one_link.from_node.bl_idname == "ShaderNodeValue", "Vector / Float reciprocal lost its 1.0 helper")
    check(abs(float(one_link.from_socket.default_value) - 1.0) < 1e-8, "Vector / Float reciprocal helper changed")
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
    else:
        raise AssertionError(f"unexpected final contract operation: {type(operation).__name__}")


def test_semantic_backend_dispatch_signatures_realize_on_blender_rna():
    representatives = {}
    for source, bindings in _all_contract_source_cases():
        program = _accepted_contract_case(source, bindings)
        if program is None:
            continue
        representatives.setdefault(
            _program_dispatch_signature(program),
            (source, bindings, program),
        )

    check(representatives, "semantic/backend contract produced no analyzer-accepted representatives")
    for index, (signature, (source, bindings, program)) in enumerate(representatives.items()):
        group, result = _lower_contract_program_on_real_blender(
            program,
            bindings,
            f"NFTest_semantic_backend_rna_{index}",
        )
        _check_program_realization(group, program, result)
        bpy.data.node_groups.remove(group)



def test_semantic_environment_preserves_legacy_geometry_builder_binding(monkeypatch):
    environments = []
    original = expression_compiler.analyze_expression

    def wrapped(expr, environment):
        environments.append(environment)
        return original(expr, environment)

    monkeypatch.setattr(expression_compiler, "analyze_expression", wrapped)
    group = compile_group(
        """
builder = geometry_builder()
builder.add(cube(1.0))
result = builder.geometry
output("Geometry", result)
""",
        "NFTest_semantic_legacy_builder_binding",
    )
    check(_nodes(group, "GeometryNodeMeshCube"), "GeometryBuilder legacy expression behavior changed")
    builder_envs = [env for env in environments if "builder" in env.legacy_binding_names]
    check(builder_envs, "GeometryBuilder name was not exported as a known legacy binding")
    for environment in builder_envs:
        check("builder" not in environment.runtime_bindings, "GeometryBuilder leaked into runtime types")
        check(all(isinstance(name, str) for name in environment.legacy_binding_names), "legacy snapshot carried non-name values")


def test_semantic_environment_exports_constant_metadata_without_legacy_containers():
    from NodeForge.compiler import Compiler

    group = bpy.data.node_groups.new("NFTest_semantic_constant_snapshot", "GeometryNodeTree")
    comp = Compiler(group, None, consts={"pi": [1.0, 2.0, 3.0], "scalar": 2.5})
    captured = {}
    original = expression_compiler.analyze_expression

    def wrapped(expr, environment):
        captured["environment"] = environment
        return original(expr, environment)

    expression_compiler.analyze_expression = wrapped
    try:
        result = expression_compiler.compile_expr(comp, ast.parse("pi", mode="eval").body)
        check(getattr(result, "typ", None) == TYPE_VECTOR, "non-scalar const did not remain on legacy constant path")
    finally:
        expression_compiler.analyze_expression = original
        bpy.data.node_groups.remove(group)

    environment = captured["environment"]
    check("pi" in environment.unsupported_constant_names, "non-scalar const name was not preserved")
    check("pi" not in environment.scalar_constants, "non-scalar const leaked into scalar metadata")
    check(environment.scalar_constants["scalar"] == (TYPE_FLOAT, 2.5), "scalar const metadata changed")
