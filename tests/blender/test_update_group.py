from helpers import *

from NodeForge import local_sources
from NodeForge.blender import update as blender_update




def test_update_group_rollback_and_preflight_contracts():
    group = compile_group('''
def nf_fixture():
    x = 1
    x = x
    return (x,)
''', 'NFTest_update')
    compiler.update_expression_group(group, '''
def nf_fixture():
    x = 2
    x = x
    return (x,)
''')
    try:
        compiler.update_expression_group(group, '\ndef inc(a):\n    return a + 1\n\nx = inc(1)\ny = missing_func(1)\noutput("y", y)\n')
    except Exception:
        pass
    else:
        raise AssertionError('failed update did not raise')
    leaked = [g.name for g in bpy.data.node_groups if g.name.startswith('NodeForge.preflight.') or (g.name.startswith('NodeForge.local.') and 'preflight' in g.name)]
    check(not leaked, f'preflight/local preflight groups leaked: {leaked}')
    print('UPDATE_SUCCESS_FAILURE_OK')



def test_rollback_backup_copy_failure_is_exception_safe(monkeypatch):
    """Backup construction cleans its private datablock before returning failure."""
    group = compile_group('''
def nf_fixture():
    x = 1
    x = x
    return (x,)
''', 'NFTest_backup_copy_failure')
    before = {g.name for g in bpy.data.node_groups if g.name.startswith("NodeForge.rollback.")}
    blender_group_backend._TEST_BACKUP_COPY_FAIL_AFTER_RESET = True
    try:
        try:
            compiler.update_expression_group(group, '''
def nf_fixture():
    x = 2
    x = x
    return (x,)
''')
        except RuntimeError as exc:
            check("rollback-backup copy failure" in str(exc), f"unexpected backup-copy failure: {exc}")
        else:
            raise AssertionError("injected rollback-backup copy failure did not raise")
    finally:
        blender_group_backend._TEST_BACKUP_COPY_FAIL_AFTER_RESET = False
    after = {g.name for g in bpy.data.node_groups if g.name.startswith("NodeForge.rollback.")}
    check(after == before, f"backup-copy failure leaked rollback groups: {sorted(after - before)}")


def test_rollback_backup_failed_cleanup_remains_authority_ineligible(monkeypatch):
    """A backup leaked only by failed removal remains permanently non-authoritative."""
    group = compile_group('''
def nf_fixture():
    x = 1
    x = x
    return (x,)
''', 'NFTest_backup_cleanup_failure')
    original_remove = blender_group_backend._remove_node_group_if_live
    leaked = []

    def fail_backup_remove(candidate, *, failures=None):
        if candidate is not None and candidate.name.startswith("NodeForge.rollback."):
            leaked.append(candidate)
            if failures is not None:
                failures.append(RuntimeError("Injected rollback backup removal failure"))
            return False
        return original_remove(candidate, failures=failures)

    monkeypatch.setattr(blender_group_backend, "_remove_node_group_if_live", fail_backup_remove)
    blender_group_backend._TEST_BACKUP_COPY_FAIL_AFTER_RESET = True
    try:
        try:
            compiler.update_expression_group(group, '''
def nf_fixture():
    x = 2
    x = x
    return (x,)
''')
        except RuntimeError as exc:
            check("rollback-backup copy failure" in str(exc), f"unexpected backup-copy failure: {exc}")
        else:
            raise AssertionError("injected rollback-backup copy failure did not raise")
    finally:
        blender_group_backend._TEST_BACKUP_COPY_FAIL_AFTER_RESET = False

    check(leaked, "failed rollback-backup cleanup did not leave the injected backup")
    backup = leaked[-1]
    check(blender_group_backend.is_authority_ineligible_group(backup), "leaked rollback backup became authoritative")
    monkeypatch.setattr(blender_group_backend, "_remove_node_group_if_live", original_remove)
    original_remove(backup)

def test_update_group_preserves_repeat_zone_dynamic_state_sockets_with_external_links():
    source = '''
def nf_fixture(count: Int['Count'] = 4):
    instance_points = points(1)
    for i in repeat_range(count):
        instance_points = join(instance_points, points(1))
    instance_points = instance_points
    return (instance_points,)
'''
    group = compile_group(source, 'NFTest_update_repeat_dynamic_sockets')
    wrapper = bpy.data.node_groups.new('NFTest_update_repeat_dynamic_wrapper', 'GeometryNodeTree')
    wrapper.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    group_output = wrapper.nodes.new('NodeGroupOutput')
    group_node = wrapper.nodes.new('GeometryNodeGroup')
    group_node.node_tree = group
    wrapper.links.new(group_node.outputs[0], group_output.inputs['Geometry'])

    compiler.update_expression_group(group, source)

    output_names = [socket.name for socket in group_node.outputs]
    check('Instance Points' in output_names, f'updated group node outputs missing instance_points: {output_names}')
    link_names = [(link.from_socket.name, link.to_socket.name) for link in wrapper.links]
    check(('Instance Points', 'Geometry') in link_names, f'instance_points external link was not preserved: {link_names}')

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
def nf_fixture(radius: Float['Radius'] = 0.1, segments: Int['Segments'] = 8):
    panel([radius, segments], name="Stem")
    radius = radius
    return (radius,)
