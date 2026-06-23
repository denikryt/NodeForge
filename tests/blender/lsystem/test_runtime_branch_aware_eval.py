import pytest
from helpers import *


pytestmark = pytest.mark.blender_eval


def test_branch_aware_runtime_backend_and_evaluation_contracts():
    origin_group = compile_group(_runtime_branched_source('[F]'), 'NFTest_lsystem_branch_aware_branch_origin')
    _origin_manifest, origin_refs = _manifest_refs(origin_group)
    check({ref.kind for ref in origin_refs} == {'MESH', 'OBJECT'}, 'branch-at-origin runtime did not use generated Mesh/Object')
    origin_mesh = bpy.data.meshes.get(_ref_by_kind(origin_refs, 'MESH').name)
    check(origin_mesh is not None, 'branch-at-origin command Mesh missing')
    check(len(origin_mesh.vertices) == 3 and len(origin_mesh.edges) == 1, 'branch-at-origin anchor topology changed')
    check(_attribute_values(origin_mesh.attributes[PARENT_ATTACH_INDEX_ATTR]) == (-1, 0, 0), 'branch-at-origin parent attach indices changed')
    group = compile_group(_runtime_branched_source('F[+F]F'), 'NFTest_lsystem_branch_aware_branched_runtime')
    _manifest, refs = _manifest_refs(group)
    check({ref.kind for ref in refs} == {'MESH', 'OBJECT'}, 'branched runtime generated unexpected resources')
    check(any((ref.role == 'branch_aware_runtime_command_mesh' for ref in refs)), 'branch-aware Mesh role missing')
    check(any((ref.role == 'branch_aware_runtime_command_object' for ref in refs)), 'branch-aware Object role missing')
    mesh = bpy.data.meshes.get(_ref_by_kind(refs, 'MESH').name)
    obj = bpy.data.objects.get(_ref_by_kind(refs, 'OBJECT').name)
    check(mesh is not None and obj is not None and (obj.data is mesh), 'branch-aware Mesh/Object graph missing')
    for attr_name, domain, data_type, expected_len in [(MOVE_MASK_ATTR, 'POINT', 'FLOAT', len(mesh.vertices)), (HEADING_INDEX_ATTR, 'POINT', 'FLOAT', len(mesh.vertices)), (PATH_ID_ATTR, 'POINT', 'INT', len(mesh.vertices)), (PATH_DEPTH_ATTR, 'POINT', 'INT', len(mesh.vertices)), (PARENT_ATTACH_INDEX_ATTR, 'POINT', 'INT', len(mesh.vertices)), (ANCHOR_MASK_ATTR, 'POINT', 'BOOLEAN', len(mesh.vertices)), (DRAW_MASK_ATTR, 'EDGE', 'BOOLEAN', len(mesh.edges))]:
        attr = mesh.attributes.get(attr_name)
        check(attr is not None, f'branch-aware command Mesh attribute missing: {attr_name}')
        check(attr.domain == domain, f'{attr_name} domain changed: {attr.domain}')
        check(attr.data_type == data_type, f'{attr_name} data type changed: {attr.data_type}')
        check(len(attr.data) == expected_len, f'{attr_name} length mismatch')
    node_types = [getattr(node, 'bl_idname', '') for node in group.nodes]
    check('GeometryNodeCurvePrimitiveLine' not in node_types, 'branched runtime used per-segment Curve Line nodes')
    for required in {'GeometryNodeObjectInfo', 'GeometryNodeInputNamedAttribute', 'GeometryNodeAccumulateField', 'GeometryNodeStoreNamedAttribute', 'GeometryNodeSampleIndex', 'GeometryNodeSetPosition', 'GeometryNodeDeleteGeometry', 'GeometryNodeMeshToCurve'}:
        check(required in node_types, f'branch-aware runtime graph missing {required}')
    check(len(group.nodes) < 120, f'branch-aware runtime node graph grew unexpectedly: {len(group.nodes)} nodes')
    _assert_branch_aware_sample_index_uses_safe_parent_index(group)
    object_infos = [node for node in group.nodes if getattr(node, 'bl_idname', '') == 'GeometryNodeObjectInfo']
    check(object_infos and _object_info_source(object_infos[0]) is obj, 'Object Info does not source branch-aware command Object')
    _assert_branch_aware_modifier_runtime_updates(group)
    evaluated_edge_cases = [('[F]', ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0)), ((0, 1),), 'branch_origin'), ('[+F]F', ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (0.0, 0.0, 0.0), (0.0, 1.0, 0.0)), ((0, 1), (2, 3)), 'branch_before_root_draw'), ('F[[F]F]', ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)), ((0, 1), (2, 3), (4, 5)), 'nested_branch_at_child_origin'), ('F[+fF]F', ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 1.0, 0.0), (1.0, 2.0, 0.0)), ((0, 1), (1, 2), (3, 4)), 'lowercase_move_branch'), ('F[+XF]F', ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)), ((0, 1), (1, 2), (3, 4)), 'ignored_symbol_branch')]
    for axiom, expected_vertices, expected_edges, suffix in evaluated_edge_cases:
        _assert_branch_aware_evaluated_fixture(axiom, expected_vertices, expected_edges, suffix)
    large_source = _runtime_branched_source('F[+F]' + 'F' * 1100, angle_default=0.0, step_default=1.0)
    large_group = compile_group(large_source, 'NFTest_lsystem_branch_aware_large_branched_runtime')
    large_manifest, large_refs = _manifest_refs(large_group)
    large_mesh = bpy.data.meshes.get(_ref_by_kind(large_refs, 'MESH').name)
    check(large_mesh is not None and len(large_mesh.edges) > 1000, 'large branched runtime command Mesh topology changed')
    check(len(large_group.nodes) < 120, f'large branched runtime node graph grew unexpectedly: {len(large_group.nodes)} nodes')
    check(generated_resources.read_group_manifest(large_group) == large_manifest, 'large branched runtime compile mutated manifest unexpectedly')
    depth_stream = '[' * (MAX_LSYSTEM_BRANCH_DEPTH + 1) + 'F' + ']' * (MAX_LSYSTEM_BRANCH_DEPTH + 1)
    expect_compile_error(_runtime_branched_source(depth_stream), 'NFTest_lsystem_branch_aware_depth_budget')
    stable_manifest, stable_refs = _manifest_refs(group)
    stable_source = compiler._extract_group_source(group)
    before_failure = _owned_generated_id_keys()
    generated_resources._TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE = True
    try:
        compiler.update_expression_group(group, _runtime_branched_source('F[+F]F[-F]F'))
    except RuntimeError:
        pass
    else:
        raise AssertionError('fault-injected branch-aware Mesh attribute failure did not raise')
    check(generated_resources.read_group_manifest(group)['generation_uuid'] == stable_manifest['generation_uuid'], 'failed branch-aware update changed manifest')
    check(compiler._extract_group_source(group) == stable_source, 'failed branch-aware update changed stored source')
    check(_owned_generated_id_keys() == before_failure, 'failed branch-aware update leaked or deleted generated IDs')
    for ref in stable_refs:
        check(_collection_for_ref(ref).get(ref.name) is not None, f'failed branch-aware update removed stable {ref.kind}')
    compiler.update_expression_group(group, _static_lsystem_source(iterations=1, step=0.2))
    static_manifest, static_refs, _ = _assert_static_baked_group(group)
    for ref in stable_refs:
        check(_collection_for_ref(ref).get(ref.name) is None, f'old branch-aware resource survived static replacement: {ref.name}')
    compiler.update_expression_group(group, _runtime_branched_source('F[+F]F'))
    runtime_manifest, runtime_refs = _manifest_refs(group)
    check(runtime_manifest['owner_group_uuid'] == static_manifest['owner_group_uuid'], 'static-to-branched replacement changed owner UUID')
    for ref in static_refs:
        check(_collection_for_ref(ref).get(ref.name) is None, f'old static resource survived branch-aware replacement: {ref.name}')
    compiler.update_expression_group(group, '\nangle_value = input_float("Angle", default=0.0)\nstep_value = input_float("Step", default=1.0)\ngeo = ls_system(ls_axiom("F+F"), ls_iterations(0), ls_angle(angle_value), ls_step(step_value))\noutput("Geometry", geo)\n')
    bf_manifest, bf_refs = _manifest_refs(group)
    check(any((ref.role == 'branch_free_runtime_command_mesh' for ref in bf_refs)), 'branched-to-branch-free replacement did not select branch-free backend')
    for ref in runtime_refs:
        check(_collection_for_ref(ref).get(ref.name) is None, f'old branch-aware resource survived branch-free replacement: {ref.name}')
    shutdown_group = compile_group(_runtime_branched_source('F[+F]F'), 'NFTest_lsystem_branch_aware_shutdown')
    _shutdown_manifest, shutdown_refs = _manifest_refs(shutdown_group)
    generated_resources.cleanup_live_group_resources()
    for ref in shutdown_refs:
        check(_collection_for_ref(ref).get(ref.name) is None, f'unregister cleanup left branch-aware {ref.kind}')
    check(generated_resources.read_group_manifest(shutdown_group)['resources'] == [], 'unregister cleanup did not clear branch-aware manifest')
    print('LSYSTEM_BRANCH_AWARE_RUNTIME_OK')
