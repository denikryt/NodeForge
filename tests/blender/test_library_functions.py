from helpers import *




def test_packaged_library_functions_and_helper_scoping():
    flat_probe = ROOT / 'functions' / 'flat_legacy_probe.py'
    flat_source = ROOT / 'functions' / 'stage2_flat_probe.nf'
    flat_probe.write_text("def compile_call(comp, expr, depth=0):\n    raise AssertionError('flat layout loaded')\n", encoding='utf-8')
    flat_source.write_text('value = input_float("Value", default=2.0)\noutput("Value", value)\n', encoding='utf-8')
    try:
        check(not library.has_module_library_function('flat_legacy_probe'), 'legacy flat function module was discovered')
        check(not library.has_library_function('flat_legacy_probe'), 'legacy flat function appeared as library function')
        check(library.has_library_function('stage2_flat_probe'), 'flat .nf function was not discovered')
        names = library.library_function_names()
        check('stage2_flat_probe' in names, 'flat .nf function missing from public function names')
        compile_group('from functions import stage2_flat_probe\nx = stage2_flat_probe(3)\noutput("x", x)', 'NFTest_flat_function_import')
        compile_group('from functions import *\nx = stage2_flat_probe(3)\noutput("x", x)', 'NFTest_flat_function_star_import')
    finally:
        flat_probe.unlink(missing_ok=True)
        flat_source.unlink(missing_ok=True)
    for fname in ['copy_by_offsets', 'layout_grid', 'grid_points', 'layout_circle', 'layout_spiral', 'spiral_points', 'layout_random', 'random_points', 'dragon_curve', 'fibonacci', 'fibonacci_spiral', 'koch_curve', 'mandelbrot', 'sierpinski_carpet']:
        group = compiler.create_library_function_group(fname)
        check(getattr(group, 'bl_idname', None) == 'GeometryNodeTree', fname)
    normal_names = set(bpy.data.node_groups.keys())
    reused = compiler.create_library_function_group('fibonacci')
    check(reused.name in bpy.data.node_groups, 'library group reuse did not return a live group')
    check(set(bpy.data.node_groups.keys()) == normal_names, 'library group reuse created unexpected groups')
    try:
        compile_group('geo = grid(4,4)\ngeo = apply_mandelbrot_material(geo, "x")\noutput("Geometry", geo)', 'NFTest_backend_scope_fail')
    except Exception:
        pass
    else:
        raise AssertionError('package-local backend helper leaked into normal source')
    print('LIBRARY_AND_SCOPE_OK')



def test_function_star_import_bindings_and_namespace_guards():
    star = compile_group('''
from functions import *
x = fibonacci(8)
output("x", x)
''', 'NFTest_function_star_import_fibonacci')
    check('fibonacci' not in [sock.name for sock in star.interface.items_tree if getattr(sock, 'in_out', None) == 'INPUT'], 'star-imported function leaked as group input')

    local = compile_group('''
from functions import *

def fib_value(n):
    return fibonacci(n)

x = fib_value(8)
output("x", x)
''', 'NFTest_function_star_import_local_function')
    check(getattr(local, 'bl_idname', None) == 'GeometryNodeTree', 'star import did not propagate into local function compilation')

    before_builtins = set(registry.CALLABLE_BUILTIN_NAMES)
    compile_group('from functions import *\nx = fibonacci(3)\noutput("x", x)', 'NFTest_function_star_no_global_mutation')
    check(set(registry.CALLABLE_BUILTIN_NAMES) == before_builtins, 'star import mutated callable builtins')

    expect_compile_error('x = fibonacci(8)\noutput("x", x)', 'NFTest_function_star_unimported_still_fails')
    expect_compile_error('from functions import *\ngeo = grid(4,4)\ngeo = apply_mandelbrot_material(geo, "x")\noutput("Geometry", geo)', 'NFTest_function_star_backend_helper_scope_fail')

    for source, name in (
        ('from functions import *\nfibonacci = 1\noutput("fibonacci", fibonacci)', 'NFTest_function_star_assignment_conflict'),
        ('from functions import *\ndef fibonacci(n):\n    return n\nx = fibonacci(1)\noutput("x", x)', 'NFTest_function_star_local_function_conflict'),
        ('from functions import fibonacci\nfrom functions import *\nx = 1\noutput("x", x)', 'NFTest_function_star_explicit_conflict'),
        ('from functions import *\nfrom functions import fibonacci as fibonacci\nx = 1\noutput("x", x)', 'NFTest_function_star_later_explicit_conflict'),
    ):
        expect_compile_error(source, name)
