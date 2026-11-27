"""Rev5 root replacement invariants on Blender 5.2 physical datablocks."""

from helpers import *

from NodeForge.blender import root_update


def _root(name):
    """Create an eligible root with a real geometry interface and structural I/O."""
    tree = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    tree.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    output = tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    tree.interface.new_socket(name='Strength', in_out='INPUT', socket_type='NodeSocketFloat').default_value = 2.5
    inlet = tree.nodes.new('NodeGroupInput')
    outlet = tree.nodes.new('NodeGroupOutput')
    outlet.is_active_output = True
    inlet.location = (-800, 60)
    outlet.location = (800, 60)
    return tree, inlet, outlet


def _interface(tree):
    """Return socket contract preserving identifiers, order and default values."""
    return root_update._interface_signature(tree)


def test_update_preserves_root_interface_io_custom_props_and_replaces_manual_group_chain():
    """Root users/structural I/O survive while every old ordinary node is replaced."""
    tree, inlet, outlet = _root('NFR5_base_manual_chain')
    tree['user_marker'] = 'keep me'
    cube = tree.nodes.new('GeometryNodeMeshCube')
    sub = bpy.data.node_groups.new('NFR5_manual_sub', 'GeometryNodeTree')
    sub.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    sub.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    manual = tree.nodes.new('GeometryNodeGroup')
    manual.node_tree = sub
    transform = tree.nodes.new('GeometryNodeTransform')
    tree.links.new(cube.outputs['Mesh'], manual.inputs['Geometry'])
    tree.links.new(manual.outputs['Geometry'], transform.inputs['Geometry'])
    tree.links.new(transform.outputs['Geometry'], outlet.inputs['Geometry'])
    old = {node.as_pointer() for node in (cube, manual, transform)}
    io = {inlet.as_pointer(), outlet.as_pointer()}
    signature = _interface(tree)
    ptr = tree.as_pointer()
    obj = bpy.data.objects.new('NFR5_modifier_obj', bpy.data.meshes.new('NFR5_modifier_mesh'))
    bpy.context.collection.objects.link(obj)
    mod = obj.modifiers.new('Geometry', 'NODES')
    mod.node_group = tree
    input_item = next(item for item in tree.interface.items_tree if item.item_type == 'SOCKET' and item.in_out == 'INPUT' and item.name == 'Strength')
    # Some background Blender builds disallow ID properties on a bare NodesModifier.
    try:
        mod[input_item.identifier] = 7.0
        input_value_supported = True
    except TypeError:
        input_value_supported = False
    outer = bpy.data.node_groups.new('NFR5_outer', 'GeometryNodeTree')
    external = outer.nodes.new('GeometryNodeGroup')
    external.node_tree = tree
    result = compile_root('a = cube(2)\n', tree)
    assert result.target is tree
    assert tree.as_pointer() == ptr and tree['user_marker'] == 'keep me'
    assert _interface(tree) == signature
    assert {node.as_pointer() for node in tree.nodes if node.bl_idname in {'NodeGroupInput', 'NodeGroupOutput'}} == io
    assert not old.intersection(node.as_pointer() for node in tree.nodes)
    assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in tree.nodes) == 1
    assert all(node.bl_idname != 'GeometryNodeGroup' for node in tree.nodes)
    assert mod.node_group.as_pointer() == tree.as_pointer()
    if input_value_supported:
        assert mod[input_item.identifier] == 7.0
    assert external.node_tree is tree
    assert result.skipped_boundary_links == 1  # Removed Transform cannot match Cube.
    bpy.data.objects.remove(obj, do_unlink=True)


def test_update_keeps_direct_io_link_and_frame_parented_io_absolute_position():
    """Detaching structural I/O from deleted Frames preserves position and I/O edges."""
    tree, inlet, outlet = _root('NFR5_frame_io')
    frame = tree.nodes.new('NodeFrame')
    frame.location = (120, 100)
    outlet.parent = frame
    outlet.location = (350, -100)
    before = tuple(root_update._point(node) for node in (inlet, outlet))
    tree.links.new(inlet.outputs['Geometry'], outlet.inputs['Geometry'])
    result = compile_root('a = cube(1)\n', tree)
    assert result.skipped_boundary_links == 0
    assert outlet.parent is None
    assert tuple(root_update._point(node) for node in (inlet, outlet)) == before
    assert any(link.from_node.as_pointer() == inlet.as_pointer() and link.to_node.as_pointer() == outlet.as_pointer() for link in tree.links)
    assert all(node.bl_idname != 'NodeFrame' for node in tree.nodes)


def test_update_reconnects_unique_compatible_boundary_and_reuses_position():
    """A uniquely matched ordinary Cube retains I/O wiring and its absolute position."""
    tree, inlet, outlet = _root('NFR5_link_match')
    old = tree.nodes.new('GeometryNodeMeshCube')
    old.location = (333, 177)
    tree.links.new(old.outputs['Mesh'], outlet.inputs['Geometry'])
    result = compile_root('a = cube(3)\n', tree)
    cube, = [node for node in tree.nodes if node.bl_idname == 'GeometryNodeMeshCube']
    assert cube.as_pointer() != old.as_pointer()
    assert root_update._point(cube) == (333, 177)
    assert result.skipped_boundary_links == 0
    assert any(link.from_node.as_pointer() == cube.as_pointer() and link.to_node.as_pointer() == outlet.as_pointer() for link in tree.links)


def test_update_skips_ambiguous_old_boundary_without_guessing():
    """A shape with two competing Cubes does not assign a previous Cube's I/O edge."""
    tree, inlet, outlet = _root('NFR5_skip_ambiguous')
    old = tree.nodes.new('GeometryNodeMeshCube')
    tree.links.new(old.outputs['Mesh'], outlet.inputs['Geometry'])
    result = compile_root('a = cube(1)\nb = cube(2)\n', tree)
    assert result.skipped_boundary_links == 1
    assert not outlet.inputs['Geometry'].is_linked
    assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in tree.nodes) == 2


