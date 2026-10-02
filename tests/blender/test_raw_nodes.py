from helpers import *

import json

from NodeForge.builtins import raw_nodes


def _nodes(group, bl_idname):
    return [node for node in group.nodes if getattr(node, "bl_idname", None) == bl_idname]


def _one_node(group, bl_idname):
    nodes = _nodes(group, bl_idname)
    check(len(nodes) == 1, f"expected one {bl_idname}, found {len(nodes)}")
    return nodes[0]


def _contract(node, selector):
    """Return one declared raw input contract selected through current source semantics."""
    spec = raw_nodes.raw_node_spec(node)
    socket = raw_nodes.resolve_socket(node.inputs, selector, direction="input", context="test")
    for entry in spec["inputs"]:
        if spec["schema_version"] == 1:
            if entry["name"] == getattr(socket, "name", None):
                return entry
        elif entry["socket"].identifier == getattr(socket, "identifier", None):
            return entry
    raise AssertionError(f"missing raw input contract for selector {selector!r}")


def _raw_schema_version(node):
    """Return the normalized raw metadata schema generation for one Blender node."""
    return raw_nodes.raw_node_spec(node)["schema_version"]


def _downgrade_raw_metadata_to_v1(node):
    """Rewrite one fresh schema-v2 raw node into an equivalent legacy v1 fixture."""
    spec = raw_nodes.raw_node_spec(node)
    check(spec["schema_version"] == raw_nodes.RAW_SCHEMA_VERSION, "expected fresh v2 raw metadata")

    inputs = []
    for contract in spec["inputs"]:
        ref = contract["socket"]
        matches = [socket for socket in node.inputs if getattr(socket, "identifier", None) == ref.identifier]
        check(len(matches) == 1, f"cannot downgrade input identifier {ref.identifier!r}")
        item = {key: value for key, value in contract.items() if key != "socket"}
        item["name"] = matches[0].name
        inputs.append(item)

    outputs = []
    for ref in spec["outputs"]:
        matches = [socket for socket in node.outputs if getattr(socket, "identifier", None) == ref.identifier]
        check(len(matches) == 1, f"cannot downgrade output identifier {ref.identifier!r}")
        outputs.append(matches[0].name)

    node[raw_nodes.RAW_INPUTS_JSON_PROP] = json.dumps(inputs, separators=(",", ":"), sort_keys=True)
    node[raw_nodes.RAW_OUTPUTS_JSON_PROP] = json.dumps(outputs, separators=(",", ":"), sort_keys=True)
    if raw_nodes.RAW_SCHEMA_VERSION_PROP in node:
        del node[raw_nodes.RAW_SCHEMA_VERSION_PROP]
    check(raw_nodes.raw_node_spec(node)["schema_version"] == 1, "v1 downgrade fixture kept v2 generation")


def _same_blender_ref(left, right):
    if left is right:
        return True
    try:
        return left == right
    except Exception:
        pass
    try:
        return left.as_pointer() == right.as_pointer()
    except Exception:
        return False


def _incoming_count(group, node, socket_name):
    socket = raw_nodes.resolve_socket(node.inputs, socket_name, direction="input", context="test")
    return sum(
        1
        for link in group.links
        if _same_blender_ref(link.to_node, node) and _same_blender_ref(link.to_socket, socket)
    )


def test_raw_compare_single_output_literal_default_and_metadata():
    group = compile_group('''
mask = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "GREATER_THAN"},
    inputs={"A": position().z, "B": 0.5},
    output="Result",
    typ=Bool,
)
output("mask", mask)
''', "NFTest_raw_compare")
    node = _one_node(group, "FunctionNodeCompare")
    check(raw_nodes.is_raw_node(node), "raw compare node missing metadata marker")
    check(_raw_schema_version(node) == raw_nodes.RAW_SCHEMA_VERSION, "fresh raw node did not write schema v2")
    check(node.data_type == "FLOAT", "raw compare data_type mismatch")
    check(node.operation == "GREATER_THAN", "raw compare operation mismatch")
    check(abs(float(node.inputs["B"].default_value) - 0.5) < 1e-6, "literal default was not assigned")
    check(_contract(node, "A")["mode"] == raw_nodes.INPUT_SINGLE_LINK, "linked input contract missing")
    check(_contract(node, "B")["mode"] == raw_nodes.INPUT_LITERAL, "literal input contract missing")
    check(abs(float(_contract(node, "B")["default"]) - 0.5) < 1e-6, "literal default metadata mismatch")
    check("Bool" not in [item.name for item in group.interface.items_tree], "Bool became an implicit input")


