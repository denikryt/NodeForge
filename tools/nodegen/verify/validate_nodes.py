"""Run generated wrappers through NodeForge, independently reproduce them with bpy.

One JSONL row per case, including NF source/traceback for failures. The baseline
Inspector state is not blindly trusted: an independent bpy readback is required.
"""
import argparse, json, os, shutil, sys, traceback
from pathlib import Path
import bpy
if os.environ.get('NODEFORGE_CORE_PARENT'):sys.path.insert(0,os.environ['NODEFORGE_CORE_PARENT'])
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'generator'))
from inspect_contract import marker
from NodeForge import compiler, packages

SOCKETS={'Float':'NodeSocketFloat','Int':'NodeSocketInt','Bool':'NodeSocketBool','Vector':'NodeSocketVector',
         'Color':'NodeSocketColor','Rotation':'NodeSocketRotation','Geometry':'NodeSocketGeometry',
         'String':'NodeSocketString','Material':'NodeSocketMaterial','Object':'NodeSocketObject'}

def basic_snapshot(node):
    def each(xs):return [(s.identifier,s.name,s.bl_idname,bool(s.enabled),bool(getattr(s,'is_unavailable',False)),bool(getattr(s,'is_inactive',False))) for s in xs]
    return {'inputs':each(node.inputs),'outputs':each(node.outputs)}

def filtered(sock):
    return sock.bl_idname not in ('NodeSocketVirtual','NodeSocketMenu') and sock.enabled and not getattr(sock,'is_unavailable',False)

def dynamic_add(node, dynamic, elements):
    if not dynamic:return
    kind=dynamic['kind']
    if kind=='index':
        while len(node.index_switch_items)>elements:node.index_switch_items.remove(node.index_switch_items[-1])
        while len(node.index_switch_items)<elements:node.index_switch_items.new()
    elif kind=='menu':
        desired=[x.strip() for x in elements.split(',') if x.strip()]
        if [it.name for it in node.enum_items]!=desired:
            node.enum_items.clear()
            for name in desired:node.enum_items.new(name)
    elif kind=='capture':
        for part in [x.strip() for x in elements.split(',') if x.strip()]:
            token,name=part.split(':',1);node.capture_items.new(token,name)

def runtime_inputs(node, dynamic, cfg):
    if dynamic:
        # Preserve index/menu order, including sockets inactive before linking.
        return [(s,marker(s.bl_idname)) for s in node.inputs if filtered(s) and marker(s.bl_idname)]
    return [([s for s in node.inputs if s.identifier==x['socket']][0],x['type']) for x in cfg['inputs']]

def source_for(item,cfg,mode,runtime,params,output_count,menu_override=None,elements=None,available=()):
    opts={f['param']:cfg['rna'][f['key']] if f['kind']=='rna' else cfg['menus'][f['key']] for f in item['fields']}
    if menu_override:opts['menu_menu']=menu_override
    if item['dynamic'] and elements is not None:opts['elements']=elements
    named={entry['socket']: entry['param'] for entry in item['named_inputs']}
    positional=[]
    keywords=[]
    if runtime:
        for index,(socket,typ) in enumerate(available):
            if socket.identifier in named:
                keywords.append(f'{named[socket.identifier]}=p{index}')
            else:
                positional.append(f'p{index}')
    args = positional + keywords + [f'{k}={v!r}' for k,v in opts.items()]
    args.append(f'__inline__={mode=="inline"}')
    call=f"gen.{item['name']}({', '.join(args)})"
    names=[f'x{i}' for i in range(output_count)]
    lhs=', '.join(names)+(',' if len(names)==1 else '')
    result=', '.join(names) if len(names)>1 else names[0]
    declaration=', '.join(f'p{i}: {t}' for i,t in enumerate(params))
    return f"from packages import nodes as gen\n\ndef entry({declaration}):\n    {lhs} = {call}\n    return {result}\n"

