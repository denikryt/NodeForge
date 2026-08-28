from helpers import *

from NodeForge.function_instances import (
    FUNCTION_INSTANCE_KEY_PROP,
    FUNCTION_ROOT_OWNER_ID_PROP,
)


def _local_helpers(function_name):
    return [
        group for group in bpy.data.node_groups
        if group.get("nodeforge_generated_kind") == "local_function_helper"
        and group.get("nodeforge_local_function_name") == function_name
    ]


def _group_nodes(group):
    return [node for node in group.nodes if getattr(node, "bl_idname", None) == "GeometryNodeGroup"]


def test_unique_local_function_creates_distinct_shallow_groups_and_false_shares():
    group = compile_group("""
def unique_leaf_a(x):
    return x + 1.0

a = unique_leaf_a(1.0, __unique__=True)
b = unique_leaf_a(2.0, __unique__=True)
c = unique_leaf_a(3.0)
d = unique_leaf_a(4.0, __unique__=False)
output("A", a + b + c + d)
""", "NFTest_unique_local_function_distinct")
    helpers = _local_helpers("unique_leaf_a")
    keys = sorted(str(helper.get(FUNCTION_INSTANCE_KEY_PROP) or "") for helper in helpers)
    check(keys.count("") == 1, f"expected one shared helper, got keys {keys}")
    check(len([key for key in keys if key]) == 2, f"expected two unique helpers, got keys {keys}")
    check(group.get(FUNCTION_ROOT_OWNER_ID_PROP), "root owner id was not persisted")


def test_unique_local_function_survives_root_rename_on_unchanged_update():
    source = """
def unique_rename_leaf(x):
    factor = node("ShaderNodeFloatCurve", inputs={"Factor": 1.0, "Value": x}, output="Value", typ=Float)
    return factor

out = unique_rename_leaf(0.5, __unique__=True)
output("Out", out)
"""
    group = compile_group(source, "NFTest_unique_root_rename")
    helper = _local_helpers("unique_rename_leaf")[0]
    helper["manual_probe"] = "keep"
    root_id = group.get(FUNCTION_ROOT_OWNER_ID_PROP)
    instance_key = helper.get(FUNCTION_INSTANCE_KEY_PROP)
    group.name = "NFTest_unique_root_rename_user_named"
    compiler.update_expression_group(group, source)
    helper_after = _local_helpers("unique_rename_leaf")[0]
    check(helper_after is helper, "unchanged unique helper was replaced after root rename")
    check(helper_after.get("manual_probe") == "keep", "manual state did not survive unchanged update")
    check(group.get(FUNCTION_ROOT_OWNER_ID_PROP) == root_id, "root owner id changed after rename update")
    check(helper_after.get(FUNCTION_INSTANCE_KEY_PROP) == instance_key, "instance key changed after rename update")


def test_unique_wrapper_rebuilds_when_transitive_local_leaf_changes():
    first = """
def unique_dependency_leaf(x):
    return x * 2.0

def unique_dependency_wrapper(x):
    factor = node("ShaderNodeFloatCurve", inputs={"Factor": 1.0, "Value": x}, output="Value", typ=Float)
    return unique_dependency_leaf(factor)

out = unique_dependency_wrapper(0.5, __unique__=True)
output("Out", out)
"""
    second = first.replace("x * 2.0", "x * 3.0")
    group = compile_group(first, "NFTest_unique_dependency")
    wrapper = _local_helpers("unique_dependency_wrapper")[0]
    wrapper["manual_probe"] = "reset-on-change"
    wrapper_key = wrapper.get(FUNCTION_INSTANCE_KEY_PROP)
    compiler.update_expression_group(group, second)
    wrapper_after = _local_helpers("unique_dependency_wrapper")[0]
    check(wrapper_after is wrapper, "changed dependency did not rebuild the exact unique wrapper")
    check(wrapper_after.get(FUNCTION_INSTANCE_KEY_PROP) == wrapper_key, "unique wrapper ownership changed")
    check(wrapper_after.get("manual_probe") is None, "manual state survived a changed effective fingerprint")


def test_shared_leaf_reached_through_two_parents_has_one_marked_nested_child():
    group = compile_group("""
def unique_nested_child(x):
    return x + 1.0

def unique_shared_leaf(x):
    return unique_nested_child(x, __unique__=True)

def unique_left(x):
    return unique_shared_leaf(x)

def unique_right(x):
    return unique_shared_leaf(x)

a = unique_left(1.0)
b = unique_right(2.0)
output("Out", a + b)
""", "NFTest_unique_shared_leaf_scope")
    leaf_helpers = _local_helpers("unique_shared_leaf")
    nested_helpers = _local_helpers("unique_nested_child")
    check(len(leaf_helpers) == 1, f"expected one shared leaf helper, got {[g.name for g in leaf_helpers]}")
    check(len(nested_helpers) == 1, f"expected one nested unique child, got {[g.name for g in nested_helpers]}")
    check(nested_helpers[0].get(FUNCTION_INSTANCE_KEY_PROP), "nested marked child has no instance key")
    check(getattr(group, "bl_idname", None) == "GeometryNodeTree", "parent group did not compile")


def test_unique_modifier_rejects_unsupported_calls():
    cases = (
        'x = sin(0.5, __unique__=False)\noutput("x", x)\n',
        'x = node("ShaderNodeValue", __unique__=True, output="Value", typ=Float)\noutput("x", x)\n',
    )
    for index, source in enumerate(cases):
        expect_compile_error(source, f"NFTest_unique_unsupported_{index}")