def test_compile_twice_no_accumulation():
    """Two Updates overwrite ordinary nodes; Compile alone remains append-only."""
    tree, _, _ = _root('NFR5_repeat_update')
    compile_root('a = cube(1)\nb = cube(2)', tree)
    compile_root('a = cube(7)\nb = cube(8)', tree)
    assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in tree.nodes) == 2
    compile_root('a = cube(1)', tree)
    assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in tree.nodes) == 1


def test_compile_definition_only_and_zero_node_source_preserves_manual_root():
    """Group-only Compile publishes definitions without clearing ordinary root nodes."""
    tree, inlet, outlet = _root('NFR5_definitions')
    old = tree.nodes.new('GeometryNodeMeshCube')
    compile_root('def make_cube() -> Geometry:\n    return cube(1)\n', tree)
    placed, = [node for node in tree.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert placed.node_tree == published_entry(tree, 'make_cube')
    compile_root('FLAG = False\n', tree)
    assert {node.as_pointer() for node in tree.nodes} == {node.as_pointer() for node in (inlet, outlet, old, placed)}


@pytest.mark.parametrize('source', ['', '  \n  ', 'x = unknown(1)', 'output(value=1)', 'x = input_float("x")'])
def test_update_rejects_invalid_source_before_root_mutation(source):
    """Missing, semantic-invalid or unsupported direct syntax cannot change any root node."""
    tree, inlet, outlet = _root('NFR5_invalid_source')
    old = tree.nodes.new('GeometryNodeMeshCube')
    ids = {node.as_pointer() for node in tree.nodes}
    with pytest.raises(CompileError):
        compile_root(source, tree)
    assert ids == {node.as_pointer() for node in tree.nodes}
    assert len(bpy.data.node_groups.get(tree.name).nodes) == 3


def test_update_rejects_function_private_and_mesh_compute_root_before_mutation():
    """Direct API rejects authoritative function, transaction and compute ownership."""
    from NodeForge.blender.group_authority import mark_transaction_private
    for name, key, value in (
        ('NFR5_function', 'nodeforge_function_root_owner_id', 'owner'),
        ('NFR5_compute', 'nodeforge_mesh_compute_stage_v1', 'stage'),
    ):
        tree, inlet, outlet = _root(name)
        tree[key] = value
        with pytest.raises(CompileError):
            compile_root('a = cube(1)', tree)
        assert {node.as_pointer() for node in tree.nodes} == {inlet.as_pointer(), outlet.as_pointer()}
    tree, inlet, outlet = _root('NFR5_private')
    mark_transaction_private(tree)
    with pytest.raises(CompileError):
        compile_root('a = cube(1)', tree)
    assert len(tree.nodes) == 2


def test_staging_error_preserves_old_graph_and_removes_private_backup(monkeypatch):
    """A second-node lowering fault removes only staged root nodes and copy."""
    from NodeForge.blender import ir_lowering
    tree, inlet, outlet = _root('NFR5_stage_failure')
    old = tree.nodes.new('GeometryNodeMeshCube')
    tree.links.new(old.outputs['Mesh'], outlet.inputs['Geometry'])
    pointers = {node.as_pointer() for node in tree.nodes}
    groups = {g.as_pointer() for g in bpy.data.node_groups}
    original = ir_lowering._cube_geometry
    count = 0

    def injected(*args, **kwargs):
        """Fail the second generated Cube after its physical insertion."""
        nonlocal count
        result = original(*args, **kwargs)
        count += 1
        if count == 2:
            raise RuntimeError('NFR5 stage failure')
        return result

    monkeypatch.setattr(ir_lowering, '_cube_geometry', injected)
    with pytest.raises(root_update.RootUpdateError, match='NFR5 stage failure') as info:
        compile_root('a = cube(1)\nb = cube(2)\n', tree)
    assert not info.value.may_have_mutated
    assert pointers == {node.as_pointer() for node in tree.nodes}
    assert len(tree.links) == 1 and tree.links[0].from_node.as_pointer() == old.as_pointer()
    assert {g.as_pointer() for g in bpy.data.node_groups} == groups


def test_late_failure_attempts_root_restoration_and_retains_undo(monkeypatch):
    """After old deletion begins, local recovery runs and must mark potential mutation."""
    tree, inlet, outlet = _root('NFR5_cutover_failure')
    old = tree.nodes.new('GeometryNodeMeshCube')
    tree.links.new(old.outputs['Mesh'], outlet.inputs['Geometry'])
    groups = {g.as_pointer() for g in bpy.data.node_groups}

    def injected(*args, **kwargs):
        """Inject a failure after ordinary root cutover begins."""
        raise RuntimeError('NFR5 late failure')

    monkeypatch.setattr(root_update, '_reconnect_boundary', injected)
    with pytest.raises(root_update.RootUpdateError, match='NFR5 late failure') as info:
        compile_root('a = cube(4)\n', tree)
    assert info.value.may_have_mutated
    assert len([n for n in tree.nodes if n.bl_idname == 'GeometryNodeMeshCube']) == 1
    assert len(tree.links) == 1
    assert {g.as_pointer() for g in bpy.data.node_groups} == groups


def test_update_frame_and_distinct_type_layout_without_guessing():
    """A sole matching type moves; duplicated types in changed shapes keep new layout."""
    tree, _, _ = _root('NFR5_layout')
    first = tree.nodes.new('GeometryNodeMeshCube')
    first.location = (901, 901)
    second = tree.nodes.new('GeometryNodeTransform')
    second.location = (432, 211)
    compile_root('a = cube(1)\nb = cube(2)\nc = transform(a)', tree)
    transforms = [node for node in tree.nodes if node.bl_idname == 'GeometryNodeTransform']
    assert len(transforms) == 1 and root_update._point(transforms[0]) == (432, 211)
    assert all(root_update._point(node) != (901, 901)
               for node in tree.nodes if node.bl_idname == 'GeometryNodeMeshCube')


def test_node_driver_is_rejected_while_root_interface_driver_is_allowed():
    """Only ordinary nodes' root animation paths block Update pre-materialization."""
    tree, inlet, outlet = _root('NFR5_node_driver')
    cube = tree.nodes.new('GeometryNodeMeshCube')
    cube.driver_add('location', 0)
    assert tree.animation_data is not None
    assert any('nodes[' in fcurve.data_path for fcurve in tree.animation_data.drivers)
    before = {n.as_pointer() for n in tree.nodes}
    with pytest.raises(CompileError, match='animation/driver'):
        compile_root('a = cube(3)', tree)
    assert {n.as_pointer() for n in tree.nodes} == before
    tree.animation_data_clear()
    # Structural node animation does not target an ordinary node.
    inlet.driver_add('location', 0)
    compile_root('a = cube(3)', tree)
    assert len([n for n in tree.nodes if n.bl_idname == 'GeometryNodeMeshCube']) == 1


@pytest.mark.parametrize('node_name', [
    'Odd"Name', r'Back\\slash', 'Point.name', "It's",
    'Bracket[nodes["Inner"]]',
])
def test_node_driver_guard_uses_blender_escaped_paths(node_name):
    """Ordinary nodes with escaped RNA names reject driver-backed replacement."""
    tree, _, outlet = _root('NFR5_escaped_node_path')
    node = tree.nodes.new('GeometryNodeMeshCube')
    node.name = node_name
    tree.links.new(node.outputs['Mesh'], outlet.inputs['Geometry'])
    node.driver_add('location', 0)
    assert node.path_from_id() in tree.animation_data.drivers[0].data_path
    before = {item.as_pointer() for item in tree.nodes}
    with pytest.raises(CompileError, match='animation/driver'):
        compile_root('a = cube(2)', tree)
    assert {item.as_pointer() for item in tree.nodes} == before
    assert tree.animation_data.drivers[0].data_path.startswith(node.path_from_id() + '.')


def test_structural_driver_does_not_match_similar_ordinary_node_prefix():
    """A structural node named like an ordinary one does not block Update."""
    tree, inlet, _ = _root('NFR5_io_prefix')
    ordinary = tree.nodes.new('GeometryNodeMeshCube')
    ordinary.name = 'Node'
    inlet.name = 'Node.001'
    inlet.driver_add('location', 0)
    assert inlet.path_from_id().startswith('nodes[')
    compile_root('a = cube(3)', tree)
    assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in tree.nodes) == 1


