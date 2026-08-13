from helpers import *


def _nodes(group, bl_idname):
    return [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]


def _repeat_output(group):
    outputs = _nodes(group, "GeometryNodeRepeatOutput")
    check(len(outputs) == 1, f"expected one Repeat Output, found {len(outputs)}")
    return outputs[0]


def test_geometry_builder_compile_time_accumulation_and_loop_preprocess():
    group = compile_group(
        """
builder = geometry_builder()
for size in [1, 2, 3]:
    builder.add(cube(size))
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_compile_time_loop",
    )
    check(len(_nodes(group, "GeometryNodeRepeatInput")) == 0, "compile-time builder loop created Repeat Input")
    check(len(_nodes(group, "GeometryNodeRepeatOutput")) == 0, "compile-time builder loop created Repeat Output")
    check(len(_nodes(group, "GeometryNodeJoinGeometry")) == 1, "expected one compile-time Join Geometry")


def test_geometry_builder_compile_time_empty_single_extend_and_snapshot():
    empty_group = compile_group(
        """
builder = geometry_builder()
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_empty",
    )
    check(len(_nodes(empty_group, "GeometryNodeJoinGeometry")) == 0, "empty builder should not create Join Geometry")

    single_group = compile_group(
        """
builder = geometry_builder()
builder.add(cube(1))
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_single",
    )
    check(len(_nodes(single_group, "GeometryNodeJoinGeometry")) == 0, "single builder add should use join identity")

    multi_group = compile_group(
        """
builder = geometry_builder()
builder.add(cube(1))
builder.extend([cube(2), cube(3)])
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_extend",
    )
    check(len(_nodes(multi_group, "GeometryNodeJoinGeometry")) == 1, "expected one Join Geometry for three parts")

    compile_group(
        """
builder = geometry_builder()
builder.add(cube(1))
first = builder.geometry
builder.add(cube(2))
output("First", first)
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_snapshot",
    )


def test_geometry_builder_repeat_zone_builder_only_and_extend():
    group = compile_group(
        """
builder = geometry_builder()
count = input_int("Count", default=3)
for i in repeat_range(count):
    builder.add(transform(cube(0.1), translation=vector(i, 0, 0)))
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_repeat_only",
    )
    check(len(_nodes(group, "GeometryNodeRepeatInput")) == 1, "expected one Repeat Input")
    repeat_output = _repeat_output(group)
    names = [item.name for item in repeat_output.repeat_items]
    socket_types = [item.socket_type for item in repeat_output.repeat_items]
    check(names == ["builder"], f"unexpected repeat items: {names}")
    check(socket_types == ["GEOMETRY"], f"unexpected repeat item types: {socket_types}")

    compile_group(
        """
builder = geometry_builder()
count = input_int("Count", default=2)
for i in repeat_range(count):
    builder.extend([cube(0.1), cube(0.2)])
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_repeat_extend",
    )


def test_geometry_builder_repeat_mixed_state_order_and_post_loop_add():
    group = compile_group(
        """
builder = geometry_builder()
pos = vector(0, 0, 0)
count = input_int("Count", default=3)
for i in repeat_range(count):
    next_pos = pos + vector(1, 0, 0)
    builder.add(line(pos, next_pos))
    builder.add(transform(cube(0.1), translation=pos))
    pos = next_pos
output("Geometry", builder.geometry)
output("Position", pos)
""",
        "NFTest_geometry_builder_repeat_mixed",
    )
    repeat_output = _repeat_output(group)
    names = [item.name for item in repeat_output.repeat_items]
    check(names == ["builder", "pos"], f"unexpected repeat item order: {names}")
    check("next_pos" not in names, "loop temporary became repeat state")

    compile_group(
        """
builder = geometry_builder()
builder.add(cube(1))
count = input_int("Count", default=2)
for i in repeat_range(count):
    builder.add(cube(0.1))
builder.add(cube(2))
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_repeat_pre_post",
    )


