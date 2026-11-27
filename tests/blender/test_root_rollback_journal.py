"""Root rollback retains only obligations for live or recoverable graph state."""

import bpy
import pytest

from NodeForge import compiler
from NodeForge.blender import group_backend as backend, raw_nodes as raw
from NodeForge.blender import root_candidate, root_update
from NodeForge.blender.group_authority import mark_transaction_private
from NodeForge.errors import CompileError
from test_native_collection_copy import trees, _links


_SOURCE = 'def shared() -> Geometry:\n    return cube(1)\na = shared()\n'


def _compile(tree, source=_SOURCE):
    return compiler.compile_source_in_tree(source, tree, module_context='test:root-rollback-journal')


def _snapshot(tree, *, interface_ids=True):
    def value(socket):
        v = getattr(socket, 'default_value', None)
        if v is None or isinstance(v, (str, int, float, bool, bpy.types.ID)):
            return v
        return tuple(v)
    def sockets(node):
        return tuple(tuple((s.name, s.bl_idname, s.hide, value(s)) for s in side)
                     for side in (node.inputs, node.outputs))
    return (sorted((n.name, n.bl_idname, sockets(n)) for n in tree.nodes),
            _links(tree), tuple(row if interface_ids else row[:2] + row[3:]
                          for row in root_update._interface_signature(tree)))


def _inventory():
    return {g.as_pointer(): g.users for g in bpy.data.node_groups}


def _graph(trees, raw_case):
    tree = trees('Journal root')
    for direction in ('INPUT', 'OUTPUT'):
        tree.interface.new_socket(name='Geometry', in_out=direction, socket_type='NodeSocketGeometry')
    tree.nodes.new('NodeGroupInput')
    output = tree.nodes.new('NodeGroupOutput')
    _compile(tree)
    shared = next(n for n in tree.nodes if n.bl_idname == 'GeometryNodeGroup')
    child = shared.node_tree
    index = tree.nodes.new('GeometryNodeIndexSwitch')
    index.data_type = 'GEOMETRY'
    index.index_switch_items.new()
    index.index_switch_items.remove(index.index_switch_items[0])
    index.inputs[0].default_value = 1
    tree.links.new(shared.outputs[0], index.inputs[2])
    tree.links.new(index.outputs[0], output.inputs[0])
    if raw_case:
        socket = index.inputs[2]
        raw._write_raw_metadata(index, index.bl_idname, {'data_type': 'GEOMETRY'},
                                [{'socket': {'identifier': socket.identifier, 'socket_type': socket.bl_idname},
                                  'mode': raw.INPUT_SINGLE_LINK}],
                                [raw.RawPhysicalSocketRef(index.outputs[0].identifier, index.outputs[0].bl_idname)],
                                raw.RAW_SINGLE_MODE)
        raw.validate_raw_node_after_cutover(index, tree)
    return tree, child, shared, index


def _mutate_again(group_backend, child, transaction):
    def populate(dst, resources):
        backend._copy_group_contents(child, dst)
        cube = next(n for n in dst.nodes if n.bl_idname == 'GeometryNodeMeshCube')
        cube.inputs[0].default_value = (7, 7, 7)
    group_backend._update_existing(child.name, child, transaction, populate,
                                   outermost=False, preserve_if_equivalent=False,
                                   finalize_before_commit=None)


