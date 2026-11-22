"""Issue #67: multi-consumer non-String inline values retain one physical source.

F02 preserves deferred String for literal-only Menu sockets. That choice must
not change sharing of other inline types when downstream builtins adapt them.
"""

import bpy
import pytest

from helpers import compile_root

pytestmark = pytest.mark.blender


def _compile(source, name):
    tree = bpy.data.node_groups.new(name, "GeometryNodeTree")
    compile_root(source, tree)
    return tree


def _nodes(tree, type_id):
    return [node for node in tree.nodes if node.bl_idname == type_id]


def _shared_output_reaches(tree, producer, consumers, input_name):
    """Assert each consumer receives the same actual output socket."""
    output = producer.outputs[0]
    assert len(consumers) == 2
    for consumer in consumers:
        destination = consumer.inputs[input_name]
        assert destination.is_linked
        assert len(destination.links) == 1
        assert destination.links[0].from_socket == output
    assert sum(link.from_socket == output for link in tree.links) == 2


def test_multi_consumer_bool_compare_uses_one_bool_source():
    """Bool-to-Int Compare must not lose a shared inline Bool source."""
    tree = _compile('''def wrap(flag: Bool = True, __inline__=True) -> Bool:
    a = flag == False
    b = flag == True
    return b
result = wrap()
''', "NFTest_SharedInline_BoolCompare")
    producer, = _nodes(tree, "FunctionNodeInputBool")
    comparisons = _nodes(tree, "FunctionNodeCompare")
    assert len(comparisons) == 2
    assert all(node.data_type == "INT" for node in comparisons)
    _shared_output_reaches(tree, producer, comparisons, "A")
    assert all(link.is_valid for link in tree.links)


def test_multi_consumer_points_count_uses_one_int_source():
    """points() must not discard multi-sink provenance during numeric conversion."""
    tree = _compile('''def wrap(count: Int = 3, __inline__=True) -> Geometry:
    a = points(count)
    return points(count)
result = wrap()
''', "NFTest_SharedInline_Points")
    producer, = _nodes(tree, "FunctionNodeInputInt")
    lines = _nodes(tree, "GeometryNodeMeshLine")
    _shared_output_reaches(tree, producer, lines, "Count")
    assert all(link.is_valid for link in tree.links)


def test_multi_consumer_scalar_cube_uses_one_float_source():
    """Scalar cube() input expanded to vectors still shares a physical Float."""
    tree = _compile('''def wrap(size: Float = 2.0, __inline__=True) -> Geometry:
    a = cube(size)
    return cube(size)
result = wrap()
''', "NFTest_SharedInline_Cubes")
    producer, = _nodes(tree, "ShaderNodeValue")
    vectors = _nodes(tree, "ShaderNodeCombineXYZ")
    assert len(vectors) == 2
    assert len(_nodes(tree, "GeometryNodeMeshCube")) == 2
    assert all(link.is_valid for link in tree.links)
    assert sum(link.from_socket == producer.outputs[0] for link in tree.links) == 6
    assert all(
        all(socket.is_linked and socket.links[0].from_socket == producer.outputs[0]
            for socket in vector.inputs)
        for vector in vectors
    )


def test_single_consumer_nonstring_still_uses_default_without_producer():
    """Restoring multi-use sharing does not restore eager materialization to a single sink."""
    tree = _compile('''def wrap(count: Int = 3, __inline__=True) -> Geometry:
    return points(count)
result = wrap()
''', "NFTest_SharedInline_Single")
    line, = _nodes(tree, "GeometryNodeMeshLine")
    assert line.inputs["Count"].default_value == 3
    assert not line.inputs["Count"].is_linked
    assert not _nodes(tree, "FunctionNodeInputInt")
