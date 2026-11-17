"""Physical Blender 5.2 regressions for .nf occurrence-scoped inline compilation."""

from __future__ import annotations

import pytest

from helpers import *

pytestmark = pytest.mark.blender


def _compose(source, title):
    """Compile a source module into a fresh Geometry Nodes base tree."""
    tree = bpy.data.node_groups.new(title, 'GeometryNodeTree')
    compiler.compose_source_initial(source, tree)
    return tree


def _nodes(tree, name):
    """Return Blender nodes having one native RNA identifier."""
    return [node for node in tree.nodes if node.bl_idname == name]


def test_inline_single_raw_wrapper_native_nodes_and_shared_group():
    """Two inline occurrences create native nodes, while False stays grouped."""
    tree = _compose('''def merge_by_distance(geometry: Geometry, max_distance: Float = 0.001, __inline__=True) -> Geometry:
    return node('GeometryNodeMergeByDistance', inputs={'Geometry': geometry, 'Distance': max_distance}, output='Geometry', typ=Geometry)
base = cube(1.0)
one = merge_by_distance(base)
two = merge_by_distance(one, max_distance=0.02)
three = merge_by_distance(two, __inline__=False)
''', 'NFTest_Inline_Raw')
    assert len(_nodes(tree, 'GeometryNodeMergeByDistance')) == 2
    assert len(_nodes(tree, 'GeometryNodeGroup')) == 1
    assert len(_nodes(_nodes(tree, 'GeometryNodeGroup')[0].node_tree, 'GeometryNodeMergeByDistance')) == 1
    assert all(link.is_valid for link in tree.links)
    assert all(node.inputs['Geometry'].is_linked for node in _nodes(tree, 'GeometryNodeMergeByDistance'))


def test_inline_scalar_tuple_and_panel_reordered_parameters():
    """An occurrence binds panel-reordered inputs and returns a flat tuple."""
    tree = _compose('''def calculate(a: Float, b: Float, c: Float = 3, __inline__: Bool = True):
    panel([c, a], name='Controls')
    return (a + b, c + a)
first, second = calculate(1.0, 2.0)
result = first + second
''', 'NFTest_Inline_Tuple_Panel')
    assert not _nodes(tree, 'GeometryNodeGroup')
    assert len(_nodes(tree, 'ShaderNodeMath')) >= 3
    assert all(link.is_valid for link in tree.links)
    assert not any(item.item_type == 'PANEL' for item in tree.interface.items_tree)


def test_inline_typed_defaults_keep_color_alpha_and_float_conversion():
    """All static inputs are runtime nodes; Int fields widen to Float explicitly."""
    tree = _compose('''def float_identity(value: Float, __inline__=True) -> Float:
    return value

def color_identity(value: Color = (0.1, 0.2, 0.3, 0.4), __inline__=True) -> Color:
    return value
value = float_identity(index())
rgba = color_identity()
''', 'NFTest_Inline_Typed_Defaults')
    assert not _nodes(tree, 'GeometryNodeGroup')
    assert _nodes(tree, 'ShaderNodeMath')
    color_nodes = _nodes(tree, 'FunctionNodeInputColor')
    assert len(color_nodes) == 1
    assert tuple(color_nodes[0].value) == pytest.approx((0.1, 0.2, 0.3, 0.4), abs=1e-7)
    assert all(link.is_valid for link in tree.links)


def test_inline_call_scopes_do_not_alias_backing_nodes():
    """Repeated calls create independent native nodes without callee datablocks."""
    tree = _compose('''def increment(v: Float, __inline__=True) -> Float:
    return v + 1.0
x = increment(2.0)
y = increment(3.0)
z = x + y
''', 'NFTest_Inline_Occurrence_Scopes')
    assert not _nodes(tree, 'GeometryNodeGroup')
    assert len(_nodes(tree, 'ShaderNodeMath')) >= 3
    assert all(link.is_valid for link in tree.links)


