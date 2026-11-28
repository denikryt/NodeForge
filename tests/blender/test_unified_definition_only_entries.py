"""Definition-only unified sources place zero-incoming module entries at level zero."""

from helpers import *


def _group_nodes(tree):
    """Return physical nested group references in display order."""
    return [node for node in tree.nodes if node.bl_idname == 'GeometryNodeGroup']


def test_definition_only_entries_have_correct_nested_levels_and_required_inputs():
    """A→B→C with D places only A and D on the base node tree."""
    group = bpy.data.node_groups.new('NFTest_definition_only_entries', 'GeometryNodeTree')
    original = group.nodes.new('GeometryNodeMeshCube')
    compile_root('''def leaf(x: Float) -> Float:
    return x + 1

def middle(x: Float) -> Float:
    return leaf(x)

def entry_a(x: Float) -> Float:
    return middle(x)

def entry_d(x: Float) -> Float:
    return x + 2
''', group)
    entries = [published_entry(group, name) for name in ('entry_a', 'entry_d')]
    instances = _group_nodes(group)
    assert [node.node_tree for node in instances] == entries
    assert all(len(node.inputs) == 1 and not node.inputs[0].is_linked for node in instances)
    entry_a = entries[0]
    child = _group_nodes(entry_a)
    assert len(child) == 1 and child[0].node_tree.get('nodeforge_module_name') == 'middle'
    grandchild = _group_nodes(child[0].node_tree)
    assert len(grandchild) == 1 and grandchild[0].node_tree.get('nodeforge_module_name') == 'leaf'
    assert any(node.as_pointer() == original.as_pointer() for node in group.nodes)
    assert len(group.interface.items_tree) == 0


def test_independent_mixed_definitions_are_placed():
    """An executable expression retains independent entry placement."""
    group = bpy.data.node_groups.new('NFTest_mixed_no_auto_entries', 'GeometryNodeTree')
    compile_root('''a = cube(1)

def unused(x: Float) -> Float:
    return x + 1
''', group)
    assert [node.node_tree for node in _group_nodes(group)] == [published_entry(group, 'unused')]
    assert any(node.bl_idname == 'GeometryNodeMeshCube' for node in group.nodes)


def test_definition_only_constant_declarations_do_not_disable_entries():
    """Compile-time-only source declarations do not count as executable composition."""
    group = bpy.data.node_groups.new('NFTest_compile_time_entries', 'GeometryNodeTree')
    compile_root('''FACTOR = 2

def constant_entry(x: Float) -> Float:
    return x + FACTOR
''', group)
    assert [node.node_tree for node in _group_nodes(group)] == [published_entry(group, 'constant_entry')]


def test_ambiguous_untyped_definition_only_entry_is_rejected():
    """An entry requiring unspecified type inference must not become arbitrary Float."""
    group = bpy.data.node_groups.new('NFTest_untyped_entry', 'GeometryNodeTree')
    with pytest.raises(CompileError, match='requires an explicit type annotation'):
        compile_root('''def ambiguous(x):
    return x
''', group)
    assert len(group.nodes) == 0


def test_definition_only_recursion_rejected_before_group_creation():
    """A recursive graph has no valid zero-incoming entry and must be diagnosed."""
    group = bpy.data.node_groups.new('NFTest_recursive_definition_entries', 'GeometryNodeTree')
    with pytest.raises(CompileError, match='Recursive'):
        compile_root('''def recur(x: Float) -> Float:
    return recur(x)
''', group)
    assert len(group.nodes) == 0


def test_two_entries_share_the_same_nested_callee():
    """Multiple roots reuse a common nested definition without duplicate backing owners."""
    group = bpy.data.node_groups.new('NFTest_shared_entries', 'GeometryNodeTree')
    compile_root('''def _common(x: Float) -> Float:
    return x + 1

def a(x: Float) -> Float:
    return _common(x)

def b(x: Float) -> Float:
    return _common(x)
''', group)
    roots = [published_entry(group, name) for name in ('a', 'b')]
    assert [node.node_tree for node in _group_nodes(group)] == roots
    shared = [_group_nodes(group)[0].node_tree for group in roots]
    assert shared[0].as_pointer() == shared[1].as_pointer()
    assert shared[0].get('nodeforge_module_name') == '_common'


