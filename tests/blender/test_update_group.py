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

    compiler.update_expression_group(group, source)

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

    compiler.update_expression_group(group, source_after)

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


def _group_input_identifiers(group):
    """Return input interface identifiers keyed by display name."""
    return {
        item.name: item.identifier
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "INPUT"
    }


def _close(actual, expected):
    """Return whether two numeric socket values match for update assertions."""
    return abs(float(actual) - float(expected)) <= 1e-6


def test_update_group_preserves_all_shared_instances_cross_links_defaults_and_rollback():
    """One in-place update must preserve every user of the shared datablock."""
    source_before = '''
x = input_float("X", default=1.0)
y = input_float("Y", default=2.0)
z = input_float("Z", default=3.0)
output("Result", x + y + z)
'''
    source_after = '''
x = input_float("X", default=10.0)
y = input_float("Y", default=20.0)
w = input_float("W", default=40.0)
z = input_float("Z", default=30.0)
output("Result", x + y + z + w)
'''
    group = compile_group(source_before, "NFTest_update_shared_instances")
    pointer = group.as_pointer()
    identifiers_before = _group_input_identifiers(group)

    wrapper_a = bpy.data.node_groups.new("NFTest_update_shared_wrapper_a", "GeometryNodeTree")
    wrapper_a.interface.new_socket(name="Driver", in_out="INPUT", socket_type="NodeSocketFloat")
    wrapper_a.interface.new_socket(name="Result", in_out="OUTPUT", socket_type="NodeSocketFloat")
    driver = wrapper_a.nodes.new("NodeGroupInput")
    output = wrapper_a.nodes.new("NodeGroupOutput")
    leaf_1 = wrapper_a.nodes.new("GeometryNodeGroup")
    leaf_1.name = "Leaf 1"
    leaf_1.node_tree = group
    leaf_2 = wrapper_a.nodes.new("GeometryNodeGroup")
    leaf_2.name = "Leaf 2"
    leaf_2.node_tree = group

    wrapper_b = bpy.data.node_groups.new("NFTest_update_shared_wrapper_b", "GeometryNodeTree")
    leaf_3 = wrapper_b.nodes.new("GeometryNodeGroup")
    leaf_3.name = "Leaf 3"
    leaf_3.node_tree = group
    math = wrapper_b.nodes.new("ShaderNodeMath")

    for node in (leaf_1, leaf_2, leaf_3):
        compiler._apply_group_defaults_to_node(node)
    for socket_name, value in zip(("X", "Y", "Z"), (1.1, 1.2, 1.3)):
        leaf_1.inputs[socket_name].default_value = value
    for socket_name, value in zip(("X", "Y", "Z"), (2.1, 2.2, 2.3)):
        leaf_2.inputs[socket_name].default_value = value
    leaf_3.inputs["X"].default_value = 3.1
    leaf_3.inputs["Z"].default_value = 3.3

    wrapper_a.links.new(driver.outputs["Driver"], leaf_1.inputs["Y"])
    wrapper_a.links.new(leaf_2.outputs["Result"], leaf_1.inputs["X"])
    wrapper_a.links.new(leaf_1.outputs["Result"], output.inputs["Result"])
    wrapper_b.links.new(leaf_3.outputs["Result"], math.inputs[0])

    compiler.update_expression_group(group, source_after)

    check(group.as_pointer() == pointer, "shared update replaced the root group datablock")
    identifiers_after = _group_input_identifiers(group)
    check(identifiers_before["X"] != identifiers_after["X"], "shared update did not exercise interface recreation")
    expected = {
        leaf_1: {"X": 1.1, "Y": 1.2, "Z": 1.3, "W": 40.0},
        leaf_2: {"X": 2.1, "Y": 2.2, "Z": 2.3, "W": 40.0},
        leaf_3: {"X": 3.1, "Y": 20.0, "Z": 3.3, "W": 40.0},
    }
    for node, values in expected.items():
        for socket_name, value in values.items():
            check(_close(node.inputs[socket_name].default_value, value), f"{node.name} lost {socket_name}: {node.inputs[socket_name].default_value}")

    links_a = [(link.from_node, link.from_socket.name, link.to_node, link.to_socket.name) for link in wrapper_a.links]
    links_b = [(link.from_node, link.from_socket.name, link.to_node, link.to_socket.name) for link in wrapper_b.links]
    check(links_a.count((driver, "Driver", leaf_1, "Y")) == 1, "stable-to-affected link was not restored exactly once")
    check(links_a.count((leaf_2, "Result", leaf_1, "X")) == 1, "affected-to-affected link was not restored exactly once")
    check(links_a.count((leaf_1, "Result", output, "Result")) == 1, "affected-to-stable link was not restored exactly once")
    check(links_b.count((leaf_3, "Result", math, "Value")) == 1, "cross-tree affected link was not restored exactly once")

    compiler._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        compiler.update_expression_group(group, source_before)
    except RuntimeError as exc:
        check("Injected NodeForge cutover failure" in str(exc), f"unexpected shared rollback failure: {exc}")
    else:
        raise AssertionError("injected shared cutover failure did not raise")

    for node, values in expected.items():
        for socket_name, value in values.items():
            check(_close(node.inputs[socket_name].default_value, value), f"rollback lost {node.name}.{socket_name}")
    links_a = [(link.from_node, link.from_socket.name, link.to_node, link.to_socket.name) for link in wrapper_a.links]
    links_b = [(link.from_node, link.from_socket.name, link.to_node, link.to_socket.name) for link in wrapper_b.links]
    check(links_a.count((driver, "Driver", leaf_1, "Y")) == 1, "rollback lost stable-to-affected link")
    check(links_a.count((leaf_2, "Result", leaf_1, "X")) == 1, "rollback lost affected-to-affected link")
    check(links_a.count((leaf_1, "Result", output, "Result")) == 1, "rollback lost affected-to-stable link")
    check(links_b.count((leaf_3, "Result", math, "Value")) == 1, "rollback lost cross-tree affected link")
    leaked = [
        item.name
        for item in bpy.data.node_groups
        if item.name.startswith("NodeForge.replacement.")
        or item.name.startswith("NodeForge.rollback.")
        or item.name.startswith("NodeForge.preflight.")
    ]
    check(not leaked, f"shared rollback leaked temporary groups: {leaked}")

    strict_state = compiler._capture_group_external_state(group)
    affected_endpoint = next(
        endpoint
        for link in strict_state["links"]
        for endpoint in (link["from"], link["to"])
        if endpoint.get("kind") == "group_node_socket"
    )
    affected_endpoint["socket_key"] = ("INPUT", "Missing", "NodeSocketFloat", 0)
    try:
        compiler._restore_group_external_state(group, strict_state, strict=True)
    except Exception:
        pass
    else:
        raise AssertionError("strict rollback accepted a missing captured endpoint")


