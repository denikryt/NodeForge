from helpers import *
from NodeForge.builtins import layout


def _nodes(group, bl_idname):
    return [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]



def _socket_key(socket):
    """Return a stable key for Blender RNA socket proxies used in link traversal."""
    try:
        return socket.as_pointer()
    except Exception:
        return id(socket)


def _same_socket(left, right):
    """Compare Blender sockets by RNA pointer instead of Python proxy identity."""
    return _socket_key(left) == _socket_key(right)


def _socket_reaches_node_input(group, source_socket, target_node, target_input_name="Position"):
    """Return True when source_socket is upstream of a target input socket."""
    target_socket = target_node.inputs[target_input_name]
    seen_sockets = set()
    stack = [target_socket]
    while stack:
        socket = stack.pop()
        socket_id = _socket_key(socket)
        if socket_id in seen_sockets:
            continue
        seen_sockets.add(socket_id)
        for link in group.links:
            if not _same_socket(link.to_socket, socket):
                continue
            if _same_socket(link.from_socket, source_socket):
                return True
            stack.extend(link.from_node.inputs)
    return False


def _has_math_operation_from_socket(group, source_socket, operation):
    """Return True when source_socket feeds a downstream Math node operation."""
    seen_nodes = set()
    stack = [source_socket]
    while stack:
        socket = stack.pop()
        for link in group.links:
            if link.from_socket != socket:
                continue
            node = link.to_node
            node_id = id(node)
            if node_id in seen_nodes:
                continue
            seen_nodes.add(node_id)
            if getattr(node, "bl_idname", "") == "ShaderNodeMath" and getattr(node, "operation", "") == operation:
                return True
            stack.extend(node.outputs)
    return False


def _has_endpoint_denominator_chain_upstream_of_position(group, set_position_node):
    """Detect the count - 1, max(..., 1), index / denom chain used by endpoint layouts."""
    for subtract in _nodes(group, "ShaderNodeMath"):
        if getattr(subtract, "operation", "") != "SUBTRACT":
            continue
        for sub_link in group.links:
            if sub_link.from_node != subtract:
                continue
            maximum = sub_link.to_node
            if getattr(maximum, "bl_idname", "") != "ShaderNodeMath" or getattr(maximum, "operation", "") != "MAXIMUM":
                continue
            for max_link in group.links:
                if max_link.from_node != maximum:
                    continue
                divide = max_link.to_node
                if getattr(divide, "bl_idname", "") != "ShaderNodeMath" or getattr(divide, "operation", "") != "DIVIDE":
                    continue
                if _socket_reaches_node_input(group, divide.outputs[0], set_position_node, "Position"):
                    return True
    return False


def _evaluated_vertices(group, name):
    """Evaluate a Geometry Nodes group on an empty mesh and return vertex coordinates."""
    mesh = bpy.data.meshes.new(name + "Mesh")
    obj = bpy.data.objects.new(name + "Object", mesh)
    bpy.context.scene.collection.objects.link(obj)
    modifier = obj.modifiers.new(name="NodeForge", type="NODES")
    modifier.node_group = group
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    evaluated_mesh = evaluated.to_mesh()
    try:
        return [tuple(vertex.co) for vertex in evaluated_mesh.vertices]
    finally:
        evaluated.to_mesh_clear()


def _same_positions(left, right, epsilon=1e-4):
    """Return True when two evaluated vertex coordinate lists match closely."""
    if len(left) != len(right):
        return False
    return all(abs(a - b) <= epsilon for lco, rco in zip(left, right) for a, b in zip(lco, rco))


def test_layout_builtin_names_registered():
    expected = {
        "layout_grid",
        "grid_points",
        "layout_circle",
        "circle_points",
        "layout_spiral",
        "spiral_points",
        "layout_random",
        "random_points",
    }
    check(layout.NAMES == expected, f"layout.NAMES drifted: {sorted(layout.NAMES ^ expected)}")
    check(expected.issubset(registry.CALLABLE_BUILTIN_NAMES), "layout names missing from registry")


def test_grid_layouts_compile_and_use_point_sources():
    group = compile_group('''
pts = points(6)
pts = layout_grid(pts, count=vector(3, 2, 1), spacing=vector(1.0, 2.0, 3.0))
out = grid_points(count=vector(3, 2, 1), spacing=1.25, centered=True)
output("Geometry", join(pts, out))
''', 'NFTest_layout_grid_compile')
    check(_nodes(group, "GeometryNodeSetPosition"), "expected Set Position nodes")
    check(_nodes(group, "GeometryNodeMeshLine"), "expected Mesh Line point source")
    check(not _nodes(group, "GeometryNodeMeshGrid"), "grid_points() must not use Mesh Grid")




