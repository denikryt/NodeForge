"""Compile three erosion models in Blender and measure fields, balances and routing."""
import sys, json, time
from pathlib import Path
import bpy
import numpy as np
ROOT=Path(__file__).resolve().parent
WORK=ROOT.parent
sys.path.insert(0,str(WORK/'extracted/NodeForge'))
import NodeForge
from NodeForge import packages,compiler
packages.set_packages_dir_for_tests(ROOT/'test_packages')
NodeForge.register()
packages.install_package_zip(WORK/'upload/nodeforge.math-v2.2_nf-v0.65.1_git.zip',allow_python=True,replace=True)


def evaluate(group,settings):
    """Evaluate a fresh modifier and copy all point scalar attributes."""
    mesh=bpy.data.meshes.new('Carrier')
    obj=bpy.data.objects.new('Experiment',mesh)
    bpy.context.collection.objects.link(obj)
    mod=obj.modifiers.new('Erosion','NODES');mod.node_group=group
    inputs={s.name:s.identifier for s in group.interface.items_tree if s.item_type=='SOCKET' and s.in_out=='INPUT'}
    for name,value in settings.items():getattr(mod.properties.inputs,inputs[name]).value=value
    start=time.perf_counter()
    bpy.context.view_layer.update()
    evaluated=obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
    output=evaluated.to_mesh()
    attrs={}
    for a in output.attributes:
        if a.domain=='POINT' and a.data_type in ('FLOAT','INT'):
            data=np.empty(len(a.data),dtype=float)
            a.data.foreach_get('value',data)
            attrs[a.name]=data
    pos=np.empty(len(output.vertices)*3);output.vertices.foreach_get('co',pos)
    result={'attrs':attrs,'coords':pos.reshape(-1,3),'seconds':time.perf_counter()-start,'faces':len(output.polygons)}
    evaluated.to_mesh_clear();bpy.data.objects.remove(obj,do_unlink=True);bpy.data.meshes.remove(mesh)
    return result


def main():
    """Compile source and inspect basic invariants before selecting presentation parameters."""
    report={}
    for name in ('hydraulic_grid','stream_power_d8','stream_power_mfd'):
        print('Compiling',name,flush=True)
        started=time.perf_counter()
        group=compiler.create_expression_group((ROOT/(name+'.nf')).read_text(),name)
        group.use_fake_user = True
        assert all(l.is_valid for l in group.links)
        print('Compiled',name,len(group.nodes),'nodes in',time.perf_counter()-started,flush=True)
        info={'nodes':len(group.nodes),'links':len(group.links),'cases':[]}
        cases=([{'Resolution':16,'Steps':10,'Open Edges':False,'Evaporation':0.0},
                {'Resolution':32,'Steps':100,'Open Edges':True},
                {'Resolution':96,'Steps':500,'Open Edges':True}]
               if name=='hydraulic_grid' else
               [{'Resolution':16,'Steps':0}, {'Resolution':16,'Steps':1,'Routing Passes':256},
                {'Resolution':96,'Steps':24,'Routing Passes':160}])
        for settings in cases:
            if name=='stream_power_mfd' and settings['Steps']>0:
                settings['Routing Passes']=512
            print('Evaluating',name,settings,flush=True)
            result=evaluate(group,settings);a=result['attrs']
            assert np.isfinite(result['coords']).all()
            assert len(a['height'])==settings['Resolution']**2
            info_case={'settings':settings,'seconds':result['seconds'],
                       'height_delta_min':float(a['height_change'].min()),
                       'height_delta_max':float(a['height_change'].max()),
                       'height_delta_rms':float(np.sqrt(np.mean(a['height_change']**2)))}
            if name=='hydraulic_grid':
                for key in ('water','sediment'):assert a[key].min()>=-1e-7
                solid_balance=float(np.sum(a['height']-a['initial_height']+a['sediment']+a['sediment_export']))
                rain=settings.get('Rain',0.03);dt=settings.get('Time Step',0.02)
                water_balance=float(np.sum(a['water']+a['water_export']+a['evap_total'])-len(a['height'])*rain*dt*settings['Steps'])
                info_case.update(solid_balance_error=solid_balance,water_balance_error=water_balance,
                    sediment_export=float(a['sediment_export'].sum()),water_export=float(a['water_export'].sum()),
                    erosion=float(a['erosion_total'].sum()),deposition=float(a['deposition_total'].sum()),
                    water_min=float(a['water'].min()),water_max=float(a['water'].max()))
                assert abs(solid_balance)<0.02,info_case
                assert abs(water_balance)<0.02,info_case
            elif settings['Steps']>0:
                info_case['max_drainage_area']=float(a['drainage_area'].max())
                if 'receiver' in a:
                    rec=a['receiver'].astype(int)
                    assert np.all((rec>=0)&(rec<len(rec)))
                assert a['height_change'].max()<1e-5
            print(info_case,flush=True)
            info['cases'].append(info_case)
            np.savez_compressed(ROOT/(name+'_'+str(settings['Resolution'])+'_'+str(settings['Steps'])+'.npz'),coords=result['coords'],**a)
        report[name]=info
    (ROOT/'experiments.json').write_text(json.dumps(report,indent=2))
    bpy.ops.wm.save_as_mainfile(filepath=str(ROOT/'erosion_models.blend'))

if __name__=='__main__':main()
