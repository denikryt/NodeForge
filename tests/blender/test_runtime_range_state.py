from helpers import *


def _nodes(group, bl_idname):
    return [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]


def _repeat_output(group):
    outputs = _nodes(group, "GeometryNodeRepeatOutput")
    check(len(outputs) == 1, f"expected one Repeat Output, found {len(outputs)}")
    return outputs[0]


def test_repeat_range_mixed_geometry_vector_float_state_order():
    group = compile_group(
        """
geo = cube(0.1)
pos = vector(0, 0, 0)
angle = input_float("Angle", default=45)
count = input_int("Count", default=4)
for i in repeat_range(count):
    next_pos = pos + vector(1, 0, 0)
    piece = transform(cube(0.05), translation=next_pos)
    geo = join(geo, piece)
    pos = next_pos
    angle = -angle
output("Geometry", geo)
output("Position", pos)
output("Angle", angle)
""",
        "NFTest_repeat_range_mixed_state",
    )

    check(len(_nodes(group, "GeometryNodeRepeatInput")) == 1, "expected one Repeat Input")
    repeat_output = _repeat_output(group)
    names = [item.name for item in repeat_output.repeat_items]
    socket_types = [item.socket_type for item in repeat_output.repeat_items]
    check(names == ["geo", "pos", "angle"], f"unexpected repeat item order: {names}")
    check(socket_types == ["GEOMETRY", "VECTOR", "FLOAT"], f"unexpected repeat item types: {socket_types}")
    check("next_pos" not in names and "piece" not in names, "loop temporaries became repeat state items")


def test_repeat_range_geometry_branch_merge_uses_geometry_switch():
    group = compile_group(
        """
geo = cube(0.1)
flag = input_bool("Flag", default=True)
count = input_int("Count", default=3)
for i in repeat_range(count):
    if flag:
        geo = transform(geo, translation=vector(1, 0, 0))
        flag = False
    else:
        geo = geo
        flag = flag
output("Geometry", geo)
output("Flag", flag)
""",
        "NFTest_repeat_range_geometry_branch",
    )

    switch_types = [getattr(node, "input_type", None) for node in _nodes(group, "GeometryNodeSwitch")]
    check("GEOMETRY" in switch_types, f"missing Geometry switch in {switch_types}")
    check("BOOLEAN" in switch_types, f"missing Boolean switch in {switch_types}")


def test_repeat_range_existing_scalar_vector_bool_still_compile():
    compile_group(
        """
x = 0
for i in repeat_range(5):
    if x < 3:
        x = x + 1
    else:
        x = x
output("x", x)
""",
        "NFTest_repeat_range_scalar_existing",
    )
    compile_group(
        """
from functions import rotate_around_axis
v = vector(1,0,0)
flag = True
for i in repeat_range(3):
    if flag:
        v = rotate_around_axis(v, vector(0,0,1), 0.1)
        flag = False
    else:
        v = v
        flag = flag
output("v", v)
output("flag", flag)
""",
        "NFTest_repeat_range_vector_bool_existing",
    )


def test_compile_time_range_with_existing_state_does_not_create_repeat_zone():
    group = compile_group(
        """
geo = cube(1)
for i in range(3):
    geo = transform(geo, translation=vector(0.1, 0, 0))
output("Geometry", geo)
""",
        "NFTest_compile_time_range_existing_geometry_state",
    )
    check(len(_nodes(group, "GeometryNodeRepeatInput")) == 0, "compile-time range unexpectedly created Repeat Input")
    check(len(_nodes(group, "GeometryNodeRepeatOutput")) == 0, "compile-time range unexpectedly created Repeat Output")


def test_range_with_runtime_count_points_to_repeat_range():
    expect_compile_error(
        """
geo = cube(1)
steps = input_int("Steps", default=3)
for i in range(steps):
    geo = transform(geo, translation=vector(0.1, 0, 0))
output("Geometry", geo)
""",
        "NFTest_range_runtime_count_error",
    )


def test_repeat_range_state_type_errors_are_controlled():
    expect_compile_error(
        """
geo = cube(1)
for i in repeat_range(3):
    geo = 1
output("Geometry", geo)
""",
        "NFTest_repeat_range_geometry_to_float_error",
    )
    expect_compile_error(
        """
x = 1
for i in repeat_range(3):
    x = cube(1)
output("x", x)
""",
        "NFTest_repeat_range_float_to_geometry_error",
    )


def test_repeat_range_rejects_repeat_socket_name_collisions():
    expect_compile_error(
        """
Iteration = 0
for i in repeat_range(3):
    Iteration = Iteration + 1
output("Iteration", Iteration)
""",
        "NFTest_repeat_range_iteration_socket_collision",
    )
    expect_compile_error(
        """
Iterations = 0
for i in repeat_range(3):
    Iterations = Iterations + 1
output("Iterations", Iterations)
""",
        "NFTest_repeat_range_iterations_socket_collision",
    )


def test_repeat_range_rejects_index_name_as_state():
    expect_compile_error(
        """
i = 0
for i in repeat_range(3):
    i = i + 1
output("i", i)
""",
        "NFTest_repeat_range_index_state_collision",
    )

def test_repeat_range_rejects_index_repeat_socket_name_collision():
    expect_compile_error(
        """
x = 0
for Iteration in repeat_range(3):
    x = Iteration
output("x", x)
""",
        "NFTest_repeat_range_index_iteration_socket_collision",
    )


def test_repeat_state_assignment_allows_explicit_item_named_like_removed_default_geometry():
    import ast

    from NodeForge.compiler import Compiler
    from NodeForge.nodes import _int_value, _value
    from NodeForge.runtime import _repeat_state_assignments

    # The public DSL reserves Geometry as a type token. This lower-level probe
    # isolates the Repeat Zone invariant: a removable default item named
    # "Geometry" must not be treated as a permanent system socket collision.
    group = bpy.data.node_groups.new("NFTest_repeat_range_lower_geometry_name", "GeometryNodeTree")
    try:
        group_input = group.nodes.new("NodeGroupInput")
        comp = Compiler(group, group_input, consts={})
        comp.vars["Geometry"] = _value(group, 0, 0, 0)
        iterations = _int_value(group, 3, 0, -80)
        body = ast.parse("Geometry = 1").body

        _repeat_state_assignments(group, comp, iterations, body, index_name="i")

        repeat_output = _repeat_output(group)
        names = [item.name for item in repeat_output.repeat_items]
        check(names == ["Geometry"], f"unexpected repeat item names: {names}")
    finally:
        bpy.data.node_groups.remove(group, do_unlink=True)
