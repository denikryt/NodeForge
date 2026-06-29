import pytest
from helpers import *

pytestmark = pytest.mark.blender_eval


def test_static_parameterized_turtle_commands_evaluate():
    group = compile_group('''
geo = ls_system(
    ls_axiom("F(2)+(90)F(1)"),
    ls_iterations(0),
    ls_angle(30),
    ls_step(0.5),
)
output("Geometry", geo)
''', 'NFTest_lsystem_static_parameterized_turtle')
    _manifest, refs, _obj = _assert_static_baked_group(group)
    curve = bpy.data.curves.get(_ref_by_kind(refs, 'CURVE').name)
    coords = []
    for spline in curve.splines:
        coords.append(tuple(tuple(round(float(c), 6) for c in point.co[:3]) for point in spline.points))
    check(coords == [((0.0, 0.0, 0.0), (2.0, 0.0, 0.0)), ((2.0, 0.0, 0.0), (2.0, 1.0, 0.0))], f'parameterized static coordinates changed: {coords}')


def test_runtime_parameterized_turtle_commands_compile_and_use_command_backend():
    group = compile_group('''
length = input_float("Length", default=1.0)
angle = input_float("Angle", default=90.0)
geo = ls_system(
    ls_axiom("F(length)+(angle)F(length)"),
    ls_iterations(0),
    ls_angle(30),
    ls_step(0.5),
    ls_param("length", length),
    ls_param("angle", angle),
)
output("Geometry", geo)
''', 'NFTest_lsystem_runtime_parameterized_turtle')
    _manifest, refs = _manifest_refs(group)
    check({ref.kind for ref in refs} == {'MESH', 'OBJECT'}, 'runtime parameterized L-system did not use command Mesh/Object backend')
    mesh = bpy.data.meshes.get(_ref_by_kind(refs, 'MESH').name)
    for attr_name in ('nf_lsys_move_distance_static', 'nf_lsys_move_param_index', 'nf_lsys_turn_degrees_static', 'nf_lsys_turn_param_index'):
        check(mesh.attributes.get(attr_name) is not None, f'parameterized command Mesh missing {attr_name}')


def test_parameterized_syntax_rejects_undeclared_param_before_resource_commit():
    before = _owned_generated_id_keys()
    expect_compile_error('''
geo = ls_system(
    ls_axiom("F(length)"),
    ls_iterations(0),
    ls_angle(30),
    ls_step(1),
)
output("Geometry", geo)
''', 'NFTest_lsystem_bad_undeclared_param')
    check(_owned_generated_id_keys() == before, 'undeclared L-system parameter leaked generated resources')


def test_marker_after_legacy_prefix_compiles_without_whitespace_separator():
    group = compile_group('''
size = input_float("Size", default=0.8)
plant = ls_system(
    ls_axiom("LLeaf(size)AApple(size)"),
    ls_iterations(0),
    ls_angle(30),
    ls_step(1),
    ls_param("size", size),
    ls_marker("Leaf", "size"),
    ls_marker("Apple", "size"),
)
output("Geometry", join(ls_points(plant, marker="Leaf"), ls_points(plant, marker="Apple")))
''', 'NFTest_lsystem_marker_after_legacy_prefix')
    _manifest, refs = _manifest_refs(group)
    check(any(ref.kind == 'MESH' for ref in refs), 'legacy-prefix marker stream did not create marker mesh')
