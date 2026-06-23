import pytest
from helpers import *


pytestmark = pytest.mark.blender_eval


def test_branch_free_runtime_backend_and_evaluation_contracts():
    table = build_branch_free_command_table('+F-F')
    check(table.vertex_count == 8 and table.edge_count == 4, 'branch-free table size mismatch')
    check(table.move_mask == (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), 'branch-free move masks drifted')
    check(table.heading_index == (0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0), 'branch-free heading semantics drifted')
    check(table.draw_mask == (False, True, False, True), 'branch-free draw masks drifted')
    table = build_branch_free_command_table('XfF')
    check(table.move_mask == (0.0, 0.0, 0.0, 1.0, 0.0, 1.0), 'ignored/move command masks drifted')
    check(table.draw_mask == (False, False, True), 'ignored/move draw masks drifted')
    try:
        build_branch_free_command_table('F[+F]')
    except CompileError:
        pass
    else:
        raise AssertionError('branch-free table builder accepted branch commands')
    try:
        build_branch_free_command_table('Xf')
    except CompileError:
        pass
    else:
        raise AssertionError('branch-free table builder accepted zero draw stream')
    eval_source = '\nangle_value = input_float("Angle", default=90.0)\nstep_value = input_float("Step", default=1.0)\ngeo = ls_system(ls_axiom("F+F"), ls_iterations(0), ls_angle(angle_value), ls_step(step_value))\noutput("Geometry", geo)\n'
    eval_group = compile_group(eval_source, 'NFTest_lsystem_branch_free_runtime_eval_source')
    _assert_branch_free_modifier_runtime_updates(eval_group)
    large_source = '\nangle_value = input_float("Angle", default=0.0)\nstep_value = input_float("Step", default=1.0)\ngeo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(11), ls_angle(angle_value), ls_step(step_value))\noutput("Geometry", geo)\n'
    large_group = compile_group(large_source, 'NFTest_lsystem_branch_free_large_branch_free_runtime')
    large_manifest, large_refs = _manifest_refs(large_group)
    check({ref.kind for ref in large_refs} == {'MESH', 'OBJECT'}, 'large branch-free runtime did not use generated Mesh/Object backend')
    large_mesh = bpy.data.meshes.get(_ref_by_kind(large_refs, 'MESH').name)
    check(large_mesh is not None and len(large_mesh.edges) == 2048, 'large branch-free command Mesh topology changed')
    check(len(large_group.nodes) < 40, f'large branch-free runtime node graph grew unexpectedly: {len(large_group.nodes)} nodes')
    wrapper, obj, mesh_data, mod = _attach_runtime_eval_modifier(large_group, 'NFTest_lsystem_branch_free_large_runtime_eval')
    try:
        _set_modifier_input(mod, wrapper, 'Angle', 0.0)
        _set_modifier_input(mod, wrapper, 'Step', 1.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices, edges, polygons = _evaluated_mesh_snapshot(obj)
        check(len(vertices) == 4096 and len(edges) == 2048 and (polygons == 0), 'large branch-free runtime evaluated output size changed')
        check(vertices[-1] == (2048.0, 0.0, 0.0), f'large branch-free runtime endpoint changed: {vertices[-1]}')
        check(generated_resources.read_group_manifest(large_group) == large_manifest, 'large branch-free runtime evaluation churned manifest')
    finally:
        _cleanup_runtime_eval_objects(wrapper, obj, mesh_data)
    source = '\nangle_value = input_float("Angle", default=60.0)\nstep_value = input_float("Step", default=0.1)\ngeo = ls_system(ls_axiom("F"), ls_rule("F", "F+F--F+F"), ls_iterations(3), ls_angle(angle_value), ls_step(step_value))\noutput("Geometry", geo)\n'
    group = compile_group(source, 'NFTest_lsystem_branch_free_branch_free_runtime')
    manifest, refs = _manifest_refs(group)
    kinds = {ref.kind for ref in refs}
    check(kinds == {'MESH', 'OBJECT'}, f'branch-free runtime generated unexpected resources: {kinds}')
    mesh_ref = _ref_by_kind(refs, 'MESH')
    object_ref = _ref_by_kind(refs, 'OBJECT')
    mesh = bpy.data.meshes.get(mesh_ref.name)
    obj = bpy.data.objects.get(object_ref.name)
    check(mesh is not None and obj is not None, 'branch-free runtime Mesh/Object missing')
    check(obj.data is mesh, 'branch-free command Object does not reference command Mesh')
    for ref in refs:
        id_obj = _collection_for_ref(ref).get(ref.name)
        meta = generated_resources.read_id_metadata(id_obj)
        check(meta is not None, f'generated {ref.kind} lacks metadata')
        check(meta.owner_group_uuid == manifest['owner_group_uuid'], 'branch-free owner UUID mismatch')
    check(len(mesh.vertices) == 2 * 148, 'command Mesh vertex count does not match expanded stream')
    check(len(mesh.edges) == 148, 'command Mesh edge count does not match expanded stream')
    for attr_name, domain, data_type, expected_len in [(MOVE_MASK_ATTR, 'POINT', 'FLOAT', len(mesh.vertices)), (HEADING_INDEX_ATTR, 'POINT', 'FLOAT', len(mesh.vertices)), (DRAW_MASK_ATTR, 'EDGE', 'BOOLEAN', len(mesh.edges))]:
        attr = mesh.attributes.get(attr_name)
        check(attr is not None, f'command Mesh attribute missing: {attr_name}')
        check(attr.domain == domain, f'{attr_name} domain changed: {attr.domain}')
        check(attr.data_type == data_type, f'{attr_name} data type changed: {attr.data_type}')
        check(len(attr.data) == expected_len, f'{attr_name} length mismatch')
    node_types = [getattr(node, 'bl_idname', '') for node in group.nodes]
    check('GeometryNodeCurvePrimitiveLine' not in node_types, 'branch-free runtime used per-segment Curve Line nodes')
    for required in {'GeometryNodeObjectInfo', 'GeometryNodeInputNamedAttribute', 'GeometryNodeAccumulateField', 'GeometryNodeSetPosition', 'GeometryNodeDeleteGeometry', 'GeometryNodeMeshToCurve'}:
        check(required in node_types, f'branch-free runtime graph missing {required}')
    check(len(group.nodes) < 40, f'branch-free runtime node graph grew unexpectedly: {len(group.nodes)} nodes')
    object_infos = [node for node in group.nodes if getattr(node, 'bl_idname', '') == 'GeometryNodeObjectInfo']
    check(object_infos and _object_info_source(object_infos[0]) is obj, 'Object Info does not source branch-free command Object')
    old_keys = _owned_generated_id_keys()
    old_manifest = generated_resources.read_group_manifest(group)
    compiler.update_expression_group(group, source.replace('ls_iterations(3)', 'ls_iterations(2)'))
    new_manifest, new_refs = _manifest_refs(group)
    check(new_manifest['generation_uuid'] != old_manifest['generation_uuid'], 'successful branch-free recompile did not advance generation')
    check(_owned_generated_id_keys() != old_keys, 'successful branch-free recompile did not replace generated IDs')
    for ref in refs:
        check(_collection_for_ref(ref).get(ref.name) is None, f'successful branch-free recompile left old {ref.kind}')
    check({ref.kind for ref in new_refs} == {'MESH', 'OBJECT'}, 'branch-free recompile lost Mesh/Object manifest')
    runtime_refs_before_static = list(new_refs)
    compiler.update_expression_group(group, source.replace('ls_angle(angle_value)', 'ls_angle(60.0)').replace('ls_step(step_value)', 'ls_step(0.1)'))
    static_manifest_after_runtime, static_refs_after_runtime, _ = _assert_static_baked_group(group)
    check(static_manifest_after_runtime['owner_group_uuid'] == new_manifest['owner_group_uuid'], 'runtime-to-static replacement did not preserve owner UUID')
    for ref in runtime_refs_before_static:
        check(_collection_for_ref(ref).get(ref.name) is None, f'old branch-free generated resource survived static replacement: {ref.name}')
    compiler.update_expression_group(group, source.replace('ls_iterations(3)', 'ls_iterations(2)'))
    stable_runtime_manifest, stable_runtime_refs = _manifest_refs(group)
    check({ref.kind for ref in stable_runtime_refs} == {'MESH', 'OBJECT'}, 'static-to-runtime replacement did not restore Mesh/Object manifest')
    for ref in static_refs_after_runtime:
        check(_collection_for_ref(ref).get(ref.name) is None, f'old static generated resource survived branch-free replacement: {ref.name}')
    stable_manifest, stable_refs = _manifest_refs(group)
    stable_source = compiler._extract_group_source(group)
    before_failure = _owned_generated_id_keys()
    generated_resources._TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE = True
    try:
        compiler.update_expression_group(group, source.replace('ls_iterations(3)', 'ls_iterations(1)'))
    except RuntimeError:
        pass
    else:
        raise AssertionError('fault-injected command Mesh attribute failure did not raise')
    check(generated_resources.read_group_manifest(group)['generation_uuid'] == stable_manifest['generation_uuid'], 'failed branch-free update changed manifest')
    check(compiler._extract_group_source(group) == stable_source, 'failed branch-free update changed stored source')
    check(_owned_generated_id_keys() == before_failure, 'failed branch-free update leaked or deleted generated IDs')
    for ref in stable_refs:
        check(_collection_for_ref(ref).get(ref.name) is not None, f'failed branch-free update removed stable {ref.kind}')
    before_cutover = _owned_generated_id_keys()
    compiler._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        compiler.update_expression_group(group, source.replace('ls_iterations(3)', 'ls_iterations(1)'))
    except RuntimeError:
        pass
    else:
        raise AssertionError('fault-injected branch-free cutover did not raise')
    check(generated_resources.read_group_manifest(group)['generation_uuid'] == stable_manifest['generation_uuid'], 'branch-free cutover failure changed manifest')
    check(compiler._extract_group_source(group) == stable_source, 'branch-free cutover failure changed source')
    check(_owned_generated_id_keys() == before_cutover, 'branch-free cutover failure leaked or deleted generated IDs')
    shared_group = compile_group(source.replace('ls_iterations(3)', 'ls_iterations(1)'), 'NFTest_lsystem_branch_free_shared_mesh')
    shared_manifest, shared_refs = _manifest_refs(shared_group)
    shared_mesh_ref = _ref_by_kind(shared_refs, 'MESH')
    shared_object_ref = _ref_by_kind(shared_refs, 'OBJECT')
    shared_mesh = bpy.data.meshes.get(shared_mesh_ref.name)
    user_obj = bpy.data.objects.new('NFTest_lsystem_branch_free_user_mesh', shared_mesh)
    try:
        bpy.context.collection.objects.link(user_obj)
    except Exception:
        pass
    try:
        generated_resources.write_empty_manifest(shared_group, shared_manifest['owner_group_uuid'])
        generated_resources.cleanup_restart_orphans()
        check(bpy.data.objects.get(shared_object_ref.name) is None, 'restart cleanup left generated command Object')
        check(bpy.data.meshes.get(shared_mesh.name) is shared_mesh, 'restart cleanup deleted generated Mesh used by user Object')
        check(bpy.data.objects.get(user_obj.name) is user_obj, 'restart cleanup deleted user Object sharing Mesh')
    finally:
        try:
            if bpy.data.objects.get(user_obj.name) is user_obj:
                bpy.data.objects.remove(user_obj, do_unlink=True)
        except Exception:
            pass
        try:
            if bpy.data.meshes.get(shared_mesh.name) is shared_mesh:
                bpy.data.meshes.remove(shared_mesh, do_unlink=True)
        except Exception:
            pass
    static_group = compile_group(_static_lsystem_source(iterations=1), 'NFTest_lsystem_branch_free_static_unchanged')
    _static_manifest, static_refs, _ = _assert_static_baked_group(static_group)
    check({ref.kind for ref in static_refs} == {'CURVE', 'OBJECT'}, 'static baked backend started using command Mesh')
    print('LSYSTEM_BRANCH_FREE_RUNTIME_OK')
