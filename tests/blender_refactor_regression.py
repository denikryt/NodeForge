"""Blender headless regression checks for compiler-module refactors."""

import ast
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = ROOT.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

import bpy
import NodeForge
from NodeForge import compiler, library
from NodeForge.constants import _FLOAT_FUNCS_1, _FLOAT_FUNCS_2
from NodeForge.errors import CompileError
from NodeForge.builtins import registry
from NodeForge.systems import registry as systems_registry
from NodeForge.builtins import fields, geometry, instancing, io, math, vector


def check(condition, message):
    """Raise an assertion error when a regression check fails."""
    if not condition:
        raise AssertionError(message)


def compile_group(source, name):
    """Compile one source fixture into a named node group."""
    group = compiler.create_expression_group(source, name)
    check(getattr(group, "bl_idname", None) == "GeometryNodeTree", name)
    return group



def _math_keyword_expr(name, params):
    """Build one keyword-call expression for a table-driven math builtin."""
    values = {
        "value": "0.5",
        "a": "0.75",
        "b": "0.25",
        "factor": "0.5",
        "cond": "True",
        "false": "0.0",
        "true": "1.0",
        "from_min": "0.0",
        "from_max": "1.0",
        "to_min": "-1.0",
        "to_max": "1.0",
        "x": "0.5",
        "edge": "0.25",
        "edge0": "0.0",
        "edge1": "1.0",
        "length": "1.0",
        "min": "0.0",
        "max": "1.0",
        "in_min": "0.0",
        "in_max": "1.0",
        "out_min": "-1.0",
        "out_max": "1.0",
    }
    args = ", ".join(f"{param}={values[param]}" for param in params)
    return f"{name}({args})"


def run_math_table_dispatch_checks():
    """Check table-driven math names, keyword support, and unsupported-name errors."""
    expected = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | {
        "ln", "clamp", "mix", "lerp", "select", "map_range",
        "inverse_lerp", "remap", "saturate", "step", "smoothstep", "smootherstep", "pingpong", "wrap",
        "noise", "random_value",
    }
    check(math.NAMES == expected, f"math.NAMES drifted: {sorted(math.NAMES ^ expected)}")
    check(set(_FLOAT_FUNCS_1).issubset(math._SPECS), "FLOAT_FUNCS_1 names missing from math specs")
    check(set(_FLOAT_FUNCS_2).issubset(math._SPECS), "FLOAT_FUNCS_2 names missing from math specs")
    math_ops = {item.identifier for item in bpy.types.ShaderNodeMath.bl_rna.properties["operation"].enum_items}
    invalid_ops = sorted((set(_FLOAT_FUNCS_1.values()) | set(_FLOAT_FUNCS_2.values())) - math_ops)
    check(not invalid_ops, f"constants contain unsupported ShaderNodeMath operations: {invalid_ops}")

    positional_lines = []
    for index, name in enumerate(sorted(_FLOAT_FUNCS_1)):
        positional_lines.append(f"p{index} = {name}(0.5)")
    base = len(positional_lines)
    for index, name in enumerate(sorted(_FLOAT_FUNCS_2)):
        positional_lines.append(f"p{base + index} = {name}(0.75, 0.25)")
    positional_lines.extend([
        "ln_v = ln(2)",
        "clamp_v = clamp(2, 0, 1)",
        "mix_v = mix(0, 1, 0.5)",
        "lerp_v = lerp(0, 1, 0.5)",
        "select_v = select(True, 0, 1)",
        "map_range_v = map_range(0.5, 0, 1, -1, 1)",
        "inverse_lerp_v = inverse_lerp(0, 10, 5)",
        "remap_v = remap(0.5, 0, 1, -1, 1)",
        "saturate_v = saturate(2)",
        "step_v = step(0.5, 1)",
        "smoothstep_v = smoothstep(0, 1, 0.5)",
        "smootherstep_v = smootherstep(0, 1, 0.5)",
        "pingpong_v = pingpong(-0.25, 1)",
        "wrap_v = wrap(-1, 0, 2)",
        "noise_v = noise(vector(0,0,0), scale=1, detail=2, roughness=0.5)",
        "random_v = random_value(0, 1, seed=3)",
        "output('v', clamp_v)",
    ])
    compile_group("\n".join(positional_lines), "NFTest_math_all_positional")

    keyword_lines = []
    for index, name in enumerate(sorted(math._SPECS)):
        keyword_lines.append(f"k{index} = {_math_keyword_expr(name, math._SPECS[name].params)}")
    keyword_lines.append("output('v', k0)")
    compile_group("\n".join(keyword_lines), "NFTest_math_all_keywords")

    error_sources = [
        "x = smoothstep(edge0=0, edge1=1, value=0.5)\noutput('x', x)",
        "x = smoothstep(0, edge0=1, edge1=2)\noutput('x', x)",
        "x = smoothstep(edge0=0, edge1=1)\noutput('x', x)",
        "x = smoothstep(**foo)\noutput('x', x)",
    ]
    for index, source in enumerate(error_sources):
        try:
            compile_group(source, f"NFTest_math_keyword_error_{index}")
        except CompileError:
            pass
        else:
            raise AssertionError(f"math keyword error fixture {index} did not fail")

    expr = ast.parse("__missing__(1)", mode="eval").body
    try:
        math.compile_call(object(), expr)
    except CompileError:
        pass
    else:
        raise AssertionError("unsupported math builtin did not raise CompileError")
    print("MATH_TABLE_DISPATCH_OK")