@pytest.mark.parametrize('raw_case', [False, True])
@pytest.mark.parametrize('deletion', ['full', 'shared', 'index', 'unrelated', 'before'])
@pytest.mark.parametrize('repeat', [False, True])
def test_nested_root_journal_rollback(trees, monkeypatch, raw_case, deletion, repeat):
    tree, child, shared, index = _graph(trees, raw_case)
    unrelated = tree.nodes.new('ShaderNodeValue')
    unrelated_name = unrelated.name
    other = trees('Other external user')
    instance = other.nodes.new('GeometryNodeGroup')
    instance.node_tree = child
    consumer = other.nodes.new('GeometryNodeTransform')
    other.links.new(instance.outputs[0], consumer.inputs[0])
    before, child_before, other_before = _snapshot(tree), _snapshot(child, interface_ids=False), _snapshot(other)
    inventory = _inventory()
    pointers = {node.name: node.as_pointer() for node in tree.nodes}
    index_name, shared_name = index.name, shared.name
    build = root_candidate.build_private_root_candidate
    if repeat:
        def twice(*args, **kwargs):
            candidate = build(*args, **kwargs)
            tx = kwargs['transaction']
            _mutate_again(args[2], child, tx)
            assert len(tx._mutation_journal) == 2
            assert any(record['tree'] == candidate.stage for record in tx._mutation_journal[-1]['external_state']['group_nodes'])
            return candidate
        monkeypatch.setattr(root_candidate, 'build_private_root_candidate', twice)
    with monkeypatch.context() as patch:
        def fail(*args):
            raise RuntimeError('injected root failure')
        if deletion == 'full':
            patch.setattr(root_update, '_reconnect_boundary', fail)
        elif deletion == 'before':
            patch.setattr(root_update, '_remove_original_node', fail)
        else:
            # Change only the deletion order, not the real reconstruction path.
            ordinary = root_update._ordinary
            first = {'shared': shared_name, 'index': index_name, 'unrelated': unrelated_name}[deletion]
            def ordered(graph):
                result = ordinary(graph)
                return tuple(sorted(result, key=lambda node: node.name != first)) if graph == tree else result
            patch.setattr(root_update, '_ordinary', ordered)
            remove = root_update._remove_original_node
            def partial(graph, node, removed):
                assert node.name == first
                remove(graph, node, removed)
                fail()
            patch.setattr(root_update, '_remove_original_node', partial)
        with pytest.raises(root_update.RootUpdateError, match='injected root failure') as error:
            _compile(tree, _SOURCE.replace('cube(1)', 'cube(5)'))
        assert str(error.value) == 'injected root failure'
    assert _snapshot(tree) == before
    assert _snapshot(child, interface_ids=False) == child_before
    assert _snapshot(other) == other_before
    assert _inventory() == inventory
    for name, ptr in pointers.items():
        if deletion == 'before' or (deletion == 'shared' and name != shared_name) or (deletion == 'index' and name != index_name) or (deletion == 'unrelated' and name != unrelated_name) or tree.nodes[name].bl_idname in root_update._IO_TYPES:
            assert tree.nodes[name].as_pointer() == ptr
    if raw_case:
        raw.validate_raw_node_after_cutover(tree.nodes[index_name], tree)
    monkeypatch.setattr(root_candidate, 'build_private_root_candidate', build)
    _compile(tree, _SOURCE.replace('cube(1)', 'cube(5)'))


@pytest.mark.parametrize('corruption', ['missing', 'duplicate', 'mismatched'])
def test_backup_journal_preflight_before_deletion(trees, monkeypatch, corruption):
    tree, child, _, _ = _graph(trees, True)
    before, child_before = _snapshot(tree), _snapshot(child, interface_ids=False)
    inventory = _inventory()
    pointers = {n.name: n.as_pointer() for n in tree.nodes}
    project = root_update._root_rollback_projection
    def corrupt(tx, target, backup, pairs, removed):
        records = tx._mutation_journal[0]['external_state']['links']
        mirror = next(r for r in records if r['tree'] == backup)
        if corruption == 'missing':
            records.remove(mirror)
        elif corruption == 'duplicate':
            records.append(dict(mirror))
        else:
            # Keep a valid resolver payload but make the captured old record unequal.
            mirror['from'] = dict(mirror['from'], kind='incorrect')
        return project(tx, target, backup, pairs, removed)
    monkeypatch.setattr(root_update, '_root_rollback_projection', corrupt)
    with pytest.raises(root_update.RootUpdateError, match='Rollback root backup'):
        _compile(tree, _SOURCE.replace('cube(1)', 'cube(5)'))
    assert _snapshot(tree) == before and _snapshot(child, interface_ids=False) == child_before
    assert {n.name: n.as_pointer() for n in tree.nodes} == pointers
    assert _inventory() == inventory


