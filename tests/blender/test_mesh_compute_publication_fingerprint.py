"""Publication hash covers Blender 5.2 native layers after eliminating duplicate RNA traversal."""

import bpy

from NodeForge.blender.mesh_compute_runtime import _publication_fingerprint


def test_native_attribute_hash_catches_position_seams_sharpness_and_uv_changes():
    """Every Blender 5.2 native layer change invalidates the published snapshot."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4,y_subdivisions=4,size=3)
    obj=bpy.context.object
    mesh=obj.data
    try:
        if not mesh.uv_layers:mesh.uv_layers.new(name='UV')
        key=_publication_fingerprint(mesh)
        assert key==_publication_fingerprint(mesh)
        mesh.vertices[0].co.x+=0.125
        new=_publication_fingerprint(mesh)
        assert new!=key;key=new
        mesh.edges[0].use_seam=True
        assert mesh.attributes.get('uv_seam') is not None
        new=_publication_fingerprint(mesh)
        assert new!=key;key=new
        mesh.edges[0].use_edge_sharp=True
        assert mesh.attributes.get('sharp_edge') is not None
        new=_publication_fingerprint(mesh)
        assert new!=key;key=new
        mesh.polygons[0].use_smooth=True
        assert mesh.attributes.get('sharp_face') is not None
        new=_publication_fingerprint(mesh)
        assert new!=key;key=new
        mesh.uv_layers[0].data[0].uv=(.27,.19)
        assert _publication_fingerprint(mesh)!=key
    finally:
        bpy.data.objects.remove(obj,do_unlink=True)


def test_publication_hash_catches_material_face_and_undeclared_attribute_edits():
    """Non-read layers still force a new Mesh even on numerical cache hits."""
    bpy.ops.mesh.primitive_grid_add(x_subdivisions=4,y_subdivisions=4,size=3)
    obj=bpy.context.object
    mesh=obj.data
    material0=bpy.data.materials.new('MeshComputeFingerprintMat0')
    material1=bpy.data.materials.new('MeshComputeFingerprintMat1')
    try:
        mesh.materials.append(material0)
        mesh.materials.append(material1)
        key=_publication_fingerprint(mesh)
        mesh.polygons[0].material_index=1
        next_key=_publication_fingerprint(mesh)
        assert key!=next_key
        layer=mesh.attributes.new('user_extra','FLOAT','POINT')
        key=_publication_fingerprint(mesh)
        layer.data[0].value=0.25
        assert _publication_fingerprint(mesh)!=key
        key=_publication_fingerprint(mesh)
        mesh.materials[1]=material0
        assert _publication_fingerprint(mesh)!=key
    finally:
        bpy.data.objects.remove(obj,do_unlink=True)
        bpy.data.materials.remove(material0,do_unlink=True)
        bpy.data.materials.remove(material1,do_unlink=True)
