from helpers import *


def _nodes(group, bl_idname):
    return [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]


def _socket_key(socket):
    try:
        return socket.as_pointer()
    except Exception:
        return id(socket)


def _same_socket(left, right):
    return _socket_key(left) == _socket_key(right)


def _socket_has_link(group, socket):
    return any(_same_socket(link.to_socket, socket) for link in group.links)


def _evaluated_mesh(group, name):
    mesh = bpy.data.meshes.new(name + "Mesh")
    obj = bpy.data.objects.new(name + "Object", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="NodeForge", type="NODES")
    modifier.node_group = group
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh()
    try:
        vertices = [tuple(vertex.co) for vertex in evaluated_mesh.vertices]
        edges = [tuple(edge.vertices) for edge in evaluated_mesh.edges]
        polygons = [tuple(poly.vertices) for poly in evaluated_mesh.polygons]
        return vertices, edges, polygons
    finally:
        evaluated.to_mesh_clear()


def _copy_input_interface(source_group, target_group):
    for item in getattr(source_group.interface, "items_tree", []):
        if getattr(item, "item_type", None) != "SOCKET" or getattr(item, "in_out", None) != "INPUT":
            continue
        copied = target_group.interface.new_socket(
            name=item.name,
            in_out="INPUT",
            socket_type=item.socket_type,
        )
        if hasattr(item, "default_value") and hasattr(copied, "default_value"):
            try:
                copied.default_value = tuple(item.default_value)
            except TypeError:
                copied.default_value = item.default_value


def _curve_to_mesh_wrapper(group, name):
    wrapper = bpy.data.node_groups.new(name + "Wrapper", "GeometryNodeTree")
    _copy_input_interface(group, wrapper)
    wrapper.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input = wrapper.nodes.new("NodeGroupInput")
    group_output = wrapper.nodes.new("NodeGroupOutput")
    group_output.is_active_output = True
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    curve_to_mesh = wrapper.nodes.new("GeometryNodeCurveToMesh")
    for item in getattr(group.interface, "items_tree", []):
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "INPUT":
            wrapper.links.new(group_input.outputs[item.name], group_node.inputs[item.name])
    wrapper.links.new(group_node.outputs["Geometry"], curve_to_mesh.inputs["Curve"])
    wrapper.links.new(curve_to_mesh.outputs["Mesh"], group_output.inputs["Geometry"])
    return wrapper


def _evaluated_curve_as_mesh(group, name):
    return _evaluated_mesh(_curve_to_mesh_wrapper(group, name), name)


def _socket_identifier_by_name(group, socket_name, in_out="INPUT"):
    for item in getattr(group.interface, "items_tree", []):
        if (
            getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "name", None) == socket_name
            and getattr(item, "in_out", None) == in_out
        ):
            return getattr(item, "identifier", None)
    raise AssertionError(f"missing {in_out} interface socket {socket_name!r}")


def _set_modifier_vector_input(modifier, group, socket_name, value):
    identifier = _socket_identifier_by_name(group, socket_name, "INPUT")
    prop = getattr(modifier.properties.inputs, identifier)
    if not hasattr(prop, "value"):
        raise AssertionError(f"modifier input {socket_name!r} / {identifier!r} has no runtime value property")
    prop.value = tuple(value)


def _evaluated_mesh_object(group, name):
    mesh = bpy.data.meshes.new(name + "Mesh")
    obj = bpy.data.objects.new(name + "Object", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="NodeForge", type="NODES")
    modifier.node_group = group
    return obj, modifier


def _snapshot_mesh_object(obj):
    obj.update_tag()
    bpy.context.view_layer.update()
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh()
    try:
        vertices = [tuple(vertex.co) for vertex in evaluated_mesh.vertices]
        edges = [tuple(edge.vertices) for edge in evaluated_mesh.edges]
        polygons = [tuple(poly.vertices) for poly in evaluated_mesh.polygons]
        return vertices, edges, polygons
    finally:
        evaluated.to_mesh_clear()


def _same_positions(left, right, epsilon=1e-4):
    if len(left) != len(right):
        return False
    return all(abs(a - b) <= epsilon for lco, rco in zip(left, right) for a, b in zip(lco, rco))


def _input_socket(node, name, index):
    socket = node.inputs.get(name) if hasattr(node.inputs, "get") else None
    return socket if socket is not None else node.inputs[index]


def test_empty_geometry_compiles_to_zero_count_mesh_line():
    group = compile_group('''
geo = empty_geometry()
output("Geometry", geo)
''', 'NFTest_empty_geometry_identity')

    mesh_lines = _nodes(group, "GeometryNodeMeshLine")
    check(len(mesh_lines) == 1, f"expected one Mesh Line, found {len(mesh_lines)}")
    check(mesh_lines[0].inputs[0].default_value == 0, "empty_geometry() must use Count 0")


