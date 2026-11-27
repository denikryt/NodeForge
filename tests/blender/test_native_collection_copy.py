"""Native exact-copy regression independent of generated Nodes or owner sessions."""

import gzip
import json
from pathlib import Path

import bpy
import pytest

from NodeForge import packages
from NodeForge.blender import group_backend as backend
from NodeForge.blender.group_authority import is_authority_ineligible_group
from NodeForge.errors import CompileError


@pytest.fixture
def trees(tmp_path):
    """Use the empty test package inventory and clean up only fixture trees."""
    assert not list((tmp_path / 'packages').glob('*'))
    assert not packages.active_package_manifests()
    before = {group.as_pointer() for group in bpy.data.node_groups}
    def new(name):
        return bpy.data.node_groups.new(name, 'GeometryNodeTree')
    try:
        yield new
    finally:
        for group in list(bpy.data.node_groups):
            if group.as_pointer() not in before:
                bpy.data.node_groups.remove(group, do_unlink=True)


def _socket_rows(node, *, physical=True):
    """Snapshot actual native sockets, not a copier-produced expectation."""
    def value(socket):
        v = getattr(socket, 'default_value', None)
        return tuple(v) if hasattr(v, '__iter__') and not isinstance(v, str) else v
    return tuple(tuple((s.identifier if physical else None, s.name, s.bl_idname,
                        bool(s.is_output), bool(s.hide), value(s)) for s in sockets)
                 for sockets in (node.inputs, node.outputs))


def _links(tree):
    """Compare actual logical endpoint positions and native link validity."""
    return sorted((link.from_node.name, list(link.from_node.outputs).index(link.from_socket),
                   link.to_node.name, list(link.to_node.inputs).index(link.to_socket), bool(link.is_valid))
                  for link in tree.links)


def _populate(node, family, count):
    """Create independent native cases, with gapped/reordered physical IDs."""
    col = getattr(node, family)
    col.clear()
    if family in {'index_switch_items', 'enum_items'}:
        node.data_type = 'FLOAT'
    if family == 'capture_items':
        args = [('FLOAT', 'Geometry'), ('BOOLEAN', 'Selection'), ('INT', 'Count'),
                ('VECTOR', 'Position'), ('RGBA', 'Tint'), ('ROTATION', 'Rotation')][:count]
        col.new('FLOAT', 'Discard')
    elif family == 'enum_items':
        args = [(f'Choice {i}',) for i in range(count)]
        col.new('Discard')
    elif family == 'index_switch_items':
        args = [()] * count
        col.new()
    else:
        args = [('FLOAT', 'A'), ('FLOAT', 'A'), ('INT', 'B'), ('STRING', 'C')][:count]
        col.new('FLOAT', 'Discard')
    for arg in args:
        col.new(*arg)
    col.remove(col[0])
    if count > 1:
        col.move(count - 1, 0)
    if family == 'enum_items':
        for i, item in enumerate(col):
            item.description = f'Description {i}'
        if col:
            node.inputs[0].default_value = col[-1].name
    for i, socket in enumerate(node.inputs):
        values = {'NodeSocketFloat': i + .25, 'NodeSocketInt': i, 'NodeSocketBool': True,
                  'NodeSocketVector': (i, 2, 3), 'NodeSocketColor': (.1, .2, .3, .4),
                  'NodeSocketRotation': (.1, .2, .3), 'NodeSocketString': 'value'}
        if socket.bl_idname in values:
            socket.default_value = values[socket.bl_idname]
    return col


_CASES = [
    ('GeometryNodeCaptureAttribute', 'capture_items', n) for n in (0, 6)
] + [
    ('GeometryNodeMenuSwitch', 'enum_items', n) for n in (1, 3, 32)
] + [
    ('GeometryNodeIndexSwitch', 'index_switch_items', n) for n in (0, 1, 3, 32)
] + [('FunctionNodeFormatString', 'format_items', 4)]


def _item_snapshot(col):
    """Independently read observable native item fields and order."""
    return tuple(tuple(getattr(item, key, None) for key in ('name', 'description', 'socket_type', 'data_type'))
                 for item in col)


