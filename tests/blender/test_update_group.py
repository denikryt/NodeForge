from helpers import *




def test_update_group_rollback_and_preflight_contracts():
    group = compile_group('x = 1\noutput("x", x)', 'NFTest_update')
    compiler.update_expression_group(group, 'x = 2\noutput("x", x)')
    try:
        compiler.update_expression_group(group, '\ndef inc(a):\n    return a + 1\n\nx = inc(1)\ny = missing_func(1)\noutput("y", y)\n')
    except Exception:
        pass
    else:
        raise AssertionError('failed update did not raise')
    leaked = [g.name for g in bpy.data.node_groups if g.name.startswith('NodeForge.preflight.') or (g.name.startswith('NodeForge.local.') and 'preflight' in g.name)]
    check(not leaked, f'preflight/local preflight groups leaked: {leaked}')
    print('UPDATE_SUCCESS_FAILURE_OK')
