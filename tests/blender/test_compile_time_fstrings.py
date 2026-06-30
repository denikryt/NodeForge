from helpers import *

from NodeForge.builtins import raw_nodes


def _socket_names(group, in_out):
    return {
        item.name
        for item in group.interface.items_tree
        if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == in_out
    }


def test_compile_time_fstrings_work_across_dsl_string_consumers():
    group = compile_group('''
prefix = "Result"
input_suffix = " Value"
out_suffix = " Output"
a = input_float(f"{prefix}{input_suffix}", default=1.0)
output(f"{prefix}{out_suffix}", a)
''', "NFTest_compile_time_fstring_io")
    check("Result Value" in _socket_names(group, "INPUT"), "evaluated input name missing")
    check("Result Output" in _socket_names(group, "OUTPUT"), "evaluated output name missing")
    check("prefix" not in _socket_names(group, "INPUT"), "string fragment became implicit input")
    check("input_suffix" not in _socket_names(group, "INPUT"), "f-string suffix became implicit input")

    compile_group('''
left = "[-F[+F]-F]"
right = "[+F+F+F]"
rule = f"F{left}FFF{right}FFF"
param_name = "Growth"
marker_name = "Tip"
marker_param = "size"
geo = ls_system(
    ls_axiom(f"F{marker_name}({marker_param})"),
    ls_rule("F", rule),
    ls_param(f"{param_name}", 1.0),
    ls_param(f"{marker_param}", 0.2),
    ls_marker(f"{marker_name}", f"{marker_param}"),
    ls_iterations(1),
    ls_angle(60),
    ls_step(0.1),
)
pts = ls_points(geo, marker=f"{marker_name}")
output("Geometry", join(geo, pts))
''', "NFTest_compile_time_fstring_lsystem")

    compile_group('''
domain_name = "FACE"
kind = "COLOR"
geo = grid(4, 3)
uv = grid_uv()
store("c", uv.x, domain=f"{domain_name}", type=f"{kind}")
output("Geometry", geo)
''', "NFTest_compile_time_fstring_store_kw")

    compile_group('''
domain_name = "FACE"
kind = "COLOR"
attr = "c"
geo = grid(4, 3)
uv = grid_uv()
geo = store_named_attribute(geo, f"{attr}", uv.x, domain=f"{domain_name}", type=f"{kind}")
output("Geometry", geo)
''', "NFTest_compile_time_fstring_store_named_kw")

    compile_group('''
name = "Mat"
geo = set_material(cube(1), f"{name}")
output("Geometry", geo)
''', "NFTest_compile_time_fstring_material")


def test_compile_time_fstrings_work_for_raw_node_strings():
    group = compile_group('''
node_prefix = "FunctionNode"
node_kind = "Compare"
prop_a = "data_"
prop_b = "type"
operation = "operation"
input_a = "A"
input_b = "B"
output_name = "Result"
mask = node(
    f"{node_prefix}{node_kind}",
    props={f"{prop_a}{prop_b}": f"FLOAT", f"{operation}": f"GREATER_THAN"},
    inputs={f"{input_a}": position().z, f"{input_b}": 0.5},
    output=f"{output_name}",
    typ=Bool,
)
output("mask", mask)
''', "NFTest_compile_time_fstring_raw_single")
    node = next(node for node in group.nodes if getattr(node, "bl_idname", None) == "FunctionNodeCompare")
    check(raw_nodes.is_raw_node(node), "raw node metadata missing")

    compile_group('''
node_prefix = "ShaderNode"
node_kind = "SeparateXYZ"
out_x = "X"
vec = "Vector"
sep = node(
    f"{node_prefix}{node_kind}",
    inputs={f"{vec}": (1.0, 2.0, 3.0)},
    outputs={f"{out_x}": Float},
)
output("x", sep.X)
''', "NFTest_compile_time_fstring_raw_multi")


def test_compile_time_fstrings_work_for_package_local_backend_helper():
    compiler._make_group('''
from examples import mandelbrot
geo = mandelbrot()
attr = "mandelbrot_color"
geo = apply_mandelbrot_material(geo, f"{attr}")
output("Geometry", geo)
''', "NFTest_compile_time_fstring_mandelbrot_backend", backend_builtins=library.backend_builtins_for_entry("examples", "mandelbrot"))


def test_compile_time_fstrings_reject_runtime_or_non_string_interpolation():
    bad_sources = [
        'x = input_float("Name")\noutput(f"{x}", 1)',
        'count = 1\noutput(f"{count}", 1)',
        'part = "F"\ngeo = ls_system(ls_axiom("F"), ls_rule("F", f"{part}{1}"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)',
        'part = ls_axiom("F")\ngeo = ls_system(ls_axiom("F"), ls_rule("F", f"{part}"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)',
        'part = "F"\ngeo = ls_system(ls_axiom("F"), ls_rule("F", f"{part!r}"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)',
        'domain_name = input_float("D")\ngeo = grid(4, 3)\nuv = grid_uv()\nstore("c", uv.x, domain=f"{domain_name}")\noutput("Geometry", geo)',
        'kind = input_float("T")\ngeo = grid(4, 3)\nuv = grid_uv()\ngeo = store_named_attribute(geo, "c", uv.x, type=f"{kind}")\noutput("Geometry", geo)',
        'x = input_float("Attr")\nfrom examples import mandelbrot\ngeo = mandelbrot()\ngeo = apply_mandelbrot_material(geo, f"{x}")\noutput("Geometry", geo)',
        'socket = input_float("Socket")\nx = node("FunctionNodeCompare", inputs={f"{socket}": 1}, output="Result", typ=Bool)\noutput("x", x)',
    ]
    for index, source in enumerate(bad_sources):
        kwargs = {}
        if "apply_mandelbrot_material" in source:
            kwargs["backend_builtins"] = library.backend_builtins_for_entry("examples", "mandelbrot")
        expect_compile_error(source, f"NFTest_compile_time_fstring_error_{index}", **kwargs)


def test_raw_node_duplicate_keys_after_fstring_interpolation_are_rejected():
    expect_compile_error('''
socket = "A"
x = node("FunctionNodeCompare", inputs={f"{socket}": 1, "A": 2}, output="Result", typ=Bool)
output("x", x)
''', "NFTest_compile_time_fstring_raw_duplicate_input")
