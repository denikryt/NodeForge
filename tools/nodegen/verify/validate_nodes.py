"""Run generated wrappers through NodeForge, independently reproduce them with bpy.

One JSONL row per case, including NF source/traceback for failures. The baseline
Inspector state is not blindly trusted: an independent bpy readback is required.
"""
import argparse, json, os, shutil, sys, traceback
from pathlib import Path
import bpy
if os.environ.get('NODEFORGE_CORE_PARENT'):sys.path.insert(0,os.environ['NODEFORGE_CORE_PARENT'])
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'generator'))
from inspect_contract import marker, sanitized
from NodeForge import compiler, packages
from NodeForge.result_keys import is_exact_attribute_key

SOCKETS={'Float':'NodeSocketFloat','Int':'NodeSocketInt','Bool':'NodeSocketBool','Vector':'NodeSocketVector',
         'Color':'NodeSocketColor','Rotation':'NodeSocketRotation','Geometry':'NodeSocketGeometry',
         'String':'NodeSocketString','Material':'NodeSocketMaterial','Object':'NodeSocketObject'}

def basic_snapshot(node):
    def each(xs):return [(s.identifier,s.name,s.bl_idname,bool(s.enabled),bool(getattr(s,'is_unavailable',False)),bool(getattr(s,'is_inactive',False))) for s in xs]
    return {'inputs':each(node.inputs),'outputs':each(node.outputs)}


def input_defaults(node):
    """Record native default values independently of wrapper declarations."""
    values = []
    for socket in node.inputs:
        if not hasattr(socket, 'default_value'):
            continue
        value = socket.default_value
        if isinstance(value, bpy.types.ID):
            value = (value.bl_rna.identifier, value.name_full)
        elif hasattr(value, '__iter__') and not isinstance(value, str):
            value = tuple(value)
        values.append((socket.identifier, value))
    return values

def filtered(sock):
    return sock.bl_idname not in ('NodeSocketVirtual','NodeSocketMenu') and sock.enabled and not getattr(sock,'is_unavailable',False)

def dynamic_add(node, dynamic, elements):
    if not dynamic:return
    kind=dynamic['kind']
    if kind=='index':
        while len(node.index_switch_items)>elements:node.index_switch_items.remove(node.index_switch_items[-1])
        while len(node.index_switch_items)<elements:node.index_switch_items.new()
    elif kind=='menu':
        desired=list(elements)
        if [it.name for it in node.enum_items]!=desired:
            node.enum_items.clear()
            for name in desired:node.enum_items.new(name)
    elif kind=='capture':
        for token,name in elements:
            node.capture_items.new(token,name)

def runtime_inputs(node, dynamic, cfg):
    if dynamic:
        # Preserve index/menu order, including sockets inactive before linking.
        return [(s,marker(s.bl_idname)) for s in node.inputs if filtered(s) and marker(s.bl_idname)]
    return [([s for s in node.inputs if s.identifier==x['socket']][0],x['type']) for x in cfg['inputs']]

def runtime_slots(item, runtime, available):
    """Select source input slots; Rotation capture needs a typed runtime leaf."""
    capture = item['dynamic'] and item['dynamic']['kind'] == 'capture'
    return [index for index, (sock, typ) in enumerate(available)
            if runtime or (capture and sock.identifier.startswith('Value') and typ == 'Rotation')]


def capture_default_expression(socket, typ):
    """Use independently observed defaults, with explicit Vector typing."""
    value = socket.default_value
    if typ in ('Vector', 'Color', 'Rotation'):
        value = tuple(value)
    return 'vector' + repr(value) if typ == 'Vector' else repr(value)


def source_for(item,cfg,mode,runtime,params,output_keys,menu_override=None,elements=None,available=()):
    opts={f['param']:cfg['rna'][f['key']] if f['kind']=='rna' else cfg['menus'][f['key']] for f in item['fields']}
    if menu_override is not None:
        menu = next(f['param'] for f in item['fields'] if f['kind']=='menu' and f['key']=='Menu')
        opts[menu]=menu_override
    kind = item['dynamic']['kind'] if item['dynamic'] else None
    if kind and kind != 'capture' and elements is not None:opts['elements']=elements
    named={entry['socket']: entry['param'] for entry in item['named_inputs']}
    positional=[]
    keywords=[]
    capture=[]
    param_by_slot = {slot: f'p{i}' for i, slot in enumerate(runtime_slots(item,runtime,available))}
    for index,(socket,typ) in enumerate(available):
        value = param_by_slot.get(index)
        if kind == 'capture' and socket.identifier not in named:
            capture.append(f'{socket.name!r}: {value if value is not None else capture_default_expression(socket,typ)}')
        elif value is not None:
            if socket.identifier in named:
                keywords.append(f'{named[socket.identifier]}={value}')
            else:
                positional.append(value)
    if kind == 'capture' and (capture or elements is not None):
        keywords.append('elements={' + ', '.join(capture) + '}')
    args = positional + keywords + [f'{k}={v!r}' for k,v in opts.items()]
    args.append(f'__inline__={mode=="inline"}')
    call=f"gen.{item['name']}({', '.join(args)})"
    if len(output_keys) == 1:
        projections = ['result']
    else:
        projections = [f'result.{key}' if is_exact_attribute_key(key) else f'result[{key!r}]'
                       for key in output_keys]
    result = '{' + ', '.join(f'{key!r}: {expr}' for key, expr in zip(output_keys, projections)) + '}'
    declaration=', '.join(f'p{i}: {t}' for i,t in enumerate(params))
    return f"from packages import nodes as gen\n\ndef entry({declaration}):\n    result = {call}\n    return {result}\n"


