"""Blender integration coverage for canonical numeric type-directed numeric semantics."""

from helpers import *

import math
import struct

from NodeForge.errors import CompileError


def _nodes(group, bl_idname, operation=None):
    """Return nodes matching one Blender node type and optional operation enum."""
    result = [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]
    if operation is not None:
        result = [node for node in result if getattr(node, "operation", None) == operation]
    return result


def _float_bits(value):
    """Return IEEE-754 binary32 bits for one evaluated Blender Float carrier."""
    return struct.unpack("!I", struct.pack("!f", float(value)))[0]


def _evaluated_point_position(group, name):
    """Evaluate a geometry-only group and return its first point position."""
    mesh = bpy.data.meshes.new(name + "_Mesh")
    obj = bpy.data.objects.new(name + "_Object", mesh)
    bpy.context.collection.objects.link(obj)
    # The new DSL function exposes Value; a physical wrapper explicitly wires
    # that geometry into the modifier's Geometry output. No root DSL I/O exists.
    wrapper = bpy.data.node_groups.new(name + "_Wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    output = wrapper.nodes.new("NodeGroupOutput")
    function_node = wrapper.nodes.new("GeometryNodeGroup")
    function_node.node_tree = group
    wrapper.links.new(function_node.outputs[0], output.inputs["Geometry"])
    modifier = obj.modifiers.new("NodeForge", "NODES")
    modifier.node_group = wrapper
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    out = evaluated.to_mesh()
    try:
        check(len(out.vertices) == 1, f"{name}: expected one evaluated point")
        return tuple(float(component) for component in out.vertices[0].co)
    finally:
        evaluated.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)
        bpy.data.node_groups.remove(group, do_unlink=True)
        bpy.data.node_groups.remove(wrapper, do_unlink=True)


def _evaluated_x(expression, name):
    """Compile one numeric expression into point position X and return the evaluated value."""
    group = compile_group(
        f'def evaluate():\n    x = {expression}\n    return point(vector(x, 0.0, 0.0))\n',
        name,
    )
    return _evaluated_point_position(group, name)[0]


def test_numeric_ir_uses_typed_integer_math_math_and_compare_nodes():
    """Typed numeric IR selects physical node families without backend type inference."""
    group = compile_group(
        '''
def nf_fixture():
    i = index()
    int_add = i + 1
    int_floor = i // 2
    int_mod = i % 3
    float_div = i / 2
    float_floor = i // 2.0
    float_mod = i % 3.0
    int_cmp = i < 4
    float_cmp = i == 4.0
    power = 2.0 ** 3.0
    int_add = int_add
    int_floor = int_floor
    int_mod = int_mod
    float_div = float_div
    float_floor = float_floor
    float_mod = float_mod
    int_cmp = int_cmp
    float_cmp = float_cmp
    power = power
    return (int_add, int_floor, int_mod, float_div, float_floor, float_mod, int_cmp, float_cmp, power,)
''',
        "NFTest_numeric_backend_families",
    )
    try:
        integer_ops = [getattr(node, "operation", None) for node in _nodes(group, "FunctionNodeIntegerMath")]
        check("ADD" in integer_ops, f"missing Integer Math ADD: {integer_ops}")
        check("DIVIDE_FLOOR" in integer_ops, f"missing Integer Math DIVIDE_FLOOR: {integer_ops}")
        check("FLOORED_MODULO" in integer_ops, f"missing Integer Math FLOORED_MODULO: {integer_ops}")

        math_ops = [getattr(node, "operation", None) for node in _nodes(group, "ShaderNodeMath")]
        check("DIVIDE" in math_ops, f"missing Float DIVIDE: {math_ops}")
        check("FLOOR" in math_ops, f"missing Float FLOOR: {math_ops}")
        check("FLOORED_MODULO" in math_ops, f"missing Float FLOORED_MODULO: {math_ops}")
        check("POWER" in math_ops, f"missing runtime POWER: {math_ops}")

        compares = _nodes(group, "FunctionNodeCompare")
        check(any(getattr(node, "data_type", None) == "INT" for node in compares), "missing Compare INT")
        float_compare = next((node for node in compares if getattr(node, "data_type", None) == "FLOAT"), None)
        check(float_compare is not None, "missing Compare FLOAT")
        epsilon = next((socket for socket in float_compare.inputs if socket.name == "Epsilon"), None)
        check(epsilon is not None and epsilon.default_value == 0.0, "Float Compare epsilon is not explicit zero")
    finally:
        bpy.data.node_groups.remove(group, do_unlink=True)