def test_grid_points_allows_compile_time_zero_count_component():
    group = compile_group('''
geo = grid_points(count=vector(0, 2, 1), spacing=1.0)
output("Geometry", geo)
''', 'NFTest_grid_points_zero_count_component')
    check(_nodes(group, "GeometryNodeMeshLine"), "expected Mesh Line zero-point source")
    check(_nodes(group, "GeometryNodeSetPosition"), "expected Set Position for zero-count shortcut")

def test_circle_spiral_and_random_points_compile():
    group = compile_group('''
circle = circle_points(16, radius=1.0)
arc = circle_points(5, start_angle=0, end_angle=pi, include_endpoint=True)
spiral = spiral_points(32, radius=2.0, turns=3, height=2.0)
rand = random_points(10, min=vector(-1,-1,-1), max=vector(1,1,1), seed=3)
output("Geometry", join(circle, arc, spiral, rand))
''', 'NFTest_layout_circle_spiral_random_compile')
    check(len(_nodes(group, "GeometryNodeMeshLine")) >= 4, "expected Mesh Line for shortcut helpers")
    check(len(_nodes(group, "GeometryNodeSetPosition")) >= 4, "expected Set Position for layouts")
    random_nodes = _nodes(group, "FunctionNodeRandomValue")
    check(random_nodes, "random_points() should create Random Value node")
    check(any(getattr(node, "data_type", None) == "FLOAT_VECTOR" for node in random_nodes), "random_points() should use vector random values")


def test_layout_random_on_existing_points_compile():
    group = compile_group('''
pts = points(10)
pts = layout_random(pts, min=vector(-2,-2,0), max=vector(2,2,1), seed=3)
output("Geometry", pts)
''', 'NFTest_layout_random_existing_points')
    check(_nodes(group, "GeometryNodeMeshLine"), "expected source points")
    check(_nodes(group, "GeometryNodeSetPosition"), "expected Set Position")
    check(_nodes(group, "FunctionNodeRandomValue"), "expected Random Value node")


def test_layout_composition_fixture_compiles():
    group = compile_group('''
pts = grid_points(count=vector(3, 2, 1), spacing=1.25)
geo = instance_on_points(cube(0.5), pts)
output("Geometry", geo)
''', 'NFTest_layout_composition_fixture')
    check(_nodes(group, "GeometryNodeInstanceOnPoints"), "expected Instance on Points")
    check(_nodes(group, "GeometryNodeSetPosition"), "expected Set Position")


def test_random_value_existing_forms_still_compile():
    compile_group('''
seed = input_int("Seed", default=3)
a = random_value(0, 1, seed=3)
b = random_value(vector(0,0,0), vector(1,1,1), seed=seed, id=index())
output("v", a)
''', 'NFTest_layout_random_value_regression')


def test_layout_controlled_errors():
    sources = [
        'geo = layout_grid(points(3), spacing=1.0)\noutput("Geometry", geo)',
        'geo = layout_grid(points(3), count=3)\noutput("Geometry", geo)',
        'geo = grid_points(count=3)\noutput("Geometry", geo)',
        'geo = layout_circle(1, count=10)\noutput("Geometry", geo)',
        'geo = circle_points(vector(1,2,3))\noutput("Geometry", geo)',
        'geo = layout_spiral(points(5), count=5, turns=vector(1,0,0))\noutput("Geometry", geo)',
        'geo = random_points(10, min=0, max=1)\noutput("Geometry", geo)',
        'geo = grid_points(count=vector(1,2,3), centered=input_bool("C"))\noutput("Geometry", geo)',
        'geo = grid_points(count=vector(1,1,1), foo=1)\noutput("Geometry", geo)',
        'geo = grid_points(count=vector(-1,2,1))\noutput("Geometry", geo)',
        'geo = layout_grid(points(3), count=vector(0,2,1))\noutput("Geometry", geo)',
        'geo = layout_grid(points(3), count=vector(2.5,2,1))\noutput("Geometry", geo)',
        'geo = grid_points(count=vector(2,2.5,1))\noutput("Geometry", geo)',
        'geo = circle_points(-4)\noutput("Geometry", geo)',
        'geo = layout_circle(points(4), count=-1)\noutput("Geometry", geo)',
        'geo = layout_spiral(points(4), count=-1)\noutput("Geometry", geo)',
        'x = random_value(vector(0,0,0), 1)\noutput("x", x)',
        'x = random_value(0, 1, seed=vector(1,0,0))\noutput("x", x)',
        'x = random_value(0, 1, id=0.5)\noutput("x", x)',
    ]
    for index, source in enumerate(sources):
        expect_compile_error(source, f'NFTest_layout_error_{index}')