def run_case(item,cfg,mode,runtime,elements=None,menu_override=None):
    dynamic=item['dynamic']
    effective_elements = elements if elements is not None else (dynamic['initial_count'] if dynamic and dynamic['kind']=='index' else ','.join(dynamic['initial_names']) if dynamic and dynamic['kind']=='menu' else '')
    record={'node':item['node'],'wrapper':item['name'],'state':cfg['inspector_state'],
            'rna':cfg['rna'],'menu':{**cfg['menus'],**({'Menu':menu_override} if menu_override else {})},
            'mode':mode,'runtime':runtime,'elements':elements,'status':'failed'}
    before={g.as_pointer() for g in bpy.data.node_groups}
    stage='reference'
    try:
        ref_group=bpy.data.node_groups.new('UnifiedReference','GeometryNodeTree');oracle=ref_group.nodes.new(item['node'])
        def assign_rna(key, value):
            if oracle.bl_rna.properties[key].is_enum_flag:
                value = set(filter(None, value.split(',')))
            setattr(oracle, key, value)
        for key in cfg['rna_order']:
            if key in cfg['rna']:assign_rna(key,cfg['rna'][key])
        for key,v in cfg['rna'].items():
            if key not in cfg['rna_order']:assign_rna(key,v)
        dynamic_add(oracle,dynamic,effective_elements)
        for key,val in cfg['menus'].items():
            if menu_override and key=='Menu':val=menu_override
            matches=[s for s in oracle.inputs if s.identifier==key]
            if len(matches)!=1:raise AssertionError(f'Missing menu {key}')
            matches[0].default_value=val
        readback={name:(','.join(sorted(getattr(oracle,name)))
                       if oracle.bl_rna.properties[name].is_enum_flag else getattr(oracle,name))
                  for name in cfg['rna']}
        record['readback_rna']=readback
        normalized=readback!=cfg['rna']
        actual_menu={key:[s for s in oracle.inputs if s.identifier==key][0].default_value for key in cfg['menus']}
        if actual_menu!=record['menu']:normalized=True
        available=runtime_inputs(oracle,dynamic,cfg)
        param_types=[kind for sock,kind in available] if runtime else []
        if runtime:
            for i,(sock,typ) in enumerate(available):
                ref_group.interface.new_socket(name=f'p{i}',in_out='INPUT',socket_type=SOCKETS[typ])
            ref_in=ref_group.nodes.new('NodeGroupInput')
            for i,(sock,typ) in enumerate(available):
                ref_group.links.new(ref_in.outputs[f'p{i}'],sock)
        expected=basic_snapshot(oracle)
        expected_links=[(link.to_socket.identifier,link.to_socket.name,link.to_socket.bl_idname)
                        for link in ref_group.links if link.to_node==oracle]
        output_count=len([s for s in oracle.outputs if filtered(s)]) if dynamic else len(cfg['outputs'])
        code=source_for(item,cfg,mode,runtime,param_types,output_count,menu_override,elements,available=available)
        record['source']=code
        stage='compile'
        root=compiler.create_expression_group(code,f'Unified_{item["name"]}_{cfg["inspector_state"]}_{mode}')
        inner=[root] if mode=='inline' else [n.node_tree for n in root.nodes if n.bl_idname=='GeometryNodeGroup' and n.node_tree]
        natives=[n for g in inner for n in g.nodes if n.bl_idname==item['node']]
        if len(natives)!=1:raise AssertionError(f'Expected one physical {item["node"]}, found {len(natives)}')
        native=natives[0]
        stage='compare'
        actual=basic_snapshot(native)
        if actual!=expected:raise AssertionError(f'Blender socket mismatch: actual={actual}, expected={expected}')
        actual_rna={name:(','.join(sorted(getattr(native,name)))
                          if native.bl_rna.properties[name].is_enum_flag else getattr(native,name))
                    for name in cfg['rna']}
        if actual_rna!=readback:raise AssertionError(f'RNA mismatch: {actual_rna} vs {readback}')
        for key,val in record['menu'].items():
            found=[s for s in native.inputs if s.identifier==key]
            if len(found)!=1 or found[0].default_value!=val:raise AssertionError(f'Menu mismatch {key!r}')
        actual_links=[(link.to_socket.identifier,link.to_socket.name,link.to_socket.bl_idname)
                      for g in inner for link in g.links if link.to_node==native]
        if sorted(actual_links)!=sorted(expected_links):raise AssertionError(f'Link mismatch: {actual_links} vs {expected_links}')
        if any(not l.is_valid for g in [root,*inner] for l in g.links):
            raise AssertionError('Invalid Blender link')
        record['status']='inspector_not_reproducible' if normalized else 'passed'
        if normalized:record['note']='Blender normalized one or more requested Inspector RNA/Menu options'
    except BaseException as exc:
        record['stage']=stage;record['error_type']=type(exc).__name__;record['error']=str(exc)
        record['traceback']=traceback.format_exc()
    finally:
        for g in list(bpy.data.node_groups):
            if g.as_pointer() not in before:bpy.data.node_groups.remove(g,do_unlink=True)
    return record

