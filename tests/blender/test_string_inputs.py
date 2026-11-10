"""Blender integration coverage for runtime String values and attribute-name sockets."""

from helpers import *

import json
import tempfile
from pathlib import Path

from NodeForge import packages
from NodeForge.semantic.constants import TYPE_STRING
from NodeForge.blender.interface import _get_group_input_defaults


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
def nf_fixture(name: String['Attribute'] = 'Weight_A'):
    literal = "Weight_B"
    runtime = name
    literal = literal
    return (runtime, literal,)
''', "NFTest_string_interface")
    iface = _interface_socket(group, "Attribute")
    check(iface.socket_type == "NodeSocketString", "input_string did not create NodeSocketString")
    check(iface.default_value == "Weight_A", f"input_string default mismatch: {iface.default_value!r}")
    defaults = _get_group_input_defaults(group)
    default_key = ("Attribute", "NodeSocketString", 0)
    check(default_key in defaults, f"stored String default key mismatch: {defaults}")
    check(defaults[default_key]["nf_type"] == "STRING", f"stored String type mismatch: {defaults}")
    check(defaults[default_key]["default"] == "Weight_A", f"stored String default mismatch: {defaults}")
    check(_interface_socket(group, "Runtime", "OUTPUT").socket_type == "NodeSocketString", "runtime String output type mismatch")
    check(_interface_socket(group, "Literal", "OUTPUT").socket_type == "NodeSocketString", "literal String output type mismatch")
    string_nodes = [node for node in group.nodes if node.bl_idname == "FunctionNodeInputString"]
    check(any(node.string == "Weight_B" for node in string_nodes), "string literal did not lower to a runtime String node")

    empty = compile_group('''
def nf_fixture(x: String['Empty']):
    empty = x
    return (empty,)
''', "NFTest_string_empty_default")
    check(_interface_socket(empty, "Empty").default_value == "", "input_string default should allow an empty string")


def test_raw_node_string_output_type_is_supported():
    group = compile_group('''
def nf_fixture():
    x = node(
        "FunctionNodeInputString",
        props={"string": "Weight_A"},
        output="String",
        typ=String,
    )
    text = x
    return (text,)
''', "NFTest_raw_string_output")
    raw = _one_node(group, "FunctionNodeInputString")
    check(raw.string == "Weight_A", "raw String node property mismatch")
    check(_interface_socket(group, "Text", "OUTPUT").socket_type == "NodeSocketString", "raw String output interface mismatch")


def test_runtime_string_wires_raw_named_attribute_and_store_named_attribute():
    group = compile_group('''
def nf_fixture(geo: Geometry['Geometry'], name: String['Attribute'] = 'Weight_A'):
    weight = node(
        "GeometryNodeInputNamedAttribute",
        props={"data_type": "FLOAT"},
        inputs={"Name": name},
        output="Attribute",
        typ=Float,
    )
    result = store_named_attribute(geo, name, weight, domain="POINT", type="FLOAT")
    result = result
    return (result,)
''', "NFTest_string_named_attribute")
    named = _one_node(group, "GeometryNodeInputNamedAttribute")
    store = _one_node(group, "GeometryNodeStoreNamedAttribute")
    check(named.inputs["Name"].is_linked, "runtime String did not wire Named Attribute Name")
    check(store.inputs["Name"].is_linked, "runtime String did not wire Store Named Attribute Name")
    check(named.inputs["Name"].links[0].from_socket.bl_idname == "NodeSocketString", "Named Attribute Name source is not String")
    check(store.inputs["Name"].links[0].from_socket.bl_idname == "NodeSocketString", "Store Named Attribute Name source is not String")


def test_top_level_store_accepts_runtime_string_name():
    group = bpy.data.node_groups.new("NFTest_string_statement_store", "GeometryNodeTree")
    compiler.compose_source_initial('''
name = node("FunctionNodeInputString", props={"string": "Weight_A"}, output="String", typ=String)
geometry = store_named_attribute(cube(1.0), name, position().x, domain="POINT", type="FLOAT")
''', group)
    store = _one_node(group, "GeometryNodeStoreNamedAttribute")
    check(store.inputs["Name"].is_linked, "store() did not wire runtime String attribute name")


def test_runtime_string_if_uses_string_switch():
    group = compile_group('''
def nf_fixture(flag: Bool['Flag'], a: String['A'] = 'Weight_A', b: String['B'] = 'Weight_B'):
    name = a if flag else b
    name = name
    return (name,)
''', "NFTest_string_switch")
    switch = _one_node(group, "GeometryNodeSwitch")
    check(switch.input_type == "STRING", f"runtime String switch type mismatch: {switch.input_type}")
    check(switch.outputs[0].bl_idname == "NodeSocketString", "runtime String switch output socket mismatch")

    statement_group = compile_group('''
def nf_fixture(flag: Bool['Flag'], name: String['Name'] = 'Initial'):
    if flag:
        name = "Weight_A"
    else:
        name = "Weight_B"
    name = name
    return (name,)
''', "NFTest_string_runtime_if_statement")
    statement_switch = _one_node(statement_group, "GeometryNodeSwitch")
    check(statement_switch.input_type == "STRING", "runtime if statement did not use a String Switch")






def test_string_default_update_preserves_override_and_applies_new_default():
    before = '''
def nf_fixture(name: String['Name'] = 'A'):
    name = name
    return (name,)
'''
    after = '''
def nf_fixture(name: String['Name'] = 'B'):
    name = name
    return (name,)
'''
    group = compile_group(before, "NFTest_string_update")
    wrapper = bpy.data.node_groups.new("NFTest_string_update_wrapper", "GeometryNodeTree")
    wrapper.interface.new_socket(name="Name", in_out="OUTPUT", socket_type="NodeSocketString")
    group_output = wrapper.nodes.new("NodeGroupOutput")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    wrapper.links.new(group_node.outputs[0], group_output.inputs["Name"])

    compiler.update_expression_group(group, after)
    check(group_node.inputs["Name"].default_value == "B", "new String script default did not reach non-overridden instance")

    group_node.inputs["Name"].default_value = "User"
    compiler.update_expression_group(group, before)
    check(group_node.inputs["Name"].default_value == "User", "String user override was not preserved across update")


def test_string_type_errors_and_compile_time_configuration_remain_strict():
    cases = (
        ('''
def nf_fixture(x: String['X'] = 1):
    x = x
    return (x,)
''', "string default"),
        ('''
def nf_fixture(geo: Geometry['Geometry'], name: Float['Name']):
    geo = store_named_attribute(geo, name, position().x)
    geometry_out = geo
    return (geometry_out,)
''', "store name type"),
        ('''
def nf_fixture(geo: Geometry['Geometry'], name: String['Name']):
    geo = store_named_attribute(geo, "x", name)
    geometry_out = geo
    return (geometry_out,)
''', "String attribute value"),
        ('''
def nf_fixture(geo: Geometry['Geometry'], domain: String['Domain']):
    geo = store_named_attribute(geo, "x", position().x, domain=domain)
    geometry_out = geo
    return (geometry_out,)
''', "runtime domain"),
        ('''
def nf_fixture(name: String['Name']):
    x = node("FunctionNodeInputString", output="String", typ=Float)
    x = x
    return (x,)
''', "raw output mismatch"),
    )
    for index, (source, label) in enumerate(cases):
        expect_compile_error(source, f"NFTest_string_error_{index}")
