"""Blender-side state explorer (version-neutral; Blender 5.2+).

Usage: blender -b -t 1 --factory-startup --python explore_states.py -- OUT.json [MAX_STATES_PER_NODE]

Discovers node classes in GeometryNodeTree at runtime. Enumerates *joint* RNA
ENUM/BOOLEAN assignments, verifies the actual values, and snapshots physical
socket topologies. Reports unobservable/dynamic Menu items, pointers, collection
state, numeric ranges, and non-observed behavior rather than claiming completeness.
The enumerator does not interpret NodeForge and does not contain node id allowlists.
"""
import bpy
import json
import hashlib
import itertools
import sys
from collections import Counter
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
from blender_rna import (sockets, describe_property, encode, base_props, schema_key)

SCHEMA='nodeforge.blender_state_exploration/0.2'


def stable_hash(value):
    data=json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
    return hashlib.sha256(data.encode('utf-8')).hexdigest()[:20]


def cfg_options(p):
    if p.get('read_error') or p['readonly']:
        return [], 'read_only_or_error'
    if p['kind']=='BOOLEAN':
        return [False,True], 'finite_boolean'
    if p['kind']=='ENUM':
        opts=[e['identifier'] for e in p.get('declared_options',[]) if e['identifier']]
        if p.get('is_enum_flag'):
            # A flag enumeration has 2^n possible subsets, not just singleton choices.
            if len(opts)<=8:
                values=[list(comb) for mask in range(1<<len(opts))
                        for comb in [tuple(opts[i] for i in range(len(opts)) if mask & (1<<i))]]
                return values, 'enum_flags_full_declared_subsets'
            return [p.get('default',[])], 'enum_flags_unbounded'
        # An ENUM with no declared options is often context-dependent (e.g.
        # FunctionNodeInputMenu.value). Do not try to assign its placeholder
        # default: Blender may reject it even though the baseline node exists.
        if not opts:
            return [], 'enum_options_not_exposed'
        values=list(dict.fromkeys(opts+([p.get('default')] if p.get('default') else [])))
        values=[v for v in values if isinstance(v,str)]
        return values,'enum_declared_options_needs_runtime_validation'
    return [],'not_discrete'


def set_config(node, cfg, order):
    for prop in order:
        value=cfg[prop]
        setattr(node,prop,set(value) if isinstance(value,list) else value)
        actual=encode(getattr(node,prop))
        if actual != value:
            raise ValueError('%s: requested=%r actual=%r'%(prop,value,actual))


def probe(tree, nid, config, key_order):
    errors=[]
    # Changing order can matter for conditional enums, try two orders.
    orders=[key_order]
    if len(key_order)>1:
        orders.append(tuple(reversed(key_order)))
    for order in orders:
        node=None
        try:
            node=tree.nodes.new(nid)
            set_config(node,config,order)
            sch=sockets(node)
            return {'status':'applied', 'sockets':sch, 'order':list(order)}
        except Exception as ex:
            errors.append(type(ex).__name__ + ': ' + str(ex)[:160])
        finally:
            if node is not None:
                tree.nodes.remove(node)
    return {'status':'rejected_by_tested_orders', 'errors':errors}


def safe_menu_info(s):
    val=None
    try: val=encode(s.default_value)
    except Exception: pass
    declared=[]
    try:
        prop=s.bl_rna.properties['default_value']
        declared=[{'identifier':it.identifier,'name':it.name} for it in prop.enum_items if it.identifier]
    except Exception:
        pass
    return {'side':'outputs' if s.is_output else 'inputs', 'identifier':s.identifier,
            'index':next((i for i,x in enumerate(s.node.outputs if s.is_output else s.node.inputs) if x==s),None),
            'default_value':val, 'declared_options':declared,
            'discovery':'RNA_enum_items' if declared else 'not_enumerable_via_RNA_enum_items',
            'full_option_coverage':False if not declared else 'unverified'}