def test_raw_node_result_attribute_and_subscript_outputs():
    group = compile_group('''
sep = node(
    "ShaderNodeSeparateXYZ",
    inputs={"Vector": (1.0, 2.0, 3.0)},
    outputs={"X": Float, "Y": Float},
)
out = sep["X"] + sep.Y
output("out", out)
''', "NFTest_raw_multi_output")
    node = _one_node(group, "ShaderNodeSeparateXYZ")
    check(raw_nodes.is_raw_node(node), "separate xyz raw metadata missing")
    check(_contract(node, "Vector")["mode"] == raw_nodes.INPUT_LITERAL, "vector default contract missing")


def test_raw_join_geometry_multi_input():
    group = compile_group('''
a = cube(1)
b = cube(2)
c = cube(3)
geo = node(
    "GeometryNodeJoinGeometry",
    inputs={"Geometry": [a, b, c]},
    output="Geometry",
    typ=Geometry,
)
output("Geometry", geo)
''', "NFTest_raw_join_geometry")
    node = _one_node(group, "GeometryNodeJoinGeometry")
    check(_contract(node, "Geometry")["mode"] == raw_nodes.INPUT_MULTI_LINK, "multi-link contract missing")
    check(_contract(node, "Geometry")["links"] == 3, "multi-link contract count mismatch")
    check(_incoming_count(group, node, "Geometry") == 3, "multi-input links were not created")


def test_removed_compare_wrappers_fail_but_operators_compile():
    compile_group('''
a = position().z > 0.25
b = position().x <= 1.0
out = a and b
output("out", out)
''', "NFTest_compare_operators_after_wrapper_removal")
    for index, source in enumerate((
        'a = greater_than(position().z, 0.25)\noutput("a", a)',
        'a = less_equal(position().x, 1.0)\noutput("a", a)',
    )):
        try:
            compile_group(source, f"NFTest_removed_compare_wrapper_{index}")
        except CompileError:
            pass
        else:
            raise AssertionError("removed compare wrapper compiled")


def test_raw_node_update_cutover_preserves_props_defaults_and_links():
    initial = '''
mask = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "GREATER_THAN"},
    inputs={"A": position().z, "B": 0.25},
    output="Result",
    typ=Bool,
)
output("mask", mask)
'''
    group = compile_group(initial, "NFTest_raw_update")
    updated = '''
mask = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "LESS_THAN"},
    inputs={"A": position().x, "B": 0.75},
    output="Result",
    typ=Bool,
)
output("mask", mask)
'''
    compiler.update_expression_group(group, updated)
    node = _one_node(group, "FunctionNodeCompare")
    check(raw_nodes.is_raw_node(node), "updated raw node lost metadata")
    check(_raw_schema_version(node) == raw_nodes.RAW_SCHEMA_VERSION, "v2 update changed metadata generation")
    check(node.operation == "LESS_THAN", "updated raw compare operation mismatch")
    check(abs(float(node.inputs["B"].default_value) - 0.75) < 1e-6, "updated literal default mismatch")
    check(_contract(node, "B")["mode"] == raw_nodes.INPUT_LITERAL, "updated literal mode missing")
    check(_incoming_count(group, node, "A") == 1, "updated linked input mismatch")


def test_raw_join_multi_input_update_cutover_preserves_links():
    group = compile_group('''
a = cube(1)
b = cube(2)
geo = node("GeometryNodeJoinGeometry", inputs={"Geometry": [a, b]}, output="Geometry", typ=Geometry)
output("Geometry", geo)
''', "NFTest_raw_join_update")
    compiler.update_expression_group(group, '''
a = cube(1)
b = cube(2)
c = cube(3)
geo = node("GeometryNodeJoinGeometry", inputs={"Geometry": [a, b, c]}, output="Geometry", typ=Geometry)
output("Geometry", geo)
''')
    node = _one_node(group, "GeometryNodeJoinGeometry")
    check(_incoming_count(group, node, "Geometry") == 3, "cutover did not preserve raw multi-input links")
    check(_contract(node, "Geometry")["links"] == 3, "cutover did not preserve multi-input contract")


