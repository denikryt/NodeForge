"""Blender acceptance for a single Compile action and definition publication."""

from helpers import *

from NodeForge.blender.direct_composition import materialize_definition_entries
from NodeForge.blender.function_materializer import FunctionMaterializationContext, FunctionMaterializer
from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
from NodeForge.function_instances import FunctionCompilationTrace


def _base(name):
    """Return a fresh editable Geometry Nodes root."""
    return bpy.data.node_groups.new(name, 'GeometryNodeTree')


def _publish(source, root, *, origin):
    """Call the extracted publication mechanism without root node placement."""
    prepared, backend, session, snapshot = compiler._prepare_direct_source(
        source, root, module_context=origin,
    )
    tx = BlenderGroupBuildTransaction()
    context = FunctionMaterializationContext(
        function_group_cache={}, function_group_transaction=tx,
        function_compilation_trace=FunctionCompilationTrace(),
        source_callable_session=session,
    )
    try:
        groups = materialize_definition_entries(prepared, FunctionMaterializer(group_backend=backend), context, snapshot)
        tx.commit()
        return groups
    except Exception:
        tx.rollback()
        raise


def test_definition_entries_publish_without_root_nodes_and_reuse_on_rename():
    """A stable source context owns an updated authoritative group despite root renames."""
    root = _base('PR1_entries')
    cube = root.nodes.new('GeometryNodeMeshCube')
    before = cube.as_pointer()
    source = 'def calc(v: Float) -> Float:\n    return v + 1.0\n'
    first, = _publish(source, root, origin='text:pr1-entries')
    assert len(root.nodes) == 1 and root.nodes[0].as_pointer() == before
    root.name = 'PR1_entries_renamed'
    second, = _publish('def calc(v: Float) -> Float:\n    return v * 2.0\n', root,
                        origin='text:pr1-entries')
    assert second.as_pointer() == first.as_pointer()
    assert any(node.bl_idname == 'ShaderNodeMath' and node.operation == 'MULTIPLY'
               for node in second.nodes)
    assert root.nodes[0].as_pointer() == before


@pytest.mark.parametrize('origin', [None, '', ' ', 'text: trailing ', 123])
def test_definition_without_stable_origin_rejected_before_build(origin):
    """Unanchored definitions cannot silently publish groups under a mutable name."""
    root = _base('PR1_unanchored')
    before_groups = {item.as_pointer() for item in bpy.data.node_groups}
    with pytest.raises(CompileError, match='stable module_context'):
        compiler._prepare_direct_source(
            'def calc(v: Float) -> Float:\n    return v + 1.0\n', root,
            module_context=origin,
        )
    assert {item.as_pointer() for item in bpy.data.node_groups} == before_groups


def test_unanchored_definition_free_source_is_allowed():
    """Pure root expressions and compile-time-only sources need no definition owner."""
    root = _base('PR1_definition_free')
    for source in ('FLAG = False', 'x = cube(1)'):
        prepared, *_ = compiler._prepare_direct_source(source, root)
        assert prepared is not None


def test_unified_modes_replace_only_for_semantically_physical_top_level():
    """Group-only leaves manual ordinary nodes in place; root output replaces them."""
    root = _base('PR1_two_modes')
    manual = root.nodes.new('GeometryNodeMeshCube')
    manual_pointer = manual.as_pointer()
    group_only = compiler.compile_source_in_tree('FLAG = False', root)
    assert group_only.outcome == 'GROUPS_ONLY'
    assert group_only.target is root and group_only.skipped_boundary_links == 0
    assert {node.as_pointer() for node in root.nodes} == {manual_pointer}
    physical = compiler.compile_source_in_tree('a = cube(1)', root)
    assert physical.outcome == 'ROOT_REPLACEMENT'
    assert len(root.nodes) == 1 and root.nodes[0].bl_idname == 'GeometryNodeMeshCube'
    assert root.nodes[0].as_pointer() != manual_pointer
    new_pointer = root.nodes[0].as_pointer()
    compiler.compile_source_in_tree('FLAG = False', root)
    assert root.nodes[0].as_pointer() == new_pointer
    compiler.compile_source_in_tree('a = cube(2)', root)
    assert sum(n.bl_idname == 'GeometryNodeMeshCube' for n in root.nodes) == 1