def test_candidate_lowering_failure_retires_own_records(trees, monkeypatch):
    from NodeForge.blender import direct_composition
    tree, child, _, _ = _graph(trees, True)
    before, child_before = _snapshot(tree), _snapshot(child, interface_ids=False)
    inventory = _inventory()
    compose = direct_composition.compose_prepared_initial
    def fail(*args, **kwargs):
        result = compose(*args, **kwargs)
        tx = kwargs['transaction']
        _mutate_again(args[2], child, tx)
        assert any(r['tree'] == args[1] for r in tx._mutation_journal[-1]['external_state']['group_nodes'])
        raise RuntimeError('injected lowering failure after stage capture')
    monkeypatch.setattr(direct_composition, 'compose_prepared_initial', fail)
    with pytest.raises(root_update.RootUpdateError, match='injected lowering failure after stage capture') as error:
        _compile(tree, _SOURCE.replace('cube(1)', 'cube(5)'))
    assert str(error.value) == 'injected lowering failure after stage capture'
    assert _snapshot(tree) == before and _snapshot(child, interface_ids=False) == child_before
    assert _inventory() == inventory


def test_nested_group_replacement_disposes_its_own_journal_records(trees):
    """A replacement populated across two child updates is abandoned on success too."""
    tree, child, _, _ = _graph(trees, False)
    parent = trees('Authoritative parent')
    initial = parent.nodes.new('ShaderNodeValue')
    initial.outputs[0].default_value = 23
    before, child_before = _snapshot(parent, interface_ids=False), _snapshot(child, interface_ids=False)
    root_before = _snapshot(tree)
    inventory = _inventory()
    tx = backend.BlenderGroupBuildTransaction()
    group_backend = backend.BlenderGroupBackend(populate_candidate=lambda *args: None)
    def populate(replacement, resources):
        instance = replacement.nodes.new('GeometryNodeGroup')
        instance.node_tree = child
        _mutate_again(group_backend, child, tx)
        assert any(record['tree'] == replacement for record in tx._mutation_journal[-1]['external_state']['group_nodes'])
        _mutate_again(group_backend, child, tx)
    group_backend._update_existing(parent.name, parent, tx, populate,
                                   outermost=False, preserve_if_equivalent=False,
                                   finalize_before_commit=None)
    tx.rollback()
    assert _snapshot(parent, interface_ids=False) == before
    assert _snapshot(child, interface_ids=False) == child_before
    assert _snapshot(tree) == root_before
    assert _inventory() == inventory


@pytest.mark.parametrize('removed_before_error', [False, True])
def test_stage_disposal_failure_preserves_exact_obligations(trees, monkeypatch, removed_before_error):
    tree, child, _, _ = _graph(trees, False)
    stage = trees('Abandoned stage')
    backup = trees('Unrelated private recovery state')
    for graph in (stage, backup):
        mark_transaction_private(graph)
        instance = graph.nodes.new('GeometryNodeGroup')
        instance.node_tree = child
        other = graph.nodes.new('GeometryNodeTransform')
        graph.links.new(instance.outputs[0], other.inputs[0])
    before, backup_before = _snapshot(tree), _snapshot(backup)
    stage_before = _snapshot(stage)
    tx = backend.BlenderGroupBuildTransaction()
    group_backend = backend.BlenderGroupBackend(populate_candidate=lambda *args: None)
    _mutate_again(group_backend, child, tx)
    records = tx._mutation_journal[-1]['external_state']
    stage_records = {key: [r for r in rows if r['tree'] == stage] for key, rows in records.items()}
    backup_records = {key: [r for r in rows if r['tree'] == backup] for key, rows in records.items()}
    assert all(stage_records.values()) and all(backup_records.values())
    remove = backend._remove_node_group_if_live
    def fail(graph, *, failures=None):
        assert graph == stage
        if removed_before_error:
            assert remove(graph, failures=failures)
        failures.append(RuntimeError('injected native disposal failure'))
        return False
    with monkeypatch.context() as patch:
        patch.setattr(backend, '_remove_node_group_if_live', fail)
        with pytest.raises(CompileError, match='injected native disposal failure'):
            tx.discard_abandoned_tree(stage)
    for key, rows in records.items():
        assert all(r in rows for r in backup_records[key])
        assert all((r in rows) != removed_before_error for r in stage_records[key])
    tx.rollback()
    assert _snapshot(tree) == before and _snapshot(backup) == backup_before
    if not removed_before_error:
        assert _snapshot(stage) == stage_before
        assert backend.is_authority_ineligible_group(stage)