def run_import_and_registry_checks():
    """Check module imports and authoritative builtin registry coverage."""
    for modname in ["compiler", "expression_compiler", "statement_compiler", "local_functions", "library_calls", "systems.registry", "systems.lsystem.compiler"]:
        __import__("NodeForge." + modname)
    check(systems_registry.NAMES == {"ls_system", "ls_axiom", "ls_rule", "ls_iterations", "ls_angle", "ls_step"}, "systems registry names drifted")
    for module in (io, math, vector, geometry, fields, instancing):
        missing = sorted(name for name in module.NAMES if not registry.has_callable_builtin(name))
        check(not missing, f"registry missing {module.__name__}: {missing}")
    print("IMPORT_AND_REGISTRY_OK")


def run_startup_shutdown_checks():
    """Exercise addon enable/disable and direct register/unregister paths."""
    bpy.ops.preferences.addon_enable(module="NodeForge")
    check("NodeForge" in bpy.context.preferences.addons, "addon_enable did not register NodeForge")
    bpy.ops.preferences.addon_disable(module="NodeForge")
    check("NodeForge" not in bpy.context.preferences.addons, "addon_disable did not unregister NodeForge")
    NodeForge.register()
    NodeForge.unregister()
    print("STARTUP_SHUTDOWN_OK")


def run_compile_fixtures():
    """Compile representative DSL fixtures for expressions, statements, and runtime loops."""
    fixtures = {
        "inputs": '''
a = input_float("A", default=0.25)
b = input_int("B", default=3)
c = input_bool("C", default=True)
v = input_vector("V", default=(1,2,3))
output("Float", a + b)
output("Bool", c)
output("Vec", v)
''',
        "math_vector": '''
a = inverse_lerp(0, 10, 5)
b = remap(a, 0, 1, -1, 1)
c = saturate(b + 2)
d = step(0.5, c)
e = smoothstep(0, 1, c)
f = smootherstep(0, 1, c)
g = pingpong(-0.25, 1)
h = wrap(-1, 0, 2)
v = rotate2d(polar(2, 1.57079632679), 1.57079632679)
w = rotate_around_axis(vector(1,0,0), vector(0,0,1), 1.57079632679)
ang = angle_between(vector(1,0,0), vector(0,1,0))
output("Scalar", a+b+c+d+e+f+g+h+ang)
output("Vector", v + w)
''',
        "geometry_fields": '''
geo = grid(4, 3)
uv = grid_uv()
height = smoothstep(0, 1, uv.x)
pos = vector(uv.x, uv.y, height)
store("h", height, domain="FACE")
set_position(pos)
''',
        "runtime_scalar": '''
x = 0
for i in runtime_range(5):
    if x < 3:
        x = x + 1
    else:
        x = x
output("x", x)
''',
        "runtime_vector_bool": '''
v = vector(1,0,0)
flag = True
for i in runtime_range(3):
    if flag:
        v = rotate_around_axis(v, vector(0,0,1), 0.1)
        flag = False
    else:
        v = v
        flag = flag
output("v", v)
output("flag", flag)
''',
        "runtime_geometry": '''
geo = cube(1)
for i in range(3):
    geo = transform(geo, translation=vector(0.1, 0, 0))
output("Geometry", geo)
''',
        "local_function": '''
def scale_add(a, b):
    c = a * 2 + b
    return c
x = scale_add(1, input_float("B"))
output("x", x)
''',
    }
    for name, source in fixtures.items():
        compile_group(source, "NFTest_" + name)
    print("COMPILE_FIXTURES_OK")


