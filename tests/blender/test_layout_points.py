from helpers import *
from NodeForge.builtins import layout


def _nodes(group, bl_idname):
    return [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]


def _library_nodes(group, library_name):
    """Return top-level group nodes that call a materialized library function."""
    out = []
    for node in _nodes(group, "GeometryNodeGroup"):
        tree = getattr(node, "node_tree", None)
        getter = getattr(tree, "get", None)
        if callable(getter) and getter("nodeforge_library_name") == library_name:
            out.append(node)
    return out


def _library_tree(name):
    """Return the materialized GeometryNodeTree for one function-library entry."""
    group = compiler.create_library_function_group(name)
    check(getattr(group, "bl_idname", None) == "GeometryNodeTree", name)
    return group

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


MIGRATED_LAYOUT_NAMES = {
    "layout_grid",
    "grid_points",
    "layout_circle",
    "layout_spiral",
    "spiral_points",
    "layout_random",
    "random_points",
}


def test_layout_builtin_names_removed_after_stage4():
    check(layout.NAMES == set(), f"layout.NAMES should be empty after Stage 4: {sorted(layout.NAMES)}")
    check(MIGRATED_LAYOUT_NAMES.isdisjoint(registry.CALLABLE_BUILTIN_NAMES), "migrated layout helpers should not be global built-ins")
    check("circle_points" not in layout.NAMES, "circle_points should not remain a layout built-in")
    check("circle_points" not in registry.CALLABLE_BUILTIN_NAMES, "circle_points should not remain globally callable")


def test_grid_layouts_compile_and_use_point_sources():
    group = compile_group('''
from functions import layout_grid, grid_points
pts = points(6)
pts = layout_grid(pts, count=vector(3, 2, 1), spacing=vector(1.0, 2.0, 3.0))
out = grid_points(count=vector(3, 2, 1), spacing=vector(1.25, 1.25, 1.25), centered=True)
output("Geometry", join(pts, out))
''', 'NFTest_layout_grid_compile')
    check(_library_nodes(group, "layout_grid"), "expected imported layout_grid library call")
    check(_library_nodes(group, "grid_points"), "expected imported grid_points library call")
    grid_tree = _library_tree("grid_points")
    layout_tree = _library_tree("layout_grid")
    check(_nodes(layout_tree, "GeometryNodeSetPosition"), "expected Set Position inside layout_grid")
    check(_nodes(grid_tree, "GeometryNodeMeshLine"), "expected Mesh Line point source inside grid_points")
    check(not _nodes(grid_tree, "GeometryNodeMeshGrid"), "grid_points() must not use Mesh Grid")




def test_grid_points_allows_compile_time_zero_count_component():
    group = compile_group('''
from functions import grid_points
geo = grid_points(count=vector(0, 2, 1), spacing=vector(1.0, 1.0, 1.0))
output("Geometry", geo)
''', 'NFTest_grid_points_zero_count_component')
    check(_library_nodes(group, "grid_points"), "expected imported grid_points library call")
    grid_tree = _library_tree("grid_points")
    check(_nodes(grid_tree, "GeometryNodeMeshLine"), "expected Mesh Line zero-point source inside grid_points")

def test_imported_circle_points_compile_forms_and_materializes_library_group():
    for source, name in (
        ('''
from functions import circle_points
geo = circle_points(16, radius=1.0)
output("Geometry", geo)
''', 'NFTest_circle_points_import'),
        ('''
from functions import circle_points as circle
geo = circle(8, start_angle=0, end_angle=pi, include_endpoint=True)
output("Geometry", geo)
''', 'NFTest_circle_points_alias_import'),
        ('''
from functions import *
geo = circle_points(12)
output("Geometry", geo)
''', 'NFTest_circle_points_star_import'),
    ):
        group = compile_group(source, name)
        function_nodes = [
            node for node in _nodes(group, "GeometryNodeGroup")
            if getattr(getattr(node, "node_tree", None), "get", lambda key, default=None: default)("nodeforge_library_name") == "circle_points"
        ]
        check(function_nodes, "imported circle_points should call the materialized library group")