@pytest.mark.parametrize('rehearsal_source', ['backup', 'replacement'])
def test_group_rehearsal_rejects_reconstruction_failure_before_reset(trees, monkeypatch, rehearsal_source):
    src = trees('Old group with native collection')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    node.index_switch_items.new()
    node.index_switch_items.remove(node.index_switch_items[0])
    before, inventory = _snapshot(src), _inventory()
    pointer = node.as_pointer()
    group_backend = backend.BlenderGroupBackend(populate_candidate=lambda *args: None)
    tx = backend.BlenderGroupBuildTransaction()
    copy = backend._copy_group_contents
    def fail(source, target, *, copy_role=None):
        # Fail the actual second-generation recovery reconstruction, or the
        # candidate's actual reconstruction. These sources are not exercised
        # by the initial private backup. Do not fail merely on scratch identity.
        prefix = 'NodeForge.rollback.' if rehearsal_source == 'backup' else 'NodeForge.replacement.'
        if source.name.startswith(prefix):
            raise CompileError('injected source reconstruction failure')
        return copy(source, target, copy_role=copy_role)
    monkeypatch.setattr(backend, '_copy_group_contents', fail)
    def populate(dst, resources):
        dst.nodes.new('GeometryNodeIndexSwitch')
    with pytest.raises(CompileError, match='injected source reconstruction failure'):
        group_backend._update_existing(src.name, src, tx, populate, outermost=False,
                                       preserve_if_equivalent=False, finalize_before_commit=None)
    assert _snapshot(src) == before and src.nodes[0].as_pointer() == pointer
    assert _inventory() == inventory and not tx._mutation_journal


def test_root_rehearsal_failure_precedes_candidate_and_old_node_removal(trees, monkeypatch):
    tree, child, _, _ = _graph(trees, True)
    before, child_before, inventory = _snapshot(tree), _snapshot(child, interface_ids=False), _inventory()
    pointers = {n.as_pointer() for n in tree.nodes}
    def fail(graph, backup, old_ptrs):
        assert graph != tree and backend.is_authority_ineligible_group(graph)
        assert not old_ptrs
        raise CompileError('injected native root reconstruction failure')
    def forbidden(*args, **kwargs):
        pytest.fail('candidate lowering reached before recovery rehearsal passed')
    monkeypatch.setattr(root_update, '_copy_missing_old_nodes', fail)
    monkeypatch.setattr(root_candidate, 'build_private_root_candidate', forbidden)
    with pytest.raises(root_update.RootUpdateError, match='injected native root reconstruction failure'):
        _compile(tree, _SOURCE.replace('cube(1)', 'cube(5)'))
    assert _snapshot(tree) == before and _snapshot(child, interface_ids=False) == child_before
    assert {n.as_pointer() for n in tree.nodes} == pointers and _inventory() == inventory


@pytest.mark.parametrize('savepoint', [False, True])
def test_nested_group_replacement_retirement_and_savepoint(trees, savepoint):
    tree, child, _, _ = _graph(trees, True)
    tx = backend.BlenderGroupBuildTransaction()
    group_backend = backend.BlenderGroupBackend(populate_candidate=lambda *args: None)
    _mutate_again(group_backend, child, tx)
    checkpoint = tx.savepoint()
    state = _snapshot(child, interface_ids=False)
    stage = trees('Stage later abandoned')
    mark_transaction_private(stage)
    instance = stage.nodes.new('GeometryNodeGroup')
    instance.node_tree = child
    _mutate_again(group_backend, child, tx)
    tx.discard_abandoned_tree(stage)
    if savepoint:
        tx.rollback_to_savepoint(checkpoint)
        assert len(tx._mutation_journal) == 1
        assert _snapshot(child, interface_ids=False) == state
        tx.commit()
    else:
        tx.rollback()
        assert not tx._mutation_journal
    raw.validate_raw_node_after_cutover(next(n for n in tree.nodes if n.bl_idname == 'GeometryNodeIndexSwitch'), tree)


