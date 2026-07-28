"""Regression tests for instance_on_points(selection=...)."""

from NodeForge.errors import CompileError

from helpers import check, compile_group


def _instance_node(group):
    nodes = [node for node in group.nodes if node.bl_idname == "GeometryNodeInstanceOnPoints"]
    check(len(nodes) == 1, "expected exactly one Instance on Points node")
    return nodes[0]


def test_instance_on_points_selection_links_bool_field():
    group = compile_group('''
pts = points(4)
selection = index() < 2
geo = instance_on_points(cube(0.5), pts, selection=selection, realize=False)
output("Geometry", geo)
''', "NFTest_instance_selection")
    socket = _instance_node(group).inputs.get("Selection")
    check(socket is not None, "expected Selection input")
    check(socket.is_linked, "expected selection field to be linked")
    check(socket.type == "BOOLEAN", "expected Boolean Selection input")


def test_instance_on_points_selection_defaults_true_when_omitted():
    group = compile_group('''
geo = instance_on_points(cube(0.5), points(2), realize=False)
output("Geometry", geo)
''', "NFTest_instance_selection_default")
    socket = _instance_node(group).inputs.get("Selection")
    check(socket is not None, "expected Selection input")
    check(not socket.is_linked, "omitted selection should remain unlinked")
    check(bool(socket.default_value) is True, "Blender default Selection should be True")


def test_instance_on_points_selection_rejects_non_bool():
    try:
        compile_group('''
geo = instance_on_points(cube(0.5), points(2), selection=1.0)
output("Geometry", geo)
''', "NFTest_instance_selection_bad")
    except CompileError as exc:
        check("selection=" in str(exc) and "Bool" in str(exc), "expected controlled Bool selection error")
    else:
        raise AssertionError("numeric selection should fail")


def test_instance_on_points_selection_combines_with_existing_keywords():
    group = compile_group('''
pts = points(4)
selection = index() >= 1
geo = instance_on_points(
    cube(0.5),
    pts,
    selection=selection,
    scale=0.5,
    rotation=vector(0.0, 0.0, 0.25),
    realize=True
)
output("Geometry", geo)
''', "NFTest_instance_selection_combined")
    check(_instance_node(group).inputs["Selection"].is_linked, "expected linked Selection")
    check(any(node.bl_idname == "GeometryNodeRealizeInstances" for node in group.nodes), "expected Realize Instances")