def test_circle_points_library_discovery_and_reuse():
    check(library.has_library_function("circle_points"), "circle_points flat function was not discovered")
    check("circle_points" in library.library_function_names(), "circle_points missing from public function names")
    check(not library.has_module_library_function("circle_points"), "circle_points must not use a package-local backend")
    first = compiler.create_library_function_group("circle_points")
    before = set(bpy.data.node_groups.keys())
    second = compiler.create_library_function_group("circle_points")
    check(first.name == second.name, "circle_points library group should be reused")
    check(set(bpy.data.node_groups.keys()) == before, "circle_points library reuse created duplicate groups")


def test_circle_points_import_does_not_mutate_global_builtins():
    before_builtins = set(registry.CALLABLE_BUILTIN_NAMES)
    compile_group('''
from functions import *
geo = circle_points(4)
output("Geometry", geo)
''', 'NFTest_circle_points_star_no_global_mutation')
    check(set(registry.CALLABLE_BUILTIN_NAMES) == before_builtins, "circle_points star import mutated callable builtins")


def test_circle_points_unimported_global_call_fails():
    expect_compile_error('''
geo = circle_points(16)
output("Geometry", geo)
''', 'NFTest_circle_points_unimported_fails')


def test_circle_points_star_import_assignment_conflict():
    expect_compile_error('''
from functions import *
circle_points = 1
output("circle_points", circle_points)
''', 'NFTest_circle_points_star_assignment_conflict')


def test_imported_circle_points_evaluates_circle_arc_and_zero_count():
    circle = compile_group('''
from functions import circle_points
geo = circle_points(4, radius=1.0)
output("Geometry", geo)
''', 'NFTest_circle_points_eval_circle')
    circle_positions = _evaluated_vertices(circle, "NFTestCirclePointsEvalCircle")
    check(_same_positions(circle_positions, [(1, 0, 0), (0, 1, 0), (-1, 0, 0), (0, -1, 0)]), "circle_points(4) should cover the full circle without duplicating the endpoint")

    arc = compile_group('''
from functions import circle_points
geo = circle_points(3, start_angle=0, end_angle=pi, include_endpoint=True)
output("Geometry", geo)
''', 'NFTest_circle_points_eval_arc_endpoint')
    arc_positions = _evaluated_vertices(arc, "NFTestCirclePointsEvalArc")
    check(_same_positions(arc_positions, [(1, 0, 0), (0, 1, 0), (-1, 0, 0)]), "endpoint arc should include both endpoints")

    zero = compile_group('''
from functions import circle_points
geo = circle_points(0)
output("Geometry", geo)
''', 'NFTest_circle_points_eval_zero')
    check(_evaluated_vertices(zero, "NFTestCirclePointsEvalZero") == [], "circle_points(0) should produce no points")


def test_imported_spiral_and_random_points_compile():
    group = compile_group('''
from functions import spiral_points, random_points
spiral = spiral_points(32, radius=2.0, turns=3, height=2.0)
rand = random_points(10, min=vector(-1,-1,-1), max=vector(1,1,1), seed=3)
output("Geometry", join(spiral, rand))
''', 'NFTest_layout_spiral_random_compile')
    check(_library_nodes(group, "spiral_points"), "expected imported spiral_points library call")
    check(_library_nodes(group, "random_points"), "expected imported random_points library call")
    random_tree = _library_tree("layout_random")
    random_nodes = _nodes(random_tree, "FunctionNodeRandomValue")
    check(random_nodes, "layout_random() should create Random Value node")
    check(any(getattr(node, "data_type", None) == "FLOAT_VECTOR" for node in random_nodes), "layout_random() should use vector random values")


def test_layout_random_on_existing_points_compile():
    group = compile_group('''
from functions import layout_random
pts = points(10)
pts = layout_random(pts, min=vector(-2,-2,0), max=vector(2,2,1), seed=3)
output("Geometry", pts)
''', 'NFTest_layout_random_existing_points')
    check(_nodes(group, "GeometryNodeMeshLine"), "expected source points")
    check(_library_nodes(group, "layout_random"), "expected imported layout_random library call")
    random_tree = _library_tree("layout_random")
    check(_nodes(random_tree, "GeometryNodeSetPosition"), "expected Set Position inside layout_random")
    check(_nodes(random_tree, "FunctionNodeRandomValue"), "expected Random Value node inside layout_random")


