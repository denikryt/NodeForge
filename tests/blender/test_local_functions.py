from helpers import *


def _local_helper_names(prefix):
    return sorted(g.name for g in bpy.data.node_groups if g.name.startswith(prefix))


def _single_local_helper(prefix):
    names = _local_helper_names(prefix)
    check(len(names) == 1, f"expected one local helper for {prefix}, got {names}")
    return bpy.data.node_groups[names[0]]


def test_local_function_local_function_captures_runtime_and_const_values():
    group = compile_group('''
scale_max = input_float("Scale Max", default=2.0)
bias = 1.5

def scale0(g):
    return scale_max * g + bias

s = scale0(3.0)
output("s", s)
''', 'NFTest_local_function_capture_runtime_const')
    helper = _single_local_helper('NodeForge.local.NFTest_local_function_capture_runtime_const.scale0.')
    inputs = [item.name for item in helper.interface.items_tree if getattr(item, 'in_out', None) == 'INPUT']
    check(inputs[:3] == ['g', 'scale_max', 'bias'], f'capture inputs were not explicit-then-hidden: {inputs}')
    check(helper.get('nodeforge_generated_kind') == 'local_function_helper', 'local helper ownership marker missing')
    check(helper.get('nodeforge_local_function_namespace') == 'NFTest_local_function_capture_runtime_const', 'local helper namespace marker mismatch')
    check(getattr(group, 'bl_idname', None) == 'GeometryNodeTree', 'parent group did not compile')


def test_local_function_nested_local_function_call_preserves_outer_capture():
    group = compile_group('''a = input_float("A", default=2.0)

def g(x):
    return x + a

def f(x):
    return g(x)

y = f(1.0)
output("y", y)
''', 'NFTest_local_function_nested_capture_success')
    f_helper = _single_local_helper('NodeForge.local.NFTest_local_function_nested_capture_success.f.')
    g_helper = _single_local_helper('NodeForge.local.NFTest_local_function_nested_capture_success.g.')
    f_inputs = [item.name for item in f_helper.interface.items_tree if getattr(item, 'in_out', None) == 'INPUT']
    g_inputs = [item.name for item in g_helper.interface.items_tree if getattr(item, 'in_out', None) == 'INPUT']
    check(f_inputs == ['x', 'a'], f'nested caller did not forward outer capture: {f_inputs}')
    check(g_inputs == ['x', 'a'], f'nested callee did not capture outer input: {g_inputs}')
    check(getattr(group, 'bl_idname', None) == 'GeometryNodeTree', 'parent group did not compile')


def test_local_function_local_function_allows_builtin_compile_time_constants_without_hidden_inputs():
    group = compile_group('''
def f(x):
    return x * pi + tau - e

y = f(1.0)
output("y", y)
''', 'NFTest_local_function_local_const_pi')
    helper = _single_local_helper('NodeForge.local.NFTest_local_function_local_const_pi.f.')
    inputs = [item.name for item in helper.interface.items_tree if getattr(item, 'in_out', None) == 'INPUT']
    check(inputs == ['x'], f'compile-time constants should not become hidden inputs: {inputs}')
    check(getattr(group, 'bl_idname', None) == 'GeometryNodeTree', 'parent group did not compile')


def test_local_function_registered_names_rejected_before_compile_time_preprocessing():
    cases = (
        """
def f(sin):
    return sin
x = f(1.0)
output("x", x)
""",
        """
def f(x):
    sin = x
    return sin
y = f(1.0)
output("y", y)
""",
        """
from functions import scene
scene = 1.0
output("x", scene)
""",
        """
Float = 1.0
output("x", Float)
""",
    )
    for index, source in enumerate(cases):
        expect_compile_error(source, f'NFTest_local_function_reserved_name_{index}')


def test_local_function_top_level_builtin_and_constant_shadowing_remains_compatible():
    group = compile_group("""
length = input_float("Length", default=1.0)
e = length + 1.0
grid = e + 2.0
output("grid", grid)
""", 'NFTest_local_function_legacy_shadowing')
    check(getattr(group, 'bl_idname', None) == 'GeometryNodeTree', 'legacy top-level builtin/constant shadowing did not compile')


def test_local_function_registered_callable_name_is_not_captured_as_value():
    expect_compile_error('''
from functions import fibonacci

def f(x):
    return x + fibonacci

y = f(1.0)
output("y", y)
''', 'NFTest_local_function_reserved_capture_import')