def numeric_samples(p):
    if p['readonly'] or p.get('read_error') or p['kind'] not in ('INT','FLOAT'):
        return []
    default=p.get('default')
    if not isinstance(default,(int,float)) or isinstance(default,bool):return []
    mi=p.get('hard_min'); ma=p.get('hard_max')
    values=[default,0,1]
    if isinstance(mi,(int,float)) and abs(mi)<1e8:values.append(mi)
    if isinstance(ma,(int,float)) and abs(ma)<1e8:values.append(ma)
    if p['kind']=='INT' and isinstance(mi,int) and isinstance(ma,int) and 0<=ma-mi<=20:
        values.extend(range(mi,ma+1))
    ans=[]
    for v in values:
        if p['kind']=='INT':v=int(v)
        else:v=float(v)
        if (isinstance(mi,(int,float)) and v<mi) or (isinstance(ma,(int,float)) and v>ma):continue
        if v not in ans:ans.append(v)
    return ans


def explore_node(tree,nid,limit):
    node=tree.nodes.new(nid)
    try:
        base=sockets(node)
        props=[describe_property(node,p) for p in node.bl_rna.properties
               if p.identifier not in base_props and p.identifier!='rna_type']
        menus=[safe_menu_info(s) for s in itertools.chain(node.inputs,node.outputs)
               if s.bl_idname=='NodeSocketMenu']
        label=node.bl_label
        rna_class=node.bl_rna.identifier
        rna_base=node.bl_rna.base.identifier if node.bl_rna.base else None
        rna_description=node.bl_rna.description
    finally:
        tree.nodes.remove(node)
    params={}; domains={}; undecidable=[]
    for p in props:
        domain,kind=cfg_options(p)
        domains[p['name']]={'kind':kind,'candidates':domain}
        if domain and p['kind'] in ('ENUM','BOOLEAN'):
            params[p['name']]=domain
        elif p['kind'] in ('INT','FLOAT','POINTER','COLLECTION','STRING') and not p['readonly']:
            undecidable.append({'property':p['name'],'kind':p['kind'], 'reason':'non_finite_or_context_dependent_domain'})
        elif kind in ('enum_options_not_exposed','enum_flags_unbounded'):
            undecidable.append({'property':p['name'],'kind':p['kind'],'reason':kind})
    keys=tuple(sorted(params))
    expected=1
    for k in keys:expected*=len(params[k])
    configs=itertools.product(*(params[k] for k in keys))
    states=[]; shapes={}; valid=0; failed=0; best_effort_limit=False
    baseline_schema=stable_hash(schema_key(base))
    shapes[baseline_schema]=base
    for i,values in enumerate(configs):
        if i>=limit:
            best_effort_limit=True
            break
        config=dict(zip(keys,values))
        r=probe(tree,nid,config,keys)
        entry={'config':config,'status':r['status']}
        if r['status']=='applied':
            sig=stable_hash(schema_key(r['sockets']))
            entry['topology_id']=sig
            shapes.setdefault(sig,r['sockets'])
            entry['used_assignment_order']=r['order']
            valid+=1
        else:
            entry['errors']=r['errors'];failed+=1
        states.append(entry)
    # Numeric probing only baseline + each numeric parameter independently.
    numeric=[]
    for p in props:
        for val in numeric_samples(p):
            node=None
            row={'property':p['name'],'value':val}
            try:
                node=tree.nodes.new(nid)
                setattr(node,p['name'],val)
                row['actual']=encode(getattr(node,p['name']))
                row['status']='applied' if row['actual']==val else 'coerced_or_ignored'
                if row['status']=='applied':
                    state=sockets(node)
                    sig=stable_hash(schema_key(state))
                    shapes.setdefault(sig,state)
                    row['topology_id']=sig
            except Exception as ex:
                row['status']='rejected';row['error']=type(ex).__name__+': '+str(ex)[:160]
            finally:
                if node is not None:tree.nodes.remove(node)
            numeric.append(row)
    reasons=[]
    if best_effort_limit: reasons.append('discrete_product_truncated')
    if failed:reasons.append('some_declared_combinations_not_applied_by_tested_orders')
    if menus: reasons.append('menu_options_not_proven_exhaustive')
    if any(p['kind']=='COLLECTION' and p.get('collection_api',{}).get('functions') for p in props):
        reasons.append('dynamic_collections_not_jointly_enumerated')
    if undecidable:reasons.append('non_finite_or_context_dependent_properties')
    reasons.extend(['link_context_and_evaluation_not_tested'])
    return {'bl_idname':nid, 'label':label, 'rna_class':rna_class,
            'rna_base':rna_base, 'description':rna_description, 'properties':props,
            'baseline_topology_id':baseline_schema,
            'discrete_properties':domains,'non_discrete_or_unknown_properties':undecidable,
            'expected_discrete_assignments':expected,'explored_discrete_assignments':len(states),
            'valid_discrete_assignments':valid,'rejected_discrete_assignments':failed,
            'discrete_enumeration_complete':not best_effort_limit,
            'topologies':shapes,'states':states,'numeric_one_at_a_time':numeric,
            'menu_sockets_at_baseline':menus,'limitations':reasons,
            'structural_state_count':len(shapes)}


