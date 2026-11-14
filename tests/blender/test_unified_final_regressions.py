"""Last-stage negative and positive regressions for the breaking .nf frontend."""

import bpy
import pytest

from NodeForge import compiler
from NodeForge.errors import CompileError

pytestmark = pytest.mark.blender


@pytest.mark.parametrize('source', [
    'def f():\n    value = input_float("Old")\n    return value\n',
    'def f(x: Float) -> Float:\n    output("Old", x)\n    return x\n',
])
def test_obsolete_function_io_is_rejected_without_publishing(source):
    """Signature/return is the only valid function interface declaration."""
    tree = bpy.data.node_groups.new('NFTest_old_function_io', 'GeometryNodeTree')
    user = tree.nodes.new('GeometryNodeMeshCube')
    before = user.as_pointer()
    with pytest.raises(CompileError):
        compiler.compose_source_initial(source, tree)
    assert [node.as_pointer() for node in tree.nodes] == [before]


def test_forward_function_alias_can_be_composed_without_duplicate_binding():
    """An alias declaration is not a second runtime assignment to a reserved name."""
    tree = bpy.data.node_groups.new('NFTest_alias_compose', 'GeometryNodeTree')
    compiler.compose_source_initial('''shortcut = later
result = shortcut(2.0)
def later(x: Float) -> Float:
    return x + 1
''', tree)
    assert len([node for node in tree.nodes if node.bl_idname == 'GeometryNodeGroup']) == 1


def test_reassigning_a_definition_alias_fails_before_node_mutation():
    """An invalid second assignment cannot be hidden by alias-declaration filtering."""
    tree = bpy.data.node_groups.new('NFTest_alias_reassignment', 'GeometryNodeTree')
    user = tree.nodes.new('GeometryNodeMeshCube')
    with pytest.raises(CompileError):
        compiler.compose_source_initial('''def f(x: Float) -> Float:
    return x
alias = f
alias = other_name
value = alias(2.0)
''', tree)
    assert tuple(tree.nodes) == (user,)