def test_two_distinct_nested_children_late_failure(trees, monkeypatch):
    tree = trees('Two distinct children')
    tree.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    output = tree.nodes.new('NodeGroupOutput')
    source = 'def first() -> Geometry:\n    return cube(1)\ndef second() -> Geometry:\n    return cube(2)\na = first()\nb = second()\n'
    _compile(tree, source)
    children = [n for n in tree.nodes if n.bl_idname == 'GeometryNodeGroup']
    assert len(children) == 2 and children[0].node_tree != children[1].node_tree
    index = tree.nodes.new('GeometryNodeIndexSwitch')
    index.data_type = 'GEOMETRY'
    index.index_switch_items.new()
    index.index_switch_items.remove(index.index_switch_items[0])
    tree.links.new(children[0].outputs[0], index.inputs[1])
    tree.links.new(children[1].outputs[0], index.inputs[2])
    tree.links.new(index.outputs[0], output.inputs[0])
    child_groups = [n.node_tree for n in children]
    before, inventory = _snapshot(tree), _inventory()
    states = [_snapshot(group, interface_ids=False) for group in child_groups]
    def fail(*args):
        raise RuntimeError('injected two-child late failure')
    monkeypatch.setattr(root_update, '_reconnect_boundary', fail)
    with pytest.raises(root_update.RootUpdateError, match='injected two-child late failure') as error:
        _compile(tree, source.replace('cube(1)', 'cube(5)').replace('cube(2)', 'cube(6)'))
    assert str(error.value) == 'injected two-child late failure'
    assert _snapshot(tree) == before and _inventory() == inventory
    assert [_snapshot(group, interface_ids=False) for group in child_groups] == states


@pytest.mark.parametrize('role', ['old_root', 'old_group', 'candidate_root', 'candidate_group'])
def test_unsupported_collection_rejected_before_authoritative_reset(trees, monkeypatch, role):
    tree = trees('Existing graph')
    node = tree.nodes.new('ShaderNodeValue')
    node.outputs[0].default_value = 31
    if role.startswith('old'):
        tree.nodes.new('GeometryNodeSimulationOutput')
    pointers = {n.as_pointer() for n in tree.nodes}
    inventory = _inventory()
    def forbidden(*args, **kwargs):
        pytest.fail('old unsupported collection reached candidate construction')
    if role.endswith('root'):
        if role == 'old_root':
            monkeypatch.setattr(root_candidate, 'build_private_root_candidate', forbidden)
        else:
            build = root_candidate.build_private_root_candidate
            def inject(*args, **kwargs):
                candidate = build(*args, **kwargs)
                candidate.stage.nodes.new('GeometryNodeSimulationOutput')
                return candidate
            monkeypatch.setattr(root_candidate, 'build_private_root_candidate', inject)
        with pytest.raises(root_update.RootUpdateError, match='unsupported'):
            _compile(tree, 'a = cube(1)')
    else:
        tx = backend.BlenderGroupBuildTransaction()
        group_backend = backend.BlenderGroupBackend(populate_candidate=lambda *args: None)
        def populate(dst, resources):
            dst.nodes.new('GeometryNodeSimulationOutput')
        with pytest.raises(CompileError, match='unsupported'):
            group_backend._update_existing(tree.name, tree, tx,
                                           forbidden if role == 'old_group' else populate,
                                           outermost=False, preserve_if_equivalent=False,
                                           finalize_before_commit=None)
    assert {n.as_pointer() for n in tree.nodes} == pointers
    assert node.outputs[0].default_value == 31 and _inventory() == inventory


