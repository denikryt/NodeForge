"""Definition-only unified sources place zero-incoming module entries at level zero."""

from helpers import *


def _group_nodes(tree):
    """Return physical nested group references in display order."""
    return [node for node in tree.nodes if node.bl_idname == 'GeometryNodeGroup']


def test_definition_only_entries_have_correct_nested_levels_and_required_inputs():
    """A→B→C with D places only A and D on the base node tree."""
    group = bpy.data.node_groups.new('NFTest_definition_only_entries', 'GeometryNodeTree')
    original = group.nodes.new('GeometryNodeMeshCube')
    compiler.compose_source_initial('''def leaf(x: Float) -> Float:
    return x + 1

def middle(x: Float) -> Float:
    return leaf(x)

def entry_a(x: Float) -> Float:
    return middle(x)

def entry_d(x: Float) -> Float:
    return x + 2
''', group)
    entries = _group_nodes(group)
    assert len(entries) == 2
    assert {node.node_tree.get('nodeforge_module_name') for node in entries} == {'entry_a', 'entry_d'}
    entry_a = next(node for node in entries if node.node_tree.get('nodeforge_module_name') == 'entry_a')
    assert entry_a.inputs[0].is_linked is False
    child = _group_nodes(entry_a.node_tree)
    assert len(child) == 1 and child[0].node_tree.get('nodeforge_module_name') == 'middle'
    grandchild = _group_nodes(child[0].node_tree)
    assert len(grandchild) == 1 and grandchild[0].node_tree.get('nodeforge_module_name') == 'leaf'
    assert any(node.as_pointer() == original.as_pointer() for node in group.nodes)
    assert len(group.interface.items_tree) == 0


def test_definition_only_entry_graph_does_not_insert_unused_mixed_definitions():
    """One effective executable expression disables automatic entry placement."""
    group = bpy.data.node_groups.new('NFTest_mixed_no_auto_entries', 'GeometryNodeTree')
    compiler.compose_source_initial('''a = cube(1)

def unused(x: Float) -> Float:
    return x + 1
''', group)
    assert len(_group_nodes(group)) == 0
    assert any(node.bl_idname == 'GeometryNodeMeshCube' for node in group.nodes)


def test_definition_only_constant_declarations_do_not_disable_entries():
    """Compile-time-only source declarations do not count as executable composition."""
    group = bpy.data.node_groups.new('NFTest_compile_time_entries', 'GeometryNodeTree')
    compiler.compose_source_initial('''FACTOR = 2

def constant_entry(x: Float) -> Float:
    return x + FACTOR
''', group)
    assert len(_group_nodes(group)) == 1


def test_ambiguous_untyped_definition_only_entry_is_rejected():
    """An entry requiring unspecified type inference must not become arbitrary Float."""
    group = bpy.data.node_groups.new('NFTest_untyped_entry', 'GeometryNodeTree')
    with pytest.raises(CompileError, match='cannot infer type'):
        compiler.compose_source_initial('''def ambiguous(x):
    return x
''', group)
    assert len(group.nodes) == 0


def test_definition_only_recursion_rejected_before_group_creation():
    """A recursive graph has no valid zero-incoming entry and must be diagnosed."""
    group = bpy.data.node_groups.new('NFTest_recursive_definition_entries', 'GeometryNodeTree')
    with pytest.raises(CompileError, match='Recursive'):
        compiler.compose_source_initial('''def recur(x: Float) -> Float:
    return recur(x)
''', group)
    assert len(group.nodes) == 0


def test_two_entries_share_the_same_nested_callee():
    """Multiple roots reuse a common nested definition without duplicate backing owners."""
    group = bpy.data.node_groups.new('NFTest_shared_entries', 'GeometryNodeTree')
    compiler.compose_source_initial('''def _common(x: Float) -> Float:
    return x + 1

def a(x: Float) -> Float:
    return _common(x)

def b(x: Float) -> Float:
    return _common(x)
''', group)
    roots = _group_nodes(group)
    assert len(roots) == 2
    shared = [_group_nodes(node.node_tree)[0].node_tree for node in roots]
    assert shared[0].as_pointer() == shared[1].as_pointer()
    assert shared[0].get('nodeforge_module_name') == '_common'


def test_top_level_function_call_does_not_create_automatic_entries():
    """Executable top-level invocation suppresses entries for untouched definitions."""
    group = bpy.data.node_groups.new('NFTest_called_entry_only', 'GeometryNodeTree')
    compiler.compose_source_initial('''def a(x: Float) -> Float:
    return x + 2

def uncalled(x: Float) -> Float:
    return x + 3

v = a(1)
''', group)
    groups = _group_nodes(group)
    assert len(groups) == 1
    assert groups[0].node_tree.get('nodeforge_module_name') == 'a'