def test_directed_boundary_preserves_output_and_skips_occupied_input():
    """Only the old incoming I/O edge conflicts with a new compiler-owned input."""
    tree, inlet, outlet = _root('NFR5_directed_boundary')
    old = tree.nodes.new('GeometryNodeTransform')
    tree.links.new(inlet.outputs['Geometry'], old.inputs['Geometry'])
    tree.links.new(old.outputs['Geometry'], outlet.inputs['Geometry'])
    result = compile_root('a = cube(1)\nb = transform(a)', tree)
    transform, = (n for n in tree.nodes if n.bl_idname == 'GeometryNodeTransform')
    assert result.skipped_boundary_links == 1
    assert any(link.from_node.bl_idname == 'GeometryNodeMeshCube'
               and link.to_node.as_pointer() == transform.as_pointer() for link in tree.links)
    assert any(link.from_node.as_pointer() == transform.as_pointer()
               and link.to_node.as_pointer() == outlet.as_pointer() for link in tree.links)
    assert not any(link.from_node.as_pointer() == inlet.as_pointer()
                   and link.to_node.as_pointer() == transform.as_pointer() for link in tree.links)


def test_layout_identical_shapes_reuses_ordered_duplicate_positions():
    """Positional duplicate mapping is valid only when the entire old/new shape agrees."""
    tree, _, _ = _root('NFR5_ordered_duplicate')
    a, b = (tree.nodes.new('GeometryNodeMeshCube') for _ in range(2))
    a.location, b.location = (25, 55), (625, -75)
    compile_root('a = cube(1)\nb = cube(2)', tree)
    positions = [root_update._point(n) for n in tree.nodes if n.bl_idname == 'GeometryNodeMeshCube']
    assert positions == [(25, 55), (625, -75)]


def test_root_update_preserves_function_identity_body_reload_and_signature_change():
    """Nested functions share existing canonical materialization semantics."""
    tree, _, _ = _root('NFR5_nested_function_updates')
    context = 'NFR5_function_source_owner'
    def source(scalar_type, body):
        """Build a canonical definition with controlled type and body changes."""
        return (f'def change(value: {scalar_type}) -> Float:\n'
                f'    return {body}\n'
                'y = change(2)\n')

    compile_root(source('Float', 'value + 1'), tree, module_context=context)
    old_group = next(n.node_tree for n in tree.nodes if n.bl_idname == 'GeometryNodeGroup')
    group_pointer = old_group.as_pointer()
    compile_root(source('Float', 'value * 3'), tree, module_context=context)
    new_group = next(n.node_tree for n in tree.nodes if n.bl_idname == 'GeometryNodeGroup')
    assert new_group.as_pointer() == group_pointer
    compile_root(source('Int', 'value + 5'), tree, module_context=context)
    new_group = next(n.node_tree for n in tree.nodes if n.bl_idname == 'GeometryNodeGroup')
    assert new_group is not None and len([n for n in tree.nodes if n.bl_idname == 'GeometryNodeGroup']) == 1
    assert any(item.item_type == "SOCKET" and item.in_out == "INPUT"
               and item.socket_type == "NodeSocketInt" for item in new_group.interface.items_tree)


