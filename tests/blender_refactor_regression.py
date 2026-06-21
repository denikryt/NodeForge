"""Blender headless regression checks for compiler-module refactors."""

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGE_PARENT = ROOT.parent
if str(PACKAGE_PARENT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_PARENT))

import bpy
import NodeForge
from NodeForge import compiler, library
from NodeForge.builtins import registry
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


def run_import_and_registry_checks():
    """Check module imports and authoritative builtin registry coverage."""
    for modname in ["compiler", "expression_compiler", "statement_compiler", "local_functions", "library_calls"]:
        __import__("NodeForge." + modname)
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
    run_library_checks()
    run_update_checks()
    run_mandelbrot_eval_check()
    print("NODEFORGE_REFACTOR_REGRESSION_OK")


if __name__ == "__main__":
    main()