def test_top_level_function_call_does_not_duplicate_explicit_entry():
    """Explicit invocation suppresses only the invoked entry."""
    group = bpy.data.node_groups.new('NFTest_called_entry_only', 'GeometryNodeTree')
    compile_root('''def a(x: Float) -> Float:
    return x + 2

def uncalled(x: Float) -> Float:
    return x + 3

v = a(1)
''', group)
    groups = _group_nodes(group)
    assert [node.node_tree.get('nodeforge_module_name') for node in groups] == ['a', 'uncalled']


def test_nested_compile_time_branch_places_only_reachable_entry():
    """Nested CT writes must produce the same edges as actual group materialization."""
    tree = bpy.data.node_groups.new('NFTest_nested_ct_true_entry', 'GeometryNodeTree')
    compile_root('''FLAG = False

def a():
    if True:
        FLAG = True
        if FLAG:
            value = b()
    return 1

def b():
    return 2
''', tree)
    entry = published_entry(tree, 'a')
    assert [node.node_tree for node in _group_nodes(tree)] == [entry]
    children = _group_nodes(entry)
    assert [node.node_tree.get('nodeforge_module_name') for node in children] == ['b']


def test_nested_compile_time_dead_call_does_not_hide_independent_entry():
    """Nested CT reassignment prunes a dead call in both graph and Blender body."""
    tree = bpy.data.node_groups.new('NFTest_nested_ct_false_entry', 'GeometryNodeTree')
    compile_root('''FLAG = True

def a():
    if True:
        FLAG = False
        if FLAG:
            value = b()
    return 1

def b():
    return 2
''', tree)
    entries = [published_entry(tree, name) for name in ('a', 'b')]
    assert [node.node_tree for node in _group_nodes(tree)] == entries
    assert _group_nodes(entries[0]) == []


def _entries_by_name(tree):
    """Index root group instances by canonical definition name."""
    return {node.node_tree.get('nodeforge_module_name'): node for node in _group_nodes(tree)}


_MIXED = '''def a() -> Geometry:
    return cube(1)

def helper(size: Float = 2.0) -> Geometry:
    return cube(size)

def preview(size: Float = 3.0) -> Geometry:
    return helper(size)

def alternate(size: Float) -> Geometry:
    return helper(size)
'''


def test_mixed_entries_recompile_and_share_nested_owner():
    """Root replacement retains one instance per independent entry and canonical shared children."""
    from test_unified_root_update import _root, _interface
    tree, inlet, outlet = _root('NFTest_mixed_recompile')
    interface = _interface(tree)
    structural = inlet.as_pointer(), outlet.as_pointer()
    source = _MIXED + '\nresult = a()\n'
    first = compile_root(source, tree)
    assert first.outcome == 'ROOT_REPLACEMENT' and not first.placed_function_groups
    entries = _entries_by_name(tree)
    assert list(entries) == ['a', 'preview', 'alternate']
    assert len(_group_nodes(tree)) == 3
    assert not tree.links
    assert entries['preview'].inputs[0].default_value == 3.0
    assert not entries['alternate'].inputs[0].is_linked
    helper, = _group_nodes(entries['preview'].node_tree)
    assert helper.node_tree == _group_nodes(entries['alternate'].node_tree)[0].node_tree
    ptrs = {name: node.node_tree.as_pointer() for name, node in entries.items()}
    entries['preview'].location = (123, 456)
    tree.links.new(entries['a'].outputs[0], outlet.inputs['Geometry'])
    tree.name = 'NFTest_mixed_recompile_renamed'
    for text in (source, source.replace('return helper(size)', 'return helper(size * 2.0)', 1),
                 source + 'p = preview()\n', source):
        compile_root(text, tree)
        current = _entries_by_name(tree)
        assert len(_group_nodes(tree)) == 3
        assert {name: node.node_tree.as_pointer() for name, node in current.items()} == ptrs
        assert _interface(tree) == interface
        assert (inlet.as_pointer(), outlet.as_pointer()) == structural
        assert outlet.inputs['Geometry'].links[0].from_node == current['a']
    assert tuple(_entries_by_name(tree)['preview'].location) == (123, 456)
    compile_root(source.split('def alternate')[0] + '\nresult = a()\n', tree)
    assert list(_entries_by_name(tree)) == ['a', 'preview']