def test_raw_math_staged_once_and_retained_without_copying():
    """A raw ShaderNodeMath remains the physical staged node after old cutover."""
    tree, _, _ = _root('NFR5_raw_math')
    tree.nodes.new('GeometryNodeMeshCube')
    src = ('x = node("ShaderNodeMath", props={"operation": "ADD"}, '
           'inputs={ID("Value_001"): 2.0}, output=ID("Value"), typ=Float)\n')
    compile_root(src, tree)
    math_nodes = [n for n in tree.nodes if n.bl_idname == 'ShaderNodeMath']
    assert len(math_nodes) == 1
    assert math_nodes[0].operation == 'ADD'
    first_pointer = math_nodes[0].as_pointer()
    compile_root(src, tree)
    math_nodes = [n for n in tree.nodes if n.bl_idname == 'ShaderNodeMath']
    assert len(math_nodes) == 1 and math_nodes[0].as_pointer() != first_pointer


def test_private_root_staging_preserves_manual_group_chain(monkeypatch):
    """Private lowering sees an isolated stage; old manual graph remains unchanged."""
    from NodeForge.blender import direct_composition
    tree, _, outlet = _root('NFR5_manual_group_staging')
    manual = bpy.data.node_groups.new('NFR5_manual_nested', 'GeometryNodeTree')
    manual.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    manual.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    cube = tree.nodes.new('GeometryNodeMeshCube')
    nested = tree.nodes.new('GeometryNodeGroup')
    nested.node_tree = manual
    moved = tree.nodes.new('GeometryNodeTransform')
    tree.links.new(cube.outputs['Mesh'], nested.inputs['Geometry'])
    tree.links.new(nested.outputs['Geometry'], moved.inputs['Geometry'])
    tree.links.new(moved.outputs['Geometry'], outlet.inputs['Geometry'])
    old_ptrs = {node.as_pointer() for node in (cube, nested, moved)}
    old_links = len(tree.links)
    original = direct_composition.compose_prepared_initial
    seen = False

    def observe(*args, **kwargs):
        """Inspect the root during the successful staging boundary."""
        nonlocal seen
        result = original(*args, **kwargs)
        stage = args[1]
        seen = (stage is not tree and old_ptrs.issubset({node.as_pointer() for node in tree.nodes})
                and len(tree.links) == old_links)
        assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in tree.nodes) == 1
        assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in stage.nodes) == 1
        return result

    monkeypatch.setattr(direct_composition, 'compose_prepared_initial', observe)
    compile_root('a = cube(9)\n', tree)
    assert seen
    assert not old_ptrs.intersection(node.as_pointer() for node in tree.nodes)


def test_root_copy_backup_is_deleted_before_nested_commit(monkeypatch):
    """Nested commit observes a published root with no invocation-private root backup."""
    from NodeForge.blender import group_backend
    transaction_commit = group_backend.BlenderGroupBuildTransaction.commit
    counts = []
    root, _, _ = _root('NFR5_commit_backup_order')
    roots_before = {g.as_pointer() for g in bpy.data.node_groups}

    def observe(self):
        """Record live group copies at nested transaction commit."""
        counts.append({g.as_pointer() for g in bpy.data.node_groups} - roots_before)
        return transaction_commit(self)

    monkeypatch.setattr(group_backend.BlenderGroupBuildTransaction, 'commit', observe)
    compile_root('a = cube(1)', root)
    assert counts == [set()]


def test_direct_lowering_with_caller_transaction_does_not_commit(monkeypatch):
    """An externally owned transaction remains open after successful direct lowering."""
    from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
    from NodeForge.blender.direct_composition import compose_prepared_initial
    from NodeForge.blender.ir_lowering import RootInputEndpoint
    target, _, _ = _root('NFR5_owned_lowering')
    prepared, backend, session, _snapshot = compiler._prepare_direct_source('a = cube(4)', target)
    tx = BlenderGroupBuildTransaction()
    compose_prepared_initial(prepared, target, backend, session,
                             transaction=tx, root_input_endpoint=RootInputEndpoint())
    assert not tx._closed
    assert sum(n.bl_idname == 'GeometryNodeMeshCube' for n in target.nodes) == 1
    tx.rollback()
    assert tx._closed


def test_repeat_pair_stages_and_survives_physical_cutover_twice():
    """Paired Repeat input/output nodes remain functional without copying on success."""
    tree, _, _ = _root('NFR5_runtime_repeat')
    tree.nodes.new('GeometryNodeMeshCube')
    source = ('x = 0\n'
              'for i in repeat_range(3):\n'
              '    x = x + 1\n')
    compile_root(source, tree)
    pair = [node for node in tree.nodes if node.bl_idname in
            {'GeometryNodeRepeatInput', 'GeometryNodeRepeatOutput'}]
    assert len(pair) == 2
    start, = (n for n in pair if n.bl_idname == 'GeometryNodeRepeatInput')
    end, = (n for n in pair if n.bl_idname == 'GeometryNodeRepeatOutput')
    assert start.paired_output.as_pointer() == end.as_pointer()
    compile_root(source, tree)
    pair = [node for node in tree.nodes if node.bl_idname in
            {'GeometryNodeRepeatInput', 'GeometryNodeRepeatOutput'}]
    assert len(pair) == 2
    start, = (n for n in pair if n.bl_idname == 'GeometryNodeRepeatInput')
    end, = (n for n in pair if n.bl_idname == 'GeometryNodeRepeatOutput')
    assert start.paired_output.as_pointer() == end.as_pointer()


def test_update_accepts_import_and_rejects_missing_import_without_root_change(tmp_path, monkeypatch):
    """Update reuses the Local module resolver and preflights unresolved imports."""
    from NodeForge import local_sources
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: tmp_path)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'registry.json')
    (tmp_path / 'shapes.nf').write_text('def model() -> Geometry:\n    return cube(2)\n')
    tree, _, _ = _root('NFR5_import_source')
    compile_root('from local import shapes\nx = shapes.model()\n', tree)
    assert sum(n.bl_idname == 'GeometryNodeGroup' for n in tree.nodes) == 1
    before = {n.as_pointer() for n in tree.nodes}
    with pytest.raises(CompileError):
        compile_root('from local import missing_module\nx = missing_module.model()', tree)
    assert before == {n.as_pointer() for n in tree.nodes}