def test_raw_cutover_literal_default_mismatch_rolls_back(monkeypatch):
    group = compile_group('''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "GREATER_THAN"}, inputs={"A": position().z, "B": 0.25}, output="Result", typ=Bool)
output("mask", mask)
''', "NFTest_raw_rollback")
    original = raw_nodes.copy_raw_node_properties

    def corrupt_literal_default(src_node, dst_node):
        original(src_node, dst_node)
        if getattr(dst_node, "bl_idname", None) == "FunctionNodeCompare":
            try:
                expected_b = _contract(src_node, "B").get("default")
            except Exception:
                expected_b = None
            if abs(float(expected_b) - 0.75) < 1e-6:
                dst_node.inputs["B"].default_value = 0.0

    monkeypatch.setattr(raw_nodes, "copy_raw_node_properties", corrupt_literal_default)
    try:
        compiler.update_expression_group(group, '''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "LESS_THAN"}, inputs={"A": position().x, "B": 0.75}, output="Result", typ=Bool)
output("mask", mask)
''')
    except Exception:
        pass
    else:
        raise AssertionError("strict raw cutover mismatch did not fail")
    node = _one_node(group, "FunctionNodeCompare")
    check(node.operation == "GREATER_THAN", "rollback did not restore previous raw compare operation")
    check(abs(float(node.inputs["B"].default_value) - 0.25) < 1e-6, "rollback did not restore previous literal default")


def test_raw_cutover_identifier_corruption_rolls_back(monkeypatch):
    # Corrupted v2 physical identity must fail closed and restore the authoritative graph.
    group = compile_group('''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "GREATER_THAN"}, inputs={"A": position().z, "B": 0.25}, output="Result", typ=Bool)
output("mask", mask)
''', "NFTest_raw_identifier_rollback")
    original = raw_nodes.copy_raw_node_properties

    def corrupt_identifier(src_node, dst_node):
        original(src_node, dst_node)
        if getattr(dst_node, "bl_idname", None) != "FunctionNodeCompare":
            return
        try:
            expected_b = _contract(src_node, "B").get("default")
        except Exception:
            expected_b = None
        if abs(float(expected_b) - 0.75) >= 1e-6:
            return
        outputs = json.loads(dst_node[raw_nodes.RAW_OUTPUTS_JSON_PROP])
        outputs[0]["identifier"] = "__nodeforge_missing_identifier__"
        dst_node[raw_nodes.RAW_OUTPUTS_JSON_PROP] = json.dumps(
            outputs, separators=(",", ":"), sort_keys=True
        )

    monkeypatch.setattr(raw_nodes, "copy_raw_node_properties", corrupt_identifier)
    try:
        compiler.update_expression_group(group, '''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "LESS_THAN"}, inputs={"A": position().x, "B": 0.75}, output="Result", typ=Bool)
output("mask", mask)
''')
    except Exception:
        pass
    else:
        raise AssertionError("corrupted v2 identifier did not fail cutover")

    node = _one_node(group, "FunctionNodeCompare")
    check(_raw_schema_version(node) == raw_nodes.RAW_SCHEMA_VERSION, "identifier rollback changed metadata generation")
    check(node.operation == "GREATER_THAN", "identifier rollback did not restore previous raw compare operation")
    check(abs(float(node.inputs["B"].default_value) - 0.25) < 1e-6, "identifier rollback did not restore previous default")


def test_raw_node_rejects_unsupported_or_mismatched_runtime_socket_types():
    bad_sources = [
        'x = node("FunctionNodeInputString", output="String", typ=Float)\noutput("x", x)',
        'x = node("ShaderNodeValue", output="Value", typ=Geometry)\noutput("Geometry", x)',
        'x = node("ShaderNodeSeparateXYZ", inputs={"Vector": position().x}, outputs={"X": Float})\noutput("x", x.X)',
    ]
    for index, source in enumerate(bad_sources):
        expect_compile_error(source, f"NFTest_raw_socket_type_error_{index}")


def test_raw_node_error_fixtures_are_controlled_compile_errors():
    bad_sources = [
        'x = node("NoSuchNode", output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", props={"no_such_prop": 1}, output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", props={"operation": "NOPE"}, output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", inputs={"Nope": 1}, output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", output="Nope", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", inputs={"A": [1, 2]}, output="Result", typ=Bool)\noutput("x", x)',
        'x = node("FunctionNodeCompare", inputs={"A": []}, output="Result", typ=Bool)\noutput("x", x)',
        'Bool = 1\noutput("x", 1)',
        'output("x", Bool)',
        'r = node("ShaderNodeSeparateXYZ", outputs={"X": Float})\noutput("x", r)',
        'r = node("ShaderNodeSeparateXYZ", outputs={"X": Float})\ny = r + 1\noutput("y", y)',
        'x = equal(True, False)\noutput("x", x)',
    ]
    for index, source in enumerate(bad_sources):
        expect_compile_error(source, f"NFTest_raw_error_{index}")