def test_numeric_semantics_vector_int_scalars_keep_scale_and_reciprocal_topology():
    """Vector Int scalars reuse SCALE and vector division keeps reciprocal-plus-SCALE topology."""
    group = compile_group(
        '''
def nf_fixture():
    i = index()
    v = vector(10000000000.0, 2.0, 3.0)
    a = v * i
    b = i * v
    c = v / i
    a = a
    b = b
    c = c
    return (a, b, c,)
''',
        "NFTest_numeric_semantics_vector_int_scalar_topology",
    )
    try:
        vector_ops = [getattr(node, "operation", None) for node in _nodes(group, "ShaderNodeVectorMath")]
        check(vector_ops.count("SCALE") >= 3, f"Vector Int scalar operations lost SCALE topology: {vector_ops}")
        math_ops = [getattr(node, "operation", None) for node in _nodes(group, "ShaderNodeMath")]
        check(math_ops.count("DIVIDE") == 1, f"Vector / Int should build one reciprocal DIVIDE: {math_ops}")
    finally:
        bpy.data.node_groups.remove(group, do_unlink=True)


@pytest.mark.parametrize(
    "suffix,expression",
    [
        ("overflow_add", "2147483647 + 1"),
        ("underflow_sub", "-2147483648 - 1"),
        ("overflow_mul", "50000 * 50000"),
        ("overflow_negate", "-(-2147483648)"),
        ("int_min_floor", "-2147483648 // -1"),
        ("int_min_modulo", "-2147483648 % -1"),
    ],
)
def test_numeric_semantics_direct_runtime_consumer_rejects_known_int_domain_errors(suffix, expression):
    """Direct runtime consumers cannot bypass hard signed-32 semantic validation."""
    try:
        compile_group(
            f'def negative_case():\n    return {expression}\n',
            f"NFTest_numeric_semantics_direct_int_domain_{suffix}",
        )
    except CompileError:
        return
    raise AssertionError(f"known invalid Int expression unexpectedly reached Blender lowering: {expression}")


def test_numeric_semantics_direct_known_valid_int_expression_remains_integer_math_add():
    """Hard-domain validation does not fold a valid known runtime Int expression."""
    group = compile_group('''
def nf_fixture():
    x = 1 + 2
    return (x,)
''', "NFTest_numeric_semantics_direct_valid_int_add")
    try:
        integer_add = _nodes(group, "FunctionNodeIntegerMath", "ADD")
        check(len(integer_add) == 1, f"expected one Integer Math ADD, got {len(integer_add)}")
    finally:
        bpy.data.node_groups.remove(group, do_unlink=True)


@pytest.mark.parametrize(
    "suffix,expression,expected,expected_bits",
    [
        ("literal", "0.1", 0.10000000149011612, 0x3DCCCCCD),
        ("pi", "pi", 3.1415927410125732, 0x40490FDB),
        ("tau", "tau", 6.2831854820251465, 0x40C90FDB),
        ("e", "e", 2.7182817459106445, 0x402DF854),
        ("precision_leaf", "16777217.0", 16777216.0, 0x4B800000),
        ("third", "1.0 / 3.0", 0.3333333432674408, 0x3EAAAAAB),
        ("divide_zero", "1.0 / 0.0", 0.0, 0x00000000),
        ("divide_negative_zero", "-1.0 / -0.0", 0.0, 0x00000000),
        ("floor_divide", "-3.0 // 2.0", -2.0, 0xC0000000),
        ("floor_divide_negative_zero", "1.0 // -0.0", 0.0, 0x00000000),
        ("modulo_negative_zero", "1.0 % -0.0", 0.0, 0x00000000),
        ("modulo_positive_zero", "-1.0 % 0.0", 0.0, 0x00000000),
        ("signed_zero", "-0.0", 0.0, 0x00000000),
        ("mixed_precision", "16777217 + 0.0", 16777216.0, 0x4B800000),
        ("per_op_rounding", "(16777217.0 + 1.0) - 16777216.0", 0.0, 0x00000000),
        ("floored_modulo", "932907.0625 % -1185.3480224609375", -1147.1875, 0xC48F6600),
    ],
)
def test_numeric_semantics_float_characterization_matches_actual_geometry_nodes(suffix, expression, expected, expected_bits):
    """Characterized Float expressions match evaluated Blender binary32 results exactly."""
    actual = _evaluated_x(expression, f"NFTest_numeric_semantics_float_eval_{suffix}")
    check(actual == expected, f"{suffix}: runtime Float value mismatch: {actual!r}")
    check(_float_bits(actual) == expected_bits, f"{suffix}: runtime Float bits mismatch")


@pytest.mark.parametrize(
    "suffix,left,right,expected_q,expected_r",
    [
        ("neg_pos", -3, 2, -2, 1),
        ("pos_neg", 3, -2, -2, -1),
        ("neg_neg", -3, -2, 1, -1),
        ("pos_pos", 3, 2, 1, 1),
        ("zero", 1, 0, 0, 0),
    ],
)
def test_numeric_semantics_integer_floor_pair_matches_actual_integer_math(suffix, left, right, expected_q, expected_r):
    """Integer floor quotient/remainder match evaluated Integer Math, including zero divisor."""
    group = compile_group(
        f'''\ndef evaluate():\n    q = {left} // {right}\n    r = {left} % {right}\n    return point(vector(q, r, 0.0))\n''',
        f"NFTest_numeric_semantics_int_floor_pair_{suffix}",
    )
    position = _evaluated_point_position(group, f"NFTest_numeric_semantics_int_floor_pair_{suffix}")
    check(position[0] == float(expected_q), f"{suffix}: Integer Math floor quotient mismatch: {position}")
    check(position[1] == float(expected_r), f"{suffix}: Integer Math floored remainder mismatch: {position}")