def run_library_checks():
    """Compile all packaged library functions and verify helper scoping."""
    flat_probe = ROOT / "functions" / "flat_legacy_probe.py"
    flat_probe.write_text("def compile_call(comp, expr, depth=0):\n    raise AssertionError('flat layout loaded')\n", encoding="utf-8")
    try:
        check(not library.has_module_library_function("flat_legacy_probe"), "legacy flat function module was discovered")
        check(not library.has_library_function("flat_legacy_probe"), "legacy flat function appeared as library function")
    finally:
        flat_probe.unlink(missing_ok=True)
    for fname in ["copy_by_offsets", "dragon_curve", "fibonacci", "fibonacci_spiral", "koch_curve", "mandelbrot", "sierpinski_carpet"]:
        group = compiler.create_library_function_group(fname)
        check(getattr(group, "bl_idname", None) == "GeometryNodeTree", fname)
    normal_names = set(bpy.data.node_groups.keys())
    reused = compiler.create_library_function_group("fibonacci")
    check(reused.name in bpy.data.node_groups, "library group reuse did not return a live group")
    check(set(bpy.data.node_groups.keys()) == normal_names, "library group reuse created unexpected groups")
    try:
        compile_group('geo = grid(4,4)\ngeo = apply_mandelbrot_material(geo, "x")\noutput("Geometry", geo)', "NFTest_backend_scope_fail")
    except Exception:
        pass
    else:
        raise AssertionError("package-local backend helper leaked into normal source")
    print("LIBRARY_AND_SCOPE_OK")




def expect_compile_error(source, name, exc_type=CompileError, **kwargs):
    """Compile one source and require a controlled error type."""
    try:
        compiler._make_group(source, name, **kwargs)
    except exc_type:
        return
    except AttributeError as exc:
        raise AssertionError(f"{name} raised uncontrolled AttributeError: {exc}") from exc
    else:
        raise AssertionError(f"{name} did not raise {exc_type.__name__}")


