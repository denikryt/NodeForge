"""Try every discovered output socket ID against every discovered input socket ID.

This probes one default-state node pair per type combination. It does not claim
that the result applies to every context or configured node state.

  blender -b -t 1 --factory-startup --python probe_type_pairs.py -- JOINT.json OUT.json
"""
import json
import sys
from collections import Counter
from pathlib import Path
import bpy
sys.path.insert(0, str(Path(__file__).resolve().parent))
from probe_connections import find_catalog, record_link, error


def main(args=None):
    args=(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []) if args is None else args
    if len(args)!=2:
        raise SystemExit('Usage: probe_type_pairs.py -- JOINT.json OUT.json')
    src_path,out_path=map(Path,args)
    source=json.loads(src_path.read_text())
    if source['blender_version']!=bpy.app.version_string:
        raise ValueError('Input inventory version differs from current Blender')
    tree=bpy.data.node_groups.new('NodeForge type connections temporary','GeometryNodeTree')
    ids=[node['bl_idname'] for node in source['nodes']]
    sources,sinks,_,_=find_catalog(tree,ids)
    result={'schema':'nodeforge.blender_socket_type_pairs/1.0',
            'blender_version':bpy.app.version_string,
            'method':'every discovered output socket ID x input socket ID, one default-state node pair per combination',
            'limitations':['single representative nodes, not exhaustive per-node or per-state proof',
                           'does not evaluate data or implicit conversion semantics'],
            'pairs':[], 'socket_states':{}}
    counts=Counter()
    for out_type,source_items in sorted(sources.items()):
        for in_type,target_items in sorted(sinks.items()):
            src=source_items[0];dest=target_items[0]
            subject=None
            try:
                subject=tree.nodes.new(dest['node'])
                trial=record_link(tree,subject,'inputs',dest['index'],src,result['socket_states'])
                row={'output_type':out_type,'input_type':in_type,
                     'source':src,'target':dest,**trial}
                counts['tested']+=1
                counts['created']+=row['status']=='created'
                counts['valid']+=row.get('valid',False)
                counts['changed']+=row.get('subject_changed',False) or row.get('partner_changed',False)
            except Exception as ex:
                row={'output_type':out_type,'input_type':in_type,'status':'error','error':error(ex)}
                counts['errors']+=1
            finally:
                if subject is not None:tree.nodes.remove(subject)
            result['pairs'].append(row)
    result['summary']=dict(counts)
    out_path.parent.mkdir(parents=True,exist_ok=True)
    out_path.write_text(json.dumps(result,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print('DONE',bpy.app.version_string,dict(counts),flush=True)
    bpy.data.node_groups.remove(tree)

if __name__=='__main__':main()
