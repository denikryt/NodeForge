"""Blender integration coverage for local-function multiple returns."""

import bpy
import pytest

from NodeForge.errors import CompileError

from .helpers import compile_group


pytestmark = pytest.mark.blender


def _group_nodes(group):
    """Return visible group-call nodes from a compiled root group."""
    return [node for node in group.nodes if node.bl_idname == "GeometryNodeGroup"]


def test_local_function_tuple_return_unpacks_from_one_group_node():
    group = compile_group(
        '''
value = input_float("Value")
def split(value: Float):
    doubled = value * 2
    tripled = value * 3
    return doubled, tripled
first, second = split(value)
output("First", first)
output("Second", second)
''',
        "LocalMultiReturn",
    )
    calls = _group_nodes(group)
    assert len(calls) == 1
    assert [socket.name for socket in calls[0].outputs] == ["Doubled", "Tripled"]


def test_local_function_tuple_result_supports_compile_time_indexing():
    group = compile_group(
        '''
value = input_float("Value")
def split(value):
    return value * 2, value * 3
result = split(value)
output("First", result[0])
output("Last", result[-1])
''',
        "LocalTupleIndex",
    )
    assert len(_group_nodes(group)) == 1


@pytest.mark.parametrize(
    "source, message",
    [
        ("a, b, c = split(value)", "expected 3 values, got 2"),
        ("a, a = split(value)", "must be unique"),
        ("a, *rest = split(value)", "Starred tuple unpacking"),
        ("output('Result', split(value))", "unpack it or select an element"),
    ],
)
def test_local_function_tuple_misuse_is_controlled(source, message):
    script = f'''
value = input_float("Value")
def split(value):
    return value * 2, value * 3
{source}
'''
    with pytest.raises(CompileError, match=message):
        compile_group(script, "LocalTupleError")


def test_list_target_and_stored_tuple_indices_compile():
    group = compile_group(
        '''
value = input_float("Value")
def split(value: Float):
    return value + 1, value + 2
[left, right] = split(value)
stored = split(value)
output("Left", left)
output("Right", right)
output("Stored First", stored[0])
output("Stored Last", stored[-1])
''',
        "LocalListTargetAndStoredTuple",
    )
    assert len(_group_nodes(group)) == 2


def test_heterogeneous_typed_return_preserves_socket_types():
    group = compile_group(
        '''
value = input_float("Value")
pos = input_vector("Position")
def inspect(value: Float, pos: Vector):
    return value, pos, value > 0
scalar, vector_value, flag = inspect(value, pos)
output("Scalar", scalar)
output("Vector", vector_value)
output("Flag", flag)
''',
        "LocalHeterogeneousTuple",
    )
    call = _group_nodes(group)[0]
    assert [socket.type for socket in call.outputs] == ["VALUE", "VECTOR", "BOOLEAN"]


@pytest.mark.parametrize(
    "tail, message",
    [
        ("a, b = value", "Cannot unpack scalar"),
        ("a, (b, c) = split(value)", "flat sequence of names"),
        ("x = split(value) + 1", "unpack it or select an element"),
        ("x = split(value)[2]", "index 2 is out of range"),
        ("idx = input_int('Index')\nx = split(value)[idx]", "compile-time integer"),
    ],
)
def test_additional_tuple_boundaries_are_controlled(tail, message):
    script = f'''
value = input_float("Value")
def split(value):
    return value, value + 1
{tail}
'''
    with pytest.raises(CompileError, match=message):
        compile_group(script, "LocalTupleBoundary")


def test_unpacking_clears_stale_compile_time_constants():
    group = compile_group(
        '''
a = 100
b = 200
value = input_float("Value")
def split(value):
    return value + 1, value + 2
a, b = split(value)
output("A", a)
output("B", b)
''',
        "LocalTupleClearsConstants",
    )
    assert len(_group_nodes(group)) == 1


def test_annotation_mismatch_is_controlled():
    with pytest.raises(CompileError, match=r"split.*value.*FLOAT.*VECTOR"):
        compile_group(
            '''
position = input_vector("Position")
def split(value: Float):
    return value, value
left, right = split(position)
''',
            "LocalAnnotationMismatch",
        )