def test_geometry_builder_runtime_snapshot_and_branch_isolation():
    compile_group(
        """
builder = geometry_builder()
count = input_int("Count", default=2)
for i in repeat_range(count):
    builder.add(cube(0.1))
    snapshot = builder.geometry
    builder.add(snapshot)
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_repeat_snapshot",
    )

    group = compile_group(
        """
builder = geometry_builder()
flag = input_bool("Flag", default=True)
count = input_int("Count", default=3)
for i in repeat_range(count):
    if flag:
        builder.add(cube(0.1))
        flag = False
    else:
        flag = flag
output("Geometry", builder.geometry)
output("Flag", flag)
""",
        "NFTest_geometry_builder_repeat_branch_merge",
    )
    switch_types = [getattr(node, "input_type", None) for node in _nodes(group, "GeometryNodeSwitch")]
    check("GEOMETRY" in switch_types, f"missing Geometry switch in {switch_types}")
    check("BOOLEAN" in switch_types, f"missing Boolean switch in {switch_types}")

    group = compile_group(
        """
builder = geometry_builder()
flag = input_bool("Flag", default=True)
count = input_int("Count", default=3)
for i in repeat_range(count):
    if flag:
        builder.add(cube(0.1))
    else:
        flag = flag
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_repeat_branch_isolation",
    )
    switch_types = [getattr(node, "input_type", None) for node in _nodes(group, "GeometryNodeSwitch")]
    check("GEOMETRY" in switch_types, f"missing Geometry switch in {switch_types}")


def _evaluated_mesh(group, name):
    mesh = bpy.data.meshes.new(name + "Mesh")
    obj = bpy.data.objects.new(name + "Object", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="NodeForge", type="NODES")
    modifier.node_group = group
    obj.update_tag()
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh()
    try:
        vertices = [tuple(round(float(coord), 4) for coord in vertex.co) for vertex in evaluated_mesh.vertices]
        edges = [tuple(edge.vertices) for edge in evaluated_mesh.edges]
        polygons = [tuple(poly.vertices) for poly in evaluated_mesh.polygons]
        return vertices, edges, polygons
    finally:
        evaluated.to_mesh_clear()


def _same_positions(left, right, epsilon=1e-4):
    if len(left) != len(right):
        return False
    left_sorted = sorted(left)
    right_sorted = sorted(tuple(float(coord) for coord in pos) for pos in right)
    return all(
        abs(left_coord - right_coord) <= epsilon
        for left_pos, right_pos in zip(left_sorted, right_sorted)
        for left_coord, right_coord in zip(left_pos, right_pos)
    )


def test_geometry_builder_evaluated_snapshot_semantics():
    compile_time_group = compile_group(
        """
builder = geometry_builder()
builder.add(point(vector(0, 0, 0)))
snapshot = transform(builder.geometry, translation=vector(10, 0, 0))
builder.add(snapshot)
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_eval_compile_time_snapshot",
    )
    vertices, edges, polygons = _evaluated_mesh(compile_time_group, "NFTest_geometry_builder_eval_compile_time_snapshot")
    check(_same_positions(vertices, [(0, 0, 0), (10, 0, 0)]), f"compile-time snapshot vertices changed: {vertices}")
    check(edges == [], f"compile-time snapshot should create no edges: {edges}")
    check(polygons == [], f"compile-time snapshot should create no polygons: {polygons}")

    runtime_group = compile_group(
        """
builder = geometry_builder()
count = input_int("Count", default=2)
for i in repeat_range(count):
    builder.add(point(vector(i, 0, 0)))
    snapshot = transform(builder.geometry, translation=vector((i + 1) * 10, 0, 0))
    builder.add(snapshot)
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_eval_runtime_snapshot",
    )
    vertices, edges, polygons = _evaluated_mesh(runtime_group, "NFTest_geometry_builder_eval_runtime_snapshot")
    expected = [(0, 0, 0), (10, 0, 0), (1, 0, 0), (20, 0, 0), (30, 0, 0), (21, 0, 0)]
    check(_same_positions(vertices, expected), f"runtime snapshot vertices changed: {vertices}")
    check(edges == [], f"runtime snapshot should create no edges: {edges}")
    check(polygons == [], f"runtime snapshot should create no polygons: {polygons}")


