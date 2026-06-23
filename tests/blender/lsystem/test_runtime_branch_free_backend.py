from helpers import *


def test_branch_free_runtime_backend_graph_and_command_mesh_contract():
    source = '''
angle_value = input_float("Angle", default=60.0)
step_value = input_float("Step", default=0.1)
geo = ls_system(ls_axiom("F"), ls_rule("F", "F+F--F+F"), ls_iterations(3), ls_angle(angle_value), ls_step(step_value))
output("Geometry", geo)
'''
    group = compile_group(source, "NFTest_lsystem_branch_free_backend_contract")
    manifest, refs = _manifest_refs(group)
    assert {ref.kind for ref in refs} == {"MESH", "OBJECT"}
    mesh = bpy.data.meshes.get(_ref_by_kind(refs, "MESH").name)
    obj = bpy.data.objects.get(_ref_by_kind(refs, "OBJECT").name)
    assert mesh is not None and obj is not None and obj.data is mesh
    for attr_name, domain, data_type, expected_len in [
        (MOVE_MASK_ATTR, "POINT", "FLOAT", len(mesh.vertices)),
        (HEADING_INDEX_ATTR, "POINT", "FLOAT", len(mesh.vertices)),
        (DRAW_MASK_ATTR, "EDGE", "BOOLEAN", len(mesh.edges)),
    ]:
        attr = mesh.attributes.get(attr_name)
        assert attr is not None
        assert attr.domain == domain
        assert attr.data_type == data_type
        assert len(attr.data) == expected_len
    node_types = [getattr(node, "bl_idname", "") for node in group.nodes]
    assert "GeometryNodeCurvePrimitiveLine" not in node_types
    for required in {
        "GeometryNodeObjectInfo",
        "GeometryNodeInputNamedAttribute",
        "GeometryNodeAccumulateField",
        "GeometryNodeSetPosition",
        "GeometryNodeDeleteGeometry",
        "GeometryNodeMeshToCurve",
    }:
        assert required in node_types
    assert len(group.nodes) < 40