def test_tuple_unpacking_inside_repeat_range_is_not_implicit_state():
    group = compile_group(
        '''
iterations = input_int("Iterations", default=2)
geometry = input_geometry("Geometry")
builder = geometry_builder()
def split(value: Int):
    return value, value + 1
for i in repeat_range(iterations):
    result = split(i)
    left, right = result
    moved = set_position(geometry, vector(left, right, 0.0))
    builder.add(moved)
output("Geometry", builder.geometry)
''',
        "LocalTupleRepeatRange",
    )
    input_names = [
        item.name
        for item in group.interface.items_tree
        if getattr(item, "in_out", None) == "INPUT"
    ]
    assert set(input_names) == {"Geometry", "Iterations"}
    assert len(_group_nodes(group)) == 1


def test_type_token_used_in_local_raw_node_is_not_treated_as_capture():
    group = compile_group(
        '''
value = input_float("Value")
def rounded_pair(value: Float):
    integer = node(
        "FunctionNodeFloatToInt",
        props={"rounding_mode": "ROUND"},
        inputs={"Float": value},
        output="Integer",
        typ=Int,
    )
    return value, integer
float_value, int_value = rounded_pair(value)
output("Float", float_value)
output("Integer", int_value)
''',
        "LocalRawNodeTypeToken",
    )
    call = _group_nodes(group)[0]
    assert [socket.type for socket in call.outputs] == ["VALUE", "INT"]


def test_long_multi_output_helper_name_compiles_without_blender_truncation_collision():
    group = compile_group(
        '''
iterations = input_int("Iterations", default=1)
geometry = input_geometry("Geometry")
builder = geometry_builder()

def mix_biomes(
    world_position: Vector,
    plains_height: Float,
    plains_depth: Int,
    mountains_height: Float,
    mountains_depth: Int,
    highlands_height: Float,
    highlands_depth: Int,
    distribution_seed: Int,
    biome_scale: Float,
    transition_width: Float,
):
    terrain_height = plains_height + mountains_height + highlands_height
    terrain_depth = plains_depth + mountains_depth + highlands_depth
    return terrain_height, terrain_depth, biome_scale, transition_width

for i in repeat_range(iterations):
    height, depth, scale, width = mix_biomes(
        vector(0.0, 0.0, 0.0),
        1.0,
        2,
        3.0,
        4,
        5.0,
        6,
        7,
        0.01,
        0.2,
    )
    moved = set_position(geometry, vector(height, depth, scale + width))
    builder.add(moved)
output("Geometry", builder.geometry)
''',
        "NodeForge Group",
    )
    calls = [
        node
        for node in _group_nodes(group)
        if node.node_tree and node.node_tree.get("nodeforge_local_function_name") == "mix_biomes"
    ]
    assert len(calls) == 1
    helper = calls[0].node_tree
    assert len(helper.name) <= 255
    assert helper.name == "Mix Biomes"
    assert calls[0].label == "Mix Biomes"
    assert len(calls[0].inputs) == 10
    assert len(calls[0].outputs) == 4
    matching = [
        candidate
        for candidate in bpy.data.node_groups
        if candidate.get("nodeforge_local_function_name") == "mix_biomes"
        and candidate.get("nodeforge_local_function_namespace") == "NodeForge Group"
    ]
    assert matching == [helper]
    assert not any(candidate.name.startswith(helper.name + ".") for candidate in bpy.data.node_groups)


def test_local_function_call_nodes_use_readable_function_labels():
    group = compile_group(
        '''
value = input_float("Value")
def generate_chunk(value: Float):
    return value + 1.0
first = generate_chunk(value)
second = generate_chunk(first)
output("Value", second)
''',
        "ReadableLocalFunctionTitles",
    )
    calls = [
        node
        for node in _group_nodes(group)
        if node.node_tree and node.node_tree.get("nodeforge_local_function_name") == "generate_chunk"
    ]
    assert len(calls) == 2
    assert {node.label for node in calls} == {"Generate Chunk"}
    assert len({node.name for node in calls}) == 2
    assert {node.node_tree.name for node in calls} == {"Generate Chunk"}
