from helpers import *




def test_math_compile_and_blender_enum_contracts():
    expected = set(_FLOAT_FUNCS_1) | set(_FLOAT_FUNCS_2) | {'ln', 'clamp', 'mix', 'lerp', 'select', 'map_range', 'inverse_lerp', 'remap', 'saturate', 'step', 'smoothstep', 'smootherstep', 'pingpong', 'wrap', 'noise', 'random_value'}
    check(math.NAMES == expected, f'math.NAMES drifted: {sorted(math.NAMES ^ expected)}')
    check(set(_FLOAT_FUNCS_1).issubset(math._SPECS), 'FLOAT_FUNCS_1 names missing from math specs')
    check(set(_FLOAT_FUNCS_2).issubset(math._SPECS), 'FLOAT_FUNCS_2 names missing from math specs')
    math_ops = {item.identifier for item in bpy.types.ShaderNodeMath.bl_rna.properties['operation'].enum_items}
    invalid_ops = sorted((set(_FLOAT_FUNCS_1.values()) | set(_FLOAT_FUNCS_2.values())) - math_ops)
    check(not invalid_ops, f'constants contain unsupported ShaderNodeMath operations: {invalid_ops}')
    positional_lines = []
    for index, name in enumerate(sorted(_FLOAT_FUNCS_1)):
        positional_lines.append(f'p{index} = {name}(0.5)')
    base = len(positional_lines)
    for index, name in enumerate(sorted(_FLOAT_FUNCS_2)):
        positional_lines.append(f'p{base + index} = {name}(0.75, 0.25)')
    positional_lines.extend(['ln_v = ln(2)', 'clamp_v = clamp(2, 0, 1)', 'mix_v = mix(0, 1, 0.5)', 'lerp_v = lerp(0, 1, 0.5)', 'select_v = select(True, 0, 1)', 'map_range_v = map_range(0.5, 0, 1, -1, 1)', 'inverse_lerp_v = inverse_lerp(0, 10, 5)', 'remap_v = remap(0.5, 0, 1, -1, 1)', 'saturate_v = saturate(2)', 'step_v = step(0.5, 1)', 'smoothstep_v = smoothstep(0, 1, 0.5)', 'smootherstep_v = smootherstep(0, 1, 0.5)', 'pingpong_v = pingpong(-0.25, 1)', 'wrap_v = wrap(-1, 0, 2)', 'noise_v = noise(vector(0,0,0), scale=1, detail=2, roughness=0.5)', 'random_v = random_value(0, 1, seed=3)', "output('v', clamp_v)"])
    compile_group('\n'.join(positional_lines), 'NFTest_math_all_positional')
    keyword_lines = []
    for index, name in enumerate(sorted(math._SPECS)):
        keyword_lines.append(f'k{index} = {_math_keyword_expr(name, math._SPECS[name].params)}')
    keyword_lines.append("output('v', k0)")
    compile_group('\n'.join(keyword_lines), 'NFTest_math_all_keywords')
    error_sources = ["x = smoothstep(edge0=0, edge1=1, value=0.5)\noutput('x', x)", "x = smoothstep(0, edge0=1, edge1=2)\noutput('x', x)", "x = smoothstep(edge0=0, edge1=1)\noutput('x', x)", "x = smoothstep(**foo)\noutput('x', x)"]
    for index, source in enumerate(error_sources):
        try:
            compile_group(source, f'NFTest_math_keyword_error_{index}')
        except CompileError:
            pass
        else:
            raise AssertionError(f'math keyword error fixture {index} did not fail')
    expr = ast.parse('__missing__(1)', mode='eval').body
    try:
        math.compile_call(object(), expr)
    except CompileError:
        pass
    else:
        raise AssertionError('unsupported math builtin did not raise CompileError')
    print('MATH_TABLE_DISPATCH_OK')
