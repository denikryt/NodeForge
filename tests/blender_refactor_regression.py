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
from NodeForge.systems.lsystem import resources as generated_resources
from NodeForge.systems.lsystem.runtime_tables import (
    DRAW_MASK_ATTR,
    HEADING_INDEX_ATTR,
    MOVE_MASK_ATTR,
    build_branch_free_command_table,
)
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

def _object_info_source(node):
    """Return the Object referenced by an Object Info node."""
    if hasattr(node, "object"):
        obj = getattr(node, "object", None)
        if obj is not None:
            return obj
    try:
        return node.inputs["Object"].default_value
    except Exception:
        pass
    for socket in node.inputs:
        if getattr(socket, "name", "") == "Object":
            return getattr(socket, "default_value", None)
    return None


def _static_lsystem_source(iterations=1, step=0.1):
    return """
geo = ls_system(ls_axiom("F"), ls_rule("F", "F+F--F+F"), ls_iterations(%d), ls_angle(60), ls_step(%s))
output("Geometry", geo)
""" % (iterations, step)


def _manifest_refs(group):
    manifest = generated_resources.read_group_manifest(group)
    check(manifest is not None, "expected generated-resource manifest")
    return manifest, generated_resources.manifest_resources(manifest)


def _owned_generated_id_keys():
    """Return all live NodeForge-generated Blender ID metadata keys."""
    keys = set()
    for id_obj in list(bpy.data.objects) + list(bpy.data.curves) + list(bpy.data.meshes):
        ref = generated_resources.read_id_metadata(id_obj)
        if ref is not None:
            keys.add((ref.kind, ref.name, ref.owner_group_uuid, ref.generation_uuid))
    return keys


def _ref_by_kind(refs, kind):
    for ref in refs:
        if ref.kind == kind:
            return ref
    raise AssertionError(f"missing generated {kind} ref")


def _collection_for_ref(ref):
    if ref.kind == "CURVE":
        return bpy.data.curves
    if ref.kind == "MESH":
        return bpy.data.meshes
    return bpy.data.objects


def _create_user_object_using_generated_curve(ref, name):
    curve = bpy.data.curves.get(ref.name)
    check(curve is not None, f"generated Curve missing before user-share test: {ref.name}")
    user_obj = bpy.data.objects.new(name, curve)
    check(generated_resources.read_id_metadata(user_obj) is None, "user-created Object unexpectedly has NodeForge metadata")
    check(user_obj.data is curve, "user Object did not retain generated Curve data")
    try:
        scene = getattr(bpy.context, "scene", None)
        collection = getattr(scene, "collection", None) or getattr(bpy.context, "collection", None)
        if collection is not None:
            collection.objects.link(user_obj)
    except Exception:
        pass
    return curve, user_obj


def _remove_user_object_and_generated_curve(user_obj, curve):
    try:
        if bpy.data.objects.get(user_obj.name) is user_obj:
            bpy.data.objects.remove(user_obj, do_unlink=True)
    except ReferenceError:
        pass
    except Exception:
        pass
    try:
        if bpy.data.curves.get(curve.name) is curve:
            generated_resources.delete_generated_id_object(curve)
            if bpy.data.curves.get(curve.name) is curve:
                bpy.data.curves.remove(curve, do_unlink=True)
    except ReferenceError:
        pass
    except Exception:
        pass


def _assert_static_baked_group(group):
    manifest, refs = _manifest_refs(group)
    check(len(refs) == 2, f"expected Curve/Object resources, got {refs}")
    kinds = {ref.kind for ref in refs}
    check(kinds == {"CURVE", "OBJECT"}, f"unexpected generated resource kinds: {kinds}")
    for ref in refs:
        coll = bpy.data.curves if ref.kind == "CURVE" else bpy.data.objects
        id_obj = coll.get(ref.name)
        check(id_obj is not None, f"generated {ref.kind} missing: {ref.name}")
        meta = generated_resources.read_id_metadata(id_obj)
        check(meta is not None, f"generated {ref.kind} lacks positive ownership metadata")
        check(meta.owner_group_uuid == manifest["owner_group_uuid"], "ID/group owner UUID mismatch")
    object_infos = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeObjectInfo"]
    check(len(object_infos) == 1, f"expected one Object Info node, got {len(object_infos)}")
    obj = _object_info_source(object_infos[0])
    check(obj is not None and generated_resources.read_id_metadata(obj) is not None, "Object Info does not source owned hidden object")
    geometry_outputs = [socket for socket in object_infos[0].outputs if getattr(socket, "name", "") == "Geometry"]
    check(geometry_outputs, "Object Info Geometry output missing")
    curve_line_nodes = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeCurvePrimitiveLine"]
    check(not curve_line_nodes, "static baked backend used Stage 1 Curve Line nodes")
    return manifest, refs, obj


