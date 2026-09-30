"""Real-Blender integration coverage for the typed core sample_index() builtin."""

from __future__ import annotations

from helpers import _owned_generated_id_keys, check, compile_group, expect_compile_error


def _sample_nodes(group):
    """Return Sample Index nodes in physical creation order."""
    return [node for node in group.nodes if node.bl_idname == "GeometryNodeSampleIndex"]


def _output_socket(group, name):
    """Return one named output interface socket."""
    return next(
        item
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET"
        and getattr(item, "in_out", None) == "OUTPUT"
        and item.name == name
    )


def test_sample_index_preserves_supported_value_types_and_uses_one_node_per_call():
    group = compile_group(
        '''
geo = grid(3, 3)
f = sample_index(geo, position().x, 0)
i = sample_index(geo, index(), 1)
b = sample_index(geo, index() > 0, 2)
v = sample_index(geo, position(), 3)
output("Float", f)
output("Int", i)
output("Bool", b)
output("Vector", v)
''',
        "NFTest_sample_index_types",
    )

    nodes = _sample_nodes(group)
    check(len(nodes) == 4, f"expected four Sample Index nodes, got {len(nodes)}")
    check(
        [node.data_type for node in nodes] == ["FLOAT", "INT", "BOOLEAN", "FLOAT_VECTOR"],
        f"unexpected Sample Index data types: {[node.data_type for node in nodes]}",
    )
    expected_outputs = {
        "Float": "NodeSocketFloat",
        "Int": "NodeSocketInt",
        "Bool": "NodeSocketBool",
        "Vector": "NodeSocketVector",
    }
    for name, socket_type in expected_outputs.items():
        check(_output_socket(group, name).socket_type == socket_type, f"{name} result type changed")


def test_sample_index_constant_and_runtime_index_use_distinct_existing_representations():
    group = compile_group(
        '''
geo = grid(3, 3)
runtime_index = input_int("Index", default=1)
constant_sample = sample_index(geo, position().x, 3)
runtime_sample = sample_index(geo, position().x, runtime_index)
output("Constant", constant_sample)
output("Runtime", runtime_sample)
''',
        "NFTest_sample_index_index_modes",
    )

    nodes = _sample_nodes(group)
    check(len(nodes) == 2, f"expected two Sample Index nodes, got {len(nodes)}")
    constant, runtime = nodes
    check(not constant.inputs["Index"].is_linked, "constant Sample Index unexpectedly linked Index")
    check(int(constant.inputs["Index"].default_value) == 3, "constant Sample Index lost exact Int default")
    check(runtime.inputs["Index"].is_linked, "runtime Sample Index did not link Int Index")


def test_sample_index_domain_and_clamp_are_frontend_normalized_configuration():
    group = compile_group(
        '''
geo = grid(3, 3)
a = sample_index(geo, position().x, 0)
b = sample_index(geo, position().x, 1, domain="face", clamp=True)
output("A", a)
output("B", b)
''',
        "NFTest_sample_index_static_options",
    )
    first, second = _sample_nodes(group)
    check(first.domain == "POINT", f"default domain changed: {first.domain}")
    check(bool(first.clamp) is False, "default clamp changed")
    check(second.domain == "FACE", f"explicit domain was not canonicalized: {second.domain}")
    check(bool(second.clamp) is True, "explicit clamp=True was not realized")


def test_sample_index_creates_no_generated_blender_resources_and_failure_publishes_none():
    before = _owned_generated_id_keys()
    group = compile_group(
        '''
geo = grid(2, 2)
value = sample_index(geo, position().x, 0)
output("Value", value)
''',
        "NFTest_sample_index_no_resources",
    )
    check(len(_sample_nodes(group)) == 1, "Sample Index node missing")
    check(_owned_generated_id_keys() == before, "Sample Index unexpectedly created generated resources")

    expect_compile_error(
        '''
geo = grid(2, 2)
value = sample_index(geo, geo, 0)
output("Value", value)
''',
        "NFTest_sample_index_rejected_type_no_resources",
    )
    check(_owned_generated_id_keys() == before, "failed Sample Index compile published generated resources")