def main():
    a=argparse.ArgumentParser()
    a.add_argument('--generation',required=True);a.add_argument('--package',required=True);a.add_argument('--out',required=True)
    a.add_argument('--limit',type=int,default=0);a.add_argument('--all',action='store_true')
    a.add_argument('--sample',type=int,default=3,help='Per-node states sampled evenly across the supported catalogue')
    a.add_argument('--grouped',action='store_true',help='Use grouped compilation for regular config scenarios')
    a.add_argument('--start',type=int,default=0,help='First wrapper index (inclusive)')
    a.add_argument('--stop',type=int,default=None,help='Last wrapper index (exclusive)')
    a.add_argument('--state-start',type=int,default=0,help='Per-node configuration index start (for partitioned full-catalogue runs)')
    a.add_argument('--state-stop',type=int,default=None,help='Per-node configuration index stop (for partitioned full-catalogue runs)')
    args=a.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    result=json.loads(Path(args.generation).read_text());out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    attempts=0;counts={'passed':0,'failed':0,'inspector_not_reproducible':0}
    package_dir=out/'isolated_packages';shutil.rmtree(package_dir,ignore_errors=True)
    packages.set_packages_dir_for_tests(package_dir);packages.invalidate_caches()
    try:
        packages.install_package_directory(Path(args.package))
        with (out/'test_cases.jsonl').open('w',encoding='utf-8') as logfile:
            for item in result['wrappers'][args.start:args.stop]:
                configurations = item['configurations']
                if args.all or len(configurations) <= args.sample:
                    selected = configurations
                else:
                    indices = sorted({round(i * (len(configurations) - 1) / (args.sample - 1))
                                      for i in range(args.sample)}) if args.sample > 1 else [0]
                    selected = [configurations[index] for index in indices]
                selected=selected[args.state_start:args.state_stop]
                scenarios=[(cfg,'grouped' if args.grouped else 'inline',runtime,None,None)
                           for cfg in selected for runtime in (False,True)]
                if item['dynamic']:
                    cfg=selected[0];kind=item['dynamic']['kind']
                    if kind=='capture':
                        choices=[f'{t}:Item{i}' for i,t in enumerate(item['dynamic']['types'])][:2]
                        if choices:scenarios.extend((cfg,mode,False,','.join(choices),None) for mode in ('inline','grouped'))
                    if kind=='index':scenarios.extend((cfg,mode,False,4,None) for mode in ('inline','grouped'))
                    if kind=='menu':scenarios.extend((cfg,mode,False,'A,B,C','C') for mode in ('inline','grouped'))
                for cfg,mode,runtime,elements,menu_override in scenarios:
                    if args.limit and attempts>=args.limit:break
                    record=run_case(item,cfg,mode,runtime,elements,menu_override)
                    counts[record['status']]+=1;attempts+=1
                    logfile.write(json.dumps(record,ensure_ascii=False)+'\n');logfile.flush()
                    if record['status']=='failed':
                        (out/'failures').mkdir(exist_ok=True)
                        (out/'failures'/f"{item['name']}-{attempts}.nf").write_text(record.get('source',''))
                    print('NODEGEN_TEST',attempts,item['node'],cfg['inspector_state'],mode,runtime,str(elements),record['status'],record.get('error','')[:145],flush=True)
    finally:
        packages.set_packages_dir_for_tests(None);packages.invalidate_caches()
    summary={'version':bpy.app.version_string,'attempted':attempts,**counts,
             'start':args.start,'stop':args.stop,'state_start':args.state_start,'state_stop':args.state_stop}
    (out/'execution.json').write_text(json.dumps(summary,indent=2)+'\n')
    print('NODEGEN_SUMMARY',summary,flush=True)
if __name__=='__main__':main()