def run_lsystem_stage2_checks():
    """Exercise Stage 2 static baked ownership, update, cutover, and cleanup behavior."""
    group = compile_group(_static_lsystem_source(iterations=2), "NFTest_lsystem_stage2_static")
    old_manifest, old_refs, old_obj = _assert_static_baked_group(group)
    old_names = {(ref.kind, ref.name) for ref in old_refs}

    large = compile_group('''
geo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(11), ls_angle(0), ls_step(0.01))
output("Geometry", geo)
''', "NFTest_lsystem_stage2_large_static")
    _assert_static_baked_group(large)
    check(len(large.nodes) <= 4, f"static baked node graph grew per segment: {len(large.nodes)} nodes")

    before_resource_failure = _owned_generated_id_keys()
    generated_resources._TEST_FAIL_AFTER_OBJECT_CREATE = True
    try:
        compile_group(_static_lsystem_source(iterations=1, step=0.15), "NFTest_lsystem_stage2_resource_failure")
    except RuntimeError:
        pass
    else:
        raise AssertionError("fault-injected generated-object failure did not raise")
    check(_owned_generated_id_keys() == before_resource_failure, "generated-object failure leaked temporary IDs")

    runtime = compile_group('''
angle_value = input_float("Angle", default=60)
geo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(2), ls_angle(angle_value), ls_step(0.1))
output("Geometry", geo)
''', "NFTest_lsystem_stage2_runtime_branch_free")
    runtime_manifest, runtime_refs = _manifest_refs(runtime)
    check({ref.kind for ref in runtime_refs} == {"MESH", "OBJECT"}, "branch-free runtime L-system used static Curve resources")
    check(any(ref.role == "branch_free_runtime_command_mesh" for ref in runtime_refs), "branch-free runtime command Mesh role missing")
    check(not any(getattr(node, "bl_idname", "") == "GeometryNodeCurvePrimitiveLine" for node in runtime.nodes), "branch-free runtime still uses bounded Stage 1 path")
    check(runtime_manifest["owner_group_uuid"], "branch-free runtime manifest lacks owner UUID")

    compiler.update_expression_group(group, _static_lsystem_source(iterations=1, step=0.2))
    new_manifest, new_refs, _new_obj = _assert_static_baked_group(group)
    check(new_manifest["owner_group_uuid"] == old_manifest["owner_group_uuid"], "owner_group_uuid was not preserved across update")
    for kind, name in old_names:
        coll = bpy.data.curves if kind == "CURVE" else bpy.data.objects
        check(coll.get(name) is None, f"old generated {kind} survived successful replacement: {name}")

    previous_refs = list(new_refs)
    runtime_update_source = '''
angle_value = input_float("Angle", default=60)
geo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(2), ls_angle(angle_value), ls_step(0.1))
output("Geometry", geo)
'''
    compiler.update_expression_group(group, runtime_update_source)
    runtime_manifest, runtime_refs = _manifest_refs(group)
    check({ref.kind for ref in runtime_refs} == {"MESH", "OBJECT"}, "runtime replacement did not commit branch-free Mesh/Object manifest")
    check(runtime_manifest["owner_group_uuid"] == new_manifest["owner_group_uuid"], "runtime manifest did not preserve owner UUID")
    check(not any(getattr(node, "bl_idname", "") == "GeometryNodeCurvePrimitiveLine" for node in group.nodes), "runtime update used bounded Stage 1 path")
    check(compiler._extract_group_source(group) == runtime_update_source, "runtime update did not store replacement source")
    for ref in previous_refs:
        coll = _collection_for_ref(ref)
        check(coll.get(ref.name) is None, f"old generated resource survived runtime replacement: {ref.name}")

    compiler.update_expression_group(group, _static_lsystem_source(iterations=1, step=0.25))
    grid_manifest, grid_refs, _ = _assert_static_baked_group(group)
    previous_refs = list(grid_refs)
    grid_source = 'geo = grid(2, 2)\noutput("Geometry", geo)'
    compiler.update_expression_group(group, grid_source)
    empty_manifest = generated_resources.read_group_manifest(group)
    check(empty_manifest is not None and empty_manifest["resources"] == [], "zero-resource replacement did not commit empty manifest")
    check(empty_manifest["owner_group_uuid"] == grid_manifest["owner_group_uuid"], "empty manifest did not preserve owner UUID")
    check(compiler._extract_group_source(group) == grid_source, "zero-resource update did not store replacement source")
    for ref in previous_refs:
        coll = bpy.data.curves if ref.kind == "CURVE" else bpy.data.objects
        check(coll.get(ref.name) is None, f"old generated resource survived zero-resource update: {ref.name}")

    shared_group = compile_group(_static_lsystem_source(iterations=1, step=0.31), "NFTest_lsystem_stage2_shared_recompile")
    _shared_manifest, shared_refs, _ = _assert_static_baked_group(shared_group)
    shared_curve_ref = _ref_by_kind(shared_refs, "CURVE")
    shared_object_ref = _ref_by_kind(shared_refs, "OBJECT")
    shared_curve, shared_user_obj = _create_user_object_using_generated_curve(shared_curve_ref, "NFTest_lsystem_stage2_user_curve_recompile")
    try:
        compiler.update_expression_group(shared_group, _static_lsystem_source(iterations=2, step=0.32))
        check(bpy.data.objects.get(shared_object_ref.name) is None, "recompile left old generated Object")
        check(bpy.data.curves.get(shared_curve.name) is shared_curve, "recompile deleted generated Curve still used by user Object")
        check(bpy.data.objects.get(shared_user_obj.name) is shared_user_obj, "recompile deleted user Object sharing generated Curve")
        check(shared_user_obj.data is shared_curve, "recompile unlinked user Object from generated Curve")
    finally:
        _remove_user_object_and_generated_curve(shared_user_obj, shared_curve)

    compiler.update_expression_group(group, _static_lsystem_source(iterations=1, step=0.3))
    stable_manifest, stable_refs, stable_obj = _assert_static_baked_group(group)
    stable_source = compiler._extract_group_source(group)
    before_failed_compile_keys = _owned_generated_id_keys()
    try:
        compiler.update_expression_group(group, 'geo = missing_func(1)\noutput("Geometry", geo)')
    except Exception:
        pass
    else:
        raise AssertionError("failed replacement compile did not raise")
    after_fail_manifest = generated_resources.read_group_manifest(group)
    check(after_fail_manifest["generation_uuid"] == stable_manifest["generation_uuid"], "failed compile changed group manifest")
    check(bpy.data.objects.get(stable_obj.name) is stable_obj, "failed compile removed old generated object")
    check(compiler._extract_group_source(group) == stable_source, "failed compile changed stored source")
    check(_owned_generated_id_keys() == before_failed_compile_keys, "failed replacement compile changed generated ID set")

    before_cutover_keys = _owned_generated_id_keys()
    compiler._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        compiler.update_expression_group(group, _static_lsystem_source(iterations=2, step=0.4))
    except RuntimeError:
        pass
    else:
        raise AssertionError("fault-injected cutover did not raise")
    after_cutover_manifest = generated_resources.read_group_manifest(group)
    check(after_cutover_manifest["generation_uuid"] == stable_manifest["generation_uuid"], "cutover failure changed group manifest")
    check(bpy.data.objects.get(stable_obj.name) is stable_obj, "cutover failure removed old generated object")
    check(compiler._extract_group_source(group) == stable_source, "cutover failure changed stored source")
    check(_owned_generated_id_keys() == before_cutover_keys, "cutover failure leaked or deleted generated IDs")
    object_infos = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeObjectInfo"]
    check(object_infos and _object_info_source(object_infos[0]) is stable_obj, "cutover failure did not restore Object Info source")

    user_curve = bpy.data.curves.new("NodeForge.fake.UserCurve", "CURVE")
    user_obj = bpy.data.objects.new("NodeForge.fake.UserObject", user_curve)
    try:
        generated_resources.cleanup_restart_orphans()
        check(bpy.data.curves.get(user_curve.name) is user_curve, "cleanup deleted user curve without metadata")
        check(bpy.data.objects.get(user_obj.name) is user_obj, "cleanup deleted user object without metadata")
    finally:
        bpy.data.objects.remove(user_obj, do_unlink=True)
        bpy.data.curves.remove(user_curve, do_unlink=True)

    orphan_group = compile_group(_static_lsystem_source(iterations=1, step=0.5), "NFTest_lsystem_stage2_orphan")
    orphan_manifest, orphan_refs, _ = _assert_static_baked_group(orphan_group)
    generated_resources.write_empty_manifest(orphan_group, orphan_manifest["owner_group_uuid"])
    generated_resources.cleanup_restart_orphans()
    for ref in orphan_refs:
        coll = bpy.data.curves if ref.kind == "CURVE" else bpy.data.objects
        check(coll.get(ref.name) is None, f"restart orphan cleanup left {ref.name}")

    shared_orphan_group = compile_group(_static_lsystem_source(iterations=1, step=0.55), "NFTest_lsystem_stage2_shared_orphan")
    shared_orphan_manifest, shared_orphan_refs, _ = _assert_static_baked_group(shared_orphan_group)
    shared_orphan_curve_ref = _ref_by_kind(shared_orphan_refs, "CURVE")
    shared_orphan_object_ref = _ref_by_kind(shared_orphan_refs, "OBJECT")
    shared_orphan_curve, shared_orphan_user_obj = _create_user_object_using_generated_curve(shared_orphan_curve_ref, "NFTest_lsystem_stage2_user_curve_orphan")
    try:
        generated_resources.write_empty_manifest(shared_orphan_group, shared_orphan_manifest["owner_group_uuid"])
        generated_resources.cleanup_restart_orphans()
        check(bpy.data.objects.get(shared_orphan_object_ref.name) is None, "restart orphan cleanup left generated Object sharing Curve")
        check(bpy.data.curves.get(shared_orphan_curve.name) is shared_orphan_curve, "restart orphan cleanup deleted generated Curve still used by user Object")
        check(bpy.data.objects.get(shared_orphan_user_obj.name) is shared_orphan_user_obj, "restart orphan cleanup deleted user Object sharing generated Curve")
        check(shared_orphan_user_obj.data is shared_orphan_curve, "restart orphan cleanup unlinked user Object from generated Curve")
    finally:
        _remove_user_object_and_generated_curve(shared_orphan_user_obj, shared_orphan_curve)

    shutdown_group = compile_group(_static_lsystem_source(iterations=1, step=0.6), "NFTest_lsystem_stage2_shutdown")
    _shutdown_manifest, shutdown_refs, _ = _assert_static_baked_group(shutdown_group)
    generated_resources.cleanup_live_group_resources()
    for ref in shutdown_refs:
        coll = bpy.data.curves if ref.kind == "CURVE" else bpy.data.objects
        check(coll.get(ref.name) is None, f"shutdown cleanup left {ref.name}")

    shared_shutdown_group = compile_group(_static_lsystem_source(iterations=1, step=0.65), "NFTest_lsystem_stage2_shared_shutdown")
    _shared_shutdown_manifest, shared_shutdown_refs, _ = _assert_static_baked_group(shared_shutdown_group)
    shared_shutdown_curve_ref = _ref_by_kind(shared_shutdown_refs, "CURVE")
    shared_shutdown_object_ref = _ref_by_kind(shared_shutdown_refs, "OBJECT")
    shared_shutdown_curve, shared_shutdown_user_obj = _create_user_object_using_generated_curve(shared_shutdown_curve_ref, "NFTest_lsystem_stage2_user_curve_shutdown")
    try:
        generated_resources.cleanup_live_group_resources()
        check(bpy.data.objects.get(shared_shutdown_object_ref.name) is None, "shutdown cleanup left generated Object sharing Curve")
        check(bpy.data.curves.get(shared_shutdown_curve.name) is shared_shutdown_curve, "shutdown cleanup deleted generated Curve still used by user Object")
        check(bpy.data.objects.get(shared_shutdown_user_obj.name) is shared_shutdown_user_obj, "shutdown cleanup deleted user Object sharing generated Curve")
        check(shared_shutdown_user_obj.data is shared_shutdown_curve, "shutdown cleanup unlinked user Object from generated Curve")
    finally:
        _remove_user_object_and_generated_curve(shared_shutdown_user_obj, shared_shutdown_curve)
    generated_resources.cleanup_live_group_resources()
    generated_resources.cleanup_restart_orphans()
    print("LSYSTEM_STAGE2_OK")


