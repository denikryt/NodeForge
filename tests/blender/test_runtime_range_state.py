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


def _evaluated_vertices(group, name, input_values=None):
    """Evaluate one Geometry output and return rounded vertex coordinates."""
    mesh = bpy.data.meshes.new(name + "Mesh")
    obj = bpy.data.objects.new(name + "Object", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="NodeForge", type="NODES")
    eval_group = group
    wrapper = None
    if input_values:
        wrapper = bpy.data.node_groups.new(name + "Wrapper", "GeometryNodeTree")
        wrapper.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
        group_node = wrapper.nodes.new("GeometryNodeGroup")
        group_node.node_tree = group
        group_output = wrapper.nodes.new("NodeGroupOutput")
        group_output.is_active_output = True
        for socket_name, value in input_values.items():
            int_node = wrapper.nodes.new("FunctionNodeInputInt")
            int_node.integer = int(value)
            wrapper.links.new(int_node.outputs["Integer"], group_node.inputs[socket_name])
        wrapper.links.new(group_node.outputs["Geometry"], group_output.inputs["Geometry"])
        eval_group = wrapper
    modifier.node_group = eval_group
    obj.update_tag()
    bpy.context.view_layer.update()
    evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    evaluated_mesh = evaluated.to_mesh()
    try:
        return [tuple(round(float(coord), 4) for coord in vertex.co) for vertex in evaluated_mesh.vertices]
    finally:
        evaluated.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh, do_unlink=True)
        if wrapper is not None:
            bpy.data.node_groups.remove(wrapper, do_unlink=True)


def test_nested_repeat_range_scalar_evaluation_and_zone_count():
    group = compile_group(
        """
x = 0
for i in repeat_range(2):
    for j in repeat_range(3):
        x = x + 1
output("Geometry", point(vector(x, 0, 0)))
output("x", x)
""",
        "NFTest_nested_repeat_scalar",
    )

    check(len(_nodes(group, "GeometryNodeRepeatInput")) == 2, "expected two nested Repeat Inputs")
    repeat_outputs = _nodes(group, "GeometryNodeRepeatOutput")
    check(len(repeat_outputs) == 2, "expected two nested Repeat Outputs")
    check(all("x" in [item.name for item in output.repeat_items] for output in repeat_outputs), "x was not carried by both Repeat Zones")
    check(_evaluated_vertices(group, "NFTest_nested_repeat_scalar_eval") == [(6.0, 0.0, 0.0)], "nested scalar result was not 6")


def test_nested_repeat_range_geometry_state_propagates_across_outer_iterations():
    group = compile_group(
        """
geo = point(vector(0, 0, 0))
for i in repeat_range(2):
    for j in repeat_range(3):
        geo = transform(geo, translation=vector(1, 0, 0))
output("Geometry", geo)
""",
        "NFTest_nested_repeat_geometry",
    )

    check(_evaluated_vertices(group, "NFTest_nested_repeat_geometry_eval") == [(6.0, 0.0, 0.0)], "inner Geometry state reset between outer iterations")


def test_nested_repeat_range_indices_and_runtime_if_scoping():
    group = compile_group(
        """
x = 0
flag = input_bool("Flag", default=True)
for i in repeat_range(2):
    for j in repeat_range(3):
        if flag:
            x = x + i * 10 + j
        else:
            x = x
    x = x + i
output("Geometry", point(vector(x, 0, 0)))
""",
        "NFTest_nested_repeat_indices_if",
    )

    check(_evaluated_vertices(group, "NFTest_nested_repeat_indices_if_eval") == [(37.0, 0.0, 0.0)], "nested index/runtime-if result changed")

    expect_compile_error(
        """
x = 0
for i in repeat_range(2):
    for j in repeat_range(3):
        x = x + 1
    x = x + j
output("x", x)
""",
        "NFTest_nested_repeat_inner_index_escape",
    )


def test_nested_repeat_range_restores_enclosing_index_after_inner_assignment():
    group = compile_group(
        """
x = 0
for i in repeat_range(2):
    for j in repeat_range(1):
        i = i + 10
    x = x + i
output("Geometry", point(vector(x, 0, 0)))
""",
        "NFTest_nested_repeat_outer_index_restore",
    )

    check(
        _evaluated_vertices(group, "NFTest_nested_repeat_outer_index_restore_eval") == [(1.0, 0.0, 0.0)],
        "inner assignment leaked over the enclosing Repeat Iteration binding",
    )


def test_nested_repeat_range_restores_branch_local_enclosing_index():
    group = compile_group(
        """
x = 0
flag = input_bool("Flag", default=True)
for i in repeat_range(2):
    if flag:
        for j in repeat_range(1):
            i = i + 10
        x = x + i
    else:
        x = x + i
output("Geometry", point(vector(x, 0, 0)))
""",
        "NFTest_nested_repeat_branch_outer_index_restore",
    )

    check(
        _evaluated_vertices(group, "NFTest_nested_repeat_branch_outer_index_restore_eval", {"Flag": 1}) == [(1.0, 0.0, 0.0)],
        "inner assignment leaked over the branch-local enclosing Repeat Iteration binding",
    )


def test_nested_repeat_range_implicit_count_is_int_and_evaluates():
    group = compile_group(
        """
x = 0
for i in repeat_range(2):
    for j in repeat_range(inner_count):
        x = x + 1
output("Geometry", point(vector(x, 0, 0)))
""",
        "NFTest_nested_repeat_implicit_count",
    )

    item = next(
        item
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and item.name == "inner_count"
    )
    check(item.socket_type == "NodeSocketInt", f"nested implicit count inferred as {item.socket_type}")
    check(
        _evaluated_vertices(group, "NFTest_nested_repeat_implicit_eval", {"inner_count": 3}) == [(6.0, 0.0, 0.0)],
        "nested implicit Int count did not evaluate correctly",
    )


def test_nested_repeat_range_controlled_diagnostics():
    expect_compile_error(
        """
x = 0
count = input_float("Count", default=3)
for i in repeat_range(2):
    for j in repeat_range(count):
        x = x + 1
output("x", x)
""",
        "NFTest_nested_repeat_float_count_error",
    )
    expect_compile_error(
        """
x = 0
for i in repeat_range(2):
    for Iteration in repeat_range(3):
        x = x + 1
output("x", x)
""",
        "NFTest_nested_repeat_index_socket_collision",
    )
    expect_compile_error(
        """
x = 0
for i in repeat_range(2):
    for j in repeat_range(3):
        x = cube(1)
output("x", x)
""",
        "NFTest_nested_repeat_state_type_error",
    )
