from helpers import *


STANDARD_MATH_NAMES = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | {'ln', 'clamp', 'mix', 'select', 'map_range', 'noise', 'random_value'}
STANDARD_MATH_PARAM_ORDER = {
    **{name: ('value',) for name in _FLOAT_FUNCS_1},
    **{name: ('a', 'b') for name in _FLOAT_FUNCS_2},
    'ln': ('value',),
    'clamp': ('value', 'min', 'max'),
    'mix': ('a', 'b', 'factor'),
    'select': ('cond', 'true', 'false'),
    'map_range': ('value', 'from_min', 'from_max', 'to_min', 'to_max'),
}


def test_math_compile_and_blender_enum_contracts():
    names = set(systems_registry.constructor_names())
    check(STANDARD_MATH_NAMES <= names, 'standard math names missing from package-backed constructor registry')
    check(systems_registry.constructor_owner('sin').package_id == 'nodeforge.standard', 'sin is not owned by nodeforge.standard')
    check(callable(systems_registry.get_handler('sin')), 'sin handler is not callable')
    math_ops = {item.identifier for item in bpy.types.ShaderNodeMath.bl_rna.properties['operation'].enum_items}
    invalid_ops = sorted((set(_FLOAT_FUNCS_1.values()) | set(_FLOAT_FUNCS_2.values())) - math_ops)
    check(not invalid_ops, f'constants contain unsupported ShaderNodeMath operations: {invalid_ops}')
    positional_lines = []
    for index, name in enumerate(sorted(_FLOAT_FUNCS_1)):
        positional_lines.append(f'p{index} = {name}(0.5)')
    base = len(positional_lines)
    for index, name in enumerate(sorted(_FLOAT_FUNCS_2)):
        positional_lines.append(f'p{base + index} = {name}(0.75, 0.25)')
    positional_lines.extend(['ln_v = ln(2)', 'clamp_v = clamp(2, 0, 1)', 'mix_v = mix(0, 1, 0.5)', 'select_v = select(True, 1, 0)', 'map_range_v = map_range(0.5, 0, 1, -1, 1)', 'noise_v = noise(vector(0,0,0), scale=1, detail=2, roughness=0.5)', 'random_v = random_value(0, 1, seed=3)', "output('v', clamp_v)"])
    compile_group('\n'.join(positional_lines), 'NFTest_math_all_positional')
    keyword_lines = []
    for index, name in enumerate(sorted(STANDARD_MATH_PARAM_ORDER)):
        keyword_lines.append(f'k{index} = {_math_keyword_expr(name, STANDARD_MATH_PARAM_ORDER[name])}')
    keyword_lines.append("output('v', k0)")
    compile_group('\n'.join(keyword_lines), 'NFTest_math_all_keywords')
    compile_group("from functions import smoothstep\nx = smoothstep(edge0=0, edge1=1)\noutput('x', x)", 'NFTest_library_function_keyword_defaults')
    error_sources = ["from functions import smoothstep\nx = smoothstep(edge0=0, edge1=1, value=0.5)\noutput('x', x)", "from functions import smoothstep\nx = smoothstep(0, edge0=1, edge1=2)\noutput('x', x)", "from functions import smoothstep\nx = smoothstep(**foo)\noutput('x', x)", "x = smoothstep(0, 1, 0.5)\noutput('x', x)", "x = lerp(0, 1, 0.5)\noutput('x', x)", "x = frac(0.5)\noutput('x', x)"]
    for index, source in enumerate(error_sources):
        try:
            compile_group(source, f'NFTest_math_keyword_error_{index}')
        except CompileError:
            pass
        else:
            raise AssertionError(f'math keyword error fixture {index} did not fail')
    expr = ast.parse('__missing__(1)', mode='eval').body
    try:
        systems_registry.get_handler('__missing__')
    except CompileError:
        pass
    else:
        raise AssertionError('unsupported package-backed math callable did not raise CompileError')
    print('MATH_TABLE_DISPATCH_OK')


def test_imported_derived_function_helpers_compile():
    compile_group('\nfrom functions import inverse_lerp, remap, saturate, step, smoothstep, smootherstep, pingpong, wrap, sign\na = inverse_lerp(0, 10, 5)\nb = remap(a, 0, 1, -1, 1)\nc = saturate(b + 2)\nd = step(0.5, c)\ne = smoothstep(edge0=0, edge1=1, x=c)\nf = smootherstep(0, 1, c)\ng = pingpong(-0.25, 1)\nh = wrap(-1, 0, 2)\ns = sign(-2)\noutput("Scalar", a+b+c+d+e+f+g+h+s)\n', 'NFTest_imported_derived_scalar_helpers')
    compile_group('\nfrom functions import rotate2d as rot, polar, angle_between, rotate_around_axis\nv = rot(polar(radius=2, angle=1.57079632679), angle=1.57079632679)\nw = rotate_around_axis(v=vector(1,0,0), axis=vector(0,0,1), angle=1.57079632679)\na = angle_between(vector(1,0,0), vector(0,1,0))\noutput("Scalar", a)\noutput("Vector", v + w)\n', 'NFTest_imported_derived_vector_helpers')
    compile_group('\nfrom functions import *\nx = smoothstep(0, 1, 0.5) + angle_between(vector(1,0,0), vector(0,1,0))\noutput("x", x)\n', 'NFTest_imported_derived_star_helpers')


def test_unimported_derived_helpers_and_removed_wrappers_fail():
    for index, name in enumerate(['inverse_lerp', 'remap', 'saturate', 'step', 'smoothstep', 'smootherstep', 'pingpong', 'wrap', 'sign', 'rotate2d', 'polar', 'angle_between', 'rotate_around_axis', 'lerp', 'frac', 'greater_than', 'greater_equal', 'less_than', 'less_equal', 'equal', 'not_equal']):
        try:
            compile_group(f'x = {name}(0, 1, 0.5)\noutput("x", x)', f'NFTest_math_unimported_{index}')
        except CompileError:
            pass
        else:
            raise AssertionError(f'{name} compiled without import')