def reference_output_schema(oracle, item, cfg, normalized):
    """Derive ordered keys and physical origins from independent native sockets."""
    dynamic = item['dynamic']
    kind = dynamic['kind'] if dynamic else None
    sockets = [s for s in oracle.outputs if filtered(s) and (dynamic or not s.is_inactive)]
    collection_count = len(oracle.capture_items) if kind == 'capture' else len(oracle.enum_items) if kind == 'menu' else 0
    base_count = len(sockets) - collection_count
    rows = [(sanitized(sock.name) if index < base_count else sock.name,
             sock.identifier, sock.name, sock.bl_idname) for index, sock in enumerate(sockets)]
    catalogue = cfg['outputs'][:1] if kind == 'menu' else cfg['outputs']
    if not normalized:
        expected = [(sanitized(sock['name']), sock['socket'], sock['name'], sock['physical']) for sock in catalogue]
        if rows[:base_count] != expected:
            raise AssertionError(f'Independent reference differs from generation metadata: {rows[:base_count]!r} vs {expected!r}')
    return rows


def check_output_links(root, native, schema, *, grouped=None):
    """Trace each root output through its optional grouped call to the native socket."""
    outlet = next(node for node in root.nodes if node.bl_idname == 'NodeGroupOutput')
    expected_root_keys = tuple(row[0] for row in schema)
    root_outputs = tuple(s for s in outlet.inputs if s.bl_idname != 'NodeSocketVirtual')
    if tuple(s.name for s in root_outputs) != expected_root_keys:
        raise AssertionError('wrong root result shape')
    expected_types = tuple(marker(row[3]) for row in schema)
    if tuple(marker(s.bl_idname) for s in root_outputs) != expected_types:
        raise AssertionError('wrong root result types')
    actual = []
    expected_named = len(schema) > 1
    expected_keys = tuple(row[0] for row in schema) if expected_named else ('Result',)
    call_node = None
    for key, identifier, name, physical in schema:
        links = list(outlet.inputs[key].links)
        if len(links) != 1:
            raise AssertionError(f'{key!r}: expected one root output link')
        socket = links[0].from_socket
        is_group = socket.node.bl_idname == 'GeometryNodeGroup'
        if grouped is not None and is_group != grouped:
            raise AssertionError(f'{key!r}: unexpected compilation form')
        if is_group:
            child = socket.node.node_tree
            marker_key = 'nodeforge_named_results_v1'
            if (marker_key in child) != expected_named or (expected_named and child[marker_key] != True):
                raise AssertionError(f'{key!r}: wrong named result marker')
            if tuple(s.name for s in socket.node.outputs) != expected_keys:
                raise AssertionError(f'{key!r}: wrong grouped result shape')
            if tuple(marker(s.bl_idname) for s in socket.node.outputs) != expected_types:
                raise AssertionError(f'{key!r}: wrong grouped result types')
            if call_node is not None and socket.node != call_node:
                raise AssertionError(f'{key!r}: outputs from distinct calls')
            call_node = socket.node
            expected_group_key = key if expected_named else 'Result'
            if socket.name != expected_group_key:
                raise AssertionError(f'{key!r}: grouped projection points to {socket.name!r}')
            child_out = next(node for node in socket.node.node_tree.nodes if node.bl_idname == 'NodeGroupOutput')
            child_links = list(child_out.inputs[expected_group_key].links)
            if len(child_links) != 1:
                raise AssertionError(f'{key!r}: expected one physical child output link')
            socket = child_links[0].from_socket
        if socket.node != native or (socket.identifier, socket.name, socket.bl_idname) != (identifier, name, physical):
            raise AssertionError(f'{key!r}: wrong output provenance: {socket.identifier!r}, {socket.name!r}, {socket.bl_idname!r}')
        actual.append((key, socket.identifier, socket.name, socket.bl_idname))
    return actual