def test_staging_failure_after_nested_update_preserves_old_root_but_marks_possible_change(monkeypatch):
    """A shared nested rollback cannot be promised globally atomic on stage errors."""
    from NodeForge.blender.function_materializer import FunctionMaterializer
    tree, _, _ = _root('NFR5_nested_stage_rollback')
    context = 'NFR5_nested_rollback_owner'
    compile_root('''def shared() -> Geometry:
    return cube(1)
''', tree, module_context=context)
    old_root = {node.as_pointer() for node in tree.nodes}
    old_group = next(g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == 'shared'
                     and context in str(g.get('nodeforge_module_origin', '')))
    pointer = old_group.as_pointer()
    nodes_before = tuple((n.bl_idname, n.name) for n in old_group.nodes)
    original = FunctionMaterializer.materialize_module

    def failing_second(self, prepared, materialization, scope, **kwargs):
        """Fail before second entry after a candidate group was already updated."""
        if prepared.contract.function_id.name == 'extra':
            raise RuntimeError('NFR5 nested stage failure')
        return original(self, prepared, materialization, scope, **kwargs)

    monkeypatch.setattr(FunctionMaterializer, 'materialize_module', failing_second)
    with pytest.raises(RuntimeError, match='NFR5 nested stage failure'):
        compile_root('''def shared() -> Geometry:
    return cube(5)
def extra() -> Geometry:
    return cube(6)
''', tree, module_context=context)
    assert {node.as_pointer() for node in tree.nodes} == old_root
    assert old_group.as_pointer() == pointer
    assert tuple((n.bl_idname, n.name) for n in old_group.nodes) == nodes_before


def test_linked_root_rejected_before_materialization(tmp_path):
    """Blender library-linked GeometryNodeTree is not an editable root target."""
    tree, _, _ = _root('NFR5_library_source')
    tree.use_fake_user = True
    pathname = tmp_path / 'external_root.blend'
    assert bpy.ops.wm.save_as_mainfile(filepath=str(pathname), copy=True) == {'FINISHED'}
    with bpy.data.libraries.load(str(pathname), link=True) as (src, dst):
        dst.node_groups = [tree.name]
    linked = dst.node_groups[0]
    assert linked is not None and linked.library is not None
    with pytest.raises(CompileError, match='linked or read-only'):
        compile_root('a = cube(1)', linked)
    assert len(linked.nodes) == 2


def test_backup_removal_failure_is_reported_as_possible_mutation(monkeypatch):
    """A failed temporary-state cleanup cannot be treated as clean success."""
    tree, _, _ = _root('NFR5_cleanup_failure')
    tree.nodes.new('GeometryNodeMeshCube')
    originals = {n.as_pointer() for n in tree.nodes}

    def failed_remove(_backup):
        """Simulate an unavailable native backup cleanup step."""
        raise RuntimeError('NFR5 backup cleanup error')

    monkeypatch.setattr(root_update, '_remove_backup', failed_remove)
    with pytest.raises(root_update.RootUpdateError, match='backup cleanup') as info:
        compile_root('a = cube(7)', tree)
    assert info.value.may_have_mutated
    assert all(n.bl_idname != 'ShaderNodeMath' for n in tree.nodes)


def test_unexpected_staged_structural_io_is_rejected_and_removed(monkeypatch):
    """Only original structural Group I/O can survive Update, even on stage failure."""
    from NodeForge.blender import direct_composition
    root, inlet, outlet = _root('NFR5_invalid_staged_io')
    before = {n.as_pointer() for n in root.nodes}
    original = direct_composition.compose_prepared_initial

    def insert_unexpected_io(*args, **kwargs):
        """Simulate a new structural I/O node escaping direct lowering."""
        result = original(*args, **kwargs)
        root.nodes.new('NodeGroupInput')
        return result

    monkeypatch.setattr(direct_composition, 'compose_prepared_initial', insert_unexpected_io)
    with pytest.raises(root_update.RootUpdateError, match='unapproved structural') as info:
        compile_root('a = cube(1)', root)
    assert not info.value.may_have_mutated
    assert {n.as_pointer() for n in root.nodes} == before


def _nla_strip_for_node(tree, node):
    """Create an NLA-owned Action with no active Action on the root."""
    assert node.keyframe_insert(data_path='location', index=0, frame=1)
    animation = tree.animation_data
    action = animation.action
    assert action is not None
    track = animation.nla_tracks.new()
    strip = track.strips.new('Root NLA', 1, action)
    animation.action = None
    assert animation.action is None and strip.action is action
    return action, strip


def test_update_rejects_ordinary_node_nla_animation_without_active_action():
    """S07: inactive NLA Action still protects its ordinary node path."""
    tree, _, outlet = _root('NFR5_nla_ordinary')
    cube = tree.nodes.new('GeometryNodeMeshCube')
    cube.name = 'NLA Cube'
    tree.links.new(cube.outputs['Mesh'], outlet.inputs['Geometry'])
    action, strip = _nla_strip_for_node(tree, cube)
    assert any('nodes["NLA Cube"]' in path for path in root_update._animation_paths(tree))
    before = {n.as_pointer() for n in tree.nodes}
    links = tuple((link.from_node.as_pointer(), link.to_node.as_pointer()) for link in tree.links)
    with pytest.raises(CompileError, match='animation/driver'):
        compile_root('a = cube(2)', tree)
    assert before == {n.as_pointer() for n in tree.nodes}
    assert links == tuple((link.from_node.as_pointer(), link.to_node.as_pointer()) for link in tree.links)
    assert strip.action is action and tree.animation_data.action is None


