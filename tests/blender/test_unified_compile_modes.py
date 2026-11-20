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
