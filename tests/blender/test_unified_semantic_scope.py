"""Rev2 F01-F09: semantic scope and preflight observed in real Geometry Nodes."""

from helpers import *
from test_module_compile_time_fingerprints import _evaluate_float_output


def _compose(source, suffix):
    tree = bpy.data.node_groups.new('NF-Rev2-sem-' + suffix, 'GeometryNodeTree')
    compiler.compose_source_initial(source, tree, module_context='test:rev2-' + suffix)
    return tree, [node for node in tree.nodes if node.bl_idname == 'GeometryNodeGroup']


def test_function_reads_module_compile_time_constant_without_hidden_capture():
    """A module constant is resolved in the defining scope, not exposed as an input."""
    source = '''FACTOR = 3.0

def scale(value: Float) -> Float:
    return value * FACTOR

result = scale(2.0)
'''
    tree, calls = _compose(source, 'module-constant-not-capture')
    assert len(calls) == 1
    assert [socket.name for socket in calls[0].node_tree.interface.items_tree
            if socket.item_type == 'SOCKET' and socket.in_out == 'INPUT'] == ['value']
    assert round(_evaluate_float_output(tree, calls[0].outputs[0]), 5) == 6.0


def test_root_runtime_value_is_not_implicitly_captured_by_definition():
    """Undeclared root runtime values cannot become hidden function parameters."""
    source = '''root_geo = cube(1.0)

def reuse() -> Geometry:
    return root_geo

result = reuse()
'''
    tree = bpy.data.node_groups.new('NF-Rev2-no-implicit-capture', 'GeometryNodeTree')
    user_node = tree.nodes.new('GeometryNodeMeshCube')
    groups_before = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='Unknown name: root_geo'):
        compiler.compose_source_initial(source, tree, module_context='test:no-implicit-capture')
    assert [node.as_pointer() for node in tree.nodes] == [user_node.as_pointer()]
    assert {group.as_pointer() for group in bpy.data.node_groups} == groups_before


def test_independent_function_compile_time_list_mutations_do_not_leak():
    """An unused function cannot modify the list visible to a demanded sibling."""
    source = '''L = [1]

def unused(flag: Bool):
    L.append(2)
    return 0.0

def ok(value: Float = len(L)):
    return value

result = ok()
'''
    tree, calls = _compose(source, 'isolated-list')
    assert [call.node_tree.get('nodeforge_module_name') for call in calls] == ['ok']
    assert round(_evaluate_float_output(tree, calls[0].outputs[0]), 5) == 1.0


def test_false_recursion_from_sibling_runtime_if_arm_does_not_reject_unused():
    """Unknown branch mutation cannot leak into its alternative sibling arm."""
    source = '''L = [1]

def unused(flag: Bool):
    if flag:
        L.append(2)
    else:
        if len(L) == 2:
            unused(flag)
    return 0.0

def ok():
    return 1.0

result = ok()
'''
    tree, calls = _compose(source, 'false-conditional-cycle')
    assert [call.node_tree.get('nodeforge_module_name') for call in calls] == ['ok']
    assert round(_evaluate_float_output(tree, calls[0].outputs[0]), 5) == 1.0


def test_module_final_default_used_for_call_before_definition_and_later_ct_write():
    """Call position and caller scope cannot override the defining final module CT."""
    source = '''K = 1.0
x = f()

def f(value: Float = K):
    return value

K = 9.0
'''
    tree, calls = _compose(source, 'final-default')
    assert len(calls) == 1
    assert calls[0].inputs[0].name == 'value'
    assert calls[0].inputs[0].is_linked is False
    assert abs(calls[0].inputs[0].default_value - 9.0) < 1e-5
    assert round(_evaluate_float_output(tree, calls[0].outputs[0]), 5) == 9.0


def test_parameter_shadows_a_function_and_canonical_sibling_still_callable():
    """A local parameter has priority over the same-named module function."""
    source = '''def h(value: Float):
    return value * 10.0

def f(h: Float):
    return h + 1.0

def g(value: Float):
    return h(value)

x = f(5.0)
y = g(2.0)
'''
    tree, calls = _compose(source, 'lexical-shadow')
    assert [call.node_tree.get('nodeforge_module_name') for call in calls] == ['f', 'g']
    assert round(_evaluate_float_output(tree, calls[0].outputs[0]), 5) == 6.0
    assert [n.node_tree.get('nodeforge_module_name') for n in calls[1].node_tree.nodes
            if n.bl_idname == 'GeometryNodeGroup'] == ['h']
    second_source = source.replace('x = f(5.0)\n', '')
    second, second_calls = _compose(second_source, 'lexical-shadow-g')
    assert len(second_calls) == 1
    assert round(_evaluate_float_output(second, second_calls[0].outputs[0]), 5) == 20.0