def test_update_group_drops_removed_and_incompatible_socket_state_without_positional_reuse():
    """Success restore skips removed/type-changed sockets without shifting state."""
    before = '''
keep = input_float("Keep", default=1.0)
removed = input_float("Removed", default=2.0)
changed = input_float("Changed", default=3.0)
output("Changed", changed)
'''
    after = '''
new = input_float("New", default=9.0)
keep = input_float("Keep", default=10.0)
changed = input_int("Changed", default=30)
output("Changed", changed)
'''
    group = compile_group(before, "NFTest_update_removed_incompatible")
    wrapper = bpy.data.node_groups.new("NFTest_update_removed_incompatible_wrapper", "GeometryNodeTree")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    compiler._apply_group_defaults_to_node(group_node)
    group_node.inputs["Keep"].default_value = 1.5
    group_node.inputs["Removed"].default_value = 2.5
    group_node.inputs["Changed"].default_value = 3.5
    math = wrapper.nodes.new("ShaderNodeMath")
    wrapper.links.new(group_node.outputs["Changed"], math.inputs[0])

    compiler.update_expression_group(group, after)

    check(_close(group_node.inputs["Keep"].default_value, 1.5), "unchanged input override was lost")
    check(_close(group_node.inputs["New"].default_value, 9.0), "new input did not receive its script default")
    check(int(group_node.inputs["Changed"].default_value) == 30, "incompatible Float state was applied to Int input")
    check(group_node.inputs.get("Removed") is None, "removed input survived cutover")
    check(not any(link.from_node == group_node and link.to_node == math for link in wrapper.links), "incompatible output link was restored")


def test_update_group_preserves_supported_vector_object_and_material_overrides():
    """Group-wide restoration must retain sequence values and Blender ID pointers."""
    before = '''
vector_value = input_vector("Vector", default=(1, 2, 3))
object_value = input_object("Object")
material_value = input_material("Material")
output("Vector", vector_value)
'''
    after = '''
vector_value = input_vector("Vector", default=(10, 20, 30))
object_value = input_object("Object")
material_value = input_material("Material")
extra = input_float("Extra", default=4.0)
output("Vector", vector_value)
'''
    group = compile_group(before, "NFTest_update_pointer_values")
    wrapper = bpy.data.node_groups.new("NFTest_update_pointer_values_wrapper", "GeometryNodeTree")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    compiler._apply_group_defaults_to_node(group_node)
    mesh = bpy.data.meshes.new("NFTest_update_pointer_mesh")
    obj = bpy.data.objects.new("NFTest_update_pointer_object", mesh)
    material = bpy.data.materials.new("NFTest_update_pointer_material")
    group_node.inputs["Vector"].default_value = (4.0, 5.0, 6.0)
    group_node.inputs["Object"].default_value = obj
    group_node.inputs["Material"].default_value = material

    compiler.update_expression_group(group, after)

    vector = tuple(float(value) for value in group_node.inputs["Vector"].default_value)
    check(all(_close(actual, expected) for actual, expected in zip(vector, (4.0, 5.0, 6.0))), f"Vector override changed: {vector}")
    check(group_node.inputs["Object"].default_value is obj, "Object override was not restored")
    check(group_node.inputs["Material"].default_value is material, "Material override was not restored")
    check(_close(group_node.inputs["Extra"].default_value, 4.0), "new Extra default was not applied")