def test_stored_raw_named_outputs_project_later_without_duplicate_raw_node():
    """IRBody stores named raw leaves by BindingId and later selections reuse one raw node."""
    group = compile_group(
        '''
parts = node("ShaderNodeSeparateXYZ", inputs={"Vector": position()}, outputs={"X": Float, "Y": Float})
output("X", parts.X)
output("Y", parts["Y"])
''',
        "NFTest_raw_stored_named_outputs",
    )
    separate = [node for node in group.nodes if node.bl_idname == "ShaderNodeSeparateXYZ"]
    check(len(separate) == 1, f"stored named-output projections duplicated raw node: {len(separate)}")
    outputs = [item.name for item in group.interface.items_tree if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "OUTPUT"]
    check(outputs == ["X", "Y"], f"stored named-output interface changed: {outputs}")
    bpy.data.node_groups.remove(group)


def test_raw_math_duplicate_inputs_use_addressable_positions():
    # Real Math duplicate-name operands are selectable without internal identifiers.
    group = compile_group('''
value = node(
    "ShaderNodeMath",
    props={"operation": "ADD"},
    inputs={0: 1.25, 1: 2.5},
    output="Value",
    typ=Float,
)
output("value", value)
''', "NFTest_raw_math_positions")
    node = _one_node(group, "ShaderNodeMath")
    first = raw_nodes.resolve_socket(node.inputs, 0, direction="input", context="test")
    second = raw_nodes.resolve_socket(node.inputs, 1, direction="input", context="test")
    check(first.name == "Value" and second.name == "Value", "Math ADD did not expose duplicate Value operands")
    check(first.identifier != second.identifier, "Math duplicate operands did not have distinct identifiers")
    check(abs(float(first.default_value) - 1.25) < 1e-6, "Math first positional default mismatch")
    check(abs(float(second.default_value) - 2.5) < 1e-6, "Math second positional default mismatch")


def test_raw_vector_math_scale_position_uses_addressable_ordinal_not_physical_index():
    # Vector Math SCALE position 1 resolves Scale after unavailable sockets are filtered.
    group = compile_group('''
value = node(
    "ShaderNodeVectorMath",
    props={"operation": "SCALE"},
    inputs={0: (1.0, 2.0, 3.0), 1: 2.0},
    output="Vector",
    typ=Vector,
)
output("value", value)
''', "NFTest_raw_vector_scale_positions")
    node = _one_node(group, "ShaderNodeVectorMath")
    vector = raw_nodes.resolve_socket(node.inputs, 0, direction="input", context="test")
    scale = raw_nodes.resolve_socket(node.inputs, 1, direction="input", context="test")
    check(vector.name == "Vector", f"Vector Math SCALE position 0 resolved {vector.name!r}")
    check(scale.name == "Scale", f"Vector Math SCALE position 1 resolved {scale.name!r}")
    physical_index = next(index for index, socket in enumerate(node.inputs) if _same_blender_ref(socket, scale))
    check(physical_index != 1, "Vector Math SCALE regression did not distinguish addressable ordinal from raw index")
    check(abs(float(scale.default_value) - 2.0) < 1e-6, "Vector Math SCALE default mismatch")


def test_raw_integer_and_boolean_math_duplicate_operands_use_positions():
    # Integer/Boolean Math duplicate operand families share the positional contract.
    int_group = compile_group('''
value = node("FunctionNodeIntegerMath", props={"operation": "ADD"}, inputs={0: 2, 1: 3}, output="Value", typ=Int)
output("value", value)
''', "NFTest_raw_integer_math_positions")
    int_node = _one_node(int_group, "FunctionNodeIntegerMath")
    int_a = raw_nodes.resolve_socket(int_node.inputs, 0, direction="input", context="test")
    int_b = raw_nodes.resolve_socket(int_node.inputs, 1, direction="input", context="test")
    check(int_a.name == int_b.name == "Value", "Integer Math duplicate Value operands changed")
    check(int_a.identifier != int_b.identifier, "Integer Math duplicate operands share identifier")

    bool_group = compile_group('''
value = node("FunctionNodeBooleanMath", props={"operation": "AND"}, inputs={0: True, 1: False}, output="Boolean", typ=Bool)
output("value", value)
''', "NFTest_raw_boolean_math_positions")
    bool_node = _one_node(bool_group, "FunctionNodeBooleanMath")
    bool_a = raw_nodes.resolve_socket(bool_node.inputs, 0, direction="input", context="test")
    bool_b = raw_nodes.resolve_socket(bool_node.inputs, 1, direction="input", context="test")
    check(bool_a.name == bool_b.name == "Boolean", "Boolean Math duplicate Boolean operands changed")
    check(bool_a.identifier != bool_b.identifier, "Boolean Math duplicate operands share identifier")