def _socket_identifier_by_name(group, socket_name, in_out="INPUT"):
    for item in getattr(group.interface, "items_tree", []):
        if (
            getattr(item, "item_type", None) == "SOCKET"
            and getattr(item, "name", None) == socket_name
            and getattr(item, "in_out", None) == in_out
        ):
            return getattr(item, "identifier", None)
    raise AssertionError(f"missing {in_out} interface socket {socket_name!r}")


def _new_stage3_eval_wrapper(compiled_group, name):
    """Wrap a compiled L-system group with Curve to Mesh so depsgraph output is inspectable."""
    wrapper = bpy.data.node_groups.new(name, "GeometryNodeTree")
    wrapper.interface.new_socket(name="Angle", in_out="INPUT", socket_type="NodeSocketFloat")
    wrapper.interface.new_socket(name="Step", in_out="INPUT", socket_type="NodeSocketFloat")
    wrapper.interface.new_socket(name="Geometry", in_out="OUTPUT", socket_type="NodeSocketGeometry")
    group_input = wrapper.nodes.new("NodeGroupInput")
    group_output = wrapper.nodes.new("NodeGroupOutput")
    group_output.is_active_output = True
    group_node = wrapper.nodes.new("GeometryNodeGroup")
    group_node.node_tree = compiled_group
    curve_to_mesh = wrapper.nodes.new("GeometryNodeCurveToMesh")
    wrapper.links.new(group_input.outputs["Angle"], group_node.inputs["Angle"])
    wrapper.links.new(group_input.outputs["Step"], group_node.inputs["Step"])
    wrapper.links.new(group_node.outputs["Geometry"], curve_to_mesh.inputs["Curve"])
    wrapper.links.new(curve_to_mesh.outputs["Mesh"], group_output.inputs["Geometry"])
    return wrapper