@pytest.mark.parametrize(
    "suffix,condition,expected",
    [
        ("float_precision_equal", "16777217.0 == 16777216.0", 1.0),
        ("mixed_precision_equal", "16777217 == 16777216.0", 1.0),
        ("mixed_less", "1 < 2.0", 1.0),
        ("mixed_false", "2 < 1.0", 0.0),
    ],
)
def test_numeric_semantics_float_and_mixed_comparisons_match_actual_geometry_nodes(suffix, condition, expected):
    """Float/mixed Compare nodes expose the characterized binary32 comparison result."""
    actual = _evaluated_x(
        f"1.0 if ({condition}) else 0.0",
        f"NFTest_numeric_semantics_compare_{suffix}",
    )
    check(actual == expected, f"{suffix}: comparison result mismatch: {actual!r}")
    check(_float_bits(actual) == _float_bits(expected), f"{suffix}: comparison carrier bits mismatch")


def test_numeric_semantics_runtime_float_overflow_remains_a_runtime_infinity():
    """Valid runtime Float arithmetic may overflow even when CTFE declines to publish the value."""
    actual = _evaluated_x("3e38 * 2.0", "NFTest_numeric_semantics_runtime_float_overflow")
    check(math.isinf(actual) and actual > 0.0, f"runtime overflow did not produce +inf: {actual!r}")
    check(_float_bits(actual) == 0x7F800000, "runtime overflow did not preserve Float +inf bits")


def test_numeric_semantics_vector_division_matches_reciprocal_then_scale_value():
    """Vector / scalar preserves DIVIDE then SCALE precision instead of direct component divide."""
    actual = _evaluated_x(
        "(vector(10000000000.0, 0.0, 0.0) / 3.0).x",
        "NFTest_numeric_semantics_vector_divide_precision",
    )
    check(actual == 3333333504.0, f"unexpected reciprocal-plus-SCALE result: {actual!r}")
    check(_float_bits(actual) == 0x4F46AEA2, "Vector division used direct component divide semantics")


def test_numeric_semantics_power_remains_runtime_capable():
    """POWER stays runtime-capable even though canonical numeric CTFE intentionally declines it."""
    actual = _evaluated_x("2.0 ** 3.0", "NFTest_numeric_semantics_power_runtime")
    check(actual == 8.0, f"runtime POWER returned {actual!r}")


def test_negative_literals_in_source_calls_use_socket_defaults_not_math_nodes():
    """The actual source-function call path must not build 0 - 2.0 for -2.0."""
    from NodeForge import compiler

    source = '''
def move_x(geometry: Geometry, x_shift: Float["X Shift"] = 0.0) -> Geometry:
    return transform(geometry, translation=vector(x_shift, 0, 0))

def move(geometry: Geometry, x: Float, y: Float) -> Geometry:
    left = move_x(cube(1.0), -2.0)
    right = move_x(cube(1.0), 2.0)
    combined = join([left, right])
    return combined
'''
    root = bpy.data.node_groups.new("NFTest_negative_literal_root", "GeometryNodeTree")
    try:
        compiler.compose_source_initial(source, root)
        move_node = next(node for node in root.nodes
                         if node.bl_idname == "GeometryNodeGroup" and node.node_tree
                         and node.node_tree.get("nodeforge_module_name") == "move")
        move = move_node.node_tree
        calls = [node for node in move.nodes if node.bl_idname == "GeometryNodeGroup"]
        check(len(calls) == 2, "expected two move_x calls")
        check(not _nodes(move, "ShaderNodeMath", "SUBTRACT"), "literal -2 created Subtract")
        check(not _nodes(move, "ShaderNodeValue"), "literal -2 created Value nodes")
        actual = sorted(round(node.inputs["X Shift"].default_value, 4) for node in calls)
        check(actual == [-2.0, 2.0], f"wrong signed socket defaults: {actual}")
    finally:
        bpy.data.node_groups.remove(root, do_unlink=True)


def test_negative_runtime_float_still_creates_subtraction_node():
    group = compile_group(
        "def negative(x: Float) -> Float:\n    return -x\n",
        "NFTest_negative_runtime_float",
    )
    try:
        check(len(_nodes(group, "ShaderNodeMath", "SUBTRACT")) == 1,
              "runtime negation lost its Subtract")
    finally:
        bpy.data.node_groups.remove(group, do_unlink=True)
