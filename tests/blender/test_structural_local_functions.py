from helpers import *


def _local_helper_names(prefix):
    """Return helpers selected by the legacy namespace/function prefix used by these tests."""
    if prefix.startswith("NodeForge.local."):
        payload = prefix[len("NodeForge.local."):].rstrip(".")
        if "." in payload:
            namespace, function_name = payload.rsplit(".", 1)
            return sorted(
                g.name for g in bpy.data.node_groups
                if g.get("nodeforge_generated_kind") == "local_function_helper"
                and g.get("nodeforge_local_function_namespace") == namespace
                and (not function_name or g.get("nodeforge_local_function_name") == function_name)
            )
    return sorted(g.name for g in bpy.data.node_groups if g.name.startswith(prefix))


def _single_local_helper(prefix):
    names = _local_helper_names(prefix)
    check(len(names) == 1, f"expected one local helper for {prefix}, got {names}")
    return bpy.data.node_groups[names[0]]








def test_structural_registered_names_rejected_before_compile_time_preprocessing():
    cases = (
        """
def f(store):
    return store
x = f(1.0)
output("x", x)
""",
        """
def f(x):
    store = x
    return store
y = f(1.0)
output("y", y)
""",
        '''
def nf_fixture():
    Float = 1.0
    x = Float
    return (x,)
''',
    )
    for index, source in enumerate(cases):
        expect_compile_error(source, f'NFTest_structural_reserved_name_{index}')


def test_structural_top_level_builtin_and_constant_shadowing_remains_compatible():
    group = compile_group('''
def nf_fixture(length_value: Float['Length'] = 1.0):
    adjusted_value = length_value + 1.0
    result_value = adjusted_value + 2.0
    return result_value
''', 'NFTest_structural_legacy_shadowing')
    check(group.bl_idname == 'GeometryNodeTree', 'non-reserved parameter shadowing failed')
    expect_compile_error(
        "def nf_fixture(length: Float = 1.0) -> Float:\n    return length + 1.0\n",
        'NFTest_reserved_builtin_parameter',
    )








def test_structural_created_nested_helpers_roll_back_after_parent_failure():
    expect_compile_error('''
a = input_float("A")

def g(x):
    return x + a

def f(x):
    return g(x)

u = f(1.0)
bad = definitely_missing_function()
output("bad", bad)
''', 'NFTest_structural_nested_failure')
    leaked = _local_helper_names('NodeForge.local.NFTest_structural_nested_failure.')
    check(not leaked, f'newly created nested local helpers leaked after failed parent build: {leaked}')
