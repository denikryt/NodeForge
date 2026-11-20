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


def test_unified_groups_only_publishes_no_root_instances_and_reuses_authority():
    """Definition compilation updates manual group references through their datablock."""
    root = _base('PR1_group_only')
    manual = root.nodes.new('GeometryNodeMeshCube')
    saved = (manual.as_pointer(), tuple(manual.location))
    source = 'def calc(v: Float) -> Float:\n    return v + 1.0\n'
    result = compiler.compile_source_in_tree(source, root, module_context='text:PR1-source')
    assert result.outcome == 'GROUPS_ONLY' and len(root.nodes) == 1
    from NodeForge.function_instances import FUNCTION_DEFINITION_OWNER_PROP
    fn_group, = [g for g in bpy.data.node_groups if g.get('nodeforge_module_name') == 'calc'
                 and g.get(FUNCTION_DEFINITION_OWNER_PROP)
                 and 'text:PR1-source' in g.get('nodeforge_module_origin', '')]
    group_node = root.nodes.new('GeometryNodeGroup')
    group_node.node_tree = fn_group
    root.name = 'PR1_group_only_renamed'
    compiler.compile_source_in_tree('def calc(v: Float) -> Float:\n    return v * 2.0\n', root,
                                    module_context='text:PR1-source')
    assert group_node.node_tree is fn_group
    assert (manual.as_pointer(), tuple(manual.location)) == saved
    assert sum(n.bl_idname == 'GeometryNodeGroup' for n in root.nodes) == 1
    assert any(n.bl_idname == 'ShaderNodeMath' and n.operation == 'MULTIPLY'
               for n in fn_group.nodes)
    result = compiler.compile_source_in_tree('FLAG = False', root)
    assert result.outcome == 'GROUPS_ONLY' and group_node.node_tree is fn_group


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
    assert not list(root.nodes)


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
    assert not list(root.nodes)
    assert not hasattr(ui, 'GNSCRIPT_MVP_OT_update_expression')

    def uncertain(*args, **kwargs):
        """Simulate a group rollback that could not restore authoritative data."""
        raise RootUpdateError('group cleanup incomplete', may_have_mutated=True)

    monkeypatch.setattr(ui, 'compile_source_in_tree', uncertain)
    assert ui.GNSCRIPT_MVP_OT_compile_expression.execute(operator, ctx) == {'FINISHED'}
    assert text.get(TEXT_MODULE_CONTEXT_PROP) == origin
    assert any('group cleanup incomplete' in msg for _, msg in reports)