def test_geometry_builder_negative_usage_errors_are_controlled():
    cases = [
        "builder = geometry_builder(1)\noutput(\"Geometry\", empty_geometry())",
        "builder = geometry_builder(foo=1)\noutput(\"Geometry\", empty_geometry())",
        "builder = geometry_builder()\nbuilder.add(1)\noutput(\"Geometry\", builder.geometry)",
        "builder = geometry_builder()\nbuilder.extend(cube(1))\noutput(\"Geometry\", builder.geometry)",
        "builder = geometry_builder()\nbuilder.extend([cube(1), 1])\noutput(\"Geometry\", builder.geometry)",
        "builder = geometry_builder()\noutput(\"Geometry\", builder)",
        "builder = geometry_builder()\nother = builder\noutput(\"Geometry\", empty_geometry())",
        "builder = geometry_builder()\nbuilder = empty_geometry()\noutput(\"Geometry\", builder)",
        "builder = geometry_builder()\nx = builder.add(cube(1))\noutput(\"Geometry\", builder.geometry)",
        "builder = geometry_builder()\nbuilder.foo\noutput(\"Geometry\", builder.geometry)",
        "builder = geometry_builder()\nbuilder.foo(cube(1))\noutput(\"Geometry\", builder.geometry)",
        "builder = geometry_builder()\noutput(\"Geometry\", join(builder))",
        "builder = geometry_builder()\ngeo = transform(builder, translation=vector(1, 0, 0))\noutput(\"Geometry\", geo)",
        "builder = geometry_builder()\ndef f():\n    return builder\noutput(\"x\", f())",
        "items = []\ncount = input_int(\"Count\", default=2)\nfor i in repeat_range(count):\n    items.add(cube(1))\noutput(\"Geometry\", empty_geometry())",
        "builder = geometry_builder()\nflag = input_bool(\"Flag\", default=True)\nif flag:\n    builder.add(cube(1))\nelse:\n    builder.add(cube(2))\noutput(\"Geometry\", builder.geometry)",
    ]
    for index, source in enumerate(cases):
        expect_compile_error(source, f"NFTest_geometry_builder_error_{index}")


def test_geometry_builder_nested_repeat_uses_current_outer_snapshot():
    group = compile_group(
        """
builder = geometry_builder()
for i in repeat_range(2):
    builder.add(point(vector(i * 10, 0, 0)))
    for j in repeat_range(3):
        builder.add(point(vector(i * 10 + j + 1, 0, 0)))
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_nested_repeat_snapshot",
    )

    check(len(_nodes(group, "GeometryNodeRepeatInput")) == 2, "expected two Repeat Inputs for nested builder")
    vertices, edges, polygons = _evaluated_mesh(group, "NFTest_geometry_builder_nested_repeat_snapshot_eval")
    expected = [(0, 0, 0), (1, 0, 0), (2, 0, 0), (3, 0, 0), (10, 0, 0), (11, 0, 0), (12, 0, 0), (13, 0, 0)]
    check(_same_positions(vertices, expected), f"nested builder lost current outer snapshot: {vertices}")
    check(edges == [], f"nested builder should create no edges: {edges}")
    check(polygons == [], f"nested builder should create no polygons: {polygons}")


def test_geometry_builder_nested_repeat_geometry_read_uses_nearest_frame():
    group = compile_group(
        """
builder = geometry_builder()
for i in repeat_range(1):
    builder.add(point(vector(0, 0, 0)))
    for j in repeat_range(1):
        snapshot = transform(builder.geometry, translation=vector(1, 0, 0))
        builder.add(snapshot)
output("Geometry", builder.geometry)
""",
        "NFTest_geometry_builder_nested_repeat_geometry_read",
    )

    vertices, edges, polygons = _evaluated_mesh(group, "NFTest_geometry_builder_nested_repeat_geometry_read_eval")
    check(_same_positions(vertices, [(0, 0, 0), (1, 0, 0)]), f"nested builder.geometry read missed nearest frame: {vertices}")
    check(edges == [], f"nested builder.geometry read should create no edges: {edges}")
    check(polygons == [], f"nested builder.geometry read should create no polygons: {polygons}")