@pytest.mark.parametrize('option', ['__inline__=True', '__unique__=True'])
def test_explicit_modes_do_not_place_extra_shared_entry(option):
    """A physical or inline explicit call is not also an independent automatic entry."""
    tree = bpy.data.node_groups.new('NFTest_mixed_modes', 'GeometryNodeTree')
    compile_root(_MIXED + f'\nresult = a({option})\n', tree)
    entries = _entries_by_name(tree)
    assert set(entries) == ({'preview', 'alternate'} if 'inline' in option else {'a', 'preview', 'alternate'})
    assert len(_group_nodes(tree)) == len(entries)
    assert len(tree.interface.items_tree) == 0
    assert not tree.links


def test_mixed_mode_transitions_and_text_identity():
    """Definition-only remains additive; root instances share owners only for the same Text."""
    first = bpy.data.node_groups.new('NFTest_mixed_text1', 'GeometryNodeTree')
    second = bpy.data.node_groups.new('NFTest_mixed_text2', 'GeometryNodeTree')
    third = bpy.data.node_groups.new('NFTest_mixed_text3', 'GeometryNodeTree')
    origin = 'text:plan53-shared'
    compile_root(_MIXED, first, module_context=origin)
    assert len(_group_nodes(first)) == 3
    mixed = _MIXED + '\nresult = a()\n'
    compile_root(mixed, first, module_context=origin)
    prior = {node.as_pointer() for node in first.nodes}
    compile_root(_MIXED, first, module_context=origin)
    assert {node.as_pointer() for node in first.nodes} == prior
    compile_root(mixed, second, module_context=origin)
    compile_root(mixed, third, module_context='text:plan53-distinct')
    assert len(_group_nodes(first)) == len(_group_nodes(second)) == len(_group_nodes(third)) == 3
    for name, node in _entries_by_name(first).items():
        assert node.node_tree == _entries_by_name(second)[name].node_tree
        assert node.node_tree != _entries_by_name(third)[name].node_tree


def test_mixed_imported_definitions_are_demand_driven(tmp_path, monkeypatch):
    """Foreign uncalled entries are not placed by the selected module's Compile."""
    from NodeForge import local_sources
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: tmp_path)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'registry.json')
    (tmp_path / 'shapes.nf').write_text('def a() -> Geometry:\n    return cube(2)\ndef foreign_unused() -> Geometry:\n    return cube(7)\n')
    tree = bpy.data.node_groups.new('NFTest_mixed_import', 'GeometryNodeTree')
    compile_root('from local import shapes\ndef preview() -> Geometry:\n    return cube(3)\nx = shapes.a()\n', tree)
    assert [node.node_tree.get('nodeforge_module_name') for node in _group_nodes(tree)] == ['a', 'preview']
    assert not any(group.get('nodeforge_module_name') == 'foreign_unused' for group in bpy.data.node_groups)