def test_layout_composition_fixture_compiles():
    group = compile_group('''
from functions import grid_points
pts = grid_points(count=vector(3, 2, 1), spacing=vector(1.25, 1.25, 1.25))
geo = instance_on_points(cube(0.5), pts)
output("Geometry", geo)
''', 'NFTest_layout_composition_fixture')
    check(_nodes(group, "GeometryNodeInstanceOnPoints"), "expected Instance on Points")
    check(_library_nodes(group, "grid_points"), "expected imported grid_points library call")


def test_random_value_existing_forms_still_compile():
    compile_group('''
seed = input_int("Seed", default=3)
a = random_value(0, 1, seed=3)
b = random_value(vector(0,0,0), vector(1,1,1), seed=seed, id=index())
output("v", a)
''', 'NFTest_layout_random_value_regression')


def test_migrated_layout_helpers_compile_through_import_forms_and_star_import():
    cases = (
        ('''
from functions import layout_circle as lc
pts = points(8)
pts = lc(pts, count=8, radius=2.0)
output("Geometry", pts)
''', "NFTest_layout_circle_alias_import", "layout_circle"),
        ('''
from functions import layout_grid, grid_points
pts = layout_grid(points(4), count=vector(2, 2, 1), spacing=vector(1, 1, 1))
grid = grid_points(count=vector(2, 2, 1), spacing=vector(1, 1, 1), centered=True)
output("Geometry", join(pts, grid))
''', "NFTest_layout_multi_import", "layout_grid"),
        ('''
from functions import *
pts = random_points(4, min=vector(-1, -1, -1), max=vector(1, 1, 1), seed=5)
output("Geometry", pts)
''', "NFTest_layout_star_import", "random_points"),
    )
    for source, name, library_name in cases:
        group = compile_group(source, name)
        check(_library_nodes(group, library_name), f"expected imported {library_name} library call")


def test_layout_function_library_discovery_and_reuse():
    expected = {
        "layout_grid",
        "grid_points",
        "layout_circle",
        "layout_spiral",
        "spiral_points",
        "layout_random",
        "random_points",
        "copy_by_offsets",
    }
    names = library.library_function_names()
    check(expected.issubset(names), "migrated layout functions missing from public function names")
    for name in expected:
        check(library.has_library_function(name), f"{name} was not discovered")
        check(not library.has_module_library_function(name), f"{name} must not use a package-local backend")
    first = compiler.create_library_function_group("grid_points")
    copy_first = compiler.create_library_function_group("copy_by_offsets")
    before = set(bpy.data.node_groups.keys())
    second = compiler.create_library_function_group("grid_points")
    copy_second = compiler.create_library_function_group("copy_by_offsets")
    check(first.name == second.name, "grid_points library group should be reused")
    check(copy_first.name == copy_second.name, "copy_by_offsets library group should be reused")
    check(set(bpy.data.node_groups.keys()) == before, "library reuse created duplicate groups")


def test_layout_import_does_not_mutate_global_builtins():
    before_builtins = set(registry.CALLABLE_BUILTIN_NAMES)
    compile_group('''
from functions import *
geo = random_points(4)
output("Geometry", geo)
''', 'NFTest_layout_star_no_global_mutation')
    check(set(registry.CALLABLE_BUILTIN_NAMES) == before_builtins, "layout star import mutated callable builtins")


def test_migrated_layout_unimported_global_calls_fail():
    sources = {
        "layout_grid": 'geo = layout_grid(points(3), count=vector(3,1,1))\noutput("Geometry", geo)',
        "grid_points": 'geo = grid_points(count=vector(3,1,1))\noutput("Geometry", geo)',
        "layout_circle": 'geo = layout_circle(points(4), count=4)\noutput("Geometry", geo)',
        "layout_spiral": 'geo = layout_spiral(points(4), count=4)\noutput("Geometry", geo)',
        "spiral_points": 'geo = spiral_points(4)\noutput("Geometry", geo)',
        "layout_random": 'geo = layout_random(points(4))\noutput("Geometry", geo)',
        "random_points": 'geo = random_points(4)\noutput("Geometry", geo)',
        "copy_by_offsets": 'geo = copy_by_offsets(cube(1))\noutput("Geometry", geo)',
    }
    for name, source in sources.items():
        expect_compile_error(source, f'NFTest_layout_unimported_{name}')


