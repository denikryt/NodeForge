from helpers import *


def _capture_nodes(group):
    return [node for node in group.nodes if getattr(node, "bl_idname", None) == "GeometryNodeCaptureAttribute"]


def test_capture_attribute_supports_existing_nodeforge_field_types():
    group = compile_group('''
def nf_fixture():
    geo = grid(2, 2)
    geo, captured_float = capture_attribute(geo, position().x)
    geo, captured_int = capture_attribute(geo, index())
    geo, captured_bool = capture_attribute(geo, position().x > 0.0)
    geo, captured_vector = capture_attribute(geo, position())
    geometry = geo
    float = captured_float
    int = captured_int
    bool = captured_bool
    captured_vector_value = captured_vector
    return (geometry, float, int, bool, captured_vector_value,)
''', "NFTest_capture_attribute_supported_types")

    nodes = _capture_nodes(group)
    check(len(nodes) == 4, f"expected 4 Capture Attribute nodes, got {len(nodes)}")
    data_types = [node.capture_items[0].data_type for node in nodes]
    check(data_types == ["FLOAT", "INT", "BOOLEAN", "FLOAT_VECTOR"], f"unexpected capture data types: {data_types}")
    for node in nodes:
        selection = node.inputs.get("Selection")
        check(selection is not None, "Capture Attribute Selection input missing")
        check(node.domain == "POINT", "omitted capture domain did not remain POINT")
        check(not selection.is_linked, "omitted Capture Attribute selection unexpectedly linked")
        check(bool(selection.default_value) is True, "omitted Capture Attribute selection is not explicitly True")


def test_capture_attribute_rejects_color_until_nodeforge_has_color_type():
    expect_compile_error('''
def nf_fixture():
    geo = grid(2, 2)
    geo, captured = capture_attribute(geo, position().x, type="FLOAT_COLOR")
    geometry = geo
    return (geometry,)
''', "NFTest_capture_attribute_color_unsupported")
    expect_compile_error('''
def nf_fixture():
    geo = grid(2, 2)
    geo, captured = capture_attribute(geo, position().x, type="RGBA")
    geometry = geo
    return (geometry,)
''', "NFTest_capture_attribute_rgba_socket_unsupported")


def test_capture_attribute_rejects_blender_domains_outside_nodeforge_contract():
    expect_compile_error('''
def nf_fixture():
    geo = grid(2, 2)
    geo, captured = capture_attribute(geo, position().x, domain="LAYER")
    geometry = geo
    return (geometry,)
''', "NFTest_capture_attribute_layer_domain_unsupported")


def test_local_geometry_helper_inside_repeat_uses_declared_output_socket():
    group = compile_entry_group('''
def add_empty(geometry: Geometry, selection: Bool):
    selected = node(
        "GeometryNodeSeparateGeometry",
        props={"domain": "POINT"},
        inputs={"Geometry": geometry, "Selection": selection},
        output="Selection",
        typ=Geometry,
    )
    return join(selected, empty_geometry())

def nf_fixture(iterations: Int["Iterations"] = 2):
    result = points(1)
    for i in repeat_range(iterations):
        result = add_empty(result, i >= 0)
    return result
''', "NFTest_local_geometry_helper_repeat_output_direction", "nf_fixture")
    check(any(node.bl_idname == "GeometryNodeRepeatOutput" for node in group.nodes), "Repeat Zone output missing")


def test_store_named_attribute_omitted_selection_is_explicit_select_all():
    group = compile_group('''
def nf_fixture():
    geo = grid(2, 2)
    geo = store_named_attribute(geo, "weight", position().x)
    geometry = geo
    return (geometry,)
''', "NFTest_store_selection_default")
    nodes = [node for node in group.nodes if node.bl_idname == "GeometryNodeStoreNamedAttribute"]
    check(len(nodes) == 1, f"expected one Store Named Attribute node, got {len(nodes)}")
    selection = nodes[0].inputs.get("Selection")
    check(selection is not None, "Store Named Attribute Selection input missing")
    check(not selection.is_linked, "omitted Store selection unexpectedly linked")
    check(bool(selection.default_value) is True, "omitted Store selection is not explicitly True")