def test_layout_circle_derives_count_from_input_points():
    group = compile_group('''
pts = points(16)
pts = layout_circle(pts, radius=2.0)
output("Geometry", pts)
''', 'NFTest_layout_circle_derived_count')
    domains = _nodes(group, "GeometryNodeAttributeDomainSize")
    check(domains, "expected Domain Size for derived count")
    domain = domains[0]
    point_count = domain.outputs["Point Count"]
    check(point_count.links, "derived Point Count must feed layout formula")
    check(
        any(getattr(link.to_node, "bl_idname", "") == "ShaderNodeMath" for link in point_count.links),
        "Point Count should feed normalized-index math",
    )
    set_positions = _nodes(group, "GeometryNodeSetPosition")
    check(set_positions, "expected Set Position")
    check(
        _socket_reaches_node_input(group, point_count, set_positions[0], "Position"),
        "derived Point Count must be upstream of Set Position.Position",
    )


def test_layout_spiral_derives_count_from_input_points_with_endpoint_denominator():
    group = compile_group('''
pts = points(16)
pts = layout_spiral(pts, radius=2.0, turns=3.0, height=1.0)
output("Geometry", pts)
''', 'NFTest_layout_spiral_derived_count')
    domains = _nodes(group, "GeometryNodeAttributeDomainSize")
    check(domains, "expected Domain Size for derived count")
    domain = domains[0]
    point_count = domain.outputs["Point Count"]
    check(point_count.links, "derived Point Count must feed spiral formula")
    set_positions = _nodes(group, "GeometryNodeSetPosition")
    check(set_positions, "expected Set Position")
    check(
        _socket_reaches_node_input(group, point_count, set_positions[0], "Position"),
        "derived Point Count must be upstream of Set Position.Position",
    )
    check(
        _has_math_operation_from_socket(group, point_count, "SUBTRACT"),
        "spiral derived count must use count - 1 endpoint denominator branch",
    )


def test_derived_circular_layouts_evaluate_like_matching_explicit_count():
    circle_derived = compile_group("""
pts = points(5)
pts = layout_circle(pts, radius=2.0)
output("Geometry", pts)
""", 'NFTest_layout_circle_derived_eval')
    circle_explicit = compile_group("""
pts = points(5)
pts = layout_circle(pts, count=5, radius=2.0)
output("Geometry", pts)
""", 'NFTest_layout_circle_explicit_eval')
    spiral_derived = compile_group("""
pts = points(5)
pts = layout_spiral(pts, radius=2.0, turns=1.0, height=1.0)
output("Geometry", pts)
""", 'NFTest_layout_spiral_derived_eval')
    spiral_explicit = compile_group("""
pts = points(5)
pts = layout_spiral(pts, count=5, radius=2.0, turns=1.0, height=1.0)
output("Geometry", pts)
""", 'NFTest_layout_spiral_explicit_eval')

    circle_positions = _evaluated_vertices(circle_derived, "NFTestCircleDerivedEval")
    spiral_positions = _evaluated_vertices(spiral_derived, "NFTestSpiralDerivedEval")
    check(
        _same_positions(circle_positions, _evaluated_vertices(circle_explicit, "NFTestCircleExplicitEval")),
        "derived circle layout should evaluate like matching explicit count",
    )
    check(
        _same_positions(spiral_positions, _evaluated_vertices(spiral_explicit, "NFTestSpiralExplicitEval")),
        "derived spiral layout should evaluate like matching explicit count",
    )
    check(abs(spiral_positions[-1][2] - 1.0) <= 1e-4, "spiral derived endpoint should reach final height")


def test_layout_circle_explicit_count_does_not_insert_domain_size():
    group = compile_group('''
pts = points(16)
pts = layout_circle(pts, count=8, radius=2.0)
output("Geometry", pts)
''', 'NFTest_layout_circle_explicit_count_override')
    check(not _nodes(group, "GeometryNodeAttributeDomainSize"), "explicit count should not need Domain Size")


def test_layout_spiral_explicit_count_keeps_endpoint_denominator():
    group = compile_group('''
pts = points(5)
pts = layout_spiral(pts, count=5, radius=2.0, height=1.0)
output("Geometry", pts)
''', 'NFTest_layout_spiral_explicit_endpoint')
    set_positions = _nodes(group, "GeometryNodeSetPosition")
    check(set_positions, "expected Set Position")
    check(
        _has_endpoint_denominator_chain_upstream_of_position(group, set_positions[0]),
        "spiral explicit count must keep count - 1 endpoint denominator branch",
    )