@pytest.mark.parametrize('kind,family,count', _CASES)
def test_native_collection_group_copy(trees, kind, family, count):
    """All admitted families retain actual defaults, flags, items and topology."""
    src, dst = trees('Native source'), trees('Native destination')
    node = src.nodes.new(kind)
    col = _populate(node, family, count)
    values = src.nodes.new('ShaderNodeValue')
    floats = [s for s in node.inputs if s.bl_idname == 'NodeSocketFloat']
    if floats:
        src.links.new(values.outputs[0], floats[0])
        if len(floats) > 1:
            floats[-1].hide = True
    output = src.nodes.new('NodeGroupOutput')
    for i, socket in enumerate(node.outputs):
        if socket.bl_idname != 'NodeSocketVirtual':
            name = f'out{i}'
            src.interface.new_socket(name=name, in_out='OUTPUT', socket_type=socket.bl_idname)
            src.links.new(socket, output.inputs[name])
    original = _socket_rows(node)
    expected = _socket_rows(node, physical=False)
    items = _item_snapshot(col)
    edges = _links(src)
    inventory = {g.as_pointer() for g in bpy.data.node_groups}
    for role in ('backup', 'cutover', None):
        backend._copy_group_contents(src, dst, copy_role=role)
        copied = dst.nodes[node.name]
        assert _item_snapshot(getattr(copied, family)) == items
        assert _socket_rows(copied, physical=False) == expected
        assert _links(dst) == edges
        assert _socket_rows(node) == original and _links(src) == edges
        assert {g.as_pointer() for g in bpy.data.node_groups} == inventory


@pytest.mark.parametrize('linked', [False, True])
def test_native_unset_menu_prefix_rebuild(trees, linked, monkeypatch):
    """AR-C09-01: unset native selection requires at most one full local rebuild."""
    src, dst = trees('Unset Menu source'), trees('Unset Menu copy')
    node = src.nodes.new('GeometryNodeMenuSwitch')
    node.data_type = 'FLOAT'
    node.inputs[0].default_value = 'B'
    node.enum_items.remove(node.enum_items[1])
    node.enum_items.new('C')
    node.inputs[1].default_value = 11
    node.inputs[2].default_value = 22
    if linked:
        child = trees('Shared Menu')
        child.interface.new_socket(name='Menu', in_out='OUTPUT', socket_type='NodeSocketMenu')
        child.nodes.new('NodeGroupOutput')
        group = src.nodes.new('GeometryNodeGroup')
        group.node_tree = child
        src.links.new(group.outputs[0], node.inputs[0])
    assert node.inputs[0].default_value == ''
    before = _socket_rows(node)
    attempts = []
    original = backend._reconstruct_collection
    def record(*args, **kwargs):
        attempts.append(kwargs['retain_prefix'])
        return original(*args, **kwargs)
    monkeypatch.setattr(backend, '_reconstruct_collection', record)
    backend._copy_group_contents(src, dst)
    copied = dst.nodes[node.name]
    assert attempts == [True, False]
    assert [item.name for item in copied.enum_items] == ['A', 'C']
    assert copied.inputs[0].default_value == ''
    assert [copied.inputs[i].default_value for i in (1, 2)] == [11, 22]
    assert _socket_rows(node) == before and _links(src) == _links(dst)


def test_native_admission_catalogue(trees):
    """Classify all pinned native classes with explicit unsupported ownership."""
    tree = trees('Native catalogue')
    catalogue = Path(__file__).resolve().parents[2] / 'tools/nodegen/data/blender-5.2.spec.json.gz'
    with gzip.open(catalogue, 'rt') as f:
        kinds = json.load(f)['nodes']
    admitted, rejected, other = set(), set(), set()
    for kind in kinds:
        node = tree.nodes.new(kind)
        try:
            prop = backend._local_collection_property(node)
        except CompileError:
            rejected.add(kind)
        else:
            (admitted if prop else other).add(kind)
        tree.nodes.remove(node)
    assert len(kinds) == 360
    assert admitted == {kind for kind, _, _ in _CASES}
    assert rejected == {'GeometryNodeBake', 'GeometryNodeClosureToList', 'GeometryNodeFieldToGrid',
                        'GeometryNodeFieldToList', 'GeometryNodeForeachGeometryElementOutput',
                        'GeometryNodeSimulationOutput', 'GeometryNodeViewer', 'NodeClosureOutput',
                        'NodeEvaluateClosure'}
    assert len(other) == 347
    for input_kind, output_kind, allowed in (
            ('GeometryNodeSimulationInput', 'GeometryNodeSimulationOutput', False),
            ('GeometryNodeRepeatInput', 'GeometryNodeRepeatOutput', True)):
        output = tree.nodes.new(output_kind)
        entry = tree.nodes.new(input_kind)
        for paired in (False, True):
            if paired:
                entry.pair_with_output(output)
            if allowed:
                assert backend._local_collection_property(output) is None
            else:
                with pytest.raises(CompileError, match='unsupported native collection'):
                    backend._local_collection_property(output)