def test_local_function_local_helper_namespace_is_stable_across_parent_update():
    group = compile_group('''
def f(x):
    return x + 1.0

y = f(1.0)
output("y", y)
''', 'NFTest_local_function_namespace')
    helper_before = _single_local_helper('NodeForge.local.NFTest_local_function_namespace.f.')
    compiler.update_expression_group(group, '''
def f(x):
    return x + 2.0

y = f(1.0)
output("y", y)
''')
    helper_after = _single_local_helper('NodeForge.local.NFTest_local_function_namespace.f.')
    check(helper_after is helper_before, 'parent update did not reuse the stable local helper')
    replacement_helpers = _local_helper_names('NodeForge.local.NodeForge_replacement_NFTest_local_function_namespace')
    check(not replacement_helpers, f'replacement-namespaced helpers leaked: {replacement_helpers}')


def test_local_function_local_helper_name_collision_rejects_non_geometry_datablock():
    collision_name = 'NodeForge.local.NFTest_local_function_collision.f.x:FLOAT'
    collision = bpy.data.node_groups.new(collision_name, 'ShaderNodeTree')
    try:
        expect_compile_error('''
def f(x):
    return x + 1.0

y = f(1.0)
output("y", y)
''', 'NFTest_local_function_collision')
        suffixed = [g.name for g in bpy.data.node_groups if g.name.startswith(collision_name + '.')]
        check(not suffixed, f'auto-suffixed helper was created: {suffixed}')
    finally:
        if bpy.data.node_groups.get(collision.name) is collision:
            bpy.data.node_groups.remove(collision, do_unlink=True)


def test_local_function_created_nested_helpers_roll_back_after_parent_failure():
    expect_compile_error('''
a = input_float("A")

def g(x):
    return x + a

def f(x):
    return g(x)

u = f(1.0)
bad = definitely_missing_function()
output("bad", bad)
''', 'NFTest_local_function_nested_failure')
    leaked = _local_helper_names('NodeForge.local.NFTest_local_function_nested_failure.')
    check(not leaked, f'newly created nested local helpers leaked after failed parent build: {leaked}')


def test_local_function_existing_helper_and_resources_restore_after_failed_parent_update():
    group = compile_group('''
def make():
    return ls_system(ls_axiom("F"), ls_iterations(1), ls_angle(90), ls_step(0.1))

geo = make()
output("Geometry", geo)
''', 'NFTest_local_function_resource_restore')
    helper = _single_local_helper('NodeForge.local.NFTest_local_function_resource_restore.make.')
    old_source = helper.get('nodeforge_local_function_source')
    old_manifest, old_refs = _manifest_refs(helper)
    old_keys = {(ref.kind, ref.name, ref.owner_group_uuid, ref.generation_uuid) for ref in old_refs}
    live_before = _owned_generated_id_keys()

    try:
        compiler.update_expression_group(group, '''
def make():
    return ls_system(ls_axiom("FF"), ls_iterations(1), ls_angle(90), ls_step(0.2))

geo = make()
bad = definitely_missing_function()
output("Geometry", geo)
''')
    except CompileError:
        pass
    else:
        raise AssertionError('failed parent update with modified helper did not raise')

    restored = _single_local_helper('NodeForge.local.NFTest_local_function_resource_restore.make.')
    check(restored is helper, 'failed parent update replaced the committed helper datablock')
    check(restored.get('nodeforge_local_function_source') == old_source, 'helper source metadata was not restored')
    restored_manifest, restored_refs = _manifest_refs(restored)
    restored_keys = {(ref.kind, ref.name, ref.owner_group_uuid, ref.generation_uuid) for ref in restored_refs}
    check(restored_manifest == old_manifest, 'helper generated-resource manifest was not restored')
    check(restored_keys == old_keys, 'helper generated-resource refs changed after failed parent update')
    live_after = _owned_generated_id_keys()
    check(old_keys <= live_after, 'old helper generated resources were deleted by failed parent update')
    check(live_after == live_before, 'failed parent update leaked generated resources')


def test_local_function_local_helper_namespace_is_collision_safe_for_sanitized_names():
    source_one = '''
def f(x):
    return x + 1.0

y = f(1.0)
output("y", y)
'''
    source_two = '''
def f(x):
    return x * 10.0

y = f(1.0)
output("y", y)
'''
    group_one = compile_group(source_one, 'NFTest_local_function_A.B')
    group_two = compile_group(source_two, 'NFTest_local_function_A_B')

    helpers = [g for g in bpy.data.node_groups if g.name.startswith('NodeForge.local.NFTest_local_function_A') and '.f.' in g.name]
    helper_names = sorted(g.name for g in helpers)
    check(len(helper_names) == 2, f'expected distinct helper groups for colliding namespaces, got {helper_names}')
    namespaces = sorted(g.get('nodeforge_local_function_namespace') for g in helpers)
    check(namespaces == ['NFTest_local_function_A.B', 'NFTest_local_function_A_B'], f'raw helper namespaces were not distinct: {namespaces}')
    check(getattr(group_one, 'bl_idname', None) == 'GeometryNodeTree', 'first parent group did not compile')
    check(getattr(group_two, 'bl_idname', None) == 'GeometryNodeTree', 'second parent group did not compile')
