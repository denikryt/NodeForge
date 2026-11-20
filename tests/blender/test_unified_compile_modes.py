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
