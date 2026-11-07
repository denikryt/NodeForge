"""Blender integration coverage for local-function multiple returns."""

import bpy
import pytest

from NodeForge.errors import CompileError

from NodeForge import compiler


pytestmark = pytest.mark.blender


def _compile_direct_module(source: str, name: str):
    """Place mixed new-DSL declarations and operations in a test-owned base tree."""
    group = bpy.data.node_groups.new(name, 'GeometryNodeTree')
    try:
        compiler.compose_source_initial(source, group)
    except Exception:
        bpy.data.node_groups.remove(group, do_unlink=True)
        raise
    return group


def _group_nodes(group):
    """Return visible group-call nodes from a compiled root group."""
    return [node for node in group.nodes if node.bl_idname == "GeometryNodeGroup"]


def test_local_function_tuple_return_unpacks_from_one_group_node():
    group = _compile_direct_module(
        '''
value = index() * 1.0
def split(value: Float):
    doubled = value * 2
    tripled = value * 3
    return doubled, tripled
first, second = split(value)
first_out = first
second_out = second
''',
        "LocalMultiReturn",
    )
    calls = _group_nodes(group)
    assert len(calls) == 1
    assert [socket.name for socket in calls[0].outputs] == ["Doubled", "Tripled"]


def test_local_function_tuple_result_supports_compile_time_indexing():
    group = _compile_direct_module(
        '''
value = index() * 1.0
def split(value: Float):
    return value * 2, value * 3
result = split(value)
first_out = result[0]
last_out = result[-1]
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
        ('''
result = split(value)
invalid = result + 1
''', "unpack it or select an element"),
    ],
)
def test_local_function_tuple_misuse_is_controlled(source, message):
    script = f'''
value = index() * 1.0
def split(value: Float):
    return value * 2, value * 3
{source}
'''
    with pytest.raises(CompileError, match=message):
        _compile_direct_module(script, "LocalTupleError")


def test_list_target_and_stored_tuple_indices_compile():
    group = _compile_direct_module(
        '''
value = index() * 1.0
def split(value: Float):
    return value + 1, value + 2
[left, right] = split(value)
stored = split(value)
left_out = left
right_out = right
first_stored = stored[0]
last_stored = stored[-1]
''',
        "LocalListTargetAndStoredTuple",
    )
    assert len(_group_nodes(group)) == 2


def test_heterogeneous_typed_return_preserves_socket_types():
    group = _compile_direct_module(
        '''
value = index() * 1.0
pos = position()
def inspect(value: Float, pos: Vector):
    return value, pos, value > 0
scalar, vector_value, flag = inspect(value, pos)
scalar_out = scalar
vector_out = vector_value
flag_out = flag
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
        ('''
idx = index()
x = split(value)[idx]
''', "compile-time integer"),
    ],
)
def test_additional_tuple_boundaries_are_controlled(tail, message):
    script = f'''
value = index() * 1.0
def split(value: Float):
    return value, value + 1
{tail}
'''
    with pytest.raises(CompileError, match=message):
        _compile_direct_module(script, "LocalTupleBoundary")


def test_unpacking_clears_stale_compile_time_constants():
    group = _compile_direct_module(
        '''
a = 100
b = 200
value = index() * 1.0
def split(value: Float):
    return value + 1, value + 2
a, b = split(value)
a_out = a
b_out = b
''',
        "LocalTupleClearsConstants",
    )
    assert len(_group_nodes(group)) == 1


def test_annotation_mismatch_is_controlled():
    with pytest.raises(CompileError, match=r"split.*value.*FLOAT.*VECTOR"):
        _compile_direct_module(
            '''
position_value = position()
def split(value: Float):
    return value, value
left, right = split(position_value)
''',
            "LocalAnnotationMismatch",
        )


def test_tuple_unpacking_inside_repeat_range_is_not_implicit_state():
    group = _compile_direct_module(
        '''
iterations = 2
geometry = cube(1.0)
builder = geometry_builder()
def split(value: Int):
    return value, value + 1
for i in repeat_range(iterations):
    result = split(i)
    left, right = result
    moved = set_position(geometry, vector(left, right, 0.0))
    builder.add(moved)
geometry_out = builder.geometry
''',
        "LocalTupleRepeatRange",
    )
    assert not tuple(group.interface.items_tree)  # direct composition never changes base I/O
    assert len(_group_nodes(group)) == 1


def test_type_token_used_in_local_raw_node_is_not_treated_as_capture():
    group = _compile_direct_module(
        '''
value = index() * 1.0
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
float_out = float_value
int_out = int_value
''',
        "LocalRawNodeTypeToken",
    )
    call = _group_nodes(group)[0]
    assert [socket.type for socket in call.outputs] == ["VALUE", "INT"]
