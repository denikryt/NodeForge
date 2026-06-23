from helpers import *


def test_static_object_creation_failure_is_atomic():
    before = _owned_generated_id_keys()
    generated_resources._TEST_FAIL_AFTER_OBJECT_CREATE = True
    try:
        try:
            compile_group(_static_lsystem_source(iterations=1), "NFTest_lsystem_static_object_fail")
        except RuntimeError:
            pass
        else:
            raise AssertionError("fault-injected object creation did not raise")
    finally:
        generated_resources._TEST_FAIL_AFTER_OBJECT_CREATE = False
    assert _owned_generated_id_keys() == before


def test_runtime_command_mesh_creation_failure_is_atomic():
    before_owned = _owned_generated_id_keys()
    before_mesh_names = {mesh.name for mesh in bpy.data.meshes}
    generated_resources._TEST_FAIL_AFTER_MESH_CREATE = True
    try:
        try:
            compile_group(
                _lsystem_source("F+F", iterations=0, angle="90", step="1", runtime=True),
                "NFTest_lsystem_runtime_mesh_fail",
            )
        except RuntimeError:
            pass
        else:
            raise AssertionError("fault-injected command mesh creation did not raise")
    finally:
        generated_resources._TEST_FAIL_AFTER_MESH_CREATE = False

    after_mesh_names = {mesh.name for mesh in bpy.data.meshes}
    leaked_nodeforge_meshes = sorted(
        name for name in after_mesh_names - before_mesh_names if name.startswith("NodeForge.")
    )
    assert leaked_nodeforge_meshes == []
    assert _owned_generated_id_keys() == before_owned


def test_renamed_generated_refs_are_found_by_metadata():
    group = compile_group(_static_lsystem_source(iterations=1), "NFTest_lsystem_static_metadata_lookup")
    manifest, refs, _curve = _assert_static_baked_group(group)
    renamed = _rename_generated_refs(refs, "_renamed")
    try:
        generated_resources.cleanup_live_group_resources()
        for kind, name in renamed:
            coll = bpy.data.curves if kind == "CURVE" else (bpy.data.meshes if kind == "MESH" else bpy.data.objects)
            assert coll.get(name) is None
    finally:
        try:
            bpy.data.node_groups.remove(group)
        except Exception:
            pass
