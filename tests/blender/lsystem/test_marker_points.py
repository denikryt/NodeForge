import pytest
from helpers import *
from NodeForge.systems.lsystem.modules import marker_identity

pytestmark = pytest.mark.blender_eval


def _mesh_attr_values(mesh, name):
    attr = mesh.attributes.get(name)
    check(attr is not None, f'marker mesh missing {name}')
    return _attribute_values(attr)


def test_static_marker_points_and_ls_points_compile():
    group = compile_group('''
leaf_size = input_float("Leaf Size", default=0.8)
plant = ls_system(
    ls_axiom("FLeaf(leaf_size)+(90)FBud"),
    ls_iterations(0),
    ls_angle(25),
    ls_step(1),
    ls_param("leaf_size", leaf_size),
    ls_marker("Leaf", "size"),
    ls_marker("Bud"),
)
leaf_points = ls_points(plant, marker="Leaf")
bud_points = ls_points(transform(plant, translation=vector(1, 0, 0)), marker="Bud")
leaves = instance_on_points(cube(0.1), leaf_points)
output("Geometry", join(plant, leaves, bud_points))
''', 'NFTest_lsystem_static_marker_points')
    _manifest, refs = _manifest_refs(group)
    check({ref.kind for ref in refs} == {'CURVE', 'MESH', 'OBJECT'}, f'marker system generated unexpected resources: {refs}')
    marker_meshes = [bpy.data.meshes.get(ref.name) for ref in refs if ref.kind == 'MESH']
    marker_mesh = next(mesh for mesh in marker_meshes if mesh is not None)
    check(len(marker_mesh.vertices) == 2, 'Leaf/Bud marker mesh point count changed')
    ids = marker_identity('Leaf')
    check(_mesh_attr_values(marker_mesh, 'nf_lsys_marker_id_a')[0] == ids[0], 'Leaf marker identity component not stored')
    check(marker_mesh.attributes.get('nf_lsys_marker_tangent') is not None, 'marker tangent attribute missing')
    check(marker_mesh.attributes.get('size') is not None, 'marker parameter attribute missing')


def test_marker_identity_collision_regression_after_join():
    group = compile_group('''
a = ls_system(
    ls_axiom("FM_3uxlqZl"),
    ls_iterations(0),
    ls_angle(0),
    ls_step(1),
    ls_marker("M_3uxlqZl"),
)
b = ls_system(
    ls_axiom("FMGRFHDue9"),
    ls_iterations(0),
    ls_angle(0),
    ls_step(1),
    ls_marker("MGRFHDue9"),
)
pts = ls_points(join(a, b), marker="M_3uxlqZl")
output("Geometry", pts)
''', 'NFTest_lsystem_marker_collision_join')
    _manifest, refs = _manifest_refs(group)
    check(any(ref.kind == 'MESH' for ref in refs), 'collision regression did not create marker meshes')


def _assert_runtime_marker_tangent_contract(group):
    _manifest, refs = _manifest_refs(group)
    command_meshes = [bpy.data.meshes.get(ref.name) for ref in refs if ref.kind == 'MESH']
    command_mesh = next((mesh for mesh in command_meshes if mesh is not None and mesh.attributes.get('nf_lsys_marker_mask') is not None), None)
    check(command_mesh is not None, 'runtime marker command mesh missing')
    check(command_mesh.attributes.get('nf_lsys_marker_tangent') is not None, 'runtime marker command mesh missing tangent attribute')
    store_nodes = [node for node in group.nodes if getattr(node, 'bl_idname', '') == 'GeometryNodeStoreNamedAttribute']
    tangent_nodes = []
    for node in store_nodes:
        try:
            if _input_socket_by_name(node, 'Name').default_value == 'nf_lsys_marker_tangent':
                tangent_nodes.append(node)
        except Exception:
            continue
    check(tangent_nodes, 'runtime graph does not store computed marker tangent')
    check(any(getattr(node, 'data_type', None) == 'FLOAT_VECTOR' for node in tangent_nodes), 'runtime marker tangent store node is not FLOAT_VECTOR')


def test_branch_free_runtime_marker_points_store_tangent_attribute():
    group = compile_group('''
length = input_float("Length", default=1.0)
angle = input_float("Angle", default=45.0)
plant = ls_system(
    ls_axiom("F(length)+(angle)Leaf"),
    ls_iterations(0),
    ls_angle(30),
    ls_step(1),
    ls_param("length", length),
    ls_param("angle", angle),
    ls_marker("Leaf"),
)
output("Geometry", ls_points(plant, marker="Leaf"))
''', 'NFTest_lsystem_branch_free_runtime_marker_tangent')
    _assert_runtime_marker_tangent_contract(group)


def test_branched_runtime_marker_points_store_tangent_attribute():
    group = compile_group('''
length = input_float("Length", default=1.0)
angle = input_float("Angle", default=45.0)
plant = ls_system(
    ls_axiom("F(length)[+(angle)Leaf]F(length)"),
    ls_iterations(0),
    ls_angle(30),
    ls_step(1),
    ls_param("length", length),
    ls_param("angle", angle),
    ls_marker("Leaf"),
)
output("Geometry", ls_points(plant, marker="Leaf"))
''', 'NFTest_lsystem_branched_runtime_marker_tangent')
    _assert_runtime_marker_tangent_contract(group)


def test_branch_free_runtime_marker_parameter_attribute_compiles_with_matching_table_lengths():
    group = compile_group('''
length = input_float("Length", default=1.0)
angle = input_float("Angle", default=45.0)
size = input_float("Size", default=0.5)
plant = ls_system(
    ls_axiom("F(length)+(angle)Leaf(size)"),
    ls_iterations(0),
    ls_angle(30),
    ls_step(1),
    ls_param("length", length),
    ls_param("angle", angle),
    ls_param("size", size),
    ls_marker("Leaf", "size"),
)
output("Geometry", ls_points(plant, marker="Leaf"))
''', 'NFTest_lsystem_branch_free_runtime_marker_param_length')
    _assert_runtime_marker_tangent_contract(group)


def test_branched_runtime_marker_parameter_attribute_compiles_with_matching_table_lengths():
    group = compile_group('''
length = input_float("Length", default=1.0)
angle = input_float("Angle", default=45.0)
size = input_float("Size", default=0.5)
plant = ls_system(
    ls_axiom("F(length)[+(angle)Leaf(size)]"),
    ls_iterations(0),
    ls_angle(30),
    ls_step(1),
    ls_param("length", length),
    ls_param("angle", angle),
    ls_param("size", size),
    ls_marker("Leaf", "size"),
)
output("Geometry", ls_points(plant, marker="Leaf"))
''', 'NFTest_lsystem_branched_runtime_marker_param_length')
    _assert_runtime_marker_tangent_contract(group)