def _native_layout(node):
    """Independent direction/identifier/class observation for ownership deltas."""
    return tuple((direction, s.identifier, s.bl_idname) for direction, sockets in enumerate((node.inputs, node.outputs))
                 for s in sockets)


def _independent_ownership(node, family):
    """Remove one item per fresh native tree to independently establish ownership."""
    original = _native_layout(node)
    result = {}
    for i in range(len(getattr(node, family))):
        copy = node.id_data.copy()
        try:
            n = copy.nodes[node.name]
            collection = getattr(n, family)
            assert _native_layout(n) == original
            collection.remove(collection[i])
            survivors = set(_native_layout(n))
            result[i] = set(original) - survivors
        finally:
            bpy.data.node_groups.remove(copy)
    return result


@pytest.mark.parametrize('kind,family,count', [case for case in _CASES if case[2] in (3, 4, 6)])
def test_native_proof_matches_independent_removals(trees, kind, family, count):
    """Both sides of gapped/reordered reconstruction agree with independent probes."""
    src, dst = trees('Proof source'), trees('Proof destination')
    node = src.nodes.new(kind)
    _populate(node, family, count)
    backend._copy_group_contents(src, dst)
    for n in (node, dst.nodes[node.name]):
        expected = _independent_ownership(n, family)
        observed = {i: set() for i in expected}
        rows = backend._standalone_socket_layout(n)
        for direction, (actual, proof) in enumerate(zip((n.inputs, n.outputs), rows)):
            for socket, row in zip(actual, proof):
                role = row[0]
                if isinstance(role, tuple):
                    observed[role[1]].add((direction, socket.identifier, socket.bl_idname))
                else:
                    assert role == socket.identifier
        assert expected == observed


def test_native_probe_cleanup_and_no_retry_on_proof_failure(trees, monkeypatch):
    """A malformed proof raises before retry, with original tree and inventory intact."""
    src, dst = trees('Failed proof source'), trees('Failed proof target')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    _populate(node, 'index_switch_items', 3)
    original = backend._physical_layout
    snapshot = original(node)
    inventory = {group.as_pointer() for group in bpy.data.node_groups}
    calls = 0
    def corrupt(n):
        nonlocal calls
        rows = original(n)
        if n.id_data not in (src, dst):
            assert is_authority_ineligible_group(n.id_data)
            calls += 1
            if calls == 2:
                return tuple(tuple(reversed(r)) for r in rows)
        return rows
    monkeypatch.setattr(backend, '_physical_layout', corrupt)
    with pytest.raises(CompileError, match='surviving layout'):
        backend._copy_node_properties(node, dst.nodes.new(node.bl_idname), proofs={})
    assert calls == 2
    assert original(node) == snapshot
    assert {group.as_pointer() for group in bpy.data.node_groups} == inventory


def test_native_probe_reuse(trees, monkeypatch):
    """Multiple link endpoints reuse a proof for unchanged ordered state."""
    src, dst = trees('Reuse source'), trees('Reuse target')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    _populate(node, 'index_switch_items', 3)
    value = src.nodes.new('ShaderNodeValue')
    for socket in node.inputs[1:-1]:
        src.links.new(value.outputs[0], socket)
    for _ in range(4):
        math = src.nodes.new('ShaderNodeMath')
        src.links.new(node.outputs[0], math.inputs[0])
    original = backend._standalone_socket_layout
    probes = []
    def record(n):
        probes.append(n)
        return original(n)
    monkeypatch.setattr(backend, '_standalone_socket_layout', record)
    backend._copy_group_contents(src, dst)
    assert len(probes) == 2
    assert _links(src) == _links(dst)


