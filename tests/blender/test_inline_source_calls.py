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
    tree = _compose('''def merge_by_distance(geometry: Geometry, distance: Float = 0.001, __inline__=True) -> Geometry:
    return node('GeometryNodeMergeByDistance', inputs={'Geometry': geometry, 'Distance': distance}, output='Geometry', typ=Geometry)
base = cube(1.0)
one = merge_by_distance(base)
two = merge_by_distance(one, distance=0.02)
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
