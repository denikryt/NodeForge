"""Blender integration coverage for runtime String values and attribute-name sockets."""

from helpers import *

import json
import tempfile
from pathlib import Path

from NodeForge import packages
from NodeForge.constants import TYPE_STRING
from NodeForge.interface import _get_group_input_defaults


def _one_node(group, bl_idname):
    nodes = [node for node in group.nodes if node.bl_idname == bl_idname]
    check(len(nodes) == 1, f"expected one {bl_idname}, found {len(nodes)}")
    return nodes[0]


def _interface_socket(group, name, in_out="INPUT"):
    return next(
        item for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET"
        and getattr(item, "in_out", None) == in_out
        and item.name == name
    )


def _write_package_manifest(root, package_id):
    (root / "nodeforge_package.json").write_text(json.dumps({
        "schema_version": 1,
        "id": package_id,
        "name": package_id,
        "version": "1.0.0",
        "author": "Tests",
        "description": "Runtime String fixture",
        "nodeforge_min_version": "0.49.47",
        "nodeforge_max_version": None,
        "contents": {"functions": "functions"},
        "permissions": {"python": False},
    }), encoding="utf-8")


def test_input_string_interface_default_literal_and_output():
    group = compile_group('''
name = input_string("Attribute", default="Weight_A")
literal = "Weight_B"
output("Runtime", name)
output("Literal", literal)
''', "NFTest_string_interface")
    iface = _interface_socket(group, "Attribute")
    check(iface.socket_type == "NodeSocketString", "input_string did not create NodeSocketString")
    check(iface.default_value == "Weight_A", f"input_string default mismatch: {iface.default_value!r}")
    defaults = _get_group_input_defaults(group)
    check(defaults["Attribute"] == {"type": TYPE_STRING, "default": "Weight_A"}, f"stored String default mismatch: {defaults}")
    check(_interface_socket(group, "Runtime", "OUTPUT").socket_type == "NodeSocketString", "runtime String output type mismatch")
    check(_interface_socket(group, "Literal", "OUTPUT").socket_type == "NodeSocketString", "literal String output type mismatch")
    string_nodes = [node for node in group.nodes if node.bl_idname == "FunctionNodeInputString"]
    check(any(node.string == "Weight_B" for node in string_nodes), "string literal did not lower to a runtime String node")

    empty = compile_group('x = input_string("Empty")\noutput("Empty", x)', "NFTest_string_empty_default")
    check(_interface_socket(empty, "Empty").default_value == "", "input_string default should allow an empty string")


def test_raw_node_string_output_type_is_supported():
    group = compile_group('''
x = node(
    "FunctionNodeInputString",
    props={"string": "Weight_A"},
    output="String",
    typ=String,
)
output("Text", x)
''', "NFTest_raw_string_output")
    raw = _one_node(group, "FunctionNodeInputString")
    check(raw.string == "Weight_A", "raw String node property mismatch")
    check(_interface_socket(group, "Text", "OUTPUT").socket_type == "NodeSocketString", "raw String output interface mismatch")


def test_runtime_string_wires_raw_named_attribute_and_store_named_attribute():
    group = compile_group('''
geo = input_geometry("Geometry")
name = input_string("Attribute", default="Weight_A")
weight = node(
    "GeometryNodeInputNamedAttribute",
    props={"data_type": "FLOAT"},
    inputs={"Name": name},
    output="Attribute",
    typ=Float,
)
result = store_named_attribute(geo, name, weight, domain="POINT", type="FLOAT")
output("Result", result)
''', "NFTest_string_named_attribute")
    named = _one_node(group, "GeometryNodeInputNamedAttribute")
    store = _one_node(group, "GeometryNodeStoreNamedAttribute")
    check(named.inputs["Name"].is_linked, "runtime String did not wire Named Attribute Name")
    check(store.inputs["Name"].is_linked, "runtime String did not wire Store Named Attribute Name")
    check(named.inputs["Name"].links[0].from_socket.bl_idname == "NodeSocketString", "Named Attribute Name source is not String")
    check(store.inputs["Name"].links[0].from_socket.bl_idname == "NodeSocketString", "Store Named Attribute Name source is not String")


def test_top_level_store_accepts_runtime_string_name():
    group = compile_group('''
name = input_string("Attribute", default="Weight_A")
store(name, position().x, domain="POINT", type="FLOAT")
''', "NFTest_string_statement_store")
    store = _one_node(group, "GeometryNodeStoreNamedAttribute")
    check(store.inputs["Name"].is_linked, "store() did not wire runtime String attribute name")


def test_runtime_string_if_uses_string_switch():
    group = compile_group('''
flag = input_bool("Flag")
a = input_string("A", default="Weight_A")
b = input_string("B", default="Weight_B")
name = a if flag else b
output("Name", name)
''', "NFTest_string_switch")
    switch = _one_node(group, "GeometryNodeSwitch")
    check(switch.input_type == "STRING", f"runtime String switch type mismatch: {switch.input_type}")
    check(switch.outputs[0].bl_idname == "NodeSocketString", "runtime String switch output socket mismatch")

    statement_group = compile_group('''
flag = input_bool("Flag")
name = input_string("Name", default="Initial")
if flag:
    name = "Weight_A"
else:
    name = "Weight_B"
output("Name", name)
''', "NFTest_string_runtime_if_statement")
    statement_switch = _one_node(statement_group, "GeometryNodeSwitch")
    check(statement_switch.input_type == "STRING", "runtime if statement did not use a String Switch")