def _modifier_input_prop(modifier, group, socket_name):
    identifier = _socket_identifier_by_name(group, socket_name, "INPUT")
    try:
        prop = getattr(modifier.properties.inputs, identifier)
    except Exception as exc:
        raise AssertionError(f"modifier input {socket_name!r} / {identifier!r} is unavailable") from exc
    if not hasattr(prop, "value"):
        raise AssertionError(f"modifier input {socket_name!r} / {identifier!r} has no runtime value property")
    return prop


def _set_modifier_input(modifier, group, socket_name, value):
    prop = _modifier_input_prop(modifier, group, socket_name)
    try:
        prop.value = float(value)
    except Exception as exc:
        raise AssertionError(f"could not set modifier input {socket_name!r}") from exc


def _attach_stage3_eval_modifier(compiled_group, name):
    wrapper = _new_stage3_eval_wrapper(compiled_group, name + "_Wrapper")
    mesh_data = bpy.data.meshes.new(name + "_BaseMesh")
    obj = bpy.data.objects.new(name + "_Object", mesh_data)
    bpy.context.collection.objects.link(obj)
    mod = obj.modifiers.new("NodeForge", "NODES")
    mod.node_group = wrapper
    return wrapper, obj, mesh_data, mod


def _evaluated_mesh_snapshot(obj):
    depsgraph = bpy.context.evaluated_depsgraph_get()
    depsgraph.update()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        vertices = tuple(tuple(round(float(coord), 6) for coord in vertex.co) for vertex in mesh.vertices)
        edges = tuple(tuple(int(index) for index in edge.vertices) for edge in mesh.edges)
        polygons = len(mesh.polygons)
    finally:
        evaluated.to_mesh_clear()
    return vertices, edges, polygons