@pytest.mark.parametrize('failure', ['second-entry', 'placement', 'invalid-body', 'transfer'])
def test_mixed_entry_precommit_failure_preserves_original(failure, monkeypatch):
    """Materialization and partial placement use one transaction, before root cutover."""
    from NodeForge.blender import direct_composition, root_update
    from NodeForge.blender.function_materializer import FunctionMaterializer
    from test_root_rollback_journal import _snapshot, _inventory
    tree = bpy.data.node_groups.new('NFTest_mixed_entry_failure', 'GeometryNodeTree')
    source = _MIXED + '\nresult = a()\n'
    compile_root(source, tree)
    preview = _entries_by_name(tree)['preview'].node_tree
    other = bpy.data.node_groups.new('NFTest_mixed_external', 'GeometryNodeTree')
    instance = other.nodes.new('GeometryNodeGroup')
    instance.node_tree = preview
    instance.inputs[0].default_value = 9.0
    sink = other.nodes.new('GeometryNodeTransform')
    other.links.new(instance.outputs[0], sink.inputs[0])
    before = _snapshot(tree), _snapshot(preview, interface_ids=False), _snapshot(other), _inventory()
    pointers = {node.as_pointer() for node in tree.nodes}
    preview_ptr = preview.as_pointer()
    owner = dict(tree.items())
    updated = source.replace('return helper(size)', 'return helper(size * 2.0)', 1)
    with monkeypatch.context() as patch:
        if failure == 'second-entry':
            original = FunctionMaterializer.materialize_module
            def fail_second(self, prepared, materialization, context, **kwargs):
                if prepared.contract.function_id.name == 'alternate':
                    raise RuntimeError('injected mixed entry materialization')
                return original(self, prepared, materialization, context, **kwargs)
            patch.setattr(FunctionMaterializer, 'materialize_module', fail_second)
            error = 'injected mixed entry materialization'
        elif failure == 'placement':
            original = direct_composition.place_definition_entries
            def fail_placement(target, groups, created_nodes):
                assert target != tree
                original(target, groups[:1], created_nodes)
                assert len(created_nodes) == 1
                raise RuntimeError('injected mixed entry placement')
            patch.setattr(direct_composition, 'place_definition_entries', fail_placement)
            error = 'injected mixed entry placement'
        elif failure == 'transfer':
            from NodeForge.blender import root_subgraph_transfer
            original = root_subgraph_transfer.transfer_private_root_subgraph
            def fail_transfer(candidate, target, **kwargs):
                transferred = original(candidate, target, **kwargs)
                assert transferred.created_nodes
                raise RuntimeError('injected mixed entry transfer')
            patch.setattr(root_subgraph_transfer, 'transfer_private_root_subgraph', fail_transfer)
            error = 'injected mixed entry transfer'
        else:
            updated = updated.replace('return helper(size)', 'return missing_name', 1)
            error = 'Unknown name: missing_name'
        with pytest.raises(root_update.RootUpdateError, match=error):
            compile_root(updated, tree)
    assert {node.as_pointer() for node in tree.nodes} == pointers
    assert preview.as_pointer() == preview_ptr and instance.node_tree == preview
    assert dict(tree.items()) == owner
    assert (_snapshot(tree), _snapshot(preview, interface_ids=False), _snapshot(other), _inventory()) == before
    compile_root(source, tree)
    assert len(_group_nodes(tree)) == 3


def test_mixed_entries_preserve_evaluated_explicit_geometry():
    """Independent unconnected entries do not change the geometry produced by a call."""
    from test_unified_root_update import _root
    tree, _, outlet = _root('NFTest_mixed_eval')
    source = _MIXED + '\nresult = a()\n'
    compile_root(source, tree)
    tree.links.new(_entries_by_name(tree)['a'].outputs[0], outlet.inputs['Geometry'])
    mesh = bpy.data.meshes.new('NFTest_mixed_eval_mesh')
    obj = bpy.data.objects.new('NFTest_mixed_eval_obj', mesh)
    bpy.context.collection.objects.link(obj)
    try:
        obj.modifiers.new('NFTest_mixed_eval', 'NODES').node_group = tree
        first = _evaluated_mesh_snapshot(obj)
        assert len(first[0]) == 8 and first[2] == 6
        assert max(v[0] for v in first[0]) - min(v[0] for v in first[0]) == 1.0
        compile_root(source.replace('return helper(size)', 'return helper(size * 4.0)', 1), tree)
        assert _evaluated_mesh_snapshot(obj) == first
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)