def test_join_empty_arrays_return_identity_without_join_node_and_evaluate_empty():
    for source, name in (
        ('''
output("Geometry", join([]))
''', 'NFTest_join_empty_literal'),
        ('''
items = []
output("Geometry", join(items))
''', 'NFTest_join_empty_variable'),
    ):
        group = compile_group(source, name)
        check(not _nodes(group, "GeometryNodeJoinGeometry"), "join([]) should not allocate Join Geometry")
        vertices, edges, polygons = _evaluated_mesh(group, name + "Eval")
        check(vertices == [], "empty join should evaluate to no vertices")
        check(edges == [], "empty join should evaluate to no edges")
        check(polygons == [], "empty join should evaluate to no polygons")


def test_join_existing_single_and_multi_argument_behaviour():
    single = compile_group('''
geo = join(cube(1))
output("Geometry", geo)
''', 'NFTest_join_single_geometry')
    check(not _nodes(single, "GeometryNodeJoinGeometry"), "join(geo) should remain node-free")

    list_join = compile_group('''
geo = join([cube(1), cube(2)])
output("Geometry", geo)
''', 'NFTest_join_list_geometry')
    check(len(_nodes(list_join, "GeometryNodeJoinGeometry")) == 1, "join([geo, geo]) should allocate one Join Geometry")

    variadic_join = compile_group('''
geo = join(cube(1), cube(2))
output("Geometry", geo)
''', 'NFTest_join_variadic_geometry')
    check(len(_nodes(variadic_join, "GeometryNodeJoinGeometry")) == 1, "join(geo, geo) should allocate one Join Geometry")


def test_empty_geometry_composes_with_cube_and_repeat_state():
    composed = compile_group('''
geo = empty_geometry()
geo = join(geo, cube(1))
output("Geometry", geo)
''', 'NFTest_empty_geometry_join_cube')
    check(len(_nodes(composed, "GeometryNodeJoinGeometry")) == 1, "empty geometry plus cube should join normally")

    repeat_group = compile_group('''
geo = empty_geometry()
count = input_int("Count", default=3)
for i in repeat_range(count):
    piece = transform(cube(0.1), translation=vector(i, 0, 0))
    geo = join(geo, piece)
output("Geometry", geo)
''', 'NFTest_empty_geometry_repeat_accumulator')
    check(len(_nodes(repeat_group, "GeometryNodeRepeatInput")) == 1, "expected one Repeat Input")
    repeat_outputs = _nodes(repeat_group, "GeometryNodeRepeatOutput")
    check(len(repeat_outputs) == 1, "expected one Repeat Output")
    names = [item.name for item in repeat_outputs[0].repeat_items]
    socket_types = [item.socket_type for item in repeat_outputs[0].repeat_items]
    check("geo" in names, f"expected geo repeat item, got {names}")
    check(socket_types[names.index("geo")] == "GEOMETRY", "geo repeat item should be Geometry")


def test_point_static_and_runtime_position_forms():
    static_group = compile_group('''
geo = point(vector(1, 2, 3))
output("Geometry", geo)
''', 'NFTest_point_static_position')
    vertices, edges, polygons = _evaluated_mesh(static_group, "NFTestPointStaticEval")
    check(_same_positions(vertices, [(1, 2, 3)]), f"point() evaluated vertices were {vertices}")
    check(edges == [], "point() should not create edges")
    check(polygons == [], "point() should not create polygons")

    runtime_group = compile_group('''
geo = point(position())
output("Geometry", geo)
''', 'NFTest_point_runtime_position')
    check(_nodes(runtime_group, "GeometryNodeMeshLine"), "point(position()) should create a Mesh Line source")
    set_positions = _nodes(runtime_group, "GeometryNodeSetPosition")
    check(len(set_positions) == 1, "point(position()) should create one Set Position")
    check(_socket_has_link(runtime_group, set_positions[0].inputs[2]), "runtime point position should be linked")


