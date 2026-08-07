from helpers import *


def _capture_nodes(group):
    return [node for node in group.nodes if getattr(node, "bl_idname", None) == "GeometryNodeCaptureAttribute"]


def test_capture_attribute_supports_existing_nodeforge_field_types():
    group = compile_group('''
geo = grid(2, 2)
geo, captured_float = capture_attribute(geo, position().x)
geo, captured_int = capture_attribute(geo, index())
geo, captured_bool = capture_attribute(geo, position().x > 0.0)
geo, captured_vector = capture_attribute(geo, position())
output("Geometry", geo)
output("Float", captured_float)
output("Int", captured_int)
output("Bool", captured_bool)
output("Vector", captured_vector)
''', "NFTest_capture_attribute_supported_types")

    nodes = _capture_nodes(group)
    check(len(nodes) == 4, f"expected 4 Capture Attribute nodes, got {len(nodes)}")
    data_types = [node.capture_items[0].data_type for node in nodes]
    check(data_types == ["FLOAT", "INT", "BOOLEAN", "FLOAT_VECTOR"], f"unexpected capture data types: {data_types}")


def test_capture_attribute_rejects_color_until_nodeforge_has_color_type():
    expect_compile_error('''
geo = grid(2, 2)
geo, captured = capture_attribute(geo, position().x, type="FLOAT_COLOR")
output("Geometry", geo)
''', "NFTest_capture_attribute_color_unsupported")
    expect_compile_error('''
geo = grid(2, 2)
geo, captured = capture_attribute(geo, position().x, type="RGBA")
output("Geometry", geo)
''', "NFTest_capture_attribute_rgba_socket_unsupported")


def test_capture_attribute_rejects_blender_domains_outside_nodeforge_contract():
    expect_compile_error('''
geo = grid(2, 2)
geo, captured = capture_attribute(geo, position().x, domain="LAYER")
output("Geometry", geo)
''', "NFTest_capture_attribute_layer_domain_unsupported")


def test_local_geometry_helper_inside_repeat_uses_declared_output_socket():
    group = compile_group('''
iterations = input_int("Iterations", default=2)


def add_empty(geometry: Geometry, selection: Bool):
    selected = node(
        "GeometryNodeSeparateGeometry",
        props={"domain": "POINT"},
        inputs={"Geometry": geometry, "Selection": selection},
        output="Selection",
        typ=Geometry,
    )
    return join(selected, empty_geometry())

result = points(1)
for i in repeat_range(iterations):
    result = add_empty(result, i >= 0)
output("Geometry", result)
''', "NFTest_local_geometry_helper_repeat_output_direction")
    check(any(node.bl_idname == "GeometryNodeRepeatOutput" for node in group.nodes), "Repeat Zone output missing")
