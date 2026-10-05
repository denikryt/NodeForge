"""Create a four-terrain comparison scene from the three native NodeForge graphs."""
from pathlib import Path
import bpy
from mathutils import Vector
ROOT=Path(__file__).resolve().parent
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'erosion_models.blend'))
for o in list(bpy.data.objects):bpy.data.objects.remove(o,do_unlink=True)
mat=bpy.data.materials.get('Erosion Terrain')
mat.use_nodes=True
mat.node_tree.nodes.get('Principled BSDF').inputs['Base Color'].default_value=(.33,.29,.23,1)
mat.node_tree.nodes.get('Principled BSDF').inputs['Roughness'].default_value=.9
entries=[('Initial','hydraulic_grid',0,(-6,6,0)),
         ('Hydraulic: 500 steps','hydraulic_grid',500,(6,6,0)),
         ('Stream power D8','stream_power_d8',24,(-6,-6,0)),
         ('Stream power MFD','stream_power_mfd',24,(6,-6,0))]
for title,group_name,steps,location in entries:
    obj=bpy.data.objects.new(title,bpy.data.meshes.new(title))
    bpy.context.collection.objects.link(obj);obj.location=location
    mod=obj.modifiers.new('Erosion','NODES');mod.node_group=bpy.data.node_groups[group_name]
    inputs={s.name:s.identifier for s in mod.node_group.interface.items_tree if s.item_type=='SOCKET' and s.in_out=='INPUT'}
    getattr(mod.properties.inputs,inputs['Steps']).value=steps
    getattr(mod.properties.inputs,inputs['Resolution']).value=96
    for path in ('hydraulic_grid.nf','stream_power_d8.nf','stream_power_mfd.nf'):
        text=bpy.data.texts.get(path) or bpy.data.texts.new(path)
        text.clear();text.write((ROOT/path).read_text())
    bpy.ops.object.text_add(location=(location[0],location[1]-5.8,0))
    text=bpy.context.object;text.data.body=title;text.data.align_x='CENTER';text.data.size=.55
scene=bpy.context.scene
scene.render.engine='CYCLES';scene.cycles.device='CPU';scene.cycles.samples=24
scene.render.resolution_x=1400;scene.render.resolution_y=1250;scene.render.resolution_percentage=100
scene.world.color=(.18,.18,.18)
bpy.ops.object.camera_add(location=(0,-25,35))
cam=bpy.context.object;cam.rotation_euler=(Vector((0,0,1))-cam.location).to_track_quat('-Z','Y').to_euler()
cam.data.type='ORTHO';cam.data.ortho_scale=28;scene.camera=cam
bpy.ops.object.light_add(type='AREA',location=(-6,-6,22))
light=bpy.context.object;light.data.energy=6500;light.data.size=14
bpy.context.view_layer.update()
deps=bpy.context.evaluated_depsgraph_get()
for title,_,_,_ in entries:
    e=bpy.data.objects[title].evaluated_get(deps);mesh=e.to_mesh()
    assert len(mesh.vertices)==96*96 and len(mesh.polygons)==95*95
    e.to_mesh_clear()
scene.render.filepath=str(ROOT/'models_comparison.png')
bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'erosion_models.blend'))
bpy.ops.render.render(write_still=True)
print('All four native modifiers evaluated; blend and render saved')
