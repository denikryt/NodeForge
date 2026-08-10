"""Blender integration coverage for native NodeForge interface panels."""

from helpers import *
from NodeForge.values import make_value
from NodeForge.constants import TYPE_FLOAT


def _interface_inputs(group):
    """Return input interface sockets keyed by display name."""
    return {
        item.name: item
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "INPUT"
    }


def _interface_panels(group):
    """Return real interface panels, excluding Blender's unnamed synthetic root."""
    return [
        item
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "PANEL" and getattr(item, "name", "")
    ]


def _parent_name(item):
    """Return a readable interface parent name across Blender root representations."""
    return getattr(getattr(item, "parent", None), "name", "") or ""


def _panel_children(group, panel_name):
    """Return direct child item names for one native interface panel."""
    return [item.name for item in group.interface.items_tree if _parent_name(item) == panel_name]


def _expect_message(source, expected, name):
    """Compile source and require a controlled CompileError containing expected text."""
    try:
        compiler.create_expression_group(source, name)
    except CompileError as exc:
        check(expected in str(exc), f"{name}: expected {expected!r}, got {exc!s}")
        return
    raise AssertionError(f"{name} did not raise CompileError")


def test_interface_panel_basic_grouping_defaults_and_real_group_node():
    source = '''
a = input_float("A", default=1.0)
b = input_int("B", default=2)
panel([a, b], name="Main")
output("A", a)
'''
    group = compile_group(source, "NFTest_panel_basic")
    panels = _interface_panels(group)
    check([panel.name for panel in panels] == ["Main"], f"unexpected panels: {[p.name for p in panels]}")
    inputs = _interface_inputs(group)
    check(_parent_name(inputs["A"]) == "Main", "A is not parented to Main")
    check(_parent_name(inputs["B"]) == "Main", "B is not parented to Main")
    check(_panel_children(group, "Main") == ["A", "B"], f"wrong Main order: {_panel_children(group, 'Main')}")
    check(abs(float(inputs["A"].default_value) - 1.0) < 1e-6, "A default changed by panel grouping")
    check(int(inputs["B"].default_value) == 2, "B default changed by panel grouping")

    wrapper = bpy.data.node_groups.new("NFTest_panel_basic_wrapper", "GeometryNodeTree")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    check(group_node.bl_idname == "GeometryNodeGroup", "fixture is not a real GeometryNodeGroup")
    check(group_node.node_tree is group or group_node.node_tree == group, "group node is not backed by panelized group")
    check([socket.name for socket in group_node.inputs] == ["A", "B"], "real group-node input order changed")


def test_interface_panel_multiple_panels_ungrouped_and_collapsed():
    source = '''
loose = input_float("Loose")
a = input_float("A")
b = input_float("B")
c = input_float("C")
panel([b, a], name="First")
panel([c], name="Second", collapsed=True)
output("Loose", loose)
'''
    group = compile_group(source, "NFTest_panel_multiple")
    panels = _interface_panels(group)
    check([panel.name for panel in panels] == ["First", "Second"], f"wrong panel order: {[p.name for p in panels]}")
    check(bool(panels[1].default_closed) is True, "collapsed=True did not set default_closed")
    inputs = _interface_inputs(group)
    check(_parent_name(inputs["Loose"]) == "", "ungrouped input did not remain at root")
    check(_panel_children(group, "First") == ["B", "A"], f"wrong First order: {_panel_children(group, 'First')}")
    check(_panel_children(group, "Second") == ["C"], "Second membership is wrong")


def test_interface_panel_accepts_implicit_external_input():
    source = '''
result = source_value * 2
panel([source_value], name="Inputs")
output("Value", result)
'''
    group = compile_group(source, "NFTest_panel_implicit")
    inputs = _interface_inputs(group)
    check("source_value" in inputs, "implicit external input was not created")
    check(_parent_name(inputs["source_value"]) == "Inputs", "implicit external input was not grouped")
    check("panel" not in inputs, "panel helper was incorrectly inferred as an implicit input")


def test_interface_panel_rejects_invalid_members_and_arguments():
    cases = [
        ('value = 1\npanel([value], name="Bad")\noutput("Value", value)', "panel() item value is not a group input"),
        ('x = input_float("X")\npanel([x * 2], name="Bad")\noutput("X", x)', "panel() items must be simple input variable names"),
        ('x = input_float("X")\npanel(x, name="Bad")\noutput("X", x)', "panel() first argument must be a list or tuple"),
        ('x = input_float("X")\npanel([], name="Bad")\noutput("X", x)', "panel() requires at least one input"),
        ('x = input_float("X")\npanel([x])\noutput("X", x)', "panel() requires name="),
        ('x = input_float("X")\npanel([x], name="Bad", collapsed=1)\noutput("X", x)', "panel() collapsed= must be a compile-time bool"),
        ('x = input_float("X")\npanel([x], name="Bad", extra=True)\noutput("X", x)', "Unsupported keyword argument(s): extra"),
    ]
    for index, (source, message) in enumerate(cases):
        _expect_message(source, message, f"NFTest_panel_invalid_{index}")