@pytest.mark.parametrize('raw_case', [False, True])
@pytest.mark.parametrize('linked', [False, True])
@pytest.mark.parametrize('role', ['root_restore', 'group_restore', 'group_cutover'])
def test_unset_menu_through_real_publication(trees, monkeypatch, raw_case, linked, role):
    graph = trees('Unset enum publication')
    def populate(dst, resources=None):
        menu = dst.nodes.new('GeometryNodeMenuSwitch')
        menu.data_type = 'FLOAT'
        menu.inputs[0].default_value = 'B'
        menu.enum_items.remove(menu.enum_items[1])
        menu.enum_items.new('C')
        menu.inputs[1].default_value = 11
        menu.inputs[2].default_value = 22
        if linked:
            value = dst.nodes.new('ShaderNodeValue')
            value.outputs[0].default_value = 11
            dst.links.new(value.outputs[0], menu.inputs[1])
        if raw_case:
            raw._write_raw_metadata(menu, menu.bl_idname, {'data_type': 'FLOAT'},
                                    [{'socket': {'identifier': menu.inputs[2].identifier,
                                                 'socket_type': menu.inputs[2].bl_idname},
                                      'mode': raw.INPUT_LITERAL, 'default': 22}],
                                    [raw.RawPhysicalSocketRef(menu.outputs[0].identifier, menu.outputs[0].bl_idname)],
                                    raw.RAW_SINGLE_MODE)
            raw.validate_raw_node_after_cutover(menu, dst)
        assert menu.inputs[0].default_value == ''
        return menu
    menu = populate(graph)
    name = menu.name
    before, inventory = _snapshot(graph, interface_ids=False), _inventory()
    if role == 'root_restore':
        def fail(*args):
            raise RuntimeError('injected Menu late failure')
        monkeypatch.setattr(root_update, '_reconnect_boundary', fail)
        with pytest.raises(root_update.RootUpdateError, match='injected Menu late failure') as error:
            _compile(graph, 'a = cube(1)')
        assert str(error.value) == 'injected Menu late failure'
    else:
        group_backend = backend.BlenderGroupBackend(populate_candidate=lambda *args: None)
        tx = backend.BlenderGroupBuildTransaction()
        if role == 'group_restore':
            monkeypatch.setattr(backend, '_TEST_CUTOVER_FAIL_AFTER_RESET', True)
            with pytest.raises(RuntimeError, match='cutover failure after destructive reset'):
                group_backend._update_existing(graph.name, graph, tx, populate, outermost=False,
                                               preserve_if_equivalent=False, finalize_before_commit=None)
        else:
            group_backend._update_existing(graph.name, graph, tx, populate, outermost=False,
                                           preserve_if_equivalent=False, finalize_before_commit=None)
            tx.commit()
    restored = graph.nodes[name]
    assert restored.inputs[0].default_value == ''
    assert [s.default_value for s in restored.inputs[1:3]] == [11, 22]
    assert _snapshot(graph, interface_ids=False) == before and _inventory() == inventory
    if raw_case:
        raw.validate_raw_node_after_cutover(restored, graph)


def test_nested_update_sharing_collection_multi_input_rejected(trees):
    tree, child, shared, index = _graph(trees, False)
    join = tree.nodes.new('GeometryNodeJoinGeometry')
    tree.links.new(shared.outputs[0], join.inputs[0])
    tree.links.new(index.outputs[0], join.inputs[0])
    before, child_before = _snapshot(tree), _snapshot(child, interface_ids=False)
    pointers = {n.name: n.as_pointer() for n in tree.nodes}
    inventory = _inventory()
    with pytest.raises(root_update.RootUpdateError, match='cannot update a group sharing multi-input'):
        _compile(tree, _SOURCE.replace('cube(1)', 'cube(5)'))
    assert _snapshot(tree) == before
    assert _snapshot(child, interface_ids=False) == child_before
    assert {n.name: n.as_pointer() for n in tree.nodes} == pointers
    assert _inventory() == inventory