def test_nested_group_outer_contains_native_inline_inner():
    """A grouped outer keeps its physical boundary, not the inline inner's."""
    root = _compose('''def inner(value: Float, __inline__=True) -> Float:
    return value + 1.0

def outer(value: Float) -> Float:
    return inner(value)
result = outer(2.0)
''', 'NFTest_Inline_Group_Outer')
    assert len(_nodes(root, 'GeometryNodeGroup')) == 1
    group = _nodes(root, 'GeometryNodeGroup')[0].node_tree
    assert len(_nodes(group, 'ShaderNodeMath')) == 1
    assert not _nodes(group, 'GeometryNodeGroup')


def test_nested_inline_outer_preserves_grouped_inner():
    """One inner group remains physically nested under an inline outer call."""
    root = _compose('''def inner(value: Float) -> Float:
    return value + 1.0

def outer(value: Float, __inline__=True) -> Float:
    return inner(value)
result = outer(2.0)
''', 'NFTest_Inline_Outer_Group_Inner')
    assert len(_nodes(root, 'GeometryNodeGroup')) == 1
    assert len(_nodes(_nodes(root, 'GeometryNodeGroup')[0].node_tree, 'ShaderNodeMath')) == 1


def test_nested_inline_inside_inline_never_constructs_helper_group():
    """Two nested source bodies are lowered to the exact same caller tree."""
    root = _compose('''def inner(value: Float, __inline__=True) -> Float:
    return value + 1.0

def outer(value: Float, __inline__=True) -> Float:
    return inner(value) * 2.0
result = outer(2.0)
''', 'NFTest_Inline_Inside_Inline')
    assert not _nodes(root, 'GeometryNodeGroup')
    assert len(_nodes(root, 'ShaderNodeMath')) == 2


def test_nested_unique_inside_two_inline_occurrences_produces_two_backing_groups():
    """An inline owner's scope participates in its nested UNIQUE instance key."""
    root = _compose('''def inner(value: Float) -> Float:
    return value + 1.0

def outer(value: Float, __inline__=True) -> Float:
    return inner(value, __unique__=True)
first = outer(2.0)
second = outer(3.0)
''', 'NFTest_Inline_Nested_Unique')
    nodes = _nodes(root, 'GeometryNodeGroup')
    assert len(nodes) == 2
    assert nodes[0].node_tree.as_pointer() != nodes[1].node_tree.as_pointer()


def test_inline_repeat_if_and_grid_uv_use_caller_tree_without_context_leaks():
    """If/Repeat and contextual values lower inside the active physical tree."""
    root = _compose('''def compute(value: Float, flag: Bool, __inline__=True) -> Float:
    for i in repeat_range(2):
        if flag:
            value = value + 1.0
        else:
            value = value + 2.0
    return value

def make_uv(size: Float, __inline__=True) -> Vector:
    geo = grid(size, 3.0)
    return grid_uv()
result = compute(1.0, True)
first_uv = make_uv(2.0)
second_uv = make_uv(3.0)
''', 'NFTest_Inline_Context_If_Repeat')
    assert not _nodes(root, 'GeometryNodeGroup')
    assert len(_nodes(root, 'GeometryNodeRepeatInput')) == 1
    assert len(_nodes(root, 'GeometryNodeRepeatOutput')) == 1
    assert len(_nodes(root, 'GeometryNodeSwitch')) >= 1
    assert len(_nodes(root, 'GeometryNodeMeshGrid')) == 2
    assert all(link.is_valid for link in root.links)


def test_two_inline_object_users_have_private_info_nodes():
    """Object.info caches belong to each inline invocation, not the source socket."""
    outer = compile_entry_group('''def read_location(obj: Object, __inline__=True) -> Vector:
    obj.info(as_instance=False)
    return obj.location

def grouped_parent(obj: Object) -> Vector:
    first = read_location(obj)
    second = read_location(obj)
    return first + second
''', 'NFTest_Inline_Object_Info', 'grouped_parent')
    assert len(_nodes(outer, 'GeometryNodeObjectInfo')) == 2
    assert not _nodes(outer, 'GeometryNodeGroup')
    assert all(node.inputs['As Instance'].default_value is False
               for node in _nodes(outer, 'GeometryNodeObjectInfo'))
