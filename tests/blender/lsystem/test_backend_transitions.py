from helpers import *


def test_static_to_branch_free_runtime_replaces_generated_resources():
    static_source = _static_lsystem_source(iterations=1)
    runtime_source = '''
angle_value = input_float("Angle", default=60.0)
step_value = input_float("Step", default=0.1)
geo = ls_system(ls_axiom("F"), ls_rule("F", "F+F--F+F"), ls_iterations(2), ls_angle(angle_value), ls_step(step_value))
output("Geometry", geo)
'''
    group = compile_group(static_source, "NFTest_lsystem_backend_transition_static_to_runtime")
    static_manifest, static_refs, _curve = _assert_static_baked_group(group)
    compiler.update_expression_group(group, runtime_source)
    runtime_manifest, runtime_refs = _manifest_refs(group)
    assert runtime_manifest["owner_group_uuid"] == static_manifest["owner_group_uuid"]
    assert {ref.kind for ref in runtime_refs} == {"MESH", "OBJECT"}
    for ref in static_refs:
        assert _collection_for_ref(ref).get(ref.name) is None