def test_layout_import_conflict_with_star_imported_helper():
    expect_compile_error('''
from functions import *
layout_grid = 1
output("layout_grid", layout_grid)
''', 'NFTest_layout_star_assignment_conflict')


def test_imported_layout_invalid_call_errors_are_controlled():
    sources = [
        'from functions import layout_grid\ngeo = layout_grid(points(3), count=3)\noutput("Geometry", geo)',
        'from functions import grid_points\ngeo = grid_points(count=3)\noutput("Geometry", geo)',
        'from functions import layout_circle\ngeo = layout_circle(1, count=10)\noutput("Geometry", geo)',
        'from functions import layout_spiral\ngeo = layout_spiral(points(5), count=5, turns=vector(1,0,0))\noutput("Geometry", geo)',
        'from functions import random_points\ngeo = random_points(10, min=0, max=1)\noutput("Geometry", geo)',
        'from functions import grid_points\ngeo = grid_points(count=vector(1,1,1), foo=1)\noutput("Geometry", geo)',
        'x = random_value(vector(0,0,0), 1)\noutput("x", x)',
        'x = random_value(0, 1, seed=vector(1,0,0))\noutput("x", x)',
        'x = random_value(0, 1, id=0.5)\noutput("x", x)',
    ]
    for index, source in enumerate(sources):
        expect_compile_error(source, f'NFTest_layout_error_{index}')


def test_imported_layout_circle_explicit_count_does_not_insert_domain_size():
    group = compile_group('''
from functions import layout_circle
pts = points(16)
pts = layout_circle(pts, count=8, radius=2.0)
output("Geometry", pts)
''', 'NFTest_layout_circle_explicit_count_override')
    check(_library_nodes(group, "layout_circle"), "expected imported layout_circle library call")
    circle_tree = _library_tree("layout_circle")
    check(not _nodes(circle_tree, "GeometryNodeAttributeDomainSize"), "imported explicit-count layout_circle should not need Domain Size")


def test_imported_layout_spiral_explicit_count_keeps_endpoint_denominator():
    group = compile_group('''
from functions import layout_spiral
pts = points(5)
pts = layout_spiral(pts, count=5, radius=2.0, height=1.0)
output("Geometry", pts)
''', 'NFTest_layout_spiral_explicit_endpoint')
    check(_library_nodes(group, "layout_spiral"), "expected imported layout_spiral library call")
    spiral_tree = _library_tree("layout_spiral")
    set_positions = _nodes(spiral_tree, "GeometryNodeSetPosition")
    check(set_positions, "expected Set Position")
    check(
        _has_endpoint_denominator_chain_upstream_of_position(spiral_tree, set_positions[0]),
        "spiral explicit count must keep count - 1 endpoint denominator branch",
    )


def test_imported_circular_layouts_evaluate_with_explicit_count():
    circle = compile_group('''
from functions import layout_circle
pts = points(5)
pts = layout_circle(pts, count=5, radius=2.0)
output("Geometry", pts)
''', 'NFTest_layout_circle_explicit_eval')
    spiral = compile_group('''
from functions import layout_spiral
pts = points(5)
pts = layout_spiral(pts, count=5, radius=2.0, turns=1.0, height=1.0)
output("Geometry", pts)
''', 'NFTest_layout_spiral_explicit_eval')
    circle_positions = _evaluated_vertices(circle, "NFTestCircleExplicitEval")
    spiral_positions = _evaluated_vertices(spiral, "NFTestSpiralExplicitEval")
    check(len(circle_positions) == 5, "explicit circle layout should keep source point count")
    check(len(spiral_positions) == 5, "explicit spiral layout should keep source point count")
    check(abs(spiral_positions[-1][2] - 1.0) <= 1e-4, "spiral explicit endpoint should reach final height")
