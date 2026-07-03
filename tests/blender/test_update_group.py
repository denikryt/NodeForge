from helpers import *




def test_update_group_rollback_and_preflight_contracts():
    group = compile_group('x = 1\noutput("x", x)', 'NFTest_update')
    compiler.update_expression_group(group, 'x = 2\noutput("x", x)')
    try:
        compiler.update_expression_group(group, '\ndef inc(a):\n    return a + 1\n\nx = inc(1)\ny = missing_func(1)\noutput("y", y)\n')
    except Exception:
        pass
    else:
        raise AssertionError('failed update did not raise')
    leaked = [g.name for g in bpy.data.node_groups if g.name.startswith('NodeForge.preflight.') or (g.name.startswith('NodeForge.local.') and 'preflight' in g.name)]
    check(not leaked, f'preflight/local preflight groups leaked: {leaked}')
    print('UPDATE_SUCCESS_FAILURE_OK')


def test_update_group_preserves_repeat_zone_dynamic_state_sockets_with_external_links():
    source = '''
count = input_int("Count", default=4)
instance_points = points(1)
for i in repeat_range(count):
    instance_points = join(instance_points, points(1))
output("instance_points", instance_points)
'''
    group = compile_group(source, 'NFTest_update_repeat_dynamic_sockets')
    wrapper = bpy.data.node_groups.new('NFTest_update_repeat_dynamic_wrapper', 'GeometryNodeTree')
    wrapper.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    group_output = wrapper.nodes.new('NodeGroupOutput')
    group_node = wrapper.nodes.new('GeometryNodeGroup')
    group_node.node_tree = group
    wrapper.links.new(group_node.outputs['instance_points'], group_output.inputs['Geometry'])

    external_state = compiler._capture_node_external_state(wrapper, group_node)
    compiler.update_expression_group(group, source)
    compiler._restore_node_external_state(wrapper, group_node, external_state)

    output_names = [socket.name for socket in group_node.outputs]
    check('instance_points' in output_names, f'updated group node outputs missing instance_points: {output_names}')
    link_names = [(link.from_socket.name, link.to_socket.name) for link in wrapper.links]
    check(('instance_points', 'Geometry') in link_names, f'instance_points external link was not preserved: {link_names}')

    repeat_outputs = [node for node in group.nodes if node.bl_idname == 'GeometryNodeRepeatOutput']
    check(len(repeat_outputs) == 1, 'expected one Repeat Output after update')
    repeat_socket_names = [socket.name for socket in repeat_outputs[0].inputs]
    check('instance_points' in repeat_socket_names, f'Repeat Output inputs missing dynamic state socket: {repeat_socket_names}')
    print('UPDATE_REPEAT_DYNAMIC_SOCKET_LINK_OK')