def test_unified_groups_only_places_once_and_reuses_authority():
    """Keep existing nodes and links when recompiling a placed function group."""
    root = _base('PR1_group_only')
    manual = root.nodes.new('GeometryNodeMeshCube')
    root.nodes.active = manual
    saved = (manual.as_pointer(), tuple(manual.location), manual.select)
    source = 'def calc(v: Float) -> Float:\n    return v + 1.0\n'
    result = compiler.compile_source_in_tree(source, root, module_context='text:PR1-source')
    assert result.outcome == 'GROUPS_ONLY' and len(root.nodes) == 2
    assert root.nodes.active == manual
    from NodeForge.function_instances import FUNCTION_DEFINITION_OWNER_PROP
    fn_group, = [g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == 'calc'
                 and g.get(FUNCTION_DEFINITION_OWNER_PROP)
                 and 'text:PR1-source' in g.get('nodeforge_module_origin', '')]
    group_node, = [node for node in root.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert group_node.node_tree == fn_group
    assert result.placed_function_groups == (fn_group.name,)
    assert group_node.location.x > manual.location.x + manual.width
    manual_math = root.nodes.new('ShaderNodeMath')
    root.links.new(group_node.outputs[0], manual_math.inputs[0])
    link_pointers = [(link.from_node.as_pointer(), link.to_node.as_pointer()) for link in root.links]
    root.name = 'PR1_group_only_renamed'
    recompiled = compiler.compile_source_in_tree('def calc(v: Float) -> Float:\n    return v * 2.0\n',
                                                 root, module_context='text:PR1-source')
    assert recompiled.placed_function_groups == ()
    assert group_node.node_tree is fn_group
    assert [(link.from_node.as_pointer(), link.to_node.as_pointer()) for link in root.links] == link_pointers
    assert manual_math.inputs[0].is_linked
    assert (manual.as_pointer(), tuple(manual.location), manual.select) == saved
    assert sum(n.bl_idname == 'GeometryNodeGroup' for n in root.nodes) == 1
    assert any(n.bl_idname == 'ShaderNodeMath' and n.operation == 'MULTIPLY'
               for n in fn_group.nodes)
    result = compiler.compile_source_in_tree('FLAG = False', root)
    assert result.outcome == 'GROUPS_ONLY' and group_node.node_tree is fn_group


def test_unified_groups_only_reuses_group_already_placed_by_user():
    """A manual instance of the same group already satisfies placement."""
    source = 'def evaluate(v: Float) -> Float:\n    return v + 1.0\n'
    origin = 'text:manual-instance'
    other_root = _base('manual_instance_source')
    compiler.compile_source_in_tree(source, other_root, module_context=origin)
    instance, = other_root.nodes
    root = _base('manual_instance_target')
    manual = root.nodes.new('GeometryNodeGroup')
    manual.node_tree = instance.node_tree
    manual.location = (120, 240)

    result = compiler.compile_source_in_tree(source, root, module_context=origin)

    assert result.placed_function_groups == ()
    assert list(root.nodes) == [manual]
    assert tuple(manual.location) == (120, 240)


def test_unified_groups_only_rolls_back_partial_placement(monkeypatch):
    """A placement error leaves the original tree and group datablocks in place."""
    from NodeForge.blender import direct_composition

    root = _base('placement_rollback')
    manual = root.nodes.new('GeometryNodeMeshCube')
    group_ids = {group.as_pointer() for group in bpy.data.node_groups}
    place = direct_composition.place_definition_entries

    def fail_after_first(target, groups, created_nodes):
        """Simulate a placement failure after one entry becomes visible."""
        place(target, groups[:1], created_nodes)
        raise RuntimeError('injected placement failure')

    monkeypatch.setattr(direct_composition, 'place_definition_entries', fail_after_first)
    with pytest.raises(RuntimeError, match='injected placement failure'):
        compiler.compile_source_in_tree(
            'def first() -> Geometry:\n    return cube(1)\n'
            'def second() -> Geometry:\n    return cube(2)\n',
            root, module_context='text:placement-rollback',
        )

    assert list(root.nodes) == [manual]
    assert {group.as_pointer() for group in bpy.data.node_groups} == group_ids


def test_unified_definitions_require_stable_origin_even_with_root_statements():
    """A source owner must be anchored before any root or group publication."""
    root = _base('PR1_anchored_physical')
    manual = root.nodes.new('GeometryNodeMeshCube')
    source = 'def calc() -> Geometry:\n    return cube(1)\na = calc()\n'
    with pytest.raises(CompileError, match='stable module_context'):
        compiler.compile_source_in_tree(source, root)
    assert len(root.nodes) == 1 and root.nodes[0].as_pointer() == manual.as_pointer()


def _radius_root(name, *, with_input):
    """Make a base interface whose Radius native socket identifier is not zero."""
    tree = _base(name)
    unused = tree.interface.new_socket(name='Unused', in_out='INPUT', socket_type='NodeSocketFloat')
    item = tree.interface.new_socket(name='Radius', in_out='INPUT', socket_type='NodeSocketFloat')
    assert item.identifier != unused.identifier
    tree.interface.remove(unused)
    inlet = tree.nodes.new('NodeGroupInput') if with_input else None
    return tree, inlet, item.identifier


def test_canonical_root_input_reuses_manual_node_and_properties():
    """Repeated raw reads never edit an existing user's Group Input node."""
    root, inlet, identifier = _radius_root('PR1_existing_group_input', with_input=True)
    inlet.label = 'Keep label'
    inlet.location = (312, 450)
    pointer = inlet.as_pointer()
    code = '''a = node("NodeGroupInput", output="Radius", typ=Float)
b = node("NodeGroupInput", output="Radius", typ=Float)
c = a + b
'''
    for _ in range(2):
        compiler.compile_source_in_tree(code, root)
        inputs = [node for node in root.nodes if node.bl_idname == 'NodeGroupInput']
        assert len(inputs) == 1 and inputs[0].as_pointer() == pointer
        assert inputs[0].label == 'Keep label' and tuple(inputs[0].location) == (312, 450)
        assert inputs[0].outputs['Radius'].identifier == identifier
        math = [node for node in root.nodes if node.bl_idname == 'ShaderNodeMath']
        assert math and all(node.inputs[0].is_linked for node in math if node.operation == 'ADD')
    with pytest.raises(CompileError, match='differs'):
        compiler.compile_source_in_tree(
            'a = node("NodeGroupInput", props={"label":"Wrong"}, output="Radius", typ=Float)', root,
        )
    assert inlet.label == 'Keep label' and inlet.as_pointer() == pointer


def test_canonical_root_input_creates_one_and_remains_across_recompiles():
    """A missing physical Input is created lazily then reused by subsequent Compile."""
    root, _, identifier = _radius_root('PR1_missing_group_input', with_input=False)
    source = 'a = node("NodeGroupInput", props={"label": "Created"}, output="Radius", typ=Float)'
    compiler.compile_source_in_tree(source, root)
    inlet, = [node for node in root.nodes if node.bl_idname == 'NodeGroupInput']
    pointer = inlet.as_pointer()
    assert inlet.label == 'Created' and inlet.outputs['Radius'].identifier == identifier
    compiler.compile_source_in_tree(source, root)
    assert [n.as_pointer() for n in root.nodes if n.bl_idname == 'NodeGroupInput'] == [pointer]
    with pytest.raises(CompileError):
        compiler.compile_source_in_tree('a = node("NodeGroupInput", output="NO_SUCH_SOCKET", typ=Float)', root)
    assert [n.as_pointer() for n in root.nodes if n.bl_idname == 'NodeGroupInput'] == [pointer]


def test_nested_inline_raw_input_shares_root_endpoint():
    """Inline source expansion references the caller's one physical input."""
    root, inlet, identifier = _radius_root('PR1_inline_group_input', with_input=True)
    code = '''def read_radius(__inline__=True) -> Float:
    return node("NodeGroupInput", output="Radius", typ=Float)
a = read_radius()
b = read_radius()
c = a + b
'''
    compiler.compile_source_in_tree(code, root, module_context='text:pr1-inline')
    assert [n.as_pointer() for n in root.nodes if n.bl_idname == 'NodeGroupInput'] == [inlet.as_pointer()]
    assert inlet.outputs['Radius'].identifier == identifier


def test_canonical_root_input_chooses_first_manual_and_validates_named_outputs():
    """Named multi-socket reads reuse the first manual Input and leave other Inputs alone."""
    root, first, radius_id = _radius_root('PR1_two_manual_inputs', with_input=True)
    amount = root.interface.new_socket(name='Amount', in_out='INPUT', socket_type='NodeSocketFloat')
    second = root.nodes.new('NodeGroupInput')
    first.label = 'First remains'
    second.label = 'Second remains'
    second.location = (333, -444)
    stable = ((first.as_pointer(), first.label), (second.as_pointer(), second.label, tuple(second.location)))
    source = 'pair = node("NodeGroupInput", outputs={"Radius": Float, "Amount": Float})\nresult = pair.Radius + pair.Amount\n'
    for _ in range(2):
        compiler.compile_source_in_tree(source, root)
        inputs = [n for n in root.nodes if n.bl_idname == 'NodeGroupInput']
        assert len(inputs) == 2
        assert (inputs[0].as_pointer(), inputs[0].label) == stable[0]
        assert (inputs[1].as_pointer(), inputs[1].label, tuple(inputs[1].location)) == stable[1]
        assert first.outputs['Radius'].identifier == radius_id
        assert first.outputs['Amount'].identifier == amount.identifier
        math, = [n for n in root.nodes if n.bl_idname == 'ShaderNodeMath' and n.operation == 'ADD']
        assert {link.from_node.as_pointer() for link in root.links if link.to_node == math} == {first.as_pointer()}
    before = {n.as_pointer() for n in root.nodes}
    with pytest.raises(CompileError):
        compiler.compile_source_in_tree('pair = node("NodeGroupInput", outputs={"Radius": Vector})', root)
    assert {n.as_pointer() for n in root.nodes} == before
    assert first.label == 'First remains' and second.label == 'Second remains'


def test_unapproved_group_output_is_rejected_and_rolled_back(monkeypatch):
    """A structural Group Output cannot be smuggled into root cutover."""
    from NodeForge.blender import ir_lowering
    root, inlet, _ = _radius_root('PR1_rogue_output', with_input=True)
    ptrs = {node.as_pointer() for node in root.nodes}
    old = ir_lowering._cube_geometry

    def rogue(*args, **kwargs):
        """Inject a forbidden structural output after an otherwise valid geometry call."""
        result = old(*args, **kwargs)
        args[0].nodes.new('NodeGroupOutput')
        return result

    monkeypatch.setattr(ir_lowering, '_cube_geometry', rogue)
    with pytest.raises(CompileError, match='unapproved structural'):
        compiler.compile_source_in_tree('a = cube(1)', root)
    assert {node.as_pointer() for node in root.nodes} == ptrs


def test_groups_only_failed_transaction_rollback_requires_undo(monkeypatch):
    """An incomplete group rollback is surfaced through the root UI error type."""
    from NodeForge.blender.root_update import RootUpdateError
    root = _base('PR1_group_rollback_failure')
    origin = 'text:PR1-rollback'
    compiler.compile_source_in_tree('def a(x: Float) -> Float:\n    return x + 1\n',
                                    root, module_context=origin)
    original = FunctionMaterializer.materialize_module
    seen = []

    def fail_after_update(self, prepared, materialization, context, **kwargs):
        """Fail the second entry after the first one updated its authority."""
        seen.append(prepared.contract.function_id.name)
        if seen[-1] == 'b':
            raise RuntimeError('second function failed')
        return original(self, prepared, materialization, context, **kwargs)

    def broken_rollback(self):
        """Expose the existing group transaction's uncertain rollback boundary."""
        raise RuntimeError('group restoration failed')

    monkeypatch.setattr(FunctionMaterializer, 'materialize_module', fail_after_update)
    monkeypatch.setattr(BlenderGroupBuildTransaction, 'rollback', broken_rollback)
    with pytest.raises(RootUpdateError, match='group restoration failed') as raised:
        compiler.compile_source_in_tree('''def a(x: Float) -> Float:
    return x * 2

def b(x: Float) -> Float:
    return x + 1
''', root, module_context=origin)
    assert seen == ['a', 'b'] and raised.value.may_have_mutated
    assert 'second function failed' in str(raised.value)
    assert isinstance(raised.value.__cause__, RuntimeError)
    assert [node.node_tree.get('nodeforge_module_name') for node in root.nodes] == ['a']


def test_single_compile_operator_preserves_undo_and_selected_text_origin(monkeypatch):
    """The retained Compile operator uses the Text origin and retains uncertain Undo."""
    from types import SimpleNamespace
    from NodeForge import ui
    from NodeForge.blender.root_update import RootUpdateError
    from NodeForge.compiler_identities import TEXT_MODULE_CONTEXT_PROP
    root = _base('PR1_operator')
    text = bpy.data.texts.new('PR1_operator_text')
    text.write('def make() -> Geometry:\n    return cube(1)\n')
    reports = []
    operator = SimpleNamespace(expression='', report=lambda kind, msg: reports.append((kind, msg)))
    ctx = SimpleNamespace(scene=SimpleNamespace(gn_script_mvp=SimpleNamespace(text_block=text)))
    monkeypatch.setattr(ui, '_eligible_base_gn_tree', lambda context: root)
    assert ui.GNSCRIPT_MVP_OT_compile_expression.execute(operator, ctx) == {'FINISHED'}
    origin = text.get(TEXT_MODULE_CONTEXT_PROP)
    assert isinstance(origin, str) and origin
    node, = root.nodes
    assert node.node_tree.get('nodeforge_module_name') == 'make'
    assert any(f'Placed in {root.name}: {node.node_tree.name}' == msg for _, msg in reports)
    assert not hasattr(ui, 'GNSCRIPT_MVP_OT_update_expression')

    def uncertain(*args, **kwargs):
        """Simulate a group rollback that could not restore authoritative data."""
        raise RootUpdateError('group cleanup incomplete', may_have_mutated=True)

    monkeypatch.setattr(ui, 'compile_source_in_tree', uncertain)
    assert ui.GNSCRIPT_MVP_OT_compile_expression.execute(operator, ctx) == {'FINISHED'}
    assert text.get(TEXT_MODULE_CONTEXT_PROP) == origin
    assert any('group cleanup incomplete' in msg for _, msg in reports)


def test_public_root_compiler_exposes_only_unified_route():
    """Neither legacy append nor Update remains available as an API escape hatch."""
    assert callable(compiler.compile_source_in_tree)
    assert not hasattr(compiler, 'compose_source_initial')
    assert not hasattr(compiler, 'update_source_in_tree')
    root = _base('PR1_single_root_api')
    assert compiler.compile_script_auto('FLAG = False', root) == (root, False)
    with pytest.raises(CompileError, match='stable module_context'):
        compiler.compile_script_auto('def calc() -> Float:\n    return 1.0\n', root)


def _postcommit_retirement_probe(monkeypatch, group_backend):
    """Identify live journal backups retired after the actual commit PONR."""
    phase = []
    commit = group_backend.BlenderGroupBuildTransaction.commit

    def tracked_commit(transaction):
        phase.append(transaction)
        try:
            return commit(transaction)
        finally:
            phase.pop()

    monkeypatch.setattr(group_backend.BlenderGroupBuildTransaction, 'commit', tracked_commit)

    def retiring(group):
        return bool(group is not None and phase and phase[-1]._closed and
                    any(event.get('backup') == group for event in phase[-1]._mutation_journal))
    return retiring


def test_groups_only_postcommit_cleanup_exception_preserves_undo(monkeypatch):
    """A warning promoted to an exception after commit retains the Undo signal."""
    import warnings
    from NodeForge.blender import group_backend
    from NodeForge.blender.root_update import RootUpdateError
    from NodeForge.function_instances import FUNCTION_DEFINITION_OWNER_PROP

    root = _base('PR1_postcommit_uncertain')
    origin = 'text:pr1-postcommit'
    source_v1 = 'def calc(v: Float) -> Float:\n    return v + 1.0\n'
    source_v2 = 'def calc(v: Float) -> Float:\n    return v * 2.0\n'
    compiler.compile_source_in_tree(source_v1, root, module_context=origin)
    published, = [g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == 'calc'
                  and g.get(FUNCTION_DEFINITION_OWNER_PROP)
                  and origin in g.get('nodeforge_module_origin', '')]
    manual = root.nodes.new('GeometryNodeGroup')
    manual.node_tree = published
    manual_pointer = manual.as_pointer()
    original_pointer = published.as_pointer()
    removal = group_backend._remove_node_group_if_live
    retiring = _postcommit_retirement_probe(monkeypatch, group_backend)

    def cleanup_with_warning(group, *, failures=None):
        """Inject one failed postcommit retirement after removing the backup."""
        inject = retiring(group)
        result = removal(group, failures=failures)
        if inject and failures is not None:
            failures.append(RuntimeError('injected postcommit backup cleanup failure'))
        return result

    monkeypatch.setattr(group_backend, '_remove_node_group_if_live', cleanup_with_warning)
    with warnings.catch_warnings():
        warnings.simplefilter('error', RuntimeWarning)
        with pytest.raises(RootUpdateError, match='postcommit backup cleanup failure') as exc:
            compiler.compile_source_in_tree(source_v2, root, module_context=origin)
    assert exc.value.may_have_mutated is True
    assert isinstance(exc.value.__cause__, RuntimeWarning)
    assert 'injected postcommit backup cleanup failure' in str(exc.value.__cause__)
    assert manual.as_pointer() == manual_pointer
    assert manual.node_tree.as_pointer() == original_pointer
    assert any(n.bl_idname == 'ShaderNodeMath' and n.operation == 'MULTIPLY'
               for n in published.nodes)


def test_groups_only_postcommit_warning_without_escalation_keeps_success(monkeypatch):
    """Ordinary best-effort retirement warnings remain successful commits."""
    import warnings
    from NodeForge.blender import group_backend

    root = _base('PR1_postcommit_warning')
    origin = 'text:pr1-postcommit-default'
    compiler.compile_source_in_tree('def calc(v: Float) -> Float:\n    return v + 1.0\n',
                                    root, module_context=origin)
    removal = group_backend._remove_node_group_if_live
    retiring = _postcommit_retirement_probe(monkeypatch, group_backend)

    def cleanup_with_warning(group, *, failures=None):
        """Record a postcommit cleanup warning without aborting publication."""
        inject = retiring(group)
        result = removal(group, failures=failures)
        if inject and failures is not None:
            failures.append(RuntimeError('injected benign retirement failure'))
        return result

    monkeypatch.setattr(group_backend, '_remove_node_group_if_live', cleanup_with_warning)
    with warnings.catch_warnings(record=True) as emitted:
        warnings.simplefilter('always', RuntimeWarning)
        result = compiler.compile_source_in_tree(
            'def calc(v: Float) -> Float:\n    return v * 2.0\n',
            root, module_context=origin,
        )
    assert result.outcome == 'GROUPS_ONLY'
    assert any('injected benign retirement failure' in str(w.message) for w in emitted)


def test_internal_root_lowering_rejects_definitions_without_mutating_root():
    """Only the unified route can place groups for definition-only scripts."""
    from NodeForge.blender.direct_composition import compose_prepared_initial

    root = _base('PR1_internal_definition_only_rejected')
    manual = root.nodes.new('GeometryNodeMeshCube')
    original_ptr = manual.as_pointer()
    source = 'def calc(v: Float) -> Float:\n    return v + 1.0\n'
    prepared, backend, session, snapshot = compiler._prepare_direct_source(
        source, root, module_context='text:pr1-internal-guard',
    )
    from NodeForge.blender.ir_lowering import RootInputEndpoint
    group_ptrs = {group.as_pointer() for group in bpy.data.node_groups}
    tx = BlenderGroupBuildTransaction()
    with pytest.raises(CompileError, match='physical top-level body'):
        compose_prepared_initial(
            prepared, root, backend, session,
            transaction=tx, root_input_endpoint=RootInputEndpoint(),
        )
    tx.rollback()
    assert {group.as_pointer() for group in bpy.data.node_groups} == group_ptrs
    assert [node.as_pointer() for node in root.nodes] == [original_ptr]
    assert compiler.compile_source_in_tree(
        source, root, module_context='text:pr1-internal-guard',
    ).outcome == 'GROUPS_ONLY'
    existing, placed = root.nodes
    assert existing.as_pointer() == original_ptr
    assert placed.bl_idname == 'GeometryNodeGroup'


def test_compile_operator_retains_undo_on_real_postcommit_cleanup_exception(monkeypatch):
    """The real group publication error travels through the UI's Undo-aware path."""
    import warnings
    from types import SimpleNamespace
    from NodeForge import ui
    from NodeForge.blender import group_backend

    root = _base('PR1_operator_postcommit')
    text = bpy.data.texts.new('PR1_operator_postcommit_source')
    text.write('def calc(v: Float) -> Float:\n    return v + 1.0\n')
    reports = []
    operator = SimpleNamespace(expression='', report=lambda kind, msg: reports.append((kind, msg)))
    context = SimpleNamespace(scene=SimpleNamespace(gn_script_mvp=SimpleNamespace(text_block=text)))
    monkeypatch.setattr(ui, '_eligible_base_gn_tree', lambda _context: root)
    assert ui.GNSCRIPT_MVP_OT_compile_expression.execute(operator, context) == {'FINISHED'}

    text.clear()
    text.write('def calc(v: Float) -> Float:\n    return v * 2.0\n')
    cleanup = group_backend._remove_node_group_if_live
    retiring = _postcommit_retirement_probe(monkeypatch, group_backend)

    def fail_postcommit(group, *, failures=None):
        """Force a retirement warning after authoritative publication."""
        inject = retiring(group)
        removed = cleanup(group, failures=failures)
        if inject and failures is not None:
            failures.append(RuntimeError('UI postcommit warning-as-error'))
        return removed

    monkeypatch.setattr(group_backend, '_remove_node_group_if_live', fail_postcommit)
    with warnings.catch_warnings():
        warnings.simplefilter('error', RuntimeWarning)
        assert ui.GNSCRIPT_MVP_OT_compile_expression.execute(operator, context) == {'FINISHED'}
    assert any('postcommit cleanup failed' in msg and 'UI postcommit warning-as-error' in msg
               for _, msg in reports)
    assert [node.node_tree.get('nodeforge_module_name') for node in root.nodes] == ['calc']
