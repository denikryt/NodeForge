from helpers import *

import json
import tempfile
from pathlib import Path

from NodeForge import packages

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
        'x = sin(0.5, __unique__=True)\noutput("x", x)\n',
        'x = vector(1, 2, 3, __unique__=True)\noutput("x", x)\n',
        'x = node("ShaderNodeValue", __unique__=True, output="Value", typ=Float)\noutput("x", x)\n',
    )
    for index, source in enumerate(cases):
        expect_compile_error(source, f"NFTest_unique_unsupported_{index}")


def test_failed_parent_update_restores_unique_float_curve_manual_state():
    """Outer rollback must restore CurveMapping edits on an updated unique helper."""
    first = """
def unique_curve_rollback_leaf(x):
    factor = node("ShaderNodeFloatCurve", inputs={"Factor": 1.0, "Value": x}, output="Value", typ=Float)
    return factor

out = unique_curve_rollback_leaf(0.5, __unique__=True)
output("Out", out)
"""
    changed_then_fail = """
def unique_curve_rollback_leaf(x):
    factor = node("ShaderNodeFloatCurve", inputs={"Factor": 1.0, "Value": x}, output="Value", typ=Float)
    return factor + 0.25

out = unique_curve_rollback_leaf(0.5, __unique__=True)
broken = missing_unique_curve_rollback_dependency(out)
output("Out", broken)
"""
    root = compile_group(first, "NFTest_unique_curve_rollback")
    helper = _local_helpers("unique_curve_rollback_leaf")[0]
    pointer = helper.as_pointer()
    curve = next(node for node in helper.nodes if node.bl_idname == "ShaderNodeFloatCurve")
    curve.mapping.curves[0].points[0].location[1] = 0.321
    middle = curve.mapping.curves[0].points.new(0.5, 0.777)
    middle.handle_type = "VECTOR"
    curve.mapping.update()
    helper["manual_probe"] = "keep"

    try:
        compiler.update_expression_group(root, changed_then_fail)
    except Exception:
        pass
    else:
        raise AssertionError("failed parent update did not raise")

    helper_after = _local_helpers("unique_curve_rollback_leaf")[0]
    check(helper_after.as_pointer() == pointer, "failed parent update replaced unique helper datablock")
    check(helper_after.get("manual_probe") == "keep", "failed parent update lost helper custom manual state")
    curve_after = next(node for node in helper_after.nodes if node.bl_idname == "ShaderNodeFloatCurve")
    restored_points = list(curve_after.mapping.curves[0].points)
    restored_y = float(restored_points[0].location[1])
    check(abs(restored_y - 0.321) <= 1e-6, f"failed parent update lost Float Curve endpoint state: {restored_y}")
    check(len(restored_points) == 3, f"failed parent update lost Float Curve point count: {len(restored_points)}")
    restored_middle = min(restored_points, key=lambda point: abs(float(point.location[0]) - 0.5))
    check(abs(float(restored_middle.location[1]) - 0.777) <= 1e-6, "failed parent update lost Float Curve interior point")
    check(restored_middle.handle_type == "VECTOR", f"failed parent update lost Float Curve handle type: {restored_middle.handle_type}")


def test_shared_calls_do_not_consume_unique_ordinals_and_callees_are_independent():
    """Preserve the legacy unique-only per-callee occurrence sequence."""
    from NodeForge.compiler_identities import CallSiteId, local_function_id
    from NodeForge.function_instances import function_group_owner_scope, instance_key_for

    group = compile_group("""
def ordinal_first(x):
    return x + 1.0

def ordinal_second(x):
    return x + 2.0

shared = ordinal_first(1.0)
first_unique = ordinal_first(2.0, __unique__=True)
second_unique = ordinal_second(3.0, __unique__=True)
output("Out", shared + first_unique + second_unique)
""", "NFTest_unique_ordinal_contract")

    root_id = str(group.get(FUNCTION_ROOT_OWNER_ID_PROP) or "")
    check(root_id, "root owner id is missing")
    root_scope = function_group_owner_scope("ROOT", root_id)

    first_id = local_function_id(root_scope, "ordinal_first", "x:FLOAT")
    second_id = local_function_id(root_scope, "ordinal_second", "x:FLOAT")
    first_expected = instance_key_for(CallSiteId(root_scope, first_id, 0))
    second_expected = instance_key_for(CallSiteId(root_scope, second_id, 0))

    first_keys = sorted(
        str(helper.get(FUNCTION_INSTANCE_KEY_PROP) or "")
        for helper in _local_helpers("ordinal_first")
    )
    second_keys = sorted(
        str(helper.get(FUNCTION_INSTANCE_KEY_PROP) or "")
        for helper in _local_helpers("ordinal_second")
    )
    check(first_keys == ["", first_expected], f"shared call changed first unique ordinal: {first_keys}")
    check(second_keys == [second_expected], f"second callee did not start at ordinal zero: {second_keys}")


def test_imported_unique_function_preserves_shared_and_occurrence_identity():
    """Use canonical imported FunctionId without changing legacy unique keys."""
    from NodeForge.compiler_identities import CallSiteId, library_function_id
    from NodeForge.function_instances import function_group_owner_scope, instance_key_for

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp) / "vendor.unique_identity"
        functions = root / "functions"
        functions.mkdir(parents=True)
        manifest = {
            "schema_version": 1,
            "id": "vendor.unique_identity",
            "name": "vendor.unique_identity",
            "version": "1.0.0",
            "author": "Tests",
            "description": "Canonical imported identity regression fixture.",
            "nodeforge_min_version": "0.50.0",
            "nodeforge_max_version": None,
            "contents": {"functions": "functions"},
            "permissions": {"python": False},
        }
        (root / "nodeforge_package.json").write_text(json.dumps(manifest), encoding="utf-8")
        (functions / "import_unique_probe.nf").write_text(
            'value = input_float("Value")\noutput("Value", value + 1.0)\n',
            encoding="utf-8",
        )
        packages.install_package_directory(root, allow_python=False)
        try:
            source = """from functions import import_unique_probe
shared = import_unique_probe(1.0)
first = import_unique_probe(2.0, __unique__=True)
second = import_unique_probe(3.0, __unique__=True)
output("Out", shared + first + second)
"""
            original_materializer_identity_builder = library.library_function_id

            def _unexpected_materializer_identity_reconstruction(*args, **kwargs):
                raise AssertionError("materializer reconstructed an already-resolved imported FunctionId")

            library.library_function_id = _unexpected_materializer_identity_reconstruction
            try:
                group = compile_group(source, "NFTest_imported_unique_identity")
            finally:
                library.library_function_id = original_materializer_identity_builder
            root_id = str(group.get(FUNCTION_ROOT_OWNER_ID_PROP) or "")
            check(root_id, "root owner id is missing for imported unique test")
            root_scope = function_group_owner_scope("ROOT", root_id)
            function_id = library_function_id("functions", "vendor.unique_identity", "import_unique_probe")
            expected = [
                "",
                instance_key_for(CallSiteId(root_scope, function_id, 0)),
                instance_key_for(CallSiteId(root_scope, function_id, 1)),
            ]
            actual = sorted(
                str(candidate.get(FUNCTION_INSTANCE_KEY_PROP) or "")
                for candidate in bpy.data.node_groups
                if candidate.get("nodeforge_library_namespace") == "functions"
                and candidate.get("nodeforge_library_name") == "import_unique_probe"
            )
            check(actual == sorted(expected), f"imported unique instance keys changed: {actual}")
        finally:
            packages.uninstall_package("vendor.unique_identity")
