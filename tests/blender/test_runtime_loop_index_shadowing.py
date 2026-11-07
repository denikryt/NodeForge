"""Runtime loop indices shadow same-named module compile-time constants."""

from helpers import *
from test_module_compile_time_fingerprints import _evaluate_float_output


@pytest.mark.parametrize('iterator', ('repeat_range(count)', 'range(count)'))
def test_runtime_index_shadowing_materializes_callee_and_evaluates(iterator):
    """The dynamic i==1 branch invokes b at iteration 1 and evaluates to 14."""
    source = f'''i = 100

def a(count: Int):
    x = 1.0
    for i in {iterator}:
        if i == 1:
            x = b(x)
        x = x + 1.0
    return x

def b(x: Float):
    return x + 10.0

result = a(3)
'''
    base = bpy.data.node_groups.new('NF-shadow-index-' + iterator[:5], 'GeometryNodeTree')
    compiler.compose_source_initial(source, base, module_context='test:runtime-index-' + iterator)
    call, = [node for node in base.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert call.node_tree.get('nodeforge_module_name') == 'a'
    nested = [node for node in call.node_tree.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(nested) == 1
    assert nested[0].node_tree.get('nodeforge_module_name') == 'b'
    assert round(_evaluate_float_output(base, call.outputs[0]), 5) == 14.0


def test_runtime_index_scope_restores_module_constant_for_following_code():
    """The module i=100 remains usable after a runtime loop's lexical scope."""
    source = '''i = 100

def a(count: Int):
    x = 1.0
    for i in repeat_range(count):
        x = x + 1.0
    if i == 100:
        x = x + 10.0
    return x

result = a(3)
'''
    base = bpy.data.node_groups.new('NF-shadow-index-scope', 'GeometryNodeTree')
    compiler.compose_source_initial(source, base, module_context='test:runtime-index-scope')
    call, = [node for node in base.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert round(_evaluate_float_output(base, call.outputs[0]), 5) == 14.0


def test_nested_runtime_index_shadowing_retains_inner_call():
    """A nested loop reusing i still calls b on its runtime index of one."""
    source = '''i = 100

def a(count: Int):
    x = 1.0
    for i in repeat_range(count):
        for i in repeat_range(2):
            if i == 1:
                x = b(x)
        x = x + 1.0
    return x

def b(x: Float):
    return x + 10.0

result = a(2)
'''
    base = bpy.data.node_groups.new('NF-shadow-index-nested', 'GeometryNodeTree')
    compiler.compose_source_initial(source, base, module_context='test:runtime-index-nested')
    call, = [node for node in base.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert round(_evaluate_float_output(base, call.outputs[0]), 5) == 23.0


def test_static_loop_target_shadows_module_constant_in_physical_group():
    """Static unrolling also binds its loop index in the function's own scope."""
    source = '''i = 100

def a():
    x = 1.0
    for i in range(3):
        if i == 1:
            x = b(x)
    return x

def b(x: Float):
    return x + 10.0

result = a()
'''
    base = bpy.data.node_groups.new('NF-shadow-static-index', 'GeometryNodeTree')
    compiler.compose_source_initial(source, base, module_context='test:static-index-scope')
    call, = [node for node in base.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert round(_evaluate_float_output(base, call.outputs[0]), 5) == 11.0
    nested = [node for node in call.node_tree.nodes if node.bl_idname == 'GeometryNodeGroup']
    assert len(nested) == 1
    assert nested[0].node_tree.get('nodeforge_module_name') == 'b'
