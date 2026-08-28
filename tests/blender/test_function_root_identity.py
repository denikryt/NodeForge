from helpers import *

from NodeForge.function_instances import (
    FUNCTION_DEFINITION_OWNER_PROP,
    FUNCTION_INSTANCE_KEY_PROP,
    FUNCTION_ROOT_OWNER_ID_PROP,
)


def _helper_by_instance_key(instance_key):
    """Return the sole live local helper carrying *instance_key*."""
    matches = [
        group for group in bpy.data.node_groups
        if group.get("nodeforge_generated_kind") == "local_function_helper"
        and str(group.get(FUNCTION_INSTANCE_KEY_PROP) or "") == str(instance_key)
    ]
    check(len(matches) == 1, f"expected one helper for instance key {instance_key}, got {[g.name for g in matches]}")
    return matches[0]


def test_unique_root_identity_survives_rename_save_reopen_and_unchanged_update(tmp_path):
    """Persist root/definition/instance identity and manual Float Curve state across reopen."""
    source = '''
def persistent_unique_leaf(x):
    curve = node("ShaderNodeFloatCurve", inputs={"Factor": 1.0, "Value": x}, output="Value", typ=Float)
    return curve

out = persistent_unique_leaf(0.5, __unique__=True)
output("Out", out)
'''
    root = compile_group(source, "NFTest_unique_root_persistence")
    root_id = str(root.get(FUNCTION_ROOT_OWNER_ID_PROP) or "")
    check(root_id, "root owner ID was not persisted")

    helper = next(
        group for group in bpy.data.node_groups
        if group.get("nodeforge_local_function_name") == "persistent_unique_leaf"
    )
    definition_owner = str(helper.get(FUNCTION_DEFINITION_OWNER_PROP) or "")
    instance_key = str(helper.get(FUNCTION_INSTANCE_KEY_PROP) or "")
    helper_name = helper.name
    check(definition_owner, "local definition owner metadata is missing")
    check(instance_key, "unique helper instance key is missing")

    curve = next(node for node in helper.nodes if node.bl_idname == "ShaderNodeFloatCurve")
    curve.mapping.curves[0].points[0].location[1] = 0.417
    curve.mapping.update()

    renamed_root = "NFTest_unique_root_persistence_user_renamed"
    root.name = renamed_root
    # Keep the standalone test root reachable across save/reopen; production
    # expression groups normally have a modifier/group-node user in the .blend.
    root.use_fake_user = True
    filepath = str(tmp_path / "unique_root_identity.blend")
    result = bpy.ops.wm.save_as_mainfile(filepath=filepath)
    check("FINISHED" in result, f"save_as_mainfile failed: {result}")
    result = bpy.ops.wm.open_mainfile(filepath=filepath)
    check("FINISHED" in result, f"open_mainfile failed: {result}")

    root_after_reopen = bpy.data.node_groups.get(renamed_root)
    check(root_after_reopen is not None, "renamed root is missing after reopen")
    check(str(root_after_reopen.get(FUNCTION_ROOT_OWNER_ID_PROP) or "") == root_id, "root owner ID changed after reopen")

    helper_after_reopen = _helper_by_instance_key(instance_key)
    check(helper_after_reopen.name == helper_name, "helper presentation name changed across reopen")
    check(str(helper_after_reopen.get(FUNCTION_DEFINITION_OWNER_PROP) or "") == definition_owner, "definition owner changed after reopen")
    curve_after_reopen = next(node for node in helper_after_reopen.nodes if node.bl_idname == "ShaderNodeFloatCurve")
    check(abs(float(curve_after_reopen.mapping.curves[0].points[0].location[1]) - 0.417) <= 1e-6, "manual Float Curve state changed across reopen")

    pointer_after_reopen = helper_after_reopen.as_pointer()
    compiler.update_expression_group(root_after_reopen, source)

    helper_after_update = _helper_by_instance_key(instance_key)
    check(helper_after_update.as_pointer() == pointer_after_reopen, "unchanged update replaced helper after reopen")
    check(str(root_after_reopen.get(FUNCTION_ROOT_OWNER_ID_PROP) or "") == root_id, "root owner ID changed on unchanged post-reopen update")
    check(str(helper_after_update.get(FUNCTION_DEFINITION_OWNER_PROP) or "") == definition_owner, "definition owner changed on post-reopen update")
    check(str(helper_after_update.get(FUNCTION_INSTANCE_KEY_PROP) or "") == instance_key, "instance key changed on post-reopen update")
    curve_after_update = next(node for node in helper_after_update.nodes if node.bl_idname == "ShaderNodeFloatCurve")
    check(abs(float(curve_after_update.mapping.curves[0].points[0].location[1]) - 0.417) <= 1e-6, "manual Float Curve state changed on unchanged post-reopen update")