def test_line_static_and_runtime_endpoint_forms():
    static_group = compile_group('''
geo = line(vector(0, 0, 0), vector(1, 2, 3))
output("Geometry", geo)
''', 'NFTest_line_static_endpoints')
    curve_lines = _nodes(static_group, "GeometryNodeCurvePrimitiveLine")
    check(len(curve_lines) == 1, "line() should create one Curve Primitive Line")
    vertices, edges, polygons = _evaluated_curve_as_mesh(static_group, "NFTestLineStaticEval")
    check(_same_positions(vertices, [(0, 0, 0), (1, 2, 3)]), f"line() evaluated vertices were {vertices}")
    check(edges == [(0, 1)], f"line() evaluated edges were {edges}")
    check(polygons == [], "line() should not create polygons")

    runtime_group = compile_group('''
start = input_vector("Start", default=(0, 0, 0))
end = input_vector("End", default=(1, 0, 0))
geo = line(start, end)
output("Geometry", geo)
''', 'NFTest_line_runtime_endpoints')
    curve_line = _nodes(runtime_group, "GeometryNodeCurvePrimitiveLine")[0]
    check(_socket_has_link(runtime_group, _input_socket(curve_line, "Start", 0)), "runtime line start should be linked")
    check(_socket_has_link(runtime_group, _input_socket(curve_line, "End", 1)), "runtime line end should be linked")


def test_point_and_line_join_together():
    group = compile_group('''
geo = join(point(vector(0, 0, 0)), line(vector(0, 0, 0), vector(0, 0, 1)))
output("Geometry", geo)
''', 'NFTest_point_line_join')
    check(len(_nodes(group, "GeometryNodeJoinGeometry")) == 1, "point + line should join through Join Geometry")
    check(_nodes(group, "GeometryNodeSetPosition"), "point constructor should feed the join")
    check(_nodes(group, "GeometryNodeCurvePrimitiveLine"), "line constructor should feed the join")



def test_point_runtime_vector_input_evaluates_and_updates():
    group = compile_group('''
pos = input_vector("Pos", default=(1, 2, 3))
geo = point(pos)
output("Geometry", geo)
''', 'NFTest_point_runtime_vector_input_eval')
    obj, modifier = _evaluated_mesh_object(group, "NFTestPointRuntimeInputEval")
    vertices, edges, polygons = _snapshot_mesh_object(obj)
    check(_same_positions(vertices, [(1, 2, 3)]), f"point(input_vector) default vertices were {vertices}")
    check(edges == [], "point(input_vector) should not create edges")
    check(polygons == [], "point(input_vector) should not create polygons")

    _set_modifier_vector_input(modifier, group, "Pos", (4, 5, 6))
    vertices, edges, polygons = _snapshot_mesh_object(obj)
    check(_same_positions(vertices, [(4, 5, 6)]), f"point(input_vector) updated vertices were {vertices}")
    check(edges == [], "point(input_vector) update should not create edges")
    check(polygons == [], "point(input_vector) update should not create polygons")


def test_line_runtime_vector_inputs_evaluate_and_update():
    group = compile_group('''
start = input_vector("Start", default=(0, 0, 1))
end = input_vector("End", default=(1, 2, 3))
geo = line(start, end)
output("Geometry", geo)
''', 'NFTest_line_runtime_vector_input_eval')
    wrapper = _curve_to_mesh_wrapper(group, "NFTestLineRuntimeInputEval")
    obj, modifier = _evaluated_mesh_object(wrapper, "NFTestLineRuntimeInputEval")
    vertices, edges, polygons = _snapshot_mesh_object(obj)
    check(_same_positions(vertices, [(0, 0, 1), (1, 2, 3)]), f"line(input_vector) default vertices were {vertices}")
    check(edges == [(0, 1)], f"line(input_vector) default edges were {edges}")
    check(polygons == [], "line(input_vector) should not create polygons")

    _set_modifier_vector_input(modifier, wrapper, "Start", (2, 0, 0))
    _set_modifier_vector_input(modifier, wrapper, "End", (2, 3, 0))
    vertices, edges, polygons = _snapshot_mesh_object(obj)
    check(_same_positions(vertices, [(2, 0, 0), (2, 3, 0)]), f"line(input_vector) updated vertices were {vertices}")
    check(edges == [(0, 1)], f"line(input_vector) updated edges were {edges}")
    check(polygons == [], "line(input_vector) update should not create polygons")

def test_empty_geometry_point_line_negative_cases():
    error_sources = [
        '''
geo = empty_geometry(1)
output("Geometry", geo)
''',
        '''
geo = empty_geometry(foo=1)
output("Geometry", geo)
''',
        '''
geo = point()
output("Geometry", geo)
''',
        '''
geo = point(1)
output("Geometry", geo)
''',
        '''
geo = line(vector(0, 0, 0))
output("Geometry", geo)
''',
        '''
geo = line(vector(0, 0, 0), 1)
output("Geometry", geo)
''',
        '''
geo = join([cube(1), 1])
output("Geometry", geo)
''',
        '''
geo = join()
output("Geometry", geo)
''',
    ]
    for index, source in enumerate(error_sources):
        expect_compile_error(source, f"NFTest_empty_point_line_error_{index}")
