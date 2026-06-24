from helpers import *
from NodeForge.builtins import layout


def _nodes(group, bl_idname):
    return [node for node in group.nodes if getattr(node, "bl_idname", "") == bl_idname]


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