def test_update_accepts_structural_io_nla_animation_without_active_action():
    """S07: preserved Group Input/Output animation is not blocked by NLA scan."""
    tree, inlet, outlet = _root('NFR5_nla_io')
    action, strip = _nla_strip_for_node(tree, inlet)
    old_io = (inlet.as_pointer(), outlet.as_pointer())
    compile_root('a = cube(2)', tree)
    assert old_io == (inlet.as_pointer(), outlet.as_pointer())
    assert strip.action is action


def _old_frame_with_io(name):
    """Return a root where old ordinary Frame owns both physical I/O nodes."""
    tree, inlet, outlet = _root(name)
    frame = tree.nodes.new('NodeFrame')
    frame.name = 'Old Frame'
    frame.location = (105, 65)
    for node, position in ((inlet, (-400, 30)), (outlet, (400, -30))):
        node.parent = frame
        node.location = position
    cube = tree.nodes.new('GeometryNodeMeshCube')
    cube.parent = frame
    cube.location = (0, 5)
    tree.links.new(cube.outputs['Mesh'], outlet.inputs['Geometry'])
    return tree, frame, inlet, outlet


def test_predetach_failure_restores_structural_io_parents_and_allows_clean_cancel(monkeypatch):
    """S08/S10: a one-shot detach exception restores the original I/O state."""
    tree, frame, inlet, outlet = _old_frame_with_io('NFR5_predetach_recovered')
    before = {node.as_pointer() for node in tree.nodes}
    positions = {node.as_pointer(): root_update._point(node) for node in (inlet, outlet)}
    original = root_update._place_absolute
    calls = 0

    def fail_once(node, position):
        """Fail immediately after the first structural I/O parent was detached."""
        nonlocal calls
        calls += 1
        if calls == 1:
            raise RuntimeError('NFR5 detach once')
        return original(node, position)

    monkeypatch.setattr(root_update, '_place_absolute', fail_once)
    with pytest.raises(root_update.RootUpdateError, match='NFR5 detach once') as info:
        compile_root('a = cube(3)', tree)
    assert not info.value.may_have_mutated
    assert before == {node.as_pointer() for node in tree.nodes}
    assert inlet.parent.as_pointer() == frame.as_pointer() and outlet.parent.as_pointer() == frame.as_pointer()
    assert {node.as_pointer(): root_update._point(node) for node in (inlet, outlet)} == positions
    assert len(tree.links) == 1


def test_predetach_unrestorable_failure_requires_undo(monkeypatch):
    """S08/S10: failed I/O restoration is reported as potentially mutating."""
    tree, frame, inlet, outlet = _old_frame_with_io('NFR5_predetach_unrestored')

    def fail_position(*args):
        """Also fail restoration so the Frame parent remains detached."""
        raise RuntimeError('NFR5 blocked I/O placement')

    monkeypatch.setattr(root_update, '_place_absolute', fail_position)
    with pytest.raises(root_update.RootUpdateError, match='structural I/O restoration') as info:
        compile_root('a = cube(3)', tree)
    assert info.value.may_have_mutated
    assert inlet.parent.as_pointer() == frame.as_pointer()  # The restore attempt reattaches before placement.
    assert outlet.parent.as_pointer() == frame.as_pointer()
    assert any(node.bl_idname == 'NodeFrame' for node in tree.nodes)


def test_late_failure_restores_io_parent_frames_with_recreated_old_graph(monkeypatch):
    """S08: after cutover, both I/O nodes reattach to reconstructed old Frame."""
    tree, frame, inlet, outlet = _old_frame_with_io('NFR5_late_frame_restore')
    positions = {node.as_pointer(): root_update._point(node) for node in (inlet, outlet)}
    original_frame_pointer = frame.as_pointer()

    def fail_boundary(*args):
        """Trigger recovery after old ordinary Frames have been removed."""
        raise RuntimeError('NFR5 late frame restore')

    monkeypatch.setattr(root_update, '_reconnect_boundary', fail_boundary)
    with pytest.raises(root_update.RootUpdateError, match='NFR5 late frame restore') as info:
        compile_root('a = cube(4)', tree)
    assert info.value.may_have_mutated
    restored_frame = tree.nodes.get('Old Frame')
    assert restored_frame is not None and restored_frame.bl_idname == 'NodeFrame'
    assert restored_frame.as_pointer() != original_frame_pointer
    assert (inlet.parent.as_pointer() == restored_frame.as_pointer()
            and outlet.parent.as_pointer() == restored_frame.as_pointer())
    assert {node.as_pointer(): root_update._point(node) for node in (inlet, outlet)} == positions
    assert len(tree.links) == 1 and tree.links[0].to_node.as_pointer() == outlet.as_pointer()
    assert 'root restoration:' not in str(info.value)


def test_late_failure_reports_structural_io_parent_restoration_error(monkeypatch):
    """S08: recoverable hierarchy failure must appear in recovery diagnostics."""
    tree, _, inlet, _ = _old_frame_with_io('NFR5_late_frame_diagnostic')
    def fail_boundary(*args):
        """Trigger restoration after the old Frame was deleted."""
        raise RuntimeError('NFR5 late frame failure')

    def fail_io_restore(*args):
        """Simulate an inaccessible parent during local recovery."""
        raise RuntimeError('parent Frame unavailable')

    monkeypatch.setattr(root_update, '_reconnect_boundary', fail_boundary)
    monkeypatch.setattr(root_update, '_restore_structural_io', fail_io_restore)
    with pytest.raises(root_update.RootUpdateError, match='root restoration: parent Frame unavailable') as info:
        compile_root('a = cube(3)', tree)
    assert info.value.may_have_mutated