def test_deferred_group_transaction_rollback_restores_first_external_snapshot():
    """Outer rollback must use the first backup and first external snapshot."""
    first = 'x = input_float("X", default=1.0)\noutput("X", x)'
    second = 'x = input_float("X", default=2.0)\noutput("X", x)'
    third = 'x = input_float("X", default=3.0)\noutput("X", x)'
    group = compile_group(first, "NFTest_deferred_external_snapshot")
    wrapper = bpy.data.node_groups.new("NFTest_deferred_external_snapshot_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="X", in_out="OUTPUT", socket_type="NodeSocketFloat")
    output = wrapper.nodes.new("NodeGroupOutput")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    compiler._apply_group_defaults_to_node(group_node)
    group_node.inputs["X"].default_value = 1.5
    wrapper.links.new(group_node.outputs["X"], output.inputs["X"])

    transaction = compiler.LocalHelperBuildTransaction()
    compiler._make_group(second, group.name, existing_group=group, local_helper_transaction=transaction)
    check(_close(group_node.inputs["X"].default_value, 1.5), "first deferred update lost the original override")
    group_node.inputs["X"].default_value = 2.5
    compiler._make_group(third, group.name, existing_group=group, local_helper_transaction=transaction)
    check(_close(group_node.inputs["X"].default_value, 2.5), "second deferred update lost the intermediate override")

    transaction.rollback()

    check(_close(group_node.inputs["X"].default_value, 1.5), "outer rollback did not restore the first external snapshot")
    check(any(link.from_node == group_node and link.to_node == output for link in wrapper.links), "outer rollback lost the original external link")
    defaults = [item.default_value for item in group.interface.items_tree if getattr(item, "name", None) == "X" and getattr(item, "in_out", None) == "INPUT"]
    check(defaults and _close(defaults[0], 1.0), f"outer rollback did not restore first interface default: {defaults}")



def test_function_group_savepoint_restores_immediate_state_after_second_update():
    """Savepoint rollback restores the mutation immediately before the checkpoint."""
    first = 'x = input_float("X", default=1.0)\noutput("X", x)'
    second = 'x = input_float("X", default=2.0)\noutput("X", x)'
    third = 'x = input_float("X", default=3.0)\noutput("X", x)'
    group = compile_group(first, "NFTest_function_group_savepoint")
    pointer = group.as_pointer()
    transaction = compiler.FunctionGroupBuildTransaction()

    compiler._make_group(second, group.name, existing_group=group, function_group_transaction=transaction)
    check(len(transaction._updated_by_identity) == 1, "first update did not create one physical identity record")
    check(len(transaction._mutation_journal) == 1, "first update did not create one mutation journal entry")
    savepoint = transaction.savepoint()

    original_name = group.name
    group.name = original_name + "_renamed"
    compiler._make_group(third, group.name, existing_group=group, function_group_transaction=transaction)
    check(group.as_pointer() == pointer, "second update changed physical group identity")
    check(len(transaction._updated_by_identity) == 1, "rename split one physical group into multiple transaction identities")
    check(len(transaction._mutation_journal) == 2, "second update was not journaled separately")

    transaction.rollback_to_savepoint(savepoint)
    check(group.as_pointer() == pointer, "savepoint rollback replaced physical group")
    defaults = [
        item.default_value for item in group.interface.items_tree
        if getattr(item, "name", None) == "X" and getattr(item, "in_out", None) == "INPUT"
    ]
    check(defaults and _close(defaults[0], 2.0), f"savepoint rollback did not restore immediate pre-savepoint state: {defaults}")
    check(len(transaction._updated_by_identity) == 1, "savepoint rollback dropped the pre-savepoint identity record")
    check(len(transaction._mutation_journal) == 1, "savepoint rollback kept the post-savepoint mutation")

    transaction.rollback()
    defaults = [
        item.default_value for item in group.interface.items_tree
        if getattr(item, "name", None) == "X" and getattr(item, "in_out", None) == "INPUT"
    ]
    check(defaults and _close(defaults[0], 1.0), f"full rollback did not restore outer-original state: {defaults}")


def test_function_group_savepoint_restores_exact_cache_snapshot():
    """Probe rollback restores overwritten, removed, and newly added cache entries."""
    transaction = compiler.FunctionGroupBuildTransaction()
    cache = {"existing": "before", "removed": "keep"}
    transaction.register_cache(cache)
    savepoint = transaction.savepoint()
    cache["existing"] = "after"
    cache.pop("removed")
    cache["new"] = "probe-only"

    transaction.rollback_to_savepoint(savepoint)

    check(cache == {"existing": "before", "removed": "keep"}, f"savepoint cache snapshot was not restored exactly: {cache}")
    transaction.rollback()


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

    compiler.update_expression_group(group, source)

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


def test_library_reload_preserves_all_instance_values_defaults_and_links():
    """Library reload must preserve every group-node user of the catalog group."""
    local = library.ensure_local_catalog_dir()
    source = local / "local_reload_state.nf"
    group = wrapper = second_wrapper = None
    try:
        source.write_text(
            'radius = input_float("Radius", default=0.1)\nsegments = input_int("Segments", default=8)\noutput("Radius", radius)\n',
            encoding="utf-8",
        )
        group = compiler.create_library_catalog_group("local", "local_reload_state")
        pointer = group.as_pointer()
        wrapper = bpy.data.node_groups.new("NFTest_library_reload_state_wrapper", "GeometryNodeTree")
        wrapper.interface.new_socket(name="External Segments", in_out="INPUT", socket_type="NodeSocketInt")
        wrapper.interface.new_socket(name="Result", in_out="OUTPUT", socket_type="NodeSocketFloat")
        wrapper_input = wrapper.nodes.new("NodeGroupInput")
        wrapper_output = wrapper.nodes.new("NodeGroupOutput")
        group_node = wrapper.nodes.new("GeometryNodeGroup")
        group_node.node_tree = group
        wrapper.links.new(wrapper_input.outputs["External Segments"], group_node.inputs["Segments"])
        wrapper.links.new(group_node.outputs["Radius"], wrapper_output.inputs["Result"])
        group_node.inputs["Radius"].default_value = 0.33

        second_wrapper = bpy.data.node_groups.new("NFTest_library_reload_state_second_wrapper", "GeometryNodeTree")
        second_node = second_wrapper.nodes.new("GeometryNodeGroup")
        second_node.node_tree = group
        compiler._apply_group_defaults_to_node(second_node)
        second_node.inputs["Radius"].default_value = 0.44
        math = second_wrapper.nodes.new("ShaderNodeMath")
        second_wrapper.links.new(second_node.outputs["Radius"], math.inputs[0])

        source.write_text(
            'radius = input_float("Radius", default=0.2)\nsegments = input_int("Segments", default=12)\noutput("Radius", radius)\n',
            encoding="utf-8",
        )
        compiler.update_library_catalog_group(group, "local", "local_reload_state")

        check(group.as_pointer() == pointer, "library reload replaced the selected root group")
        check(abs(float(group_node.inputs["Radius"].default_value) - 0.33) < 1e-6, "library reload lost user input override")
        check(int(group_node.inputs["Segments"].default_value) == 12, "library reload did not apply new default to non-overridden input")
        check(abs(float(second_node.inputs["Radius"].default_value) - 0.44) < 1e-6, "library reload lost another instance override")
        check(int(second_node.inputs["Segments"].default_value) == 12, "library reload did not apply new default to another instance")
        incoming = [link for link in wrapper.links if link.to_node == group_node and link.to_socket.name == "Segments"]
        outgoing = [link for link in wrapper.links if link.from_node == group_node and link.from_socket.name == "Radius"]
        check(len(incoming) == 1, "library reload lost incoming external link")
        check(len(outgoing) == 1, "library reload lost outgoing external link")
        check(any(link.from_node == second_node and link.to_node == math for link in second_wrapper.links), "library reload lost another tree's link")
    finally:
        source.unlink(missing_ok=True)
        if wrapper is not None and bpy.data.node_groups.get(wrapper.name) is wrapper:
            bpy.data.node_groups.remove(wrapper, do_unlink=True)
        if second_wrapper is not None and bpy.data.node_groups.get(second_wrapper.name) is second_wrapper:
            bpy.data.node_groups.remove(second_wrapper, do_unlink=True)
        if group is not None and bpy.data.node_groups.get(group.name) is group:
            bpy.data.node_groups.remove(group, do_unlink=True)