'''
    source_after = '''
def nf_fixture(radius: Float['Radius'] = 0.2, segments: Int['Segments'] = 12):
    panel([segments, radius], name="Stem", collapsed=True)
    radius = radius
    return (radius,)
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
    wrapper.links.new(group_node.outputs[0], wrapper_output.inputs["Result"])
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
    blender_group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        compiler.update_expression_group(
            group,
            '''
def nf_fixture(radius: Float['Radius'] = 0.4, segments: Int['Segments'] = 16):
    panel([radius, segments], name="Changed")
    radius = radius
    return (radius,)
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
def nf_fixture(x: Float['X'] = 1.0, y: Float['Y'] = 2.0, z: Float['Z'] = 3.0):
    result = x + y + z
    return (result,)
'''
    source_after = '''
def nf_fixture(x: Float['X'] = 10.0, y: Float['Y'] = 20.0, z: Float['Z'] = 30.0):
    result = x + y + z + 40.0
    return (result,)
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
        blender_update._apply_group_defaults_to_node(node)
    for socket_name, value in zip(("X", "Y", "Z"), (1.1, 1.2, 1.3)):
        leaf_1.inputs[socket_name].default_value = value
    for socket_name, value in zip(("X", "Y", "Z"), (2.1, 2.2, 2.3)):
        leaf_2.inputs[socket_name].default_value = value
    leaf_3.inputs["X"].default_value = 3.1
    leaf_3.inputs["Z"].default_value = 3.3

    wrapper_a.links.new(driver.outputs["Driver"], leaf_1.inputs["Y"])
    wrapper_a.links.new(leaf_2.outputs[0], leaf_1.inputs["X"])
    wrapper_a.links.new(leaf_1.outputs[0], output.inputs["Result"])
    wrapper_b.links.new(leaf_3.outputs[0], math.inputs[0])

    compiler.update_expression_group(group, source_after)

    check(group.as_pointer() == pointer, "shared update replaced the root group datablock")
    identifiers_after = _group_input_identifiers(group)
    check(identifiers_before["X"] != identifiers_after["X"], "shared update did not exercise interface recreation")
    expected = {
        leaf_1: {"X": 1.1, "Y": 1.2, "Z": 1.3},
        leaf_2: {"X": 2.1, "Y": 2.2, "Z": 2.3},
        leaf_3: {"X": 3.1, "Y": 20.0, "Z": 3.3},
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

    blender_group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = True
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

    strict_state = blender_update._capture_group_external_state(group)
    affected_endpoint = next(
        endpoint
        for link in strict_state["links"]
        for endpoint in (link["from"], link["to"])
        if endpoint.get("kind") == "group_node_socket"
    )
    if affected_endpoint.get("input_reference") is not None:
        affected_endpoint["input_reference"]["old_socket_key"] = ("INPUT", "Missing", "NodeSocketFloat", 0)
    else:
        affected_endpoint["socket_key"] = ("INPUT", "Missing", "NodeSocketFloat", 0)
    try:
        compiler._restore_group_external_state(group, strict_state, strict=True)
    except Exception:
        pass
    else:
        raise AssertionError("strict rollback accepted a missing captured endpoint")


def test_update_group_drops_removed_and_incompatible_socket_state_without_positional_reuse():
    """Selected MODULE_DEF Reload rejects incompatible specialization before changing users."""
    before = '''def nf_fixture(keep: Float['Keep'] = 1.0, removed: Float['Removed'] = 2.0, changed: Float['Changed'] = 3.0):
    return changed
'''
    after = '''def nf_fixture(new: Float['New'] = 9.0, keep: Float['Keep'] = 10.0, changed: Int['Changed'] = 30):
    return changed
'''
    group = compile_group(before, 'NFTest_update_removed_incompatible')
    pointer = group.as_pointer()
    wrapper = bpy.data.node_groups.new('NFTest_incompatible_wrapper', 'GeometryNodeTree')
    group_node = wrapper.nodes.new('GeometryNodeGroup')
    group_node.node_tree = group
    group_node.inputs['Keep'].default_value = 1.5
    group_node.inputs['Changed'].default_value = 3.5
    math = wrapper.nodes.new('ShaderNodeMath')
    wrapper.links.new(group_node.outputs[0], math.inputs[0])
    previous_links = tuple((link.from_socket.name, link.to_socket.name) for link in wrapper.links)
    with pytest.raises(CompileError, match='specialization'):
        compiler.update_expression_group(group, after)
    check(group.as_pointer() == pointer, 'incompatible update replaced original datablock')
    check([socket.name for socket in group_node.inputs] == ['Keep', 'Removed', 'Changed'],
          'incompatible update changed published source signature')
    check(_close(group_node.inputs['Keep'].default_value, 1.5), 'incompatible update changed user override')
    check(_close(group_node.inputs['Changed'].default_value, 3.5), 'incompatible update changed user override')
    check(tuple((link.from_socket.name, link.to_socket.name) for link in wrapper.links) == previous_links,
          'incompatible update changed existing endpoint links')


def test_update_group_preserves_supported_vector_object_and_material_overrides():
    """Compatible typed Vector/Object/Material inputs retain per-instance user values."""
    before = '''def nf_fixture(object_value: Object['Object'], material_value: Material['Material'], vector_value: Vector['Vector'] = (1, 2, 3)):
    return vector_value
'''
    after = '''def nf_fixture(object_value: Object['Object'], material_value: Material['Material'], vector_value: Vector['Vector'] = (10, 20, 30)):
    return vector_value
'''
    group = compile_group(before, 'NFTest_update_pointer_values')
    wrapper = bpy.data.node_groups.new('NFTest_update_pointer_values_wrapper', 'GeometryNodeTree')
    group_node = wrapper.nodes.new('GeometryNodeGroup')
    group_node.node_tree = group
    blender_update._apply_group_defaults_to_node(group_node)
    mesh = bpy.data.meshes.new('NFTest_update_pointer_mesh')
    obj = bpy.data.objects.new('NFTest_update_pointer_object', mesh)
    material = bpy.data.materials.new('NFTest_update_pointer_material')
    group_node.inputs['Vector'].default_value = (4.0, 5.0, 6.0)
    group_node.inputs['Object'].default_value = obj
    group_node.inputs['Material'].default_value = material
    compiler.update_expression_group(group, after)
    vector = tuple(float(value) for value in group_node.inputs['Vector'].default_value)
    check(all(_close(actual, expected) for actual, expected in zip(vector, (4.0, 5.0, 6.0))),
          f'Vector override changed: {vector}')
    check(group_node.inputs['Object'].default_value is obj, 'Object override was not restored')
    check(group_node.inputs['Material'].default_value is material, 'Material override was not restored')


def test_deferred_group_transaction_rollback_restores_first_external_snapshot():
    """Outer rollback must use the first backup and first external snapshot."""
    first = '''
def nf_fixture(x: Float['X'] = 1.0):
    x = x
    return (x,)
'''
    second = '''
def nf_fixture(x: Float['X'] = 2.0):
    x = x
    return (x,)
'''
    third = '''
def nf_fixture(x: Float['X'] = 3.0):
    x = x
    return (x,)
'''
    group = compile_group(first, "NFTest_deferred_external_snapshot")
    wrapper = bpy.data.node_groups.new("NFTest_deferred_external_snapshot_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="X", in_out="OUTPUT", socket_type="NodeSocketFloat")
    output = wrapper.nodes.new("NodeGroupOutput")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    blender_update._apply_group_defaults_to_node(group_node)
    group_node.inputs["X"].default_value = 1.5
    wrapper.links.new(group_node.outputs[0], output.inputs["X"])

    transaction = blender_group_backend.BlenderGroupBuildTransaction()
    prepared_create_or_update(second, group.name, existing_group=group, build_options={"local_helper_transaction": transaction})
    check(_close(group_node.inputs["X"].default_value, 1.5), "first deferred update lost the original override")
    group_node.inputs["X"].default_value = 2.5
    prepared_create_or_update(third, group.name, existing_group=group, build_options={"local_helper_transaction": transaction})
    check(_close(group_node.inputs["X"].default_value, 2.5), f"second deferred update lost the intermediate override: {group_node.inputs['X'].default_value}")

    transaction.rollback()

    check(_close(group_node.inputs["X"].default_value, 1.5), "outer rollback did not restore the first external snapshot")
    check(any(link.from_node == group_node and link.to_node == output for link in wrapper.links), "outer rollback lost the original external link")
    defaults = [item.default_value for item in group.interface.items_tree if getattr(item, "name", None) == "X" and getattr(item, "in_out", None) == "INPUT"]
    check(defaults and _close(defaults[0], 1.0), f"outer rollback did not restore first interface default: {defaults}")



def test_function_group_savepoint_restores_immediate_state_after_second_update():
    """Savepoint rollback restores the mutation immediately before the checkpoint."""
    first = '''
def nf_fixture(x: Float['X'] = 1.0):
    x = x
    return (x,)
'''
    second = '''
def nf_fixture(x: Float['X'] = 2.0):
    x = x
    return (x,)
'''
    third = '''
def nf_fixture(x: Float['X'] = 3.0):
    x = x
    return (x,)
'''
    group = compile_group(first, "NFTest_function_group_savepoint")
    pointer = group.as_pointer()
    transaction = blender_group_backend.BlenderGroupBuildTransaction()

    prepared_create_or_update(second, group.name, existing_group=group, build_options={"function_group_transaction": transaction})
    check(len(transaction._updated_by_identity) == 1, "first update did not create one physical identity record")
    check(len(transaction._mutation_journal) == 1, "first update did not create one mutation journal entry")
    savepoint = transaction.savepoint()

    original_name = group.name
    group.name = original_name + "_renamed"
    prepared_create_or_update(third, group.name, existing_group=group, build_options={"function_group_transaction": transaction})
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
    transaction = blender_group_backend.BlenderGroupBuildTransaction()
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
    source = '''
def nf_fixture():
    x = 0
    for i in repeat_range(2):
        for j in repeat_range(3):
            x = x + 1
    geometry = point(vector(x, 0, 0))
    return (geometry,)
'''
    group = compile_group(source, "NFTest_update_nested_repeat")

    wrapper = bpy.data.node_groups.new("NFTest_update_nested_repeat_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_output = wrapper.nodes.new("NodeGroupOutput")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    wrapper.links.new(group_node.outputs[0], group_output.inputs["Geometry"])

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
    source = '''
def nf_fixture():
    x = 0
    for i in repeat_range(2):
        for j in repeat_range(3):
            x = x + 1
    geometry = point(vector(x, 0, 0))
    return (geometry,)
'''
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
    local = local_sources.ensure_local_catalog_dir()
    source = local / "local_reload_state.nf"
    group = wrapper = second_wrapper = None
    try:
        source.write_text(
            '''
def nf_fixture(radius: Float['Radius'] = 0.1, segments: Int['Segments'] = 8):
    radius = radius
    return (radius,)
''',
            encoding="utf-8",
        )
        group, _runtime = compiler.create_selected_library_group_auto("local", str(source))
        pointer = group.as_pointer()
        wrapper = bpy.data.node_groups.new("NFTest_library_reload_state_wrapper", "GeometryNodeTree")
        wrapper.interface.new_socket(name="External Segments", in_out="INPUT", socket_type="NodeSocketInt")
        wrapper.interface.new_socket(name="Result", in_out="OUTPUT", socket_type="NodeSocketFloat")
        wrapper_input = wrapper.nodes.new("NodeGroupInput")
        wrapper_output = wrapper.nodes.new("NodeGroupOutput")
        group_node = wrapper.nodes.new("GeometryNodeGroup")
        group_node.node_tree = group
        wrapper.links.new(wrapper_input.outputs["External Segments"], group_node.inputs["Segments"])
        wrapper.links.new(group_node.outputs[0], wrapper_output.inputs["Result"])
        group_node.inputs["Radius"].default_value = 0.33

        second_wrapper = bpy.data.node_groups.new("NFTest_library_reload_state_second_wrapper", "GeometryNodeTree")
        second_node = second_wrapper.nodes.new("GeometryNodeGroup")
        second_node.node_tree = group
        blender_update._apply_group_defaults_to_node(second_node)
        second_node.inputs["Radius"].default_value = 0.44
        math = second_wrapper.nodes.new("ShaderNodeMath")
        second_wrapper.links.new(second_node.outputs[0], math.inputs[0])

        source.write_text(
            '''
def nf_fixture(radius: Float['Radius'] = 0.2, segments: Int['Segments'] = 12):
    radius = radius
    return (radius,)
''',
            encoding="utf-8",
        )
        compiler.update_library_catalog_group(group)

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


def test_duplicate_input_update_preserves_live_state_by_declaration_identity():
    """Insertion/reorder/display rename cannot retarget state between duplicate input labels."""
    before = '''
def nf_fixture(first: Float['Scale'] = 1.0, second: Float['Scale'] = 7.0):
    second = second
    return (second,)
'''
    after = '''
def nf_fixture(inserted: Float['Scale'] = 0.5, second: Float['Renamed'] = 8.0, first: Float['Scale'] = 2.0):
    second = second
    return (second,)
'''
    group = compile_group(before, "NFTest_input_declaration_identity_update")
    wrapper = bpy.data.node_groups.new("NFTest_input_declaration_identity_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="Driver", in_out="INPUT", socket_type="NodeSocketFloat")
    wrapper_input = wrapper.nodes.new("NodeGroupInput")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    blender_update._apply_group_defaults_to_node(group_node)
    old_scale = [socket for socket in group_node.inputs if socket.name == "Scale"]
    check(len(old_scale) == 2, "fixture did not create duplicate Scale sockets")
    old_scale[1].default_value = 9.0
    wrapper.links.new(wrapper_input.outputs["Driver"], old_scale[1])

    prepared_create_or_update(after, group.name, existing_group=group)

    scale = [socket for socket in group_node.inputs if socket.name == "Scale"]
    renamed = [socket for socket in group_node.inputs if socket.name == "Renamed"]
    check(len(scale) == 2 and len(renamed) == 1, "updated duplicate/display-rename interface is incorrect")
    check(_close(scale[0].default_value, 0.5), f"inserted input default changed: {scale[0].default_value}")
    check(_close(scale[1].default_value, 2.0), f"first input default changed: {scale[1].default_value}")
    check(_close(renamed[0].default_value, 9.0), "second declaration override was not restored by declaration identity")
    incoming = [link for link in wrapper.links if link.to_node == group_node]
    check(len(incoming) == 1 and incoming[0].to_socket == renamed[0], "duplicate input link was retargeted by occurrence")

    bpy.data.node_groups.remove(wrapper, do_unlink=True)
    bpy.data.node_groups.remove(group, do_unlink=True)


def test_legacy_duplicate_input_live_state_aborts_before_cutover():
    """A copied authoritative MODULE_DEF owner blocks update until explicit detachment."""
    from NodeForge.blender.group_authority import detach_selected_source_owner
    before = '''def nf_fixture(x: Float['Scale'] = 0.0):
    return x
'''
    after = '''def nf_fixture(x: Float['Scale'] = 1.0):
    return x + 1.0
'''
    group = compile_group(before, 'NFTest_duplicate_source_owner')
    copied = group.copy()
    pointer = group.as_pointer()
    initial_nodes = len(group.nodes)
    with pytest.raises(CompileError, match='Duplicate authoritative source owners'):
        compiler.update_expression_group(group, after)
    check(group.as_pointer() == pointer and len(group.nodes) == initial_nodes,
          'duplicate owner allowed selected mutation')
    check(len(copied.nodes) == initial_nodes, 'duplicate owner changed independent copy')
    detach_selected_source_owner(copied)
    compiler.update_expression_group(group, after)
    check(group.as_pointer() == pointer, 'post-detach update replaced canonical owner')
    check(len(copied.nodes) == initial_nodes, 'post-detach update changed detached copy')


def test_legacy_unique_input_live_state_migrates_unambiguously():
    """Legacy groups without declaration metadata still migrate one provably unique input."""
    from NodeForge.blender import interface as interface_module

    group = compile_group('''
def nf_fixture(x: Float['Scale'] = 0.0):
    value = x
    return (value,)
''', "NFTest_legacy_unique_input")
    wrapper = bpy.data.node_groups.new("NFTest_legacy_unique_input_wrapper", "GeometryNodeTree")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    blender_update._apply_group_defaults_to_node(group_node)
    group_node.inputs["Scale"].default_value = 3.0
    if interface_module.INPUT_DECLARATIONS_PROP in group:
        del group[interface_module.INPUT_DECLARATIONS_PROP]

    compiler.update_expression_group(group, '''
def nf_fixture(x: Float['Scale'] = 1.0):
    value = x
    return (value,)
''')
    check(_close(group_node.inputs["Scale"].default_value, 3.0), "unambiguous legacy override was not migrated")

    bpy.data.node_groups.remove(wrapper, do_unlink=True)
    bpy.data.node_groups.remove(group, do_unlink=True)


def test_input_declaration_metadata_failure_never_publishes_or_cuts_over(monkeypatch):
    """Mandatory declaration metadata failure stays inside the existing group transaction."""
    from NodeForge.blender import interface

    source = '''
def nf_fixture(x: Float['Scale'] = 1.0):
    x = x
    return (x,)
'''
    group = compile_group(source, "NFTest_input_metadata_failure_existing")
    pointer = group.as_pointer()
    interface_before = [
        (item.name, item.in_out, getattr(item, "socket_type", ""))
        for item in group.interface.items_tree
        if getattr(item, "item_type", "") == "SOCKET"
    ]
    temporary_before = {
        item.as_pointer()
        for item in bpy.data.node_groups
        if item.name.startswith("NodeForge.replacement.")
    }

    def fail_write(*_args, **_kwargs):
        raise CompileError("Failed to persist NodeForge input declaration metadata")

    monkeypatch.setattr(interface, "_write_group_input_declarations", fail_write)

    try:
        compiler.create_expression_group(source, "NFTest_input_metadata_failure_new")
    except CompileError as exc:
        check("Failed to persist NodeForge input declaration metadata" in str(exc), f"unexpected metadata failure: {exc}")
    else:
        raise AssertionError("initial compilation published a group without mandatory declaration metadata")
    check(bpy.data.node_groups.get("NFTest_input_metadata_failure_new") is None, "failed initial compilation published a group")

    try:
        compiler.update_expression_group(group, '''
def nf_fixture(x: Float['Renamed'] = 2.0):
    x = x
    return (x,)
''')
    except CompileError as exc:
        check("Failed to persist NodeForge input declaration metadata" in str(exc), f"unexpected update metadata failure: {exc}")
    else:
        raise AssertionError("update cut over after mandatory declaration metadata failure")

    check(group.as_pointer() == pointer, "metadata failure replaced the authoritative datablock")
    interface_after = [
        (item.name, item.in_out, getattr(item, "socket_type", ""))
        for item in group.interface.items_tree
        if getattr(item, "item_type", "") == "SOCKET"
    ]
    check(interface_after == interface_before, "metadata failure changed the authoritative interface")
    temporary_after = {
        item.as_pointer()
        for item in bpy.data.node_groups
        if item.name.startswith("NodeForge.replacement.")
    }
    check(temporary_after == temporary_before, "metadata failure leaked a replacement group")
    bpy.data.node_groups.remove(group)


def test_invalid_nested_input_update_fails_before_cutover():
    """Legacy input declarations inside typed functions fail without changing the group."""
    source = '''def nf_fixture(x: Float['Scale'] = 1.0):
    return x
'''
    invalid = '''def nf_fixture(x: Float['Scale'] = 1.0):
    y = input_float('Other', default=7.0) + 1.0
    return x + y
'''
    group = compile_group(source, 'NFTest_nested_input_update_rejected')
    pointer = group.as_pointer()
    interface_before = tuple((item.name, item.in_out, getattr(item, 'socket_type', ''))
                             for item in group.interface.items_tree
                             if getattr(item, 'item_type', '') == 'SOCKET')
    with pytest.raises(CompileError, match='obsolete group interface declaration input_float'):
        compiler.update_expression_group(group, invalid)
    check(group.as_pointer() == pointer, 'invalid nested input replaced authoritative group')
    interface_after = tuple((item.name, item.in_out, getattr(item, 'socket_type', ''))
                            for item in group.interface.items_tree
                            if getattr(item, 'item_type', '') == 'SOCKET')
    check(interface_after == interface_before, 'invalid nested input changed published interface')


def test_update_group_preserves_zero_override_with_mixed_implicit_and_explicit_input_defaults():
    """Explicit typed defaults retain zero-valued user overrides across in-place updates."""
    source = '''def nf_fixture(iterations: Int['Iterations'] = 1, x: Float['Scale'] = 1.0):
    for i in repeat_range(iterations):
        x = x + 1.0
    return x
'''
    updated = '''def nf_fixture(iterations: Int['Iterations'] = 3, x: Float['Scale'] = 1.0):
    for i in repeat_range(iterations):
        x = x + 2.0
    return x
'''
    group = compile_group(source, 'NFTest_update_mixed_input_defaults')
    wrapper = bpy.data.node_groups.new('NFTest_update_mixed_input_defaults_wrapper', 'GeometryNodeTree')
    group_node = wrapper.nodes.new('GeometryNodeGroup')
    group_node.node_tree = group
    blender_update._apply_group_defaults_to_node(group_node)
    check(int(group_node.inputs['Iterations'].default_value) == 1, 'initial Int default was not applied')
    group_node.inputs['Iterations'].default_value = 0
    compiler.update_expression_group(group, updated)
    check(int(group_node.inputs['Iterations'].default_value) == 0, 'zero-valued override was lost')
    check(abs(float(group_node.inputs['Scale'].default_value) - 1.0) < 1e-6,
          'unmodified Float default changed unexpectedly')


def test_update_group_preserves_branch_input_declaration_identity_overrides_and_link():
    """Typed parameters keep stable origins across branch and default changes."""
    from NodeForge.blender import interface
    before = '''def nf_fixture(flag: Bool['Flag'] = True, a: Float['A'] = 1.0, b: Float['B'] = 2.0):
    if flag:
        x = a
    else:
        x = b
    return x
'''
    after = '''def nf_fixture(flag: Bool['Flag'] = True, a: Float['A'] = 10.0, b: Float['B'] = 20.0):
    if flag:
        x = a + 0.0
    else:
        x = b
    return x
'''
    group = compile_group(before, 'NFTest_update_branch_input_identity')
    before_records = interface._get_group_input_declarations(group)
    before_keys = {record['declaration_id'].target_name: key for key, record in before_records.items()}
    check(set(before_keys) == {'flag', 'a', 'b'}, 'expected stable typed parameter origins')
    wrapper = bpy.data.node_groups.new('NFTest_update_branch_input_identity_wrapper', 'GeometryNodeTree')
    wrapper.interface.new_socket(name='External B', in_out='INPUT', socket_type='NodeSocketFloat')
    wrapper_input = wrapper.nodes.new('NodeGroupInput')
    group_node = wrapper.nodes.new('GeometryNodeGroup')
    group_node.node_tree = group
    blender_update._apply_group_defaults_to_node(group_node)
    group_node.inputs['A'].default_value = 11.0
    group_node.inputs['B'].default_value = 22.0
    wrapper.links.new(wrapper_input.outputs['External B'], group_node.inputs['B'])
    compiler.update_expression_group(group, after)
    after_records = interface._get_group_input_declarations(group)
    after_keys = {record['declaration_id'].target_name: key for key, record in after_records.items()}
    check(after_keys == before_keys, 'typed parameter origin IDs changed during update')
    check(abs(float(group_node.inputs['A'].default_value) - 11.0) < 1e-6, 'A override was not restored')
    check(abs(float(group_node.inputs['B'].default_value) - 22.0) < 1e-6, 'B override was not restored')
    incoming = [link for link in wrapper.links if link.to_node == group_node and link.to_socket.name == 'B']
    check(len(incoming) == 1 and incoming[0].from_socket.name == 'External B', 'external B link was not restored')


def test_single_return_label_update_preserves_existing_external_link():
    """A scalar output's display rename must not disconnect pre-existing Blender users."""
    original = '''
def nf_fixture() -> Geometry["Value"]:
    result = cube(1.0)
    return result
'''
    renamed = '''
def nf_fixture() -> Geometry:
    result = cube(1.0)
    return result
'''
    group = compile_group(original, 'NFTest_scalar_label_migration')
    wrapper = bpy.data.node_groups.new('NFTest_scalar_label_migration_wrapper', 'GeometryNodeTree')
    wrapper.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    group_node = wrapper.nodes.new('GeometryNodeGroup')
    group_node.node_tree = group
    output = wrapper.nodes.new('NodeGroupOutput')
    wrapper.links.new(group_node.outputs['Value'], output.inputs['Geometry'])

    compiler.update_expression_group(group, renamed)
    check([sock.name for sock in group_node.outputs] == ['Result'], 'label rename was not published')
    check([(link.from_socket.name, link.to_socket.name) for link in wrapper.links] ==
          [('Result', 'Geometry')], 'scalar output link was lost on label rename')

    compiler.update_expression_group(group, original)
    check([(link.from_socket.name, link.to_socket.name) for link in wrapper.links] ==
          [('Value', 'Geometry')], 'scalar output link was lost on reverse rename')


def test_multi_return_label_change_does_not_guess_external_correspondence():
    """Ambiguous output changes must not restore an unrelated external connection."""
    before = '''
def nf_fixture(x: Float):
    first = x
    second = x + 1
    return first, second
'''
    after = '''
def nf_fixture(x: Float):
    left = x
    right = x + 1
    return left, right
'''
    group = compile_group(before, 'NFTest_multi_output_migration')
    wrapper = bpy.data.node_groups.new('NFTest_multi_output_migration_wrapper', 'GeometryNodeTree')
    math = wrapper.nodes.new('ShaderNodeMath')
    node = wrapper.nodes.new('GeometryNodeGroup')
    node.node_tree = group
    wrapper.links.new(node.outputs['First'], math.inputs[0])

    compiler.update_expression_group(group, after)
    check([sock.name for sock in node.outputs] == ['Left', 'Right'], 'updated multi-output labels missing')
    check(len(wrapper.links) == 0, 'ambiguous multi-output rename reconnected unrelated output')


@pytest.mark.parametrize('changed_angle', [1e-7, 1e-5])
def test_rotation_update_preserves_binary32_user_edits_and_updates_untouched_defaults(changed_angle):
    """Any representable Rotation edit survives while an untouched peer gets the new default."""
    from NodeForge.blender.interface import _get_group_input_defaults, _get_group_input_declarations
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    before = '''def nf_fixture(angle: Rotation['Angle'] = (0.0, 0.0, 0.0)) -> Rotation:
    return angle
'''
    after = '''def nf_fixture(angle: Rotation['Angle'] = (0.25, 0.0, 0.0)) -> Rotation:
    return angle
'''
    group = compile_group(before, 'NFTest_RotationUpdate')
    wrapper = bpy.data.node_groups.new('NFTest_RotationUpdateWrapper', 'GeometryNodeTree')
    edited, untouched = (wrapper.nodes.new('GeometryNodeGroup') for _ in range(2))
    edited.node_tree = untouched.node_tree = group
    blender_update._apply_group_defaults_to_node(edited)
    blender_update._apply_group_defaults_to_node(untouched)
    assert edited.inputs['Angle'].bl_idname == 'NodeSocketRotation'
    assert tuple(untouched.inputs['Angle'].default_value) == (0.0, 0.0, 0.0)
    edited.inputs['Angle'].default_value = (changed_angle, 0.0, 0.0)
    assert tuple(edited.inputs['Angle'].default_value)[0] == normalize_float_constant(changed_angle)

    converter = wrapper.nodes.new('FunctionNodeRotationToEuler')
    wrapper.links.new(edited.outputs[0], converter.inputs['Rotation'])
    declaration_before = _get_group_input_declarations(group)
    assert all(link.is_valid for link in wrapper.links)

    compiler.update_expression_group(group, after)
    assert tuple(edited.inputs['Angle'].default_value) == (normalize_float_constant(changed_angle), 0.0, 0.0)
    assert tuple(untouched.inputs['Angle'].default_value) == (0.25, 0.0, 0.0)
    assert edited.inputs['Angle'].bl_idname == untouched.inputs['Angle'].bl_idname == 'NodeSocketRotation'
    assert len(wrapper.links) == 1 and all(link.is_valid for link in wrapper.links)
    defaults = _get_group_input_defaults(group)
    assert len(defaults) == 1
    assert next(iter(defaults.values()))['nf_type'] == 'ROTATION'
    assert tuple(next(iter(defaults.values()))['default']) == (0.25, 0.0, 0.0)
    assert set(_get_group_input_declarations(group)) == set(declaration_before)


def test_rotation_override_compare_rejects_malformed_metadata_and_keeps_other_type_tolerance():
    """Missing/malformed triples are captured conservatively; Float comparison stays tolerant."""
    from NodeForge.blender.update import _rotation_defaults_equal, _defaults_equal
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    assert _rotation_defaults_equal((0.0, 0.0, 0.0), (0.0, 0.0, 0.0))
    assert not _rotation_defaults_equal((normalize_float_constant(1e-7), 0.0, 0.0), (0.0, 0.0, 0.0))
    for malformed in [None, (0.0, 0.0), (0.0, 0.0, 0.0, 1.0), (False, 0.0, 0.0),
                      (float('nan'), 0.0, 0.0), ('0', 0.0, 0.0)]:
        assert not _rotation_defaults_equal((0.0, 0.0, 0.0), malformed)
    assert _defaults_equal((normalize_float_constant(1e-7), 0.0, 0.0), (0.0, 0.0, 0.0))


@pytest.mark.parametrize('flag, diagnostic', [
    ('_TEST_BACKUP_COPY_FAIL_AFTER_RESET', 'rollback-backup copy failure'),
    ('_TEST_CUTOVER_FAIL_AFTER_RESET', 'Injected NodeForge cutover failure'),
])
def test_rotation_update_failure_keeps_group_node_user_override(flag, diagnostic):
    """Backup and cutover failures retain authoritative Rotation values and links."""
    from NodeForge.blender import group_backend
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    before = '''def nf_fixture(angle: Rotation = (0.0, 0.0, 0.0)) -> Rotation:
    return angle
'''
    after = '''def nf_fixture(angle: Rotation = (0.5, 0.0, 0.0)) -> Rotation:
    return angle
'''
    group = compile_group(before, 'NFTest_RotationUpdateFailure')
    wrapper = bpy.data.node_groups.new('NFTest_RotationFailureWrapper', 'GeometryNodeTree')
    node = wrapper.nodes.new('GeometryNodeGroup')
    node.node_tree = group
    blender_update._apply_group_defaults_to_node(node)
    node.inputs[0].default_value = (1e-7, 0.0, 0.0)
    source_pointer = group.as_pointer()
    setattr(group_backend, flag, True)
    try:
        with pytest.raises(RuntimeError, match=diagnostic):
            compiler.update_expression_group(group, after)
    finally:
        setattr(group_backend, flag, False)
    assert group.as_pointer() == source_pointer
    assert tuple(node.inputs[0].default_value) == (normalize_float_constant(1e-7), 0.0, 0.0)
    assert node.node_tree == group
    compiler.update_expression_group(group, after)
    assert tuple(node.inputs[0].default_value) == (normalize_float_constant(1e-7), 0.0, 0.0)


def test_rotation_default_and_user_edit_persist_after_blend_reopen(tmp_path):
    """A native .blend retains the typed default and small per-instance override."""
    from NodeForge.blender.interface import _get_group_input_defaults
    from NodeForge.semantic.numeric_semantics import normalize_float_constant

    before = "def nf_fixture(angle: Rotation = (0.0, 0.0, 0.0)) -> Rotation:\n    return angle\n"
    after = "def nf_fixture(angle: Rotation = (0.25, 0.0, 0.0)) -> Rotation:\n    return angle\n"
    group = compile_group(before, 'NFTest_RotationReopen')
    group.use_fake_user = True
    wrapper = bpy.data.node_groups.new('NFTest_RotationReopenWrapper', 'GeometryNodeTree')
    wrapper.use_fake_user = True
    node = wrapper.nodes.new('GeometryNodeGroup')
    node.node_tree = group
    blender_update._apply_group_defaults_to_node(node)
    node.inputs[0].default_value = (1e-7, 0.0, 0.0)
    filename = str(tmp_path / 'rotation_reopen.blend')
    bpy.ops.wm.save_as_mainfile(filepath=filename)
    bpy.ops.wm.open_mainfile(filepath=filename)
    restored_group = bpy.data.node_groups['NFTest_RotationReopen']
    restored_node = next(n for n in bpy.data.node_groups['NFTest_RotationReopenWrapper'].nodes
                         if n.bl_idname == 'GeometryNodeGroup')
    assert restored_node.node_tree == restored_group
    assert restored_node.inputs[0].bl_idname == 'NodeSocketRotation'
    assert tuple(restored_node.inputs[0].default_value) == (normalize_float_constant(1e-7), 0.0, 0.0)
    assert next(iter(_get_group_input_defaults(restored_group).values()))['nf_type'] == 'ROTATION'
    compiler.update_expression_group(restored_group, after)
    assert tuple(restored_node.inputs[0].default_value) == (normalize_float_constant(1e-7), 0.0, 0.0)
    assert all(link.is_valid for link in restored_group.links)