def test_local_function_accepts_runtime_and_constant_string_arguments():
    group = compile_group('''
def identity(name: String):
    return name

source = input_string("Source", default="Weight_A")
runtime = identity(source)
literal = identity("Weight_B")
output("Runtime", runtime)
output("Literal", literal)
''', "NFTest_string_local_function")
    helper = next(
        node.node_tree for node in group.nodes
        if node.bl_idname == "GeometryNodeGroup"
        and getattr(node.node_tree, "get", lambda *_: None)("nodeforge_local_function_name") == "identity"
    )
    check(_interface_socket(helper, "name").socket_type == "NodeSocketString", "local function String parameter socket mismatch")
    call_nodes = [node for node in group.nodes if node.bl_idname == "GeometryNodeGroup" and node.node_tree is helper]
    check(len(call_nodes) == 2, f"expected two String local-function calls, got {len(call_nodes)}")
    check(any(node.inputs["name"].is_linked for node in call_nodes), "runtime String local-function argument was not linked")
    check(any(node.inputs["name"].default_value == "Weight_B" for node in call_nodes), "constant String local-function argument was not assigned as default")


def test_packaged_library_function_accepts_runtime_and_constant_string_arguments():
    with tempfile.TemporaryDirectory() as tmp:
        tmp = Path(tmp)
        root = tmp / "vendor.string"
        functions = root / "functions"
        functions.mkdir(parents=True)
        _write_package_manifest(root, "vendor.string")
        (functions / "string_identity.nf").write_text(
            'value = input_string("Text", default="Default")\noutput("Text", value)\n',
            encoding="utf-8",
        )
        packages.set_packages_dir_for_tests(tmp / "inventory")
        try:
            packages.install_package_directory(root, allow_python=False)
            group = compile_group('''
from functions import string_identity
source = input_string("Source", default="Weight_A")
runtime = string_identity(source)
literal = string_identity("Weight_B")
output("Runtime", runtime)
output("Literal", literal)
''', "NFTest_string_library_function")
            calls = [node for node in group.nodes if node.bl_idname == "GeometryNodeGroup"]
            check(len(calls) == 2, f"expected two String library calls, got {len(calls)}")
            check(any(node.inputs["Text"].is_linked for node in calls), "runtime String library argument was not linked")
            check(any(node.inputs["Text"].default_value == "Weight_B" for node in calls), "constant String library argument was not assigned as default")
            check(all(node.outputs["Text"].bl_idname == "NodeSocketString" for node in calls), "library String output type mismatch")
        finally:
            packages.set_packages_dir_for_tests(None)
            packages.invalidate_caches()


def test_string_default_update_preserves_override_and_applies_new_default():
    before = 'name = input_string("Name", default="A")\noutput("Name", name)'
    after = 'name = input_string("Name", default="B")\noutput("Name", name)'
    group = compile_group(before, "NFTest_string_update")
    wrapper = bpy.data.node_groups.new("NFTest_string_update_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="Name", in_out="OUTPUT", socket_type="NodeSocketString")
    group_output = wrapper.nodes.new("NodeGroupOutput")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    wrapper.links.new(group_node.outputs["Name"], group_output.inputs["Name"])

    state = compiler._capture_node_external_state(wrapper, group_node)
    compiler.update_expression_group(group, after)
    compiler._restore_node_external_state(wrapper, group_node, state)
    check(group_node.inputs["Name"].default_value == "B", "new String script default did not reach non-overridden instance")

    group_node.inputs["Name"].default_value = "User"
    state = compiler._capture_node_external_state(wrapper, group_node)
    compiler.update_expression_group(group, before)
    compiler._restore_node_external_state(wrapper, group_node, state)
    check(group_node.inputs["Name"].default_value == "User", "String user override was not preserved across update")


def test_string_type_errors_and_compile_time_configuration_remain_strict():
    cases = (
        ('x = input_string("X", default=1)\noutput("X", x)', "string default"),
        ('geo = input_geometry("Geometry")\nname = input_float("Name")\ngeo = store_named_attribute(geo, name, position().x)\noutput("Geometry Out", geo)', "store name type"),
        ('geo = input_geometry("Geometry")\nname = input_string("Name")\ngeo = store_named_attribute(geo, "x", name)\noutput("Geometry Out", geo)', "String attribute value"),
        ('geo = input_geometry("Geometry")\ndomain = input_string("Domain")\ngeo = store_named_attribute(geo, "x", position().x, domain=domain)\noutput("Geometry Out", geo)', "runtime domain"),
        ('name = input_string("Name")\nx = node("FunctionNodeInputString", output="String", typ=Float)\noutput("X", x)', "raw output mismatch"),
    )
    for index, (source, label) in enumerate(cases):
        expect_compile_error(source, f"NFTest_string_error_{index}")