def check_input_links(root, native, expected, *, grouped):
    """Trace native input links to independent root parameter identities."""
    call = None
    if grouped:
        calls = [n for n in root.nodes if n.bl_idname == 'GeometryNodeGroup' and n.node_tree == native.id_data]
        if len(calls) != 1:
            raise AssertionError('Missing or ambiguous native input owner')
        call = calls[0]
    actual = []
    for link in native.id_data.links:
        if link.to_node != native:
            continue
        source = link.from_socket
        if source.node.bl_idname != 'NodeGroupInput':
            raise AssertionError(f'Wrong native input source for {link.to_socket.identifier}')
        if grouped:
            targets = [s for s in call.inputs if s.identifier == source.identifier]
            if len(targets) != 1 or len(targets[0].links) != 1:
                raise AssertionError(f'Missing grouped input link for {source.identifier}')
            source = targets[0].links[0].from_socket
            if source.node.bl_idname != 'NodeGroupInput':
                raise AssertionError('Wrong root input source')
        actual.append((link.to_socket.identifier, link.to_socket.name, link.to_socket.bl_idname, source.name))
    if sorted(actual) != sorted(expected):
        raise AssertionError(f'Input provenance mismatch: {actual!r} vs {expected!r}')
    return actual


def run_case(item,cfg,mode,runtime,elements=None,menu_override=None):
    dynamic=item['dynamic']
    effective_elements = elements if elements is not None else (dynamic['initial_count'] if dynamic and dynamic['kind']=='index' else dynamic['initial_names'] if dynamic and dynamic['kind']=='menu' else [])
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
        chosen = [available[index] for index in runtime_slots(item,runtime,available)]
        param_types=[kind for sock,kind in chosen]
        if chosen:
            for i,(sock,typ) in enumerate(chosen):
                ref_group.interface.new_socket(name=f'p{i}',in_out='INPUT',socket_type=SOCKETS[typ])
            ref_in=ref_group.nodes.new('NodeGroupInput')
            for i,(sock,typ) in enumerate(chosen):
                ref_group.links.new(ref_in.outputs[f'p{i}'],sock)
        expected=basic_snapshot(oracle)
        expected_defaults=input_defaults(oracle)
        expected_links=[(link.to_socket.identifier,link.to_socket.name,link.to_socket.bl_idname,link.from_socket.name)
                        for link in ref_group.links if link.to_node==oracle]
        record['expected_inputs'] = expected_links
        schema = reference_output_schema(oracle, item, cfg, normalized)
        record['expected_outputs'] = schema
        code=source_for(item,cfg,mode,runtime,param_types,[row[0] for row in schema],menu_override,elements,available=available)
        record['source']=code
        stage='compile'
        root=compiler.create_expression_group(code,f'Unified_{item["name"]}_{cfg["inspector_state"]}_{mode}')
        inner=[root] if mode=='inline' else [n.node_tree for n in root.nodes if n.bl_idname=='GeometryNodeGroup' and n.node_tree]
        natives=[n for g in inner for n in g.nodes if n.bl_idname==item['node']]
        if len(natives)!=1:raise AssertionError(f'Expected one physical {item["node"]}, found {len(natives)}')
        native=natives[0]
        stage='compare'
        record['actual_outputs'] = check_output_links(root, native, schema, grouped=mode=='grouped')
        actual=basic_snapshot(native)
        actual_defaults=input_defaults(native)
        if actual_defaults != expected_defaults:
            raise AssertionError(f'Input default mismatch: {actual_defaults!r} vs {expected_defaults!r}')
        if actual!=expected:raise AssertionError(f'Blender socket mismatch: actual={actual}, expected={expected}')
        actual_rna={name:(','.join(sorted(getattr(native,name)))
                          if native.bl_rna.properties[name].is_enum_flag else getattr(native,name))
                    for name in cfg['rna']}
        if actual_rna!=readback:raise AssertionError(f'RNA mismatch: {actual_rna} vs {readback}')
        for key,val in actual_menu.items():
            found=[s for s in native.inputs if s.identifier==key]
            if len(found)!=1 or found[0].default_value!=val:raise AssertionError(f'Menu mismatch {key!r}')
        record['actual_inputs'] = check_input_links(root, native, expected_links, grouped=mode=='grouped')
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
                if item['dynamic'] and selected:
                    cfg=selected[0];kind=item['dynamic']['kind']
                    if kind=='capture':
                        choices=[(t,f'Item {i}') for i,t in enumerate(item['dynamic']['types'])][:2]
                        if choices:scenarios.extend((cfg,mode,False,choices,None) for mode in ('inline','grouped'))
                    if kind=='index':scenarios.extend((cfg,mode,False,4,None) for mode in ('inline','grouped'))
                    if kind=='menu':scenarios.extend((cfg,mode,False,['A','Two Words','lower'],'lower') for mode in ('inline','grouped'))
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
