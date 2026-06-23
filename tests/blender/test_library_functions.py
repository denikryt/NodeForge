from helpers import *




def test_packaged_library_functions_and_helper_scoping():
    flat_probe = ROOT / 'functions' / 'flat_legacy_probe.py'
    flat_probe.write_text("def compile_call(comp, expr, depth=0):\n    raise AssertionError('flat layout loaded')\n", encoding='utf-8')
    try:
        check(not library.has_module_library_function('flat_legacy_probe'), 'legacy flat function module was discovered')
        check(not library.has_library_function('flat_legacy_probe'), 'legacy flat function appeared as library function')
    finally:
        flat_probe.unlink(missing_ok=True)
    for fname in ['copy_by_offsets', 'dragon_curve', 'fibonacci', 'fibonacci_spiral', 'koch_curve', 'mandelbrot', 'sierpinski_carpet']:
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
