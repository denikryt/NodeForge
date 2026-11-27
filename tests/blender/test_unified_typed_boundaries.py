"""Physical type-interface regressions for independent .nf function definitions."""

from helpers import compile_root, published_entry

import ast

import bpy
import pytest

from NodeForge import compiler
from NodeForge.errors import CompileError

pytestmark = pytest.mark.blender


def _compose(source: str):
    """Compile a definition-only module into a fresh existing user tree."""
    tree = bpy.data.node_groups.new('NFTest_unified_typed_interface', 'GeometryNodeTree')
    compile_root(source, tree)
    placed, = tree.nodes
    # Root pointer addresses can be recycled after a previous test removes its tree.
    # Filter by the declaration's canonical public name, not just the test origin.
    fn_name = next(node.name for node in ast.parse(source).body if isinstance(node, ast.FunctionDef))
    groups = [g for g in bpy.data.node_groups
              if g.get('nodeforge_module_name') == fn_name
              and f"test:root:{tree.as_pointer()}" in str(g.get('nodeforge_module_origin', ''))]
    assert groups == [placed.node_tree]
    return tree, placed.node_tree


@pytest.mark.parametrize('source,input_type,output_type', [
    ('def echo(value: String) -> String:\n    return value\n', 'NodeSocketString', 'NodeSocketString'),
    ('def keep_material(value: Material) -> Material:\n    return value\n', 'NodeSocketMaterial', 'NodeSocketMaterial'),
    ('def keep_object(value: Object) -> Object:\n    return value\n', 'NodeSocketObject', 'NodeSocketObject'),
])
def test_definition_interface_preserves_typed_input_and_return(source, input_type, output_type):
    """The new declaration path maps material/object/string sockets without legacy I/O."""
    _root, nested = _compose(source)
    sockets = [item for item in nested.interface.items_tree if item.item_type == 'SOCKET']
    ins = [item for item in sockets if item.in_out == 'INPUT']
    outs = [item for item in sockets if item.in_out == 'OUTPUT']
    assert len(ins) == len(outs) == 1
    assert ins[0].socket_type == input_type
    assert outs[0].socket_type == output_type


def test_nested_material_function_wires_material_socket():
    """Material inputs remain runtime-linked to the Set Material node."""
    _root, nested = _compose('''def paint(geo: Geometry, material: Material) -> Geometry:
    return set_material(geo, material)
''')
    node = next(item for item in nested.nodes if item.bl_idname == 'GeometryNodeSetMaterial')
    assert node.inputs['Material'].is_linked
    assert node.inputs['Material'].bl_idname == 'NodeSocketMaterial'


def test_typed_argument_mismatch_leaves_existing_tree_untouched():
    """Incompatible types fail before adding an invalid call Group Node."""
    root = bpy.data.node_groups.new('NFTest_unified_typed_mismatch', 'GeometryNodeTree')
    existing = root.nodes.new('GeometryNodeMeshCube')
    with pytest.raises(CompileError):
        compile_root('''def echo(value: String) -> String:
    return value

result = echo(cube(1.0))
''', root)
    assert tuple(root.nodes) == (existing,)


def test_definition_bundle_parameter_and_return_use_native_bundle_socket():
    """Bundle remains a typed physical interface in the unified frontend."""
    _root, nested = _compose('''def keep_bundle(value: Bundle) -> Bundle:
    return value
''')
    sockets = [item for item in nested.interface.items_tree if item.item_type == 'SOCKET']
    assert len(sockets) == 2
    assert all('Bundle' in item.socket_type for item in sockets)


def test_explicit_float_defaults_and_int_calls_share_one_physical_group():
    """An Int runtime argument does not specialize a declared Float input."""
    tree = bpy.data.node_groups.new('NFTest_explicit_float_identity', 'GeometryNodeTree')
    compile_root('def scale(value: Float = 2.0) -> Float:\n    return value\n\na = scale()\nb = scale(3)\nc = scale(3.5)\n', tree)
    calls = [node for node in tree.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(calls) == 3
    assert len({node.node_tree.as_pointer() for node in calls}) == 1
    sockets = [item for item in calls[0].node_tree.interface.items_tree
               if item.item_type == 'SOCKET' and item.in_out == 'INPUT']
    assert len(sockets) == 1
    assert sockets[0].socket_type == 'NodeSocketFloat'
    assert sockets[0].default_value == 2.0


def test_definition_only_invalid_unused_parameter_does_not_mutate_user_tree():
    """A bad unused declaration is rejected before publishing any level-zero node."""
    tree = bpy.data.node_groups.new('NFTest_explicit_unused_rollback', 'GeometryNodeTree')
    original = tree.nodes.new('GeometryNodeMeshCube')
    with pytest.raises(CompileError, match='requires an explicit type annotation'):
        compile_root('cube(1)\ndef unused(x=3):\n    return x\n', tree)
    assert tuple(tree.nodes) == (original,)


def test_imported_default_uses_definition_module_not_caller_constants(tmp_path):
    """Imported defaults are evaluated with the defining module's captured constants."""
    from NodeForge import catalog
    from NodeForge.local_index import LocalSourceIndex
    from NodeForge.resolved_environment import ResolvedEnvironment, ResolvedCatalog

    local_file = tmp_path / 'defaults.nf'
    local_file.write_text('''RATE = 2.0

def imported_echo(value: Float = RATE) -> Float:
    return value
''', encoding='utf-8')
    local_index = LocalSourceIndex.from_discovery(*catalog.discover_local_sources((tmp_path,)))
    environment = ResolvedEnvironment({
        name: ResolvedCatalog(name, {}, local_index=local_index if name == 'local' else None)
        for name in ('functions', 'examples', 'local')
    })
    root = bpy.data.node_groups.new('NFTest_imported_explicit_default', 'GeometryNodeTree')
    compile_root('''RATE = 99.0
from local import defaults
result = defaults.imported_echo()
''', root, resolved_environment=environment)
    nodes = [node for node in root.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(nodes) == 1
    inputs = [item for item in nodes[0].node_tree.interface.items_tree
              if item.item_type == 'SOCKET' and item.in_out == 'INPUT']
    assert len(inputs) == 1
    assert inputs[0].socket_type == 'NodeSocketFloat'
    assert inputs[0].default_value == 2.0