def test_root_update_owns_one_transaction_and_cleans_failed_lowering(monkeypatch):
    """A lowering failure is cleaned and rolled back once by the outer root owner."""
    from NodeForge.blender import direct_composition
    from NodeForge.blender.group_backend import BlenderGroupBuildTransaction

    tree, _, outlet = _root('NFR5_single_attempt_owner')
    old = tree.nodes.new('GeometryNodeMeshCube')
    tree.links.new(old.outputs['Mesh'], outlet.inputs['Geometry'])
    old_pointers = {node.as_pointer() for node in tree.nodes}
    old_groups = {group.as_pointer() for group in bpy.data.node_groups}
    transactions = []
    rollbacks = []
    commits = []

    class ObservedTransaction(BlenderGroupBuildTransaction):
        """Count the transaction transitions for one root-producing attempt."""

        def __init__(self):
            super().__init__()
            transactions.append(self)

        def rollback(self):
            rollbacks.append(self)
            return super().rollback()

        def commit(self):
            commits.append(self)
            return super().commit()

    original_lower = direct_composition.lower_body

    def fail_after_lowering(*args, **kwargs):
        """Raise only after the real lowerer has inserted its ordinary node."""
        original_lower(*args, **kwargs)
        raise RuntimeError('single-owner staged failure')

    monkeypatch.setattr(root_update, 'BlenderGroupBuildTransaction', ObservedTransaction)
    monkeypatch.setattr(direct_composition, 'lower_body', fail_after_lowering)
    with pytest.raises(root_update.RootUpdateError, match='single-owner staged failure') as info:
        compile_root('a = cube(2)\n', tree)
    assert info.value.may_have_mutated is False
    assert len(transactions) == 1
    assert rollbacks == transactions and commits == []
    assert transactions[0]._closed
    assert {node.as_pointer() for node in tree.nodes} == old_pointers
    assert len(tree.links) == 1 and tree.links[0].from_node.as_pointer() == old.as_pointer()
    assert {group.as_pointer() for group in bpy.data.node_groups} == old_groups


def test_root_lowerer_propagates_without_owning_node_or_transaction_cleanup(monkeypatch):
    """Direct lowering leaves both failure cleanup and transaction closure to its caller."""
    from NodeForge.blender import direct_composition
    from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
    from NodeForge.blender.ir_lowering import RootInputEndpoint

    tree, _, _ = _root('NFR5_lowering_delegates_failures')
    prepared, backend, session, _snapshot = compiler._prepare_direct_source('a = cube(3)', tree)
    tx = BlenderGroupBuildTransaction()
    old_pointers = {node.as_pointer() for node in tree.nodes}

    def fail_with_created_node(context, *_args, **_kwargs):
        """Simulate a backend failure after inserting a physical node."""
        context.group.nodes.new('GeometryNodeMeshCube')
        raise RuntimeError('caller must clean this node')

    monkeypatch.setattr(direct_composition, 'lower_body', fail_with_created_node)
    with pytest.raises(RuntimeError, match='caller must clean this node'):
        direct_composition.compose_prepared_initial(
            prepared, tree, backend, session,
            transaction=tx, root_input_endpoint=RootInputEndpoint(),
        )
    assert tx._closed is False
    assert len([node for node in tree.nodes if node.as_pointer() not in old_pointers]) == 1
    # Explicitly emulate the only supported caller's cleanup policy in this test.
    for node in tuple(tree.nodes):
        if node.as_pointer() not in old_pointers:
            tree.nodes.remove(node)
    tx.rollback()
    assert tx._closed
    assert {node.as_pointer() for node in tree.nodes} == old_pointers


def test_update_preserves_contextual_position_link_vs_true_omission_and_schema_v2():
    """A replaced raw node retains the precise present/absent input contract."""
    from NodeForge.blender import raw_nodes

    tree, _, outlet = _root('NFR5_constant_position_update')
    explicit = ('base = cube(2)\n'
                'result = node("GeometryNodeSetPosition", '
                'inputs={"Geometry": base, "Position": vector(0, 0, 0)}, '
                'output="Geometry", typ=Geometry)\n')
    omitted = ('base = cube(2)\n'
               'result = node("GeometryNodeSetPosition", '
               'inputs={"Geometry": base}, output="Geometry", typ=Geometry)\n')

    def contract(node, identifier):
        spec = raw_nodes.raw_node_spec(node)
        assert spec['schema_version'] == raw_nodes.RAW_SCHEMA_VERSION == 2
        return next((row for row in spec['inputs']
                     if row['socket'].identifier == identifier), None)

    def setpos():
        nodes = [node for node in tree.nodes if node.bl_idname == 'GeometryNodeSetPosition']
        assert len(nodes) == 1
        return nodes[0]

    def vertices(obj):
        bpy.context.view_layer.update()
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        mesh = evaluated.to_mesh()
        try:
            return [tuple(round(float(x), 4) for x in v.co) for v in mesh.vertices]
        finally:
            evaluated.to_mesh_clear()

    obj = bpy.data.objects.new('NFR5_position_obj', bpy.data.meshes.new('NFR5_position_mesh'))
    bpy.context.collection.objects.link(obj)
    mod = obj.modifiers.new('Nodes', 'NODES')
    mod.node_group = tree
    try:
        compile_root(explicit, tree)
        node = setpos()
        tree.links.new(node.outputs['Geometry'], outlet.inputs['Geometry'])
        assert node.inputs['Position'].is_linked
        assert contract(node, 'Position')['mode'] == raw_nodes.INPUT_SINGLE_LINK
        assert set(vertices(obj)) == {(0.0, 0.0, 0.0)}
        compile_root(omitted, tree)
        node = setpos()
        assert not node.inputs['Position'].is_linked
        assert contract(node, 'Position') is None
        assert outlet.inputs['Geometry'].is_linked
        assert len(set(vertices(obj))) == 8
        compile_root(explicit, tree)
        node = setpos()
        assert node.inputs['Position'].is_linked
        assert contract(node, 'Position')['mode'] == raw_nodes.INPUT_SINGLE_LINK
        assert set(vertices(obj)) == {(0.0, 0.0, 0.0)}
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)