def run_lsystem_stage1_checks():
    """Exercise embedded L-system Stage 1 syntax, guards, and bounded backend."""
    group = compile_group("""
angle_value = input_float("Angle", default=60.0)
step_value = input_float("Step", default=0.1)
geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F+F--F+F"),
    ls_iterations(2),
    ls_angle(angle_value),
    ls_step(step_value),
)
geo = transform(geo, translation=vector(0, 0, 1))
output("Geometry", geo)
""", "NFTest_lsystem_runtime_angle_step")
    input_names = {item.name for item in group.interface.items_tree if getattr(item, "item_type", None) == "SOCKET" and getattr(item, "in_out", None) == "INPUT"}
    check("ls_system" not in input_names and "ls_axiom" not in input_names and "ls_step" not in input_names, "system constructors became implicit inputs")
    check({"Angle", "Step"}.issubset(input_names), "runtime L-system inputs missing")

    compile_group("""
a = ls_axiom("F")
r = ls_rule("F", "F+X")
geo = ls_system(a, r, ls_iterations(1), ls_angle(60), ls_step(0.1))
output("Geometry", geo)
""", "NFTest_lsystem_assigned_parts")

    compile_group("""
geo = ls_system(ls_axiom("X"), ls_rule("X", "F+X"), ls_iterations(2), ls_angle(60), ls_step(0.1))
output("Geometry", geo)
""", "NFTest_lsystem_grammar_symbols")

    compile_group("""
geo = ls_system(ls_axiom("F[+F]F[-F]F"), ls_iterations(0), ls_angle(25), ls_step(0.1))
output("Geometry", geo)
""", "NFTest_lsystem_branching_static")

    compile_group("""
angle_value = input_float("Angle", default=25.0)
geo = ls_system(ls_axiom("F[+F]F[-F]F"), ls_iterations(0), ls_angle(angle_value), ls_step(0.1))
output("Geometry", geo)
""", "NFTest_lsystem_branching_runtime_angle")

    compile_group("""
step_value = input_float("Step", default=0.1)
geo = ls_system(ls_axiom("ffF"), ls_iterations(0), ls_angle(90), ls_step(step_value))
output("Geometry", geo)
""", "NFTest_lsystem_lowercase_move_runtime_step")

    compile_group("x = step(0.5, 1.0)\noutput(\"x\", x)", "NFTest_lsystem_builtin_step_priority")

    error_sources = {
        "output_part": "output(\"Geometry\", ls_axiom(\"F\"))",
        "store_part": "geo = grid(2,2)\nstore(\"bad\", ls_axiom(\"F\"))",
        "set_position_part": "set_position(ls_axiom(\"F\"))",
        "final_part": "ls_axiom(\"F\")",
        "builtin_part": "x = transform(ls_axiom(\"F\"), translation=vector(0,0,1))\noutput(\"x\", x)",
        "local_function_part": "def ident(x):\n    return x\ny = ident(ls_axiom(\"F\"))\noutput(\"y\", y)",
        "runtime_if_part": "flag = input_bool(\"Flag\")\nif flag:\n    x = ls_axiom(\"F\")\nelse:\n    x = ls_axiom(\"F\")\noutput(\"x\", x)",
        "axiom_runtime": "a = input_float(\"A\")\ngeo = ls_system(ls_axiom(a), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "iterations_runtime": "n = input_int(\"N\")\ngeo = ls_system(ls_axiom(\"F\"), ls_iterations(n), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "bad_rule_multi": "geo = ls_system(ls_axiom(\"F\"), ls_rule(\"AB\", \"F\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "bad_rule_empty": "geo = ls_system(ls_axiom(\"F\"), ls_rule(\"\", \"F\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "bad_rule_pipe": "geo = ls_system(ls_axiom(\"F\"), ls_rule(\"X\", \"F|X\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "bad_rule_space": "geo = ls_system(ls_axiom(\"F\"), ls_rule(\"X\", \"F X\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "bad_rule_unicode": "geo = ls_system(ls_axiom(\"F\"), ls_rule(\"X\", \"F→X\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "unmatched_close": "geo = ls_system(ls_axiom(\"F]\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "unclosed_open": "geo = ls_system(ls_axiom(\"[F\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "duplicate_axiom": "geo = ls_system(ls_axiom(\"F\"), ls_axiom(\"F\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "duplicate_rule": "geo = ls_system(ls_axiom(\"F\"), ls_rule(\"F\", \"FF\"), ls_rule(\"F\", \"F\"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "reserved_local": "def ls_axiom(x):\n    return x\noutput(\"x\", 1)",
        "bool_angle_const": "geo = ls_system(ls_axiom(\"F\"), ls_iterations(1), ls_angle(True), ls_step(1))\noutput(\"Geometry\", geo)",
        "bool_angle_runtime": "b = input_bool(\"B\")\ngeo = ls_system(ls_axiom(\"F\"), ls_iterations(1), ls_angle(b), ls_step(1))\noutput(\"Geometry\", geo)",
        "list_smuggle": "p = [ls_axiom(\"F\")]\ngeo = ls_system(p, ls_iterations(1), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "zero_draw_bootstrap_limit": "geo = ls_system(ls_axiom(\"Xf\"), ls_iterations(0), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "max_symbols": "geo = ls_system(ls_axiom(\"F\"), ls_rule(\"F\", \"FF\"), ls_iterations(18), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
        "max_segments": "geo = ls_system(ls_axiom(\"F\"), ls_rule(\"F\", \"FF\"), ls_iterations(11), ls_angle(60), ls_step(1))\noutput(\"Geometry\", geo)",
    }
    for name, source in error_sources.items():
        expect_compile_error(source, "NFTest_lsystem_error_" + name)

    expect_compile_error(
        "output(\"x\", 1)",
        "NFTest_lsystem_reserved_backend_helper",
        backend_builtins={"ls_rule": lambda comp, expr, depth=0: None},
    )

    library_collision_dir = ROOT / "functions" / "ls_step"
    library_collision_dir.mkdir(exist_ok=True)
    (library_collision_dir / "source.nf").write_text('output("x", 1)\n', encoding="utf-8")
    try:
        expect_compile_error("output(\"x\", 1)", "NFTest_lsystem_reserved_library_function")
    finally:
        (library_collision_dir / "source.nf").unlink(missing_ok=True)
        library_collision_dir.rmdir()

    native_collision_dir = ROOT / "functions" / "ls_step"
    native_collision_dir.mkdir(exist_ok=True)
    (native_collision_dir / "function.py").write_text(
        'import bpy\n\n'
        'def materialize_group(compile_group_callback):\n'
        '    return bpy.data.node_groups.new("NFTest_bad_ls_step", "GeometryNodeTree")\n',
        encoding="utf-8",
    )
    try:
        for action_name, action in {
            "library_function_names": library.library_function_names,
            "has_library_function": lambda: library.has_library_function("ls_step"),
            "library_function_records": library.library_function_records,
            "get_or_create_library_group": lambda: library.get_or_create_library_group("ls_step", compiler._make_group),
            "materialize_library_function_group": lambda: library.materialize_library_function_group("ls_step", compiler._make_group),
        }.items():
            try:
                action()
            except CompileError:
                pass
            except AttributeError as exc:
                raise AssertionError(f"{action_name} raised uncontrolled AttributeError: {exc}") from exc
            else:
                raise AssertionError(f"{action_name} accepted reserved native library function")
    finally:
        (native_collision_dir / "function.py").unlink(missing_ok=True)
        pycache = native_collision_dir / "__pycache__"
        if pycache.exists():
            for child in pycache.iterdir():
                child.unlink()
            pycache.rmdir()
        native_collision_dir.rmdir()

    expect_compile_error("x = koch_curve(ls_axiom(\"F\"))\noutput(\"Geometry\", x)", "NFTest_lsystem_koch_native_guard")
    expect_compile_error("x = dragon_curve(ls_axiom(\"F\"))\noutput(\"Geometry\", x)", "NFTest_lsystem_dragon_native_guard")
    expect_compile_error(
        "geo = apply_mandelbrot_material(ls_axiom(\"F\"), \"x\")\noutput(\"Geometry\", geo)",
        "NFTest_lsystem_backend_helper_guard",
        backend_builtins=library.backend_builtins_for_function("mandelbrot"),
    )
    print("LSYSTEM_STAGE1_OK")

def run_update_checks():
    """Exercise successful and failed update_expression_group paths."""
    group = compile_group('x = 1\noutput("x", x)', "NFTest_update")
    compiler.update_expression_group(group, 'x = 2\noutput("x", x)')
    try:
        compiler.update_expression_group(
            group,
            '\ndef inc(a):\n    return a + 1\n\nx = inc(1)\ny = missing_func(1)\noutput("y", y)\n'
        )
    except Exception:
        pass
    else:
        raise AssertionError("failed update did not raise")
    leaked = [
        g.name
        for g in bpy.data.node_groups
        if g.name.startswith("NodeForge.preflight.")
        or (g.name.startswith("NodeForge.local.") and "preflight" in g.name)
    ]
    check(not leaked, f"preflight/local preflight groups leaked: {leaked}")
    print("UPDATE_SUCCESS_FAILURE_OK")


def run_mandelbrot_eval_check():
    """Compile and evaluate a small Mandelbrot library call through Blender."""
    group = compile_group('geo = mandelbrot(resolution=12, max_iter=8)\noutput("Geometry", geo)', "NFTest_mandelbrot_eval")
    mesh_data = bpy.data.meshes.new("NFTestMesh")
    obj = bpy.data.objects.new("NFTestObject", mesh_data)
    bpy.context.collection.objects.link(obj)
    mod = obj.modifiers.new("NodeForge", "NODES")
    mod.node_group = group
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        check(len(mesh.vertices) > 0, "Mandelbrot mesh has no vertices")
        attr = mesh.attributes.get("mandelbrot_color")
        check(attr is not None, "Mandelbrot color attribute missing")
        check(attr.data_type == "FLOAT_COLOR", "Mandelbrot color attribute type changed")
    finally:
        evaluated.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh_data, do_unlink=True)
    print("MANDELBROT_EVAL_OK")


def main():
    run_import_and_registry_checks()
    run_startup_shutdown_checks()
    run_compile_fixtures()
    run_math_table_dispatch_checks()
    run_lsystem_stage1_checks()
    run_library_checks()
    run_update_checks()
    run_mandelbrot_eval_check()
    print("NODEFORGE_REFACTOR_REGRESSION_OK")


if __name__ == "__main__":
    main()
