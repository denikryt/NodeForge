from helpers import *




def test_static_baked_backend_and_static_ownership_contracts():
    group = compile_group(_static_lsystem_source(iterations=2), 'NFTest_lsystem_static_ownership_static')
    old_manifest, old_refs, old_obj = _assert_static_baked_group(group)
    old_names = {(ref.kind, ref.name) for ref in old_refs}
    large = compile_group('\ngeo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(11), ls_angle(0), ls_step(0.01))\noutput("Geometry", geo)\n', 'NFTest_lsystem_static_ownership_large_static')
    _assert_static_baked_group(large)
    check(len(large.nodes) <= 4, f'static baked node graph grew per segment: {len(large.nodes)} nodes')
    static_branch_edge_cases = {'[F]': 1, '[+F]F': 2, 'F[[F]F]': 3, 'F[+fF]F': 3, 'F[+XF]F': 3, 'F[-F[+F]F]F': 5}
    for index, (axiom, expected_splines) in enumerate(static_branch_edge_cases.items()):
        edge_group = compile_group(f'geo = ls_system(ls_axiom("{axiom}"), ls_iterations(0), ls_angle(90), ls_step(1))\noutput("Geometry", geo)', f'NFTest_lsystem_static_ownership_static_branch_edge_{index}')
        _edge_manifest, edge_refs, edge_obj = _assert_static_baked_group(edge_group)
        curve_ref = _ref_by_kind(edge_refs, 'CURVE')
        curve = bpy.data.curves.get(curve_ref.name)
        check(curve is not None, f'static branch edge fixture {axiom} missing Curve')
        check(len(curve.splines) == expected_splines, f'static branch edge fixture {axiom} segment count changed')
        check(all((len(spline.points) == 2 for spline in curve.splines)), f'static branch edge fixture {axiom} created non-polyline segment')
        check(edge_obj.hide_viewport and edge_obj.hide_render, f'static branch edge fixture {axiom} generated Object is visible')
    before_resource_failure = _owned_generated_id_keys()
    generated_resources._TEST_FAIL_AFTER_OBJECT_CREATE = True
    try:
        compile_group(_static_lsystem_source(iterations=1, step=0.15), 'NFTest_lsystem_static_ownership_resource_failure')
    except RuntimeError:
        pass
    else:
        raise AssertionError('fault-injected generated-object failure did not raise')
    check(_owned_generated_id_keys() == before_resource_failure, 'generated-object failure leaked temporary IDs')
    runtime = compile_group('\nangle_value = input_float("Angle", default=60)\ngeo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(2), ls_angle(angle_value), ls_step(0.1))\noutput("Geometry", geo)\n', 'NFTest_lsystem_static_ownership_runtime_branch_free')
    runtime_manifest, runtime_refs = _manifest_refs(runtime)
    check({ref.kind for ref in runtime_refs} == {'MESH', 'OBJECT'}, 'branch-free runtime L-system used static Curve resources')
    check(any((ref.role == 'branch_free_runtime_command_mesh' for ref in runtime_refs)), 'branch-free runtime command Mesh role missing')
    check(not any((getattr(node, 'bl_idname', '') == 'GeometryNodeCurvePrimitiveLine' for node in runtime.nodes)), 'branch-free runtime used per-segment Curve Line nodes')
    check(runtime_manifest['owner_group_uuid'], 'branch-free runtime manifest lacks owner UUID')
    compiler.update_expression_group(group, _static_lsystem_source(iterations=1, step=0.2))
    new_manifest, new_refs, _new_obj = _assert_static_baked_group(group)
    check(new_manifest['owner_group_uuid'] == old_manifest['owner_group_uuid'], 'owner_group_uuid was not preserved across update')
    for kind, name in old_names:
        coll = bpy.data.curves if kind == 'CURVE' else bpy.data.objects
        check(coll.get(name) is None, f'old generated {kind} survived successful replacement: {name}')
    previous_refs = list(new_refs)
    runtime_update_source = '\nangle_value = input_float("Angle", default=60)\ngeo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(2), ls_angle(angle_value), ls_step(0.1))\noutput("Geometry", geo)\n'
    compiler.update_expression_group(group, runtime_update_source)
    runtime_manifest, runtime_refs = _manifest_refs(group)
    check({ref.kind for ref in runtime_refs} == {'MESH', 'OBJECT'}, 'runtime replacement did not commit branch-free Mesh/Object manifest')
    check(runtime_manifest['owner_group_uuid'] == new_manifest['owner_group_uuid'], 'runtime manifest did not preserve owner UUID')
    check(not any((getattr(node, 'bl_idname', '') == 'GeometryNodeCurvePrimitiveLine' for node in group.nodes)), 'runtime update used per-segment Curve Line nodes')
    check(compiler._extract_group_source(group) == runtime_update_source, 'runtime update did not store replacement source')
    for ref in previous_refs:
        coll = _collection_for_ref(ref)
        check(coll.get(ref.name) is None, f'old generated resource survived runtime replacement: {ref.name}')
    compiler.update_expression_group(group, _static_lsystem_source(iterations=1, step=0.25))
    grid_manifest, grid_refs, _ = _assert_static_baked_group(group)
    previous_refs = list(grid_refs)
    grid_source = 'geo = grid(2, 2)\noutput("Geometry", geo)'
    compiler.update_expression_group(group, grid_source)
    empty_manifest = generated_resources.read_group_manifest(group)
    check(empty_manifest is not None and empty_manifest['resources'] == [], 'zero-resource replacement did not commit empty manifest')
    check(empty_manifest['owner_group_uuid'] == grid_manifest['owner_group_uuid'], 'empty manifest did not preserve owner UUID')
    check(compiler._extract_group_source(group) == grid_source, 'zero-resource update did not store replacement source')
    for ref in previous_refs:
        coll = bpy.data.curves if ref.kind == 'CURVE' else bpy.data.objects
        check(coll.get(ref.name) is None, f'old generated resource survived zero-resource update: {ref.name}')
    shared_group = compile_group(_static_lsystem_source(iterations=1, step=0.31), 'NFTest_lsystem_static_ownership_shared_recompile')
    _shared_manifest, shared_refs, _ = _assert_static_baked_group(shared_group)
    shared_curve_ref = _ref_by_kind(shared_refs, 'CURVE')
    shared_object_ref = _ref_by_kind(shared_refs, 'OBJECT')
    shared_curve, shared_user_obj = _create_user_object_using_generated_curve(shared_curve_ref, 'NFTest_lsystem_static_ownership_user_curve_recompile')
    try:
        compiler.update_expression_group(shared_group, _static_lsystem_source(iterations=2, step=0.32))
        check(bpy.data.objects.get(shared_object_ref.name) is None, 'recompile left old generated Object')
        check(bpy.data.curves.get(shared_curve.name) is shared_curve, 'recompile deleted generated Curve still used by user Object')
        check(bpy.data.objects.get(shared_user_obj.name) is shared_user_obj, 'recompile deleted user Object sharing generated Curve')
        check(shared_user_obj.data is shared_curve, 'recompile unlinked user Object from generated Curve')
    finally:
        _remove_user_object_and_generated_curve(shared_user_obj, shared_curve)
    renamed_group = compile_group(_static_lsystem_source(iterations=1, step=0.33), 'NFTest_lsystem_static_ownership_renamed_recompile')
    _renamed_manifest, renamed_refs, _ = _assert_static_baked_group(renamed_group)
    renamed_live_names = _rename_generated_refs(renamed_refs, '.UserRenamed')
    compiler.update_expression_group(renamed_group, _static_lsystem_source(iterations=2, step=0.34))
    _assert_static_baked_group(renamed_group)
    for kind, live_name in renamed_live_names:
        coll = bpy.data.curves if kind == 'CURVE' else bpy.data.objects
        check(coll.get(live_name) is None, f'renamed old generated {kind} survived successful replacement: {live_name}')
    compiler.update_expression_group(group, _static_lsystem_source(iterations=1, step=0.3))
    stable_manifest, stable_refs, stable_obj = _assert_static_baked_group(group)
    stable_source = compiler._extract_group_source(group)
    before_failed_compile_keys = _owned_generated_id_keys()
    try:
        compiler.update_expression_group(group, 'geo = missing_func(1)\noutput("Geometry", geo)')
    except Exception:
        pass
    else:
        raise AssertionError('failed replacement compile did not raise')
    after_fail_manifest = generated_resources.read_group_manifest(group)
    check(after_fail_manifest['generation_uuid'] == stable_manifest['generation_uuid'], 'failed compile changed group manifest')
    check(bpy.data.objects.get(stable_obj.name) is stable_obj, 'failed compile removed old generated object')
    check(compiler._extract_group_source(group) == stable_source, 'failed compile changed stored source')
    check(_owned_generated_id_keys() == before_failed_compile_keys, 'failed replacement compile changed generated ID set')
    before_cutover_keys = _owned_generated_id_keys()
    compiler._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        compiler.update_expression_group(group, _static_lsystem_source(iterations=2, step=0.4))
    except RuntimeError:
        pass
    else:
        raise AssertionError('fault-injected cutover did not raise')
    after_cutover_manifest = generated_resources.read_group_manifest(group)
    check(after_cutover_manifest['generation_uuid'] == stable_manifest['generation_uuid'], 'cutover failure changed group manifest')
    check(bpy.data.objects.get(stable_obj.name) is stable_obj, 'cutover failure removed old generated object')
    check(compiler._extract_group_source(group) == stable_source, 'cutover failure changed stored source')
    check(_owned_generated_id_keys() == before_cutover_keys, 'cutover failure leaked or deleted generated IDs')
    object_infos = [node for node in group.nodes if getattr(node, 'bl_idname', '') == 'GeometryNodeObjectInfo']
    check(object_infos and _object_info_source(object_infos[0]) is stable_obj, 'cutover failure did not restore Object Info source')
    user_curve = bpy.data.curves.new('NodeForge.fake.UserCurve', 'CURVE')
    user_obj = bpy.data.objects.new('NodeForge.fake.UserObject', user_curve)
    try:
        generated_resources.cleanup_restart_orphans()
        check(bpy.data.curves.get(user_curve.name) is user_curve, 'cleanup deleted user curve without metadata')
        check(bpy.data.objects.get(user_obj.name) is user_obj, 'cleanup deleted user object without metadata')
    finally:
        bpy.data.objects.remove(user_obj, do_unlink=True)
        bpy.data.curves.remove(user_curve, do_unlink=True)
    orphan_group = compile_group(_static_lsystem_source(iterations=1, step=0.5), 'NFTest_lsystem_static_ownership_orphan')
    orphan_manifest, orphan_refs, _ = _assert_static_baked_group(orphan_group)
    generated_resources.write_empty_manifest(orphan_group, orphan_manifest['owner_group_uuid'])
    generated_resources.cleanup_restart_orphans()
    for ref in orphan_refs:
        coll = bpy.data.curves if ref.kind == 'CURVE' else bpy.data.objects
        check(coll.get(ref.name) is None, f'restart orphan cleanup left {ref.name}')
    shared_orphan_group = compile_group(_static_lsystem_source(iterations=1, step=0.55), 'NFTest_lsystem_static_ownership_shared_orphan')
    shared_orphan_manifest, shared_orphan_refs, _ = _assert_static_baked_group(shared_orphan_group)
    shared_orphan_curve_ref = _ref_by_kind(shared_orphan_refs, 'CURVE')
    shared_orphan_object_ref = _ref_by_kind(shared_orphan_refs, 'OBJECT')
    shared_orphan_curve, shared_orphan_user_obj = _create_user_object_using_generated_curve(shared_orphan_curve_ref, 'NFTest_lsystem_static_ownership_user_curve_orphan')
    try:
        generated_resources.write_empty_manifest(shared_orphan_group, shared_orphan_manifest['owner_group_uuid'])
        generated_resources.cleanup_restart_orphans()
        check(bpy.data.objects.get(shared_orphan_object_ref.name) is None, 'restart orphan cleanup left generated Object sharing Curve')
        check(bpy.data.curves.get(shared_orphan_curve.name) is shared_orphan_curve, 'restart orphan cleanup deleted generated Curve still used by user Object')
        check(bpy.data.objects.get(shared_orphan_user_obj.name) is shared_orphan_user_obj, 'restart orphan cleanup deleted user Object sharing generated Curve')
        check(shared_orphan_user_obj.data is shared_orphan_curve, 'restart orphan cleanup unlinked user Object from generated Curve')
    finally:
        _remove_user_object_and_generated_curve(shared_orphan_user_obj, shared_orphan_curve)
    shutdown_group = compile_group(_static_lsystem_source(iterations=1, step=0.6), 'NFTest_lsystem_static_ownership_shutdown')
    _shutdown_manifest, shutdown_refs, _ = _assert_static_baked_group(shutdown_group)
    generated_resources.cleanup_live_group_resources()
    for ref in shutdown_refs:
        coll = bpy.data.curves if ref.kind == 'CURVE' else bpy.data.objects
        check(coll.get(ref.name) is None, f'shutdown cleanup left {ref.name}')
    shared_shutdown_group = compile_group(_static_lsystem_source(iterations=1, step=0.65), 'NFTest_lsystem_static_ownership_shared_shutdown')
    _shared_shutdown_manifest, shared_shutdown_refs, _ = _assert_static_baked_group(shared_shutdown_group)
    shared_shutdown_curve_ref = _ref_by_kind(shared_shutdown_refs, 'CURVE')
    shared_shutdown_object_ref = _ref_by_kind(shared_shutdown_refs, 'OBJECT')
    shared_shutdown_curve, shared_shutdown_user_obj = _create_user_object_using_generated_curve(shared_shutdown_curve_ref, 'NFTest_lsystem_static_ownership_user_curve_shutdown')
    try:
        generated_resources.cleanup_live_group_resources()
        check(bpy.data.objects.get(shared_shutdown_object_ref.name) is None, 'shutdown cleanup left generated Object sharing Curve')
        check(bpy.data.curves.get(shared_shutdown_curve.name) is shared_shutdown_curve, 'shutdown cleanup deleted generated Curve still used by user Object')
        check(bpy.data.objects.get(shared_shutdown_user_obj.name) is shared_shutdown_user_obj, 'shutdown cleanup deleted user Object sharing generated Curve')
        check(shared_shutdown_user_obj.data is shared_shutdown_curve, 'shutdown cleanup unlinked user Object from generated Curve')
    finally:
        _remove_user_object_and_generated_curve(shared_shutdown_user_obj, shared_shutdown_curve)
    renamed_shutdown_group = compile_group(_static_lsystem_source(iterations=1, step=0.66), 'NFTest_lsystem_static_ownership_renamed_shutdown')
    _renamed_shutdown_manifest, renamed_shutdown_refs, _ = _assert_static_baked_group(renamed_shutdown_group)
    renamed_shutdown_live_names = _rename_generated_refs(renamed_shutdown_refs, '.UserRenamed')
    generated_resources.cleanup_live_group_resources()
    for kind, live_name in renamed_shutdown_live_names:
        coll = bpy.data.curves if kind == 'CURVE' else bpy.data.objects
        check(coll.get(live_name) is None, f'unregister cleanup left renamed generated {kind}: {live_name}')
    check(generated_resources.read_group_manifest(renamed_shutdown_group)['resources'] == [], 'unregister cleanup did not clear renamed-resource manifest')
    generated_resources.cleanup_live_group_resources()
    generated_resources.cleanup_restart_orphans()
    print('LSYSTEM_STATIC_OWNERSHIP_OK')