@pytest.mark.parametrize('error,retry', [(TypeError, True), (RuntimeError, False)])
def test_native_default_rebuild_is_bounded(trees, monkeypatch, error, retry):
    """Only a qualifying default failure can request one local rebuild."""
    src, dst = trees('Retry source'), trees('Retry destination')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    node.inputs[1].default_value = 7
    copied = dst.nodes.new(node.bl_idname)
    attempts = []
    def reject(*args, **kwargs):
        attempts.append(True)
        if retry:
            raise backend._CollectionDefaultError('injected native readback failure')
        raise error('unrelated error')
    monkeypatch.setattr(backend, '_restore_collection_defaults', reject)
    with pytest.raises(CompileError, match='rebuild failed' if retry else 'unrelated error'):
        backend._copy_standalone_collection(node, copied, proofs={})
    assert len(attempts) == (2 if retry else 1)
    assert node.inputs[1].default_value == 7


def test_native_probe_cleanup_failure_keeps_private_residue(trees, monkeypatch):
    """Preserve both diagnostics and exclude a probe Blender refuses to remove."""
    src = trees('Cleanup source')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    _populate(node, 'index_switch_items', 3)
    original = backend._physical_layout
    calls = []
    def fail_layout(n):
        if n.id_data != src:
            calls.append(n.id_data)
            if len(calls) == 2:
                raise RuntimeError('injected probe failure')
        return original(n)
    def fail_remove(group, *, failures=None):
        failures.append(RuntimeError('injected cleanup failure'))
        return False
    monkeypatch.setattr(backend, '_physical_layout', fail_layout)
    monkeypatch.setattr(backend, '_remove_node_group_if_live', fail_remove)
    before = original(node)
    with pytest.raises(CompileError, match='injected probe failure.*injected cleanup failure'):
        backend._standalone_socket_layout(node)
    assert is_authority_ineligible_group(calls[0])
    assert original(node) == before


def test_native_shared_menu_probe_isolation(trees):
    """Probing a connected Menu cannot change another live user of the child."""
    child = trees('Shared Menu child')
    interface = child.interface.new_socket(name='Menu', in_out='OUTPUT', socket_type='NodeSocketMenu')
    go = child.nodes.new('NodeGroupOutput')
    parents = []
    for name in ('Parent', 'Other'):
        tree = trees(name)
        group = tree.nodes.new('GeometryNodeGroup')
        group.node_tree = child
        node = tree.nodes.new('GeometryNodeMenuSwitch')
        node.data_type = 'FLOAT'
        node.enum_items.new('C')
        node.inputs[0].default_value = 'C'
        node['unrelated'] = 'metadata'
        tree.links.new(group.outputs[0], node.inputs[0])
        parents.append((tree, group, node))
    def snapshot():
        return (child.users, interface.default_value, _socket_rows(go),
                tuple((_socket_rows(group), _socket_rows(node), _item_snapshot(node.enum_items),
                       _links(tree), node['unrelated']) for tree, group, node in parents),
                {g.as_pointer() for g in bpy.data.node_groups})
    before = snapshot()
    backend._standalone_socket_layout(parents[0][2])
    assert snapshot() == before


def test_native_index_copy_evaluates_same_geometry(trees):
    """A reordered Index result must actually displace geometry by the intended value."""
    from test_bundle_runtime import _evaluate_single_vertex
    src, dst = trees('Index evaluation'), trees('Index copied evaluation')
    src.interface.new_socket(name='Geometry', in_out='INPUT', socket_type='NodeSocketGeometry')
    src.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    entry, output = src.nodes.new('NodeGroupInput'), src.nodes.new('NodeGroupOutput')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    node.data_type = 'FLOAT'
    node.inputs[1].default_value = 11
    node.inputs[2].default_value = 12
    node.index_switch_items.move(0, 1)
    node.inputs[0].default_value = 0
    combine = src.nodes.new('ShaderNodeCombineXYZ')
    move = src.nodes.new('GeometryNodeSetPosition')
    src.links.new(node.outputs[0], combine.inputs[0])
    src.links.new(combine.outputs[0], move.inputs['Offset'])
    src.links.new(entry.outputs[0], move.inputs['Geometry'])
    src.links.new(move.outputs[0], output.inputs[0])
    backend._copy_group_contents(src, dst)
    assert _evaluate_single_vertex(src, 'Original Index') == pytest.approx((12, 0, 0))
    assert _evaluate_single_vertex(dst, 'Copied Index') == pytest.approx((12, 0, 0))


