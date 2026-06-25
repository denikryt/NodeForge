import pytest
from helpers import *


pytestmark = pytest.mark.blender_eval


def test_mandelbrot_modifier_evaluation():
    group = compile_group('from functions import mandelbrot\ngeo = mandelbrot(resolution=12, max_iter=8)\noutput("Geometry", geo)', 'NFTest_mandelbrot_eval')
    mesh_data = bpy.data.meshes.new('NFTestMesh')
    obj = bpy.data.objects.new('NFTestObject', mesh_data)
    bpy.context.collection.objects.link(obj)
    mod = obj.modifiers.new('NodeForge', 'NODES')
    mod.node_group = group
    depsgraph = bpy.context.evaluated_depsgraph_get()
    evaluated = obj.evaluated_get(depsgraph)
    mesh = evaluated.to_mesh()
    try:
        check(len(mesh.vertices) > 0, 'Mandelbrot mesh has no vertices')
        attr = mesh.attributes.get('mandelbrot_color')
        check(attr is not None, 'Mandelbrot color attribute missing')
        check(attr.data_type == 'FLOAT_COLOR', 'Mandelbrot color attribute type changed')
    finally:
        evaluated.to_mesh_clear()
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh_data, do_unlink=True)
    print('MANDELBROT_EVAL_OK')
