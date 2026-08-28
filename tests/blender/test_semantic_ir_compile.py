"""Blender integration coverage for the initial Semantic IR expression slice."""

from helpers import *

from NodeForge import expression_compiler
from NodeForge.semantic_ir import IRBinary, IRCompare, IRConditional, IRVectorComponent


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