def test_invalid_unused_signature_rejected_before_composition_mutation():
    """Eager source validation precedes any level-0 or nested group publication."""
    source = '''a = cube(1.0)

def unused(value):
    return value
'''
    tree = bpy.data.node_groups.new('NF-Rev2-preflight-error', 'GeometryNodeTree')
    first = tree.nodes.new('GeometryNodeMeshCube')
    second = tree.nodes.new('GeometryNodeTransform')
    tree.links.new(first.outputs['Mesh'], second.inputs['Geometry'])
    initial = {node.as_pointer() for node in tree.nodes}
    connections = {(link.from_node.as_pointer(), link.to_node.as_pointer()) for link in tree.links}
    groups = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='explicit type annotation'):
        compiler.compose_source_initial(source, tree, module_context='test:rev2-preflight-error')
    assert {node.as_pointer() for node in tree.nodes} == initial
    assert {(link.from_node.as_pointer(), link.to_node.as_pointer()) for link in tree.links} == connections
    assert {group.as_pointer() for group in bpy.data.node_groups} == groups


def test_read_before_local_binding_never_falls_back_to_outer_ct():
    """A later local assignment shadows the same-named module constant from entry."""
    source = '''K = 100.0

def f(x: Float):
    y = K + 1.0
    K = x
    return y

result = f(1.0)
'''
    tree = bpy.data.node_groups.new('NF-Rev2-before-assignment', 'GeometryNodeTree')
    user = tree.nodes.new('GeometryNodeMeshCube')
    with pytest.raises(CompileError, match='used before assignment'):
        compiler.compose_source_initial(source, tree, module_context='test:rev2-before-assignment')
    assert len(tree.nodes) == 1
    assert tree.nodes[0].as_pointer() == user.as_pointer()


@pytest.mark.parametrize('iterator,expected', [('range(2)', 12.0), ('repeat_range(count)', 14.0)])
def test_loop_index_temporarily_shadows_outer_function_without_losing_after_scope(iterator, expected):
    """Outer callable h survives a same-named static/dynamic for-index after the loop."""
    source = f'''def h(x: Float):
    return x + 10.0

def f(count: Int):
    result = 0.0
    for h in {iterator}:
        result = result + h
    return result + h(1.0)

value = f(3)
'''
    tree, calls = _compose(source, 'loop-callable-' + iterator[:5])
    assert len(calls) == 1
    assert round(_evaluate_float_output(tree, calls[0].outputs[0]), 5) == expected


@pytest.mark.parametrize(('parameter', 'assignment', 'expected'), [
    ('helper: Float', '', 5.0),
    ('x: Float', 'helper = x + 1.0\n    ', 6.0),
])
def test_function_name_shadowing_keeps_copied_numeric_local(parameter, assignment, expected):
    """Physical Group Node must return the numeric binding, not a function alias."""
    source = f'''def helper(x: Float) -> Float:
    return x * 2.0

def outer({parameter}) -> Float:
    {assignment}copied = helper
    return copied
result = outer(5.0)'''
    tree, calls = _compose(source, 'copied-numeric-' + str(expected))
    assert [call.node_tree.get('nodeforge_module_name') for call in calls] == ['outer']
    assert not any(n.bl_idname == 'GeometryNodeGroup' for n in calls[0].node_tree.nodes)
    assert round(_evaluate_float_output(tree, calls[0].outputs[0]), 5) == expected


@pytest.mark.parametrize(('parameter', 'assignment'), [
    ('helper: Float', ''),
    ('x: Float', 'helper = x + 1.0\n    '),
])
def test_calling_copied_numeric_shadow_fails_without_publication(parameter, assignment):
    """Reject a non-callable local before modifying unrelated user graph/groups."""
    source = f'''def helper(x: Float) -> Float:
    return x * 2.0

def outer({parameter}) -> Float:
    {assignment}copied = helper
    return copied(3.0)
result = outer(5.0)'''
    tree = bpy.data.node_groups.new('NF-Rev2-invalid-copied-numeric', 'GeometryNodeTree')
    cube = tree.nodes.new('GeometryNodeMeshCube')
    transform = tree.nodes.new('GeometryNodeTransform')
    tree.links.new(cube.outputs['Mesh'], transform.inputs['Geometry'])
    initial_nodes = {node.as_pointer() for node in tree.nodes}
    initial_links = {(link.from_node.as_pointer(), link.to_node.as_pointer()) for link in tree.links}
    original_groups = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match="Cannot call 'copied'"):
        compiler.compose_source_initial(source, tree, module_context='test:rev2-invalid-copied-numeric')
    assert {node.as_pointer() for node in tree.nodes} == initial_nodes
    assert {(link.from_node.as_pointer(), link.to_node.as_pointer()) for link in tree.links} == initial_links
    assert {group.as_pointer() for group in bpy.data.node_groups} == original_groups


def test_verified_source_alias_chain_still_physically_invokes_canonical_definition():
    src = '''def outer(x: Float) -> Float:
    first = helper
    second = first
    return second(x)

def helper(x: Float) -> Float:
    return x * 2.0
result = outer(5.0)'''
    tree, calls = _compose(src, 'verified-source-alias-chain')
    assert len(calls) == 1
    assert [node.node_tree.get('nodeforge_module_name') for node in calls[0].node_tree.nodes
            if node.bl_idname == 'GeometryNodeGroup'] == ['helper']
    assert round(_evaluate_float_output(tree, calls[0].outputs[0]), 5) == 10.0
