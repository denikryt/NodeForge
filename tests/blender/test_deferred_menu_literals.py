"""Known inline String constants use literal-only Blender Menu socket defaults."""

import bpy
import pytest

from helpers import compile_root
from NodeForge.blender import raw_nodes
from NodeForge.blender.root_update import RootUpdateError

pytestmark = pytest.mark.blender


def _compile_menu(source, suffix):
    """Compile a test source into a fresh tree, returning its raw Menu node."""
    tree = bpy.data.node_groups.new(f'NFTest_Menu_Deferred_{suffix}', 'GeometryNodeTree')
    compile_root(source, tree)
    nodes = [n for n in tree.nodes if n.bl_idname == 'GeometryNodeResampleCurve']
    assert len(nodes) == 1
    return tree, nodes[0]


@pytest.mark.parametrize('call,expected', [('wrap()', 'Count'), ("wrap('Length')", 'Length')])
def test_inline_menu_string_default_and_explicit_enum_use_native_literal(call, expected):
    """Inline String constants retain raw literal metadata without producers."""
    tree, node = _compile_menu('''def wrap(mode: String = 'Count', __inline__=True) -> Geometry:
    return node('GeometryNodeResampleCurve', inputs={ID('Mode'):mode}, output=ID('Curve'), typ=Geometry)
result = ''' + call, expected)
    socket, = [s for s in node.inputs if s.identifier == 'Mode' and s.bl_idname == 'NodeSocketMenu']
    assert socket.bl_idname == 'NodeSocketMenu'
    assert socket.default_value == expected and not socket.is_linked
    assert len(tree.links) == 0
    assert not any(n.bl_idname == 'FunctionNodeInputString' for n in tree.nodes)
    spec = raw_nodes.raw_node_spec(node)
    assert spec['schema_version'] == raw_nodes.RAW_SCHEMA_VERSION
    assert any(e['mode'] == raw_nodes.INPUT_LITERAL and e['default'] == expected
               and e['socket'].identifier == socket.identifier for e in spec['inputs'])


@pytest.mark.parametrize('bad', ["wrap('NOT_A_MODE')", "wrap('')"])
def test_inline_menu_invalid_enum_is_rejected_without_publication(bad):
    """Invalid/empty compile-time strings cannot publish a partial Menu node."""
    tree = bpy.data.node_groups.new('NFTest_Menu_Deferred_Invalid', 'GeometryNodeTree')
    source = '''def wrap(mode: String = 'Count', __inline__=True) -> Geometry:
    return node('GeometryNodeResampleCurve', inputs={ID('Mode'):mode}, output=ID('Curve'), typ=Geometry)
result = ''' + bad
    with pytest.raises(RootUpdateError):
        compile_root(source, tree)
    assert not [n for n in tree.nodes if n.bl_idname == 'GeometryNodeResampleCurve']


def test_runtime_string_menu_remains_unsupported():
    """A physical String output is not interchangeable with a Menu enum."""
    tree = bpy.data.node_groups.new('NFTest_Menu_Deferred_Runtime', 'GeometryNodeTree')
    source = '''def wrap(mode: String, __inline__=True) -> Geometry:
    return node('GeometryNodeResampleCurve', inputs={ID('Mode'):mode}, output=ID('Curve'), typ=Geometry)
value = node('FunctionNodeInputString', props={'string':'Count'}, output='String', typ=String)
result = wrap(value)
'''
    with pytest.raises(RootUpdateError, match='compile-time String enum literal'):
        compile_root(source, tree)
    assert not [n for n in tree.nodes if n.bl_idname == 'GeometryNodeResampleCurve']


def test_menu_rejects_deferred_non_string_type_at_physical_boundary():
    """A typed numeric constant cannot bypass the strict Menu String contract."""
    from NodeForge.blender.input_delivery import validate_input
    from NodeForge.blender.values import DeferredConstant
    from NodeForge.errors import CompileError
    from NodeForge.nf_types import NFType

    tree = bpy.data.node_groups.new('NFTest_Menu_Deferred_Type', 'GeometryNodeTree')
    node = tree.nodes.new('GeometryNodeResampleCurve')
    socket, = [s for s in node.inputs if s.identifier == 'Mode' and s.bl_idname == 'NodeSocketMenu']
    with pytest.raises(CompileError, match='String enum literal'):
        validate_input(tree, socket, DeferredConstant(NFType.INT, 1))
    assert not socket.is_linked