@pytest.mark.parametrize('nondefault', [False, True])
def test_native_unsupported_local_and_multiple_collections(trees, nondefault):
    """Default-looking and edited unsupported item owners are equally rejected."""
    tree = trees('Unsupported collections')
    bake = tree.nodes.new('GeometryNodeBake')
    multiple = tree.nodes.new('GeometryNodeForeachGeometryElementOutput')
    if nondefault:
        bake.bake_items.new('FLOAT', 'Value')
        multiple.main_items.new('FLOAT', 'Value')
    for node in (bake, multiple):
        with pytest.raises(CompileError, match='native collection'):
            backend._local_collection_property(node)
    assert backend._local_collection_property(tree.nodes.new('ShaderNodeMath')) is None
    for kind in ('NodeCombineBundle', 'NodeSeparateBundle'):
        assert backend._local_collection_property(tree.nodes.new(kind)) is None


def test_native_exact_id_fast_path_and_reproof(trees, monkeypatch):
    """Equal ordered native IDs need no probes; a changed layout invalidates a proof."""
    src, dst = trees('Fast path source'), trees('Fast path target')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    copy = dst.nodes.new(node.bl_idname)
    original = backend._standalone_socket_layout
    probes = []
    def record(n):
        probes.append(n)
        return original(n)
    monkeypatch.setattr(backend, '_standalone_socket_layout', record)
    proofs = {}
    backend._copy_standalone_collection(node, copy, proofs=proofs)
    assert not probes
    node.index_switch_items.move(0, 1)
    backend._verify_standalone_copy(node, copy, proofs=proofs)
    assert len(probes) == 2
    backend._verify_standalone_copy(node, copy, proofs=proofs)
    assert len(probes) == 2
    node.index_switch_items.new()
    node.index_switch_items.remove(node.index_switch_items[0])
    backend._verify_standalone_copy(node, copy, proofs=proofs)
    assert len(probes) == 3


def test_native_incomplete_proof_is_not_accepted(trees, monkeypatch):
    """An incomplete proof must not turn zip truncation into an apparent match."""
    src, dst = trees('Incomplete proof source'), trees('Incomplete proof target')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    node.index_switch_items.move(0, 1)
    copied = dst.nodes.new(node.bl_idname)
    original = backend._standalone_socket_layout
    def incomplete(n):
        return tuple(rows[:-1] for rows in original(n))
    monkeypatch.setattr(backend, '_standalone_socket_layout', incomplete)
    with pytest.raises(CompileError, match='incomplete'):
        backend._verify_standalone_copy(node, copied, proofs={})


@pytest.mark.parametrize('fault', ['duplicate', 'swapped', 'fixed'])
def test_native_corrupt_ownership_is_rejected(trees, monkeypatch, fault):
    """Reject duplicate roles, changed fixed IDs and same-class ownership swaps."""
    src, dst = trees('Corrupt proof source'), trees('Corrupt proof target')
    node = src.nodes.new('GeometryNodeIndexSwitch')
    node.index_switch_items.move(0, 1)
    copied = dst.nodes.new(node.bl_idname)
    original = backend._standalone_socket_layout
    def corrupt(n):
        inputs, outputs = original(n)
        if n != copied:
            return inputs, outputs
        rows = list(inputs)
        if fault == 'duplicate':
            rows[2] = (rows[1][0], *rows[2][1:])
        elif fault == 'swapped':
            left, right = rows[1][0], rows[2][0]
            rows[1], rows[2] = (right, *rows[1][1:]), (left, *rows[2][1:])
        else:
            rows[0] = ('not-the-fixed-id', *rows[0][1:])
        return tuple(rows), outputs
    monkeypatch.setattr(backend, '_standalone_socket_layout', corrupt)
    with pytest.raises(CompileError, match='proof|layout'):
        backend._verify_standalone_copy(node, copied, proofs={})
