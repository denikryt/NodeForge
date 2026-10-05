"""Check disabled erosion and water-source controls using the compiled native graphs."""
import sys,json
from pathlib import Path
import bpy,numpy as np
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT))
from run_experiments import evaluate
bpy.ops.wm.open_mainfile(filepath=str(ROOT/'erosion_models.blend'))
results=[]
for name,settings in [('hydraulic_grid',{'Resolution':16,'Steps':40,'Erosion Rate':0.0}),
                      ('hydraulic_grid',{'Resolution':16,'Steps':40,'Rain':0.0}),
                      ('stream_power_d8',{'Resolution':16,'Steps':4,'Erodibility':0.0}),
                      ('stream_power_mfd',{'Resolution':16,'Steps':4,'Erodibility':0.0})]:
    result=evaluate(bpy.data.node_groups[name],settings)
    delta=float(np.max(np.abs(result['attrs']['height_change'])))
    assert delta==0.0,(name,settings,delta)
    results.append({'model':name,'settings':settings,'max_height_change':delta,'passed':True})
(ROOT/'control_checks.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