def test_update_failed_constant_delivery_keeps_published_nodes_and_realization(monkeypatch):
    """An adapter failure in a staged candidate must not alter the old tree."""
    from NodeForge.blender import raw_nodes

    tree, _, outlet = _root('NFR5_constant_failure_update')
    old = ('base = cube(2)\n'
           'result = node("GeometryNodeSetPosition", '
           'inputs={"Geometry": base, "Position": vector(0, 0, 0)}, '
           'output="Geometry", typ=Geometry)\n')
    compile_root(old, tree)
    node, = [item for item in tree.nodes if item.bl_idname == 'GeometryNodeSetPosition']
    tree.links.new(node.outputs['Geometry'], outlet.inputs['Geometry'])
    ids = {item.as_pointer() for item in tree.nodes}
    links = {(link.from_socket.as_pointer(), link.to_socket.as_pointer()) for link in tree.links}
    spec_before = raw_nodes.raw_node_spec(node)
    group_refs = {g.as_pointer() for g in bpy.data.node_groups}
    real_deliver = raw_nodes.deliver_input

    def failing_delivery(group, socket, operand, *, context='node input'):
        result = real_deliver(group, socket, operand, context=context)
        if socket.identifier == 'Position' and socket.node.bl_idname == 'GeometryNodeSetPosition':
            raise RuntimeError('injected deferred constant candidate failure')
        return result

    monkeypatch.setattr(raw_nodes, 'deliver_input', failing_delivery)
    with pytest.raises(root_update.RootUpdateError, match='injected deferred constant candidate failure'):
        compile_root(old.replace('cube(2)', 'cube(3)'), tree)
    node, = [item for item in tree.nodes if item.bl_idname == 'GeometryNodeSetPosition']
    assert ids == {item.as_pointer() for item in tree.nodes}
    assert links == {(link.from_socket.as_pointer(), link.to_socket.as_pointer()) for link in tree.links}
    assert raw_nodes.raw_node_spec(node) == spec_before
    assert group_refs == {g.as_pointer() for g in bpy.data.node_groups}


@pytest.mark.parametrize('kind', ['Capture', 'Menu', 'Index'])
def test_late_root_rollback_restores_gapped_collection_endpoints(kind, monkeypatch):
    """A deleted native item must not redirect a restored link to a same-type fixed slot."""
    tree, inlet, outlet = _root('NFR5_gapped_collection_' + kind)
    source = tree.nodes.new('FunctionNodeInputBool' if kind == 'Capture' else 'ShaderNodeValue')
    if kind == 'Capture':
        native = tree.nodes.new('GeometryNodeCaptureAttribute')
        native.capture_items.new('BOOLEAN', 'Removed')
        native.capture_items.new('BOOLEAN', 'Selection')
        native.capture_items.new('FLOAT', 'Weight')
        native.capture_items.remove(native.capture_items[0])
        native.inputs[1].default_value = True
        native.inputs[2].default_value = False
        native.inputs[3].default_value = .75
        source.boolean = True
        slot = 2
        output_slot = 2
        sink = tree.nodes.new('FunctionNodeBooleanMath')
        tree.links.new(inlet.outputs['Geometry'], native.inputs[0])
        tree.links.new(native.outputs[0], outlet.inputs['Geometry'])
    elif kind == 'Menu':
        native = tree.nodes.new('GeometryNodeMenuSwitch')
        native.data_type = 'FLOAT'
        native.enum_items.clear()
        native.enum_items.new('Removed')
        native.enum_items.new('First').description = 'First description'
        native.enum_items.new('Last').description = 'Last description'
        native.enum_items.remove(native.enum_items[0])
        native.inputs[0].default_value = 'Last'
        native.inputs[2].default_value = 4.0
        slot, output_slot = 1, 0
        sink = tree.nodes.new('ShaderNodeMath')
    else:
        native = tree.nodes.new('GeometryNodeIndexSwitch')
        native.index_switch_items.new()
        native.index_switch_items.remove(native.index_switch_items[0])
        native.inputs[0].default_value = 1
        native.inputs[2].default_value = 4.0
        slot, output_slot = 1, 0
        sink = tree.nodes.new('ShaderNodeMath')
    tree.links.new(source.outputs[0], native.inputs[slot])
    tree.links.new(native.outputs[output_slot], sink.inputs[0])
    name = native.name

    def snapshot():
        """Compare logical endpoints with physical ordinals, defaults and item order."""
        node = tree.nodes[name]
        vals = tuple(getattr(s, 'default_value', None) for s in node.inputs)
        links = sorted((l.from_node.name, list(l.from_node.outputs).index(l.from_socket),
                        l.to_node.name, list(l.to_node.inputs).index(l.to_socket)) for l in tree.links)
        if kind == 'Capture':
            items = tuple((i.name,i.data_type) for i in node.capture_items)
        elif kind == 'Menu':
            items = tuple((i.name,i.description) for i in node.enum_items)
        else:
            items = tuple(s.name for s in list(node.inputs)[1:-1])
        return items, vals, links

    before = snapshot()
    identities = {g.as_pointer() for g in bpy.data.node_groups}
    # The rollback backup retains source IDs; rebuilt native IDs may differ.
    assert native.inputs[slot].identifier not in ('Value', 'Item_0')
    def fail(*args):
        raise RuntimeError('injected collection late cutover failure')
    with monkeypatch.context() as patch:
        patch.setattr(root_update, '_reconnect_boundary', fail)
        with pytest.raises(root_update.RootUpdateError, match='injected collection late cutover failure') as info:
            compile_root('a = cube(4)\n', tree)
        assert info.value.may_have_mutated
    assert snapshot() == before
    assert {g.as_pointer() for g in bpy.data.node_groups} == identities
    # A subsequent valid root publication still succeeds.
    compile_root('a = cube(4)\n', tree)