def test_raw_contextual_id_selects_identifier_but_plain_string_does_not():
    # ID(...) is exact identifier syntax while plain string remains socket.name-only.
    group = compile_group('''
value = node(
    "ShaderNodeMath",
    props={"operation": "ADD"},
    inputs={ID("Value"): 1.0, ID("Value_001"): 2.0},
    output=ID("Value"),
    typ=Float,
)
output("value", value)
''', "NFTest_raw_identifier_selector")
    node = _one_node(group, "ShaderNodeMath")
    check(_contract(node, ("identifier", "Value"))["mode"] == raw_nodes.INPUT_LITERAL, "ID first operand missing")
    check(_contract(node, ("identifier", "Value_001"))["mode"] == raw_nodes.INPUT_LITERAL, "ID second operand missing")

    expect_compile_error('''
value = node("ShaderNodeMath", props={"operation": "ADD"}, inputs={"Value_001": 2.0}, output="Value", typ=Float)
output("value", value)
''', "NFTest_raw_plain_string_not_identifier")


def test_raw_single_and_named_outputs_accept_positional_and_id_selectors():
    # Single and named results preserve source aliases separately from selectors.
    single = compile_group('''
value = node("ShaderNodeValue", output=0, typ=Float)
output("value", value)
''', "NFTest_raw_single_position_output")
    check(_one_node(single, "ShaderNodeValue") is not None, "single positional output did not compile")

    named = compile_group('''
parts = node(
    "ShaderNodeSeparateXYZ",
    inputs={"Vector": (1.0, 2.0, 3.0)},
    outputs={"left": (0, Float), "middle": (ID("Y"), Float)},
)
output("left", parts.left)
output("middle", parts.middle)
''', "NFTest_raw_named_selector_outputs")
    node = _one_node(named, "ShaderNodeSeparateXYZ")
    spec = raw_nodes.raw_node_spec(node)
    check([ref.identifier for ref in spec["outputs"]] == ["X", "Y"], "named output physical refs changed")


def test_raw_v1_successful_update_writes_v2_authoritative_metadata():
    # Legacy name-only metadata remains readable, while fresh success becomes schema v2.
    group = compile_group('''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "GREATER_THAN"}, inputs={"A": position().z, "B": 0.25}, output="Result", typ=Bool)
output("mask", mask)
''', "NFTest_raw_v1_upgrade")
    node = _one_node(group, "FunctionNodeCompare")
    _downgrade_raw_metadata_to_v1(node)

    compiler.update_expression_group(group, '''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "LESS_THAN"}, inputs={"A": position().x, "B": 0.75}, output="Result", typ=Bool)
output("mask", mask)
''')
    node = _one_node(group, "FunctionNodeCompare")
    check(_raw_schema_version(node) == raw_nodes.RAW_SCHEMA_VERSION, "successful v1 replacement did not become v2")
    check(node.operation == "LESS_THAN", "v1 update did not install fresh replacement")


def test_raw_v1_failed_identifierless_replacement_rolls_back_v1(monkeypatch):
    # Simulate a fresh socket that cannot satisfy the v2 durable-identifier gate.
    group = compile_group('''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "GREATER_THAN"}, inputs={"A": position().z, "B": 0.25}, output="Result", typ=Bool)
output("mask", mask)
''', "NFTest_raw_v1_identifierless_rollback")
    node = _one_node(group, "FunctionNodeCompare")
    _downgrade_raw_metadata_to_v1(node)

    def fail_fresh_capture(socket, *, direction, context):
        raise CompileError(f"{context}: declared raw socket cannot be persisted safely without a Blender identifier")

    monkeypatch.setattr(raw_nodes, "_capture_physical_socket_ref", fail_fresh_capture)
    try:
        compiler.update_expression_group(group, '''
mask = node("FunctionNodeCompare", props={"data_type": "FLOAT", "operation": "LESS_THAN"}, inputs={"A": position().x, "B": 0.75}, output="Result", typ=Bool)
output("mask", mask)
''')
    except CompileError:
        pass
    else:
        raise AssertionError("identifier-less fresh replacement did not fail")

    node = _one_node(group, "FunctionNodeCompare")
    check(_raw_schema_version(node) == 1, "failed v1 replacement did not restore v1 metadata generation")
    check(node.operation == "GREATER_THAN", "failed v1 replacement did not restore prior node state")
