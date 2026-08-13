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


def _panel_snapshot(group):
    """Return panel hierarchy/order state for transactional update assertions."""
    result = []
    for item in group.interface.items_tree:
        if getattr(item, "item_type", None) != "PANEL" or not getattr(item, "name", ""):
            continue
        parent_name = getattr(getattr(item, "parent", None), "name", "") or ""
        children = [
            child.name
            for child in group.interface.items_tree
            if (getattr(getattr(child, "parent", None), "name", "") or "") == item.name
        ]
        result.append((item.name, parent_name, bool(item.default_closed), children))
    return result


def test_update_group_preserves_panel_hierarchy_values_links_and_rollback():
    source_before = '''
radius = input_float("Radius", default=0.1)
segments = input_int("Segments", default=8)
panel([radius, segments], name="Stem")
output("Radius", radius)
'''
    source_after = '''
radius = input_float("Radius", default=0.2)
segments = input_int("Segments", default=12)
panel([segments, radius], name="Stem", collapsed=True)
output("Radius", radius)
'''
    group = compile_group(source_before, "NFTest_update_panels")

    wrapper = bpy.data.node_groups.new("NFTest_update_panels_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="External Segments", in_out="INPUT", socket_type="NodeSocketInt")
    wrapper.interface.new_socket(name="Result", in_out="OUTPUT", socket_type="NodeSocketFloat")
    wrapper_input = wrapper.nodes.new("NodeGroupInput")
    wrapper_output = wrapper.nodes.new("NodeGroupOutput")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    wrapper.links.new(wrapper_input.outputs["External Segments"], group_node.inputs["Segments"])
    wrapper.links.new(group_node.outputs["Radius"], wrapper_output.inputs["Result"])
    group_node.inputs["Radius"].default_value = 0.33

    external_state = compiler._capture_node_external_state(wrapper, group_node)
    compiler.update_expression_group(group, source_after)
    compiler._restore_node_external_state(wrapper, group_node, external_state)

    snapshot = _panel_snapshot(group)
    check(snapshot == [("Stem", "", True, ["Segments", "Radius"])], f"panel cutover mismatch: {snapshot}")
    check(abs(float(group_node.inputs["Radius"].default_value) - 0.33) < 1e-6, "Radius user override was not preserved")
    check(int(group_node.inputs["Segments"].default_value) == 12, "new Segments script default did not reach group node")
    incoming = [link for link in wrapper.links if link.to_node == group_node and link.to_socket.name == "Segments"]
    check(len(incoming) == 1 and incoming[0].from_socket.name == "External Segments", "Segments external link was not restored")
    leaked = [
        g.name for g in bpy.data.node_groups
        if g.name.startswith("NodeForge.replacement.") or g.name.startswith("NodeForge.rollback.") or g.name.startswith("NodeForge.preflight.")
    ]
    check(not leaked, f"panel update leaked temporary groups: {leaked}")

    stable_snapshot = _panel_snapshot(group)
    compiler._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        compiler.update_expression_group(
            group,
            '''
radius = input_float("Radius", default=0.4)
segments = input_int("Segments", default=16)
panel([radius, segments], name="Changed")
output("Radius", radius)
''',
        )
    except RuntimeError as exc:
        check("Injected NodeForge cutover failure" in str(exc), f"unexpected injected failure: {exc}")
    else:
        raise AssertionError("injected panel cutover failure did not raise")

    check(_panel_snapshot(group) == stable_snapshot, "rollback did not restore panel hierarchy")
    leaked = [
        g.name for g in bpy.data.node_groups
        if g.name.startswith("NodeForge.replacement.") or g.name.startswith("NodeForge.rollback.") or g.name.startswith("NodeForge.preflight.")
    ]
    check(not leaked, f"panel rollback leaked temporary groups: {leaked}")



def _nested_repeat_pairs(group):
    """Return Repeat Input/Output pairs keyed by each output node."""
    repeat_inputs = [node for node in group.nodes if node.bl_idname == "GeometryNodeRepeatInput"]
    repeat_outputs = [node for node in group.nodes if node.bl_idname == "GeometryNodeRepeatOutput"]
    pairs = []
    for repeat_input in repeat_inputs:
        paired_output = getattr(repeat_input, "paired_output", None)
        check(paired_output is not None, f"Repeat Input {repeat_input.name!r} lost its paired_output")
        check(paired_output in repeat_outputs, "Repeat Input paired_output is not a Repeat Output in the group")
        pairs.append((repeat_input, paired_output))
    check(len({output.as_pointer() for _, output in pairs}) == len(pairs), "multiple Repeat Inputs point at the same Repeat Output")
    return repeat_inputs, repeat_outputs, pairs


def _evaluate_group_geometry_vertices(group, name):
    """Evaluate a Geometry-output group through a temporary Geometry Nodes modifier."""
    mesh_data = bpy.data.meshes.new(name + "_BaseMesh")
    obj = bpy.data.objects.new(name + "_Object", mesh_data)
    bpy.context.collection.objects.link(obj)
    modifier = obj.modifiers.new("NodeForge", "NODES")
    modifier.node_group = group
    try:
        depsgraph = bpy.context.evaluated_depsgraph_get()
        depsgraph.update()
        evaluated = obj.evaluated_get(depsgraph)
        mesh = evaluated.to_mesh()
        try:
            return [tuple(round(float(coord), 6) for coord in vertex.co) for vertex in mesh.vertices]
        finally:
            evaluated.to_mesh_clear()
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        if bpy.data.meshes.get(mesh_data.name) is mesh_data:
            bpy.data.meshes.remove(mesh_data, do_unlink=True)


def test_update_group_preserves_nested_repeat_pairings_dynamic_state_and_result():
    """Transactional cutover must rebuild both nested Repeat pairs and their state sockets."""
    source = """
x = 0
for i in repeat_range(2):
    for j in repeat_range(3):
        x = x + 1
output("Geometry", point(vector(x, 0, 0)))
"""
    group = compile_group(source, "NFTest_update_nested_repeat")

    wrapper = bpy.data.node_groups.new("NFTest_update_nested_repeat_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_output = wrapper.nodes.new("NodeGroupOutput")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    wrapper.links.new(group_node.outputs["Geometry"], group_output.inputs["Geometry"])

    external_state = compiler._capture_node_external_state(wrapper, group_node)
    compiler.update_expression_group(group, source)
    compiler._restore_node_external_state(wrapper, group_node, external_state)

    repeat_inputs, repeat_outputs, pairs = _nested_repeat_pairs(group)
    check(len(repeat_inputs) == 2, f"expected two Repeat Inputs after nested cutover, got {len(repeat_inputs)}")
    check(len(repeat_outputs) == 2, f"expected two Repeat Outputs after nested cutover, got {len(repeat_outputs)}")
    check(len(pairs) == 2, f"expected two independent Repeat pairs after nested cutover, got {len(pairs)}")

    for _, repeat_output in pairs:
        item_names = [item.name for item in repeat_output.repeat_items]
        input_names = [socket.name for socket in repeat_output.inputs]
        output_names = [socket.name for socket in repeat_output.outputs]
        check("x" in item_names, f"nested Repeat Output lost x dynamic item: {item_names}")
        check("x" in input_names, f"nested Repeat Output lost x input socket: {input_names}")
        check("x" in output_names, f"nested Repeat Output lost x output socket: {output_names}")

    link_names = [(link.from_socket.name, link.to_socket.name) for link in wrapper.links]
    check(("Geometry", "Geometry") in link_names, f"nested update lost external Geometry link: {link_names}")
    check(
        _evaluate_group_geometry_vertices(group, "NFTest_update_nested_repeat_eval") == [(6.0, 0.0, 0.0)],
        "nested Repeat result changed after transactional cutover",
    )


def test_nested_repeat_save_reopen_preserves_pairings_dynamic_state_and_result(tmp_path):
    """Nested Repeat pairings and dynamic sockets must survive .blend serialization and reload."""
    source = """
x = 0
for i in repeat_range(2):
    for j in repeat_range(3):
        x = x + 1
output("Geometry", point(vector(x, 0, 0)))
"""
    group_name = "NFTest_nested_repeat_persistence"
    object_name = "NFTest_nested_repeat_persistence_object"
    group = compile_group(source, group_name)

    mesh_data = bpy.data.meshes.new(object_name + "_mesh")
    obj = bpy.data.objects.new(object_name, mesh_data)
    bpy.context.collection.objects.link(obj)
    modifier = obj.modifiers.new("NodeForge", "NODES")
    modifier.node_group = group

    check(_evaluate_group_geometry_vertices(group, "NFTest_nested_repeat_pre_save_eval") == [(6.0, 0.0, 0.0)], "nested Repeat pre-save evaluation changed")
    repeat_inputs, repeat_outputs, pairs = _nested_repeat_pairs(group)
    check(len(repeat_inputs) == 2 and len(repeat_outputs) == 2 and len(pairs) == 2, "nested Repeat pairs missing before save")

    filepath = str(tmp_path / "nested_repeat_persistence.blend")
    result = bpy.ops.wm.save_as_mainfile(filepath=filepath)
    check("FINISHED" in result, f"save_as_mainfile failed: {result}")
    result = bpy.ops.wm.open_mainfile(filepath=filepath)
    check("FINISHED" in result, f"open_mainfile failed: {result}")

    reloaded_group = bpy.data.node_groups.get(group_name)
    check(reloaded_group is not None, "nested Repeat group missing after reopen")
    repeat_inputs, repeat_outputs, pairs = _nested_repeat_pairs(reloaded_group)
    check(len(repeat_inputs) == 2, f"expected two Repeat Inputs after reopen, got {len(repeat_inputs)}")
    check(len(repeat_outputs) == 2, f"expected two Repeat Outputs after reopen, got {len(repeat_outputs)}")
    check(len(pairs) == 2, f"expected two independent Repeat pairs after reopen, got {len(pairs)}")
    for _, repeat_output in pairs:
        item_names = [item.name for item in repeat_output.repeat_items]
        input_names = [socket.name for socket in repeat_output.inputs]
        output_names = [socket.name for socket in repeat_output.outputs]
        check("x" in item_names, f"nested Repeat dynamic item missing after reopen: {item_names}")
        check("x" in input_names, f"nested Repeat input socket missing after reopen: {input_names}")
        check("x" in output_names, f"nested Repeat output socket missing after reopen: {output_names}")

    reloaded_obj = bpy.data.objects.get(object_name)
    check(reloaded_obj is not None, "nested Repeat evaluation object missing after reopen")
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = reloaded_obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        vertices = [tuple(round(float(coord), 6) for coord in vertex.co) for vertex in mesh.vertices]
    finally:
        evaluated.to_mesh_clear()
    check(vertices == [(6.0, 0.0, 0.0)], f"nested Repeat result changed after reopen: {vertices}")
