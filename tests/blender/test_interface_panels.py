"""Blender integration coverage for native NodeForge interface panels."""

from helpers import *


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
def nf_fixture(a: Float['A'] = 1.0, b: Int['B'] = 2):
    panel([a, b], name="Main")
    a = a
    return (a,)
'''
    group = compile_group(source, "NFTest_panel_basic")
    panels = _interface_panels(group)
    check([panel.name for panel in panels] == ["Main"], f"unexpected panels: {[p.name for p in panels]}")
    check(bool(panels[0].default_closed) is False, "omitted collapsed did not remain False")
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
def nf_fixture(loose: Float['Loose'], a: Float['A'], b: Float['B'], c: Float['C']):
    panel([b, a], name="First")
    panel([c], name="Second", collapsed=True)
    loose = loose
    return (loose,)
'''
    group = compile_group(source, "NFTest_panel_multiple")
    panels = _interface_panels(group)
    check([panel.name for panel in panels] == ["First", "Second"], f"wrong panel order: {[p.name for p in panels]}")
    check(bool(panels[1].default_closed) is True, "collapsed=True did not set default_closed")
    inputs = _interface_inputs(group)
    check(_parent_name(inputs["Loose"]) == "", "ungrouped input did not remain at root")
    check(_panel_children(group, "First") == ["B", "A"], f"wrong First order: {_panel_children(group, 'First')}")
    check(_panel_children(group, "Second") == ["C"], "Second membership is wrong")

    wrapper = bpy.data.node_groups.new("NFTest_panel_multiple_wrapper", "GeometryNodeTree")
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = group
    physical_order = [socket.name for socket in group_node.inputs]
    check(
        physical_order == ["Loose", "B", "A", "C"],
        f"panelized callable order mismatch: {physical_order}",
    )


def test_interface_panel_rejects_implicit_external_input():
    """Only declared function parameters can appear in a panel prefix."""
    _expect_message('''
def nf_fixture(loose: Float['Loose']):
    panel([source_value], name="Inputs")
    return loose
''', "is not a function parameter", "NFTest_panel_implicit_rejected")


def test_interface_panel_rejects_invalid_members_and_arguments():
    cases = [
        ('''
def nf_fixture():
    panel([value], name="Bad")
    value = 1
    value = value
    return (value,)
''', "is not a function parameter"),
        ('''
def nf_fixture(x: Float['X']):
    panel([x * 2], name="Bad")
    x = x
    return (x,)
''', "simple parameter names"),
        ('''
def nf_fixture(x: Float['X']):
    panel(x, name="Bad")
    x = x
    return (x,)
''', "expects exactly one positional list or tuple"),
        ('''
def nf_fixture(x: Float['X']):
    panel([], name="Bad")
    x = x
    return (x,)
''', "requires one or more simple parameter names"),
        ('''
def nf_fixture(x: Float['X']):
    panel([x])
    x = x
    return (x,)
''', "panel() requires name="),
        ('''
def nf_fixture(x: Float['X']):
    panel([x], name="Bad", collapsed=1)
    x = x
    return (x,)
''', "collapsed must be Bool"),
        ('''
def nf_fixture(x: Float['X']):
    panel([x], name="Bad", extra=True)
    x = x
    return (x,)
''', "panel() unknown keyword"),
    ]
    for index, (source, message) in enumerate(cases):
        _expect_message(source, message, f"NFTest_panel_invalid_{index}")




def test_interface_panel_duplicate_membership_uses_resolved_socket_identity():
    cases = [
        '''
def nf_fixture(x: Float['X']):
    panel([x], name="One")
    panel([x], name="Two")
    x = x
    return (x,)
''',
        '''
def nf_fixture(a: Float['A']):
    panel([a, b], name="Bad")
    b = a
    a = a
    return (a,)
''',
        '''
def nf_fixture(a: Float['A']):
    panel([a], name="One")
    panel([b], name="Two")
    b = a
    a = a
    return (a,)
''',
    ]
    before = {group.name for group in bpy.data.node_groups}
    for index, source in enumerate(cases):
        _expect_message(source, "panel()", f"NFTest_panel_duplicate_{index}")
    after = {group.name for group in bpy.data.node_groups}
    leaked = sorted(name for name in after - before if name.startswith("NFTest_panel_duplicate_"))
    check(not leaked, f"failed panel builds leaked partial node groups: {leaked}")


def test_interface_panel_rejects_computed_and_local_function_outputs_as_members():
    _expect_message('''
def nf_fixture(source: Float['Source']):
    panel([x], name="Bad")
    x = source * 2
    return x
''', "is not a function parameter", "NFTest_panel_foreign_socket")


def test_interface_panel_duplicate_names_and_reserved_binding():
    _expect_message(
        '''
def nf_fixture(a: Float['A'], b: Float['B']):
    panel([a], name="Same")
    panel([b], name="Same")
    a = a
    return (a,)
''',
        "duplicate panel name: Same",
        "NFTest_panel_duplicate_name",
    )
    _expect_message('''
def nf_fixture():
    panel = 1
    x = 1
    return (x,)
''', "Cannot assign to panel", "NFTest_panel_reserved_binding")


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
    blender_group_backend._copy_group_contents(source, destination)

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


def test_interface_panel_rejects_aliases_and_keeps_parameter_identity():
    """Panel references name formal parameters, not local aliases or values."""
    group = compile_group('''
def nf_fixture(x: Float['X']):
    panel([x], name="Inputs")
    y = x
    return y
''', "NFTest_panel_exact_parameter")
    check(_parent_name(_interface_inputs(group)["X"]) == "Inputs", "declared input was not grouped")
    bpy.data.node_groups.remove(group)
    _expect_message('''
def nf_fixture(x: Float['X']):
    panel([y], name="Alias")
    y = x
    return y
''', "is not a function parameter", "NFTest_panel_alias_rejected")
    _expect_message('''
def nf_fixture(x: Float['X'], flag: Bool['Flag']):
    panel([y], name="Bad")
    if flag:
        y = x
    else:
        y = x
    return y
''', "is not a function parameter", "NFTest_panel_switch_alias")
    _expect_message('''
def nf_fixture(x: Float['X']):
    for i in repeat_range(1):
        x = x
    panel([x], name="Late")
    return x
''', "panel()", "NFTest_panel_after_repeat")
