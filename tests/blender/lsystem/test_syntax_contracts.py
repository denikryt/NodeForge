from helpers import *




def test_lsystem_syntax_and_guard_contracts():
    group = compile_group('\nangle_value = input_float("Angle", default=60.0)\nstep_value = input_float("Step", default=0.1)\ngeo = ls_system(\n    ls_axiom("F"),\n    ls_rule("F", "F+F--F+F"),\n    ls_iterations(2),\n    ls_angle(angle_value),\n    ls_step(step_value),\n)\ngeo = transform(geo, translation=vector(0, 0, 1))\noutput("Geometry", geo)\n', 'NFTest_lsystem_runtime_angle_step')
    input_names = {item.name for item in group.interface.items_tree if getattr(item, 'item_type', None) == 'SOCKET' and getattr(item, 'in_out', None) == 'INPUT'}
    check('ls_system' not in input_names and 'ls_axiom' not in input_names and ('ls_step' not in input_names), 'system constructors became implicit inputs')
    check({'Angle', 'Step'}.issubset(input_names), 'runtime L-system inputs missing')
    compile_group('\na = ls_axiom("F")\nr = ls_rule("F", "F+X")\ngeo = ls_system(a, r, ls_iterations(1), ls_angle(60), ls_step(0.1))\noutput("Geometry", geo)\n', 'NFTest_lsystem_assigned_parts')
    compile_group('\ngeo = ls_system(ls_axiom("X"), ls_rule("X", "F+X"), ls_iterations(2), ls_angle(60), ls_step(0.1))\noutput("Geometry", geo)\n', 'NFTest_lsystem_grammar_symbols')
    compile_group('\ngeo = ls_system(ls_axiom("F[+F]F[-F]F"), ls_iterations(0), ls_angle(25), ls_step(0.1))\noutput("Geometry", geo)\n', 'NFTest_lsystem_branching_static')
    compile_group('\nangle_value = input_float("Angle", default=25.0)\ngeo = ls_system(ls_axiom("F[+F]F[-F]F"), ls_iterations(0), ls_angle(angle_value), ls_step(0.1))\noutput("Geometry", geo)\n', 'NFTest_lsystem_branching_runtime_angle')
    compile_group('\nstep_value = input_float("Step", default=0.1)\ngeo = ls_system(ls_axiom("ffF"), ls_iterations(0), ls_angle(90), ls_step(step_value))\noutput("Geometry", geo)\n', 'NFTest_lsystem_lowercase_move_runtime_step')
    compile_group('from functions import step\nx = step(0.5, 1.0)\noutput("x", x)', 'NFTest_lsystem_imported_step_coexists')
    error_sources = {'output_part': 'output("Geometry", ls_axiom("F"))', 'store_part': 'geo = grid(2,2)\nstore("bad", ls_axiom("F"))', 'set_position_part': 'set_position(ls_axiom("F"))', 'final_part': 'ls_axiom("F")', 'builtin_part': 'x = transform(ls_axiom("F"), translation=vector(0,0,1))\noutput("x", x)', 'local_function_part': 'def ident(x):\n    return x\ny = ident(ls_axiom("F"))\noutput("y", y)', 'runtime_if_part': 'flag = input_bool("Flag")\nif flag:\n    x = ls_axiom("F")\nelse:\n    x = ls_axiom("F")\noutput("x", x)', 'axiom_runtime': 'a = input_float("A")\ngeo = ls_system(ls_axiom(a), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'iterations_runtime': 'n = input_int("N")\ngeo = ls_system(ls_axiom("F"), ls_iterations(n), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'bad_rule_multi': 'geo = ls_system(ls_axiom("F"), ls_rule("AB", "F"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'bad_rule_empty': 'geo = ls_system(ls_axiom("F"), ls_rule("", "F"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'bad_rule_pipe': 'geo = ls_system(ls_axiom("F"), ls_rule("X", "F|X"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'bad_rule_space': 'geo = ls_system(ls_axiom("F"), ls_rule("X", "F X"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'bad_rule_unicode': 'geo = ls_system(ls_axiom("F"), ls_rule("X", "F→X"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'unmatched_close': 'geo = ls_system(ls_axiom("F]"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'unclosed_open': 'geo = ls_system(ls_axiom("[F"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'duplicate_axiom': 'geo = ls_system(ls_axiom("F"), ls_axiom("F"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'duplicate_rule': 'geo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_rule("F", "F"), ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'reserved_local': 'def ls_axiom(x):\n    return x\noutput("x", 1)', 'bool_angle_const': 'geo = ls_system(ls_axiom("F"), ls_iterations(1), ls_angle(True), ls_step(1))\noutput("Geometry", geo)', 'bool_angle_runtime': 'b = input_bool("B")\ngeo = ls_system(ls_axiom("F"), ls_iterations(1), ls_angle(b), ls_step(1))\noutput("Geometry", geo)', 'list_smuggle': 'p = [ls_axiom("F")]\ngeo = ls_system(p, ls_iterations(1), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'zero_draw_bootstrap_limit': 'geo = ls_system(ls_axiom("Xf"), ls_iterations(0), ls_angle(60), ls_step(1))\noutput("Geometry", geo)', 'max_symbols': 'geo = ls_system(ls_axiom("F"), ls_rule("F", "FF"), ls_iterations(18), ls_angle(60), ls_step(1))\noutput("Geometry", geo)'}
    for name, source in error_sources.items():
        expect_compile_error(source, 'NFTest_lsystem_error_' + name)
    expect_compile_error('output("x", 1)', 'NFTest_lsystem_reserved_backend_helper', backend_builtins={'ls_rule': lambda comp, expr, depth=0: None})
    library_collision_dir = ROOT / 'functions' / 'ls_step'
    library_collision_dir.mkdir(exist_ok=True)
    (library_collision_dir / 'source.nf').write_text('output("x", 1)\n', encoding='utf-8')
    try:
        expect_compile_error('output("x", 1)', 'NFTest_lsystem_reserved_library_function')
    finally:
        (library_collision_dir / 'source.nf').unlink(missing_ok=True)
        library_collision_dir.rmdir()
    native_collision_dir = ROOT / 'functions' / 'ls_step'
    native_collision_dir.mkdir(exist_ok=True)
    (native_collision_dir / 'function.py').write_text('import bpy\n\ndef materialize_group(compile_group_callback):\n    return bpy.data.node_groups.new("NFTest_bad_ls_step", "GeometryNodeTree")\n', encoding='utf-8')
    try:
        for action_name, action in {'library_function_names': library.library_function_names, 'has_library_function': lambda: library.has_library_function('ls_step'), 'library_function_records': library.library_function_records, 'get_or_create_library_group': lambda: library.get_or_create_library_group('ls_step', compiler._make_group), 'materialize_library_function_group': lambda: library.materialize_library_function_group('ls_step', compiler._make_group)}.items():
            try:
                action()
            except CompileError:
                pass
            except AttributeError as exc:
                raise AssertionError(f'{action_name} raised uncontrolled AttributeError: {exc}') from exc
            else:
                raise AssertionError(f'{action_name} accepted reserved native library function')
    finally:
        (native_collision_dir / 'function.py').unlink(missing_ok=True)
        pycache = native_collision_dir / '__pycache__'
        if pycache.exists():
            for child in pycache.iterdir():
                child.unlink()
            pycache.rmdir()
        native_collision_dir.rmdir()
    expect_compile_error('x = koch_curve(ls_axiom("F"))\noutput("Geometry", x)', 'NFTest_lsystem_koch_native_guard')
    expect_compile_error('x = dragon_curve(ls_axiom("F"))\noutput("Geometry", x)', 'NFTest_lsystem_dragon_native_guard')
    expect_compile_error('geo = apply_mandelbrot_material(ls_axiom("F"), "x")\noutput("Geometry", geo)', 'NFTest_lsystem_backend_helper_guard', backend_builtins=library.backend_builtins_for_function('mandelbrot'))
    print('LSYSTEM_SYNTAX_AND_GUARDS_OK')