def main(args=None):
    args=(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []) if args is None else args
    if len(args)<1:raise SystemExit('Usage: blender ... -- OUT.json [MAX_STATES_PER_NODE]')
    out=Path(args[0]);limit=int(args[1]) if len(args)>1 else 2000
    if limit<1:raise ValueError('MAX_STATES_PER_NODE must be >= 1')
    tree=bpy.data.node_groups.new('NodeForge state explorer temporary','GeometryNodeTree')
    candidates=[]
    for name in dir(bpy.types):
        try:
            typ=getattr(bpy.types,name)
            if isinstance(typ,type) and typ is not bpy.types.Node and issubclass(typ,bpy.types.Node):
                instance=None
                try:
                    instance=tree.nodes.new(name)
                    candidates.append(name)
                except Exception:pass
                finally:
                    if instance is not None:tree.nodes.remove(instance)
        except Exception:pass
    nodes=[];failures=[]
    for index,name in enumerate(sorted(candidates)):
        try:nodes.append(explore_node(tree,name,limit))
        except Exception as ex:
            failures.append({'bl_idname':name,'error':type(ex).__name__+': '+str(ex)[:250]})
        if (index+1)%50==0:print('EXPLORED',index+1,'/',len(candidates),flush=True)
    summary={'nodes_discovered':len(candidates),'nodes_explored':len(nodes),
             'node_exploration_errors':len(failures),
             'discrete_states_attempted':sum(n['explored_discrete_assignments'] for n in nodes),
             'discrete_states_applied':sum(n['valid_discrete_assignments'] for n in nodes),
             'distinct_node_topologies':sum(n['structural_state_count'] for n in nodes),
             'nodes_with_multiple_topologies':sum(n['structural_state_count']>1 for n in nodes),
             'nodes_with_menus':sum(bool(n['menu_sockets_at_baseline']) for n in nodes),
             'nodes_with_incomplete_discrete_enumeration':sum(not n['discrete_enumeration_complete'] for n in nodes)}
    result={'schema':SCHEMA,'blender_version':bpy.app.version_string,
            'blender_build_hash':bpy.app.build_hash.decode('utf-8','replace') if isinstance(bpy.app.build_hash,bytes) else str(bpy.app.build_hash),
            'method':'Cartesian product of declared RNA enum/boolean values, runtime set/get verification, two setter orders, structural socket snapshots',
            'scope':{'complete_for':'attempted, declared ENUM/BOOLEAN combinations up to configured limit (not semantic correctness)',
                     'not_complete_for':['RNA dynamically unlisted enum options','Menu socket dropdown choices when RNA enum_items empty',
                      'all numeric values and combined numeric states','pointer references and external assets',
                      'collection modifications and combinations','paired-zone/context-sensitive behavior',
                      'links/field evaluation and Blender geometry results','all setter order permutations']},
            'max_discrete_states_per_node':limit,'summary':summary,'failures':failures,'nodes':nodes}
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(result,indent=2,sort_keys=True,ensure_ascii=False),encoding='utf-8')
    print('SUMMARY',json.dumps(summary,ensure_ascii=False),flush=True)

if __name__=='__main__':main()