def _cleanup_stage3_eval_objects(wrapper, obj, mesh_data):
    try:
        if bpy.data.objects.get(obj.name) is obj:
            bpy.data.objects.remove(obj, do_unlink=True)
    except Exception:
        pass
    try:
        if bpy.data.meshes.get(mesh_data.name) is mesh_data:
            bpy.data.meshes.remove(mesh_data, do_unlink=True)
    except Exception:
        pass
    try:
        if bpy.data.node_groups.get(wrapper.name) is wrapper:
            bpy.data.node_groups.remove(wrapper, do_unlink=True)
    except Exception:
        pass


def _assert_stage3_modifier_runtime_updates(compiled_group):
    """Evaluate branch-free runtime output and mutate Angle/Step without recompilation."""
    manifest_before = generated_resources.read_group_manifest(compiled_group)
    generation_before = manifest_before["generation_uuid"]
    wrapper, obj, mesh_data, mod = _attach_stage3_eval_modifier(compiled_group, "NFTest_lsystem_stage3_runtime_eval")
    try:
        _set_modifier_input(mod, wrapper, "Angle", 90.0)
        _set_modifier_input(mod, wrapper, "Step", 1.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_90, edges_90, polygons_90 = _evaluated_mesh_snapshot(obj)
        check(vertices_90 == ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 1.0, 0.0)), f"runtime Angle=90 vertices changed: {vertices_90}")
        check(edges_90 == ((0, 1), (2, 3)), f"runtime Angle=90 edges changed: {edges_90}")
        check(polygons_90 == 0, "runtime evaluated mesh unexpectedly has polygons")

        _set_modifier_input(mod, wrapper, "Angle", 0.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_angle_changed, edges_angle_changed, _polygons = _evaluated_mesh_snapshot(obj)
        check(vertices_angle_changed == ((0.0, 0.0, 0.0), (1.0, 0.0, 0.0), (1.0, 0.0, 0.0), (2.0, 0.0, 0.0)), f"runtime Angle update did not change evaluated positions: {vertices_angle_changed}")
        check(edges_angle_changed == edges_90, "runtime Angle update changed topology")
        check(generated_resources.read_group_manifest(compiled_group)["generation_uuid"] == generation_before, "runtime Angle update churned generated resources")

        _set_modifier_input(mod, wrapper, "Step", 2.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices_step_changed, edges_step_changed, _polygons = _evaluated_mesh_snapshot(obj)
        check(vertices_step_changed == ((0.0, 0.0, 0.0), (2.0, 0.0, 0.0), (2.0, 0.0, 0.0), (4.0, 0.0, 0.0)), f"runtime Step update did not scale evaluated positions: {vertices_step_changed}")
        check(edges_step_changed == edges_90, "runtime Step update changed topology")
        check(generated_resources.read_group_manifest(compiled_group)["generation_uuid"] == generation_before, "runtime Step update churned generated resources")
    finally:
        _cleanup_stage3_eval_objects(wrapper, obj, mesh_data)


def run_lsystem_stage3_checks():
    """Exercise Stage 3 branch-free vectorized runtime backend and Mesh ownership."""
    table = build_branch_free_command_table("+F-F")
    check(table.vertex_count == 8 and table.edge_count == 4, "branch-free table size mismatch")
    check(table.move_mask == (0.0, 0.0, 0.0, 1.0, 0.0, 0.0, 0.0, 1.0), "branch-free move masks drifted")
    check(table.heading_index == (0.0, 1.0, 1.0, 1.0, 1.0, 0.0, 0.0, 0.0), "branch-free heading semantics drifted")
    check(table.draw_mask == (False, True, False, True), "branch-free draw masks drifted")
    table = build_branch_free_command_table("XfF")
    check(table.move_mask == (0.0, 0.0, 0.0, 1.0, 0.0, 1.0), "ignored/move command masks drifted")
    check(table.draw_mask == (False, False, True), "ignored/move draw masks drifted")
    try:
        build_branch_free_command_table("F[+F]")
    except CompileError:
        pass
    else:
        raise AssertionError("branch-free table builder accepted branch commands")
    try:
        build_branch_free_command_table("Xf")
    except CompileError:
        pass
    else:
        raise AssertionError("branch-free table builder accepted zero draw stream")

    eval_source = '''
angle_value = input_float("Angle", default=90.0)
step_value = input_float("Step", default=1.0)
geo = ls_system(ls_axiom("F+F"), ls_iterations(0), ls_angle(angle_value), ls_step(step_value))
output("Geometry", geo)
'''
    eval_group = compile_group(eval_source, "NFTest_lsystem_stage3_runtime_eval_source")
    _assert_stage3_modifier_runtime_updates(eval_group)

    large_source = '''
angle_value = input_float("Angle", default=0.0)
step_value = input_float("Step", default=1.0)
geo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(11), ls_angle(angle_value), ls_step(step_value))
output("Geometry", geo)
'''
    large_group = compile_group(large_source, "NFTest_lsystem_stage3_large_branch_free_runtime")
    large_manifest, large_refs = _manifest_refs(large_group)
    check({ref.kind for ref in large_refs} == {"MESH", "OBJECT"}, "large branch-free runtime did not use generated Mesh/Object backend")
    large_mesh = bpy.data.meshes.get(_ref_by_kind(large_refs, "MESH").name)
    check(large_mesh is not None and len(large_mesh.edges) == 2048, "large branch-free command Mesh did not exceed Stage 1 segment budget")
    check(len(large_group.nodes) < 40, f"large branch-free runtime node graph grew unexpectedly: {len(large_group.nodes)} nodes")
    wrapper, obj, mesh_data, mod = _attach_stage3_eval_modifier(large_group, "NFTest_lsystem_stage3_large_runtime_eval")
    try:
        _set_modifier_input(mod, wrapper, "Angle", 0.0)
        _set_modifier_input(mod, wrapper, "Step", 1.0)
        obj.update_tag()
        bpy.context.view_layer.update()
        vertices, edges, polygons = _evaluated_mesh_snapshot(obj)
        check(len(vertices) == 4096 and len(edges) == 2048 and polygons == 0, "large branch-free runtime evaluated output size changed")
        check(vertices[-1] == (2048.0, 0.0, 0.0), f"large branch-free runtime endpoint changed: {vertices[-1]}")
        check(generated_resources.read_group_manifest(large_group) == large_manifest, "large branch-free runtime evaluation churned manifest")
    finally:
        _cleanup_stage3_eval_objects(wrapper, obj, mesh_data)

    source = '''
angle_value = input_float("Angle", default=60.0)
step_value = input_float("Step", default=0.1)
geo = ls_system(ls_axiom("F"), ls_rule("F", "F+F--F+F"), ls_iterations(3), ls_angle(angle_value), ls_step(step_value))
output("Geometry", geo)
'''
    group = compile_group(source, "NFTest_lsystem_stage3_branch_free_runtime")
    manifest, refs = _manifest_refs(group)
    kinds = {ref.kind for ref in refs}
    check(kinds == {"MESH", "OBJECT"}, f"branch-free runtime generated unexpected resources: {kinds}")
    mesh_ref = _ref_by_kind(refs, "MESH")
    object_ref = _ref_by_kind(refs, "OBJECT")
    mesh = bpy.data.meshes.get(mesh_ref.name)
    obj = bpy.data.objects.get(object_ref.name)
    check(mesh is not None and obj is not None, "branch-free runtime Mesh/Object missing")
    check(obj.data is mesh, "branch-free command Object does not reference command Mesh")
    for ref in refs:
        id_obj = _collection_for_ref(ref).get(ref.name)
        meta = generated_resources.read_id_metadata(id_obj)
        check(meta is not None, f"generated {ref.kind} lacks metadata")
        check(meta.owner_group_uuid == manifest["owner_group_uuid"], "branch-free owner UUID mismatch")
    check(len(mesh.vertices) == 2 * 148, "command Mesh vertex count does not match expanded stream")
    check(len(mesh.edges) == 148, "command Mesh edge count does not match expanded stream")
    for attr_name, domain, data_type, expected_len in [
        (MOVE_MASK_ATTR, "POINT", "FLOAT", len(mesh.vertices)),
        (HEADING_INDEX_ATTR, "POINT", "FLOAT", len(mesh.vertices)),
        (DRAW_MASK_ATTR, "EDGE", "BOOLEAN", len(mesh.edges)),
    ]:
        attr = mesh.attributes.get(attr_name)
        check(attr is not None, f"command Mesh attribute missing: {attr_name}")
        check(attr.domain == domain, f"{attr_name} domain changed: {attr.domain}")
        check(attr.data_type == data_type, f"{attr_name} data type changed: {attr.data_type}")
        check(len(attr.data) == expected_len, f"{attr_name} length mismatch")
    node_types = [getattr(node, "bl_idname", "") for node in group.nodes]
    check("GeometryNodeCurvePrimitiveLine" not in node_types, "branch-free runtime used per-segment Curve Line nodes")
    for required in {
        "GeometryNodeObjectInfo",
        "GeometryNodeInputNamedAttribute",
        "GeometryNodeAccumulateField",
        "GeometryNodeSetPosition",
        "GeometryNodeDeleteGeometry",
        "GeometryNodeMeshToCurve",
    }:
        check(required in node_types, f"branch-free runtime graph missing {required}")
    check(len(group.nodes) < 40, f"branch-free runtime node graph grew unexpectedly: {len(group.nodes)} nodes")
    object_infos = [node for node in group.nodes if getattr(node, "bl_idname", "") == "GeometryNodeObjectInfo"]
    check(object_infos and _object_info_source(object_infos[0]) is obj, "Object Info does not source branch-free command Object")

    old_keys = _owned_generated_id_keys()
    old_manifest = generated_resources.read_group_manifest(group)
    compiler.update_expression_group(group, source.replace('ls_iterations(3)', 'ls_iterations(2)'))
    new_manifest, new_refs = _manifest_refs(group)
    check(new_manifest["generation_uuid"] != old_manifest["generation_uuid"], "successful branch-free recompile did not advance generation")
    check(_owned_generated_id_keys() != old_keys, "successful branch-free recompile did not replace generated IDs")
    for ref in refs:
        check(_collection_for_ref(ref).get(ref.name) is None, f"successful branch-free recompile left old {ref.kind}")
    check({ref.kind for ref in new_refs} == {"MESH", "OBJECT"}, "branch-free recompile lost Mesh/Object manifest")

    runtime_refs_before_static = list(new_refs)
    compiler.update_expression_group(group, source.replace('ls_angle(angle_value)', 'ls_angle(60.0)').replace('ls_step(step_value)', 'ls_step(0.1)'))
    static_manifest_after_runtime, static_refs_after_runtime, _ = _assert_static_baked_group(group)
    check(static_manifest_after_runtime["owner_group_uuid"] == new_manifest["owner_group_uuid"], "runtime-to-static replacement did not preserve owner UUID")
    for ref in runtime_refs_before_static:
        check(_collection_for_ref(ref).get(ref.name) is None, f"old branch-free generated resource survived static replacement: {ref.name}")
    compiler.update_expression_group(group, source.replace('ls_iterations(3)', 'ls_iterations(2)'))
    stable_runtime_manifest, stable_runtime_refs = _manifest_refs(group)
    check({ref.kind for ref in stable_runtime_refs} == {"MESH", "OBJECT"}, "static-to-runtime replacement did not restore Mesh/Object manifest")
    for ref in static_refs_after_runtime:
        check(_collection_for_ref(ref).get(ref.name) is None, f"old static generated resource survived branch-free replacement: {ref.name}")

    stable_manifest, stable_refs = _manifest_refs(group)
    stable_source = compiler._extract_group_source(group)
    before_failure = _owned_generated_id_keys()
    generated_resources._TEST_FAIL_AFTER_MESH_ATTRIBUTE_WRITE = True
    try:
        compiler.update_expression_group(group, source.replace('ls_iterations(3)', 'ls_iterations(1)'))
    except RuntimeError:
        pass
    else:
        raise AssertionError("fault-injected command Mesh attribute failure did not raise")
    check(generated_resources.read_group_manifest(group)["generation_uuid"] == stable_manifest["generation_uuid"], "failed branch-free update changed manifest")
    check(compiler._extract_group_source(group) == stable_source, "failed branch-free update changed stored source")
    check(_owned_generated_id_keys() == before_failure, "failed branch-free update leaked or deleted generated IDs")
    for ref in stable_refs:
        check(_collection_for_ref(ref).get(ref.name) is not None, f"failed branch-free update removed stable {ref.kind}")

    before_cutover = _owned_generated_id_keys()
    compiler._TEST_CUTOVER_FAIL_AFTER_RESET = True
    try:
        compiler.update_expression_group(group, source.replace('ls_iterations(3)', 'ls_iterations(1)'))
    except RuntimeError:
        pass
    else:
        raise AssertionError("fault-injected branch-free cutover did not raise")
    check(generated_resources.read_group_manifest(group)["generation_uuid"] == stable_manifest["generation_uuid"], "branch-free cutover failure changed manifest")
    check(compiler._extract_group_source(group) == stable_source, "branch-free cutover failure changed source")
    check(_owned_generated_id_keys() == before_cutover, "branch-free cutover failure leaked or deleted generated IDs")

    shared_group = compile_group(source.replace('ls_iterations(3)', 'ls_iterations(1)'), "NFTest_lsystem_stage3_shared_mesh")
    shared_manifest, shared_refs = _manifest_refs(shared_group)
    shared_mesh_ref = _ref_by_kind(shared_refs, "MESH")
    shared_object_ref = _ref_by_kind(shared_refs, "OBJECT")
    shared_mesh = bpy.data.meshes.get(shared_mesh_ref.name)
    user_obj = bpy.data.objects.new("NFTest_lsystem_stage3_user_mesh", shared_mesh)
    try:
        bpy.context.collection.objects.link(user_obj)
    except Exception:
        pass
    try:
        generated_resources.write_empty_manifest(shared_group, shared_manifest["owner_group_uuid"])
        generated_resources.cleanup_restart_orphans()
        check(bpy.data.objects.get(shared_object_ref.name) is None, "restart cleanup left generated command Object")
        check(bpy.data.meshes.get(shared_mesh.name) is shared_mesh, "restart cleanup deleted generated Mesh used by user Object")
        check(bpy.data.objects.get(user_obj.name) is user_obj, "restart cleanup deleted user Object sharing Mesh")
    finally:
        try:
            if bpy.data.objects.get(user_obj.name) is user_obj:
                bpy.data.objects.remove(user_obj, do_unlink=True)
        except Exception:
            pass
        try:
            if bpy.data.meshes.get(shared_mesh.name) is shared_mesh:
                bpy.data.meshes.remove(shared_mesh, do_unlink=True)
        except Exception:
            pass

    static_group = compile_group(_static_lsystem_source(iterations=1), "NFTest_lsystem_stage3_static_unchanged")
    _static_manifest, static_refs, _ = _assert_static_baked_group(static_group)
    check({ref.kind for ref in static_refs} == {"CURVE", "OBJECT"}, "static baked backend started using command Mesh")

    branched_runtime = compile_group('''
angle_value = input_float("Angle", default=25.0)
geo = ls_system(ls_axiom("F[+F]F[-F]F"), ls_iterations(0), ls_angle(angle_value), ls_step(0.1))
output("Geometry", geo)
''', "NFTest_lsystem_stage3_branched_runtime_unchanged")
    check(generated_resources.read_group_manifest(branched_runtime) is None, "branched runtime unexpectedly created generated resources")
    check(any(getattr(node, "bl_idname", "") == "GeometryNodeCurvePrimitiveLine" for node in branched_runtime.nodes), "branched runtime no longer uses bounded Stage 1 backend")
    expect_compile_error('''
angle_value = input_float("Angle", default=25.0)
geo = ls_system(ls_axiom("F[+F]"), ls_rule("F", "FF"), ls_iterations(10), ls_angle(angle_value), ls_step(0.1))
output("Geometry", geo)
''', "NFTest_lsystem_stage3_branched_runtime_budget")
    print("LSYSTEM_STAGE3_OK")

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
    run_lsystem_stage2_checks()
    run_lsystem_stage3_checks()
    run_library_checks()
    run_update_checks()
    run_mandelbrot_eval_check()
    print("NODEFORGE_REFACTOR_REGRESSION_OK")


if __name__ == "__main__":
    main()