def test_interface_panel_is_structurally_root_only_before_preprocessing():
    cases = [
        '''
x = input_float("X")
if True:
    panel([x], name="Bad")
output("X", x)
''',
        '''
x = input_float("X")
if False:
    panel([x], name="Bad")
output("X", x)
''',
        '''
x = input_float("X")
for i in [1]:
    panel([x], name="Bad")
output("X", x)
''',
        '''
x = input_float("X")
for i in []:
    panel([x], name="Bad")
output("X", x)
''',
        '''
def helper(v):
    panel([v], name="Bad")
    return v
x = input_float("X")
y = helper(x)
output("Y", y)
''',
        '''
x = input_float("X")
y = panel([x], name="Bad")
output("X", x)
''',
        '''
x = input_float("X")
y = input_float("Y")
z = select(True, panel([x], name="Bad"), y)
output("Z", z)
''',
        '''
x = input_float("X")
flag = input_bool("Flag")
if flag:
    panel([x], name="Bad")
    y = x
else:
    y = x * 2
output("Y", y)
''',
        '''
x = input_float("X")
count = input_int("Count", default=1)
for i in repeat_range(count):
    panel([x], name="Bad")
output("X", x)
''',
    ]
    for index, source in enumerate(cases):
        _expect_message(source, "panel() is a top-level interface declaration", f"NFTest_panel_scope_{index}")


def test_interface_panel_duplicate_membership_uses_resolved_socket_identity():
    cases = [
        '''
x = input_float("X")
panel([x], name="One")
panel([x], name="Two")
output("X", x)
''',
        '''
a = input_float("A")
b = a
panel([a, b], name="Bad")
output("A", a)
''',
        '''
a = input_float("A")
b = a
panel([a], name="One")
panel([b], name="Two")
output("A", a)
''',
    ]
    before = {group.name for group in bpy.data.node_groups}
    for index, source in enumerate(cases):
        _expect_message(source, "panel()", f"NFTest_panel_duplicate_{index}")
    after = {group.name for group in bpy.data.node_groups}
    leaked = sorted(name for name in after - before if name.startswith("NFTest_panel_duplicate_"))
    check(not leaked, f"failed panel builds leaked partial node groups: {leaked}")


def test_interface_panel_rejects_computed_and_local_function_outputs_as_members():
    source = '''
def helper(v):
    return v * 2

source = input_float("Source")
x = helper(source)
panel([x], name="Bad")
output("Result", x)
'''
    _expect_message(source, "panel() item x is not a group input", "NFTest_panel_foreign_socket")


def test_interface_panel_duplicate_names_and_reserved_binding():
    _expect_message(
        'a = input_float("A")\nb = input_float("B")\npanel([a], name="Same")\npanel([b], name="Same")\noutput("A", a)',
        "panel() duplicate panel name: Same",
        "NFTest_panel_duplicate_name",
    )
    _expect_message('panel = 1\noutput("x", 1)', "Cannot assign to panel", "NFTest_panel_reserved_binding")


def test_interface_input_resolver_rejects_foreign_owner_even_on_identifier_collision():
    current = bpy.data.node_groups.new("NFTest_panel_resolver_current", "GeometryNodeTree")
    current_iface = current.interface.new_socket(name="Current", in_out="INPUT", socket_type="NodeSocketFloat")
    current_input = current.nodes.new("NodeGroupInput")
    current_socket = current_input.outputs["Current"]
    comp = compiler.Compiler(current, current_input)
    comp._register_interface_input(current_socket, current_iface)

    foreign = bpy.data.node_groups.new("NFTest_panel_resolver_foreign", "GeometryNodeTree")
    foreign.interface.new_socket(name="Foreign", in_out="INPUT", socket_type="NodeSocketFloat")
    foreign_input = foreign.nodes.new("NodeGroupInput")
    foreign_socket = foreign_input.outputs["Foreign"]
    check(
        current_socket.identifier == foreign_socket.identifier,
        "resolver fixture did not produce the intended cross-owner identifier collision",
    )
    foreign_value = make_value(foreign_socket, TYPE_FLOAT)
    check(comp.interface_input_for_value(foreign_value) is None, "foreign owner resolved through identifier collision")


def test_copy_interface_preserves_nested_native_panel_hierarchy():
    source = bpy.data.node_groups.new("NFTest_panel_nested_source", "GeometryNodeTree")
    outer = source.interface.new_panel(name="Outer", description="outer description", default_closed=False)
    first = source.interface.new_socket(name="First", in_out="INPUT", socket_type="NodeSocketFloat")
    source.interface.move_to_parent(first, outer, 0)
    inner = source.interface.new_panel(name="Inner", description="inner description", default_closed=True)
    source.interface.move_to_parent(inner, outer, 1)
    nested = source.interface.new_socket(name="Nested", in_out="INPUT", socket_type="NodeSocketInt")
    nested.default_value = 7
    source.interface.move_to_parent(nested, inner, 0)
    last = source.interface.new_socket(name="Last", in_out="INPUT", socket_type="NodeSocketFloat")
    source.interface.move_to_parent(last, outer, 2)
    source.interface.new_socket(name="Result", in_out="OUTPUT", socket_type="NodeSocketFloat")

    destination = bpy.data.node_groups.new("NFTest_panel_nested_destination", "GeometryNodeTree")
    compiler._copy_group_contents(source, destination)

    panels = {panel.name: panel for panel in _interface_panels(destination)}
    check(set(panels) == {"Outer", "Inner"}, f"nested copy panels missing: {set(panels)}")
    check(_parent_name(panels["Outer"]) == "", "Outer did not remain root-level")
    check(_parent_name(panels["Inner"]) == "Outer", "Inner nesting was lost")
    check(
        _panel_children(destination, "Outer") == _panel_children(source, "Outer"),
        f"Outer sibling order changed: {_panel_children(source, 'Outer')} -> {_panel_children(destination, 'Outer')}",
    )
    check(_panel_children(destination, "Inner") == _panel_children(source, "Inner"), "Inner membership changed")
    check(panels["Outer"].description == "outer description", "Outer description was not copied")
    check(panels["Inner"].description == "inner description", "Inner description was not copied")
    check(bool(panels["Inner"].default_closed) is True, "nested default_closed was not copied")
    inputs = _interface_inputs(destination)
    check(int(inputs["Nested"].default_value) == 7, "nested socket default was not copied")
