"""Investigate final-state construction: socket defaults, dynamic items and order.

Run in Blender 5.2+:
  blender -b -t 1 --factory-startup --python probe_final_states.py -- RNA.json JOINT.json OUT.json

No fixed node inventories, node-specific rules or external asset creation.
Every trial uses a temporary GeometryNodeTree. Group interface / node_tree
pointer mutation, zone pairing and actual geometry evaluation are out of scope.
"""
import json
import math
import sys
from collections import Counter
from pathlib import Path

import bpy
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_rna import encode, sockets
from explore_joint_states import (new_configured_node, assign_rna, menu_choices,
                                  collection_descriptions, error)

SCHEMA = 'nodeforge.blender_final_state_probe/1.0'


def current_value(owner, name):
    return encode(getattr(owner, name))


def property_spec(prop):
    data = {'kind':prop.type, 'readonly':bool(prop.is_readonly),
            'subtype':getattr(prop,'subtype','NONE')}
    for key in ('array_length','hard_min','hard_max','soft_min','soft_max'):
        try:
            val=getattr(prop,key)
            if val is not None: data[key]=encode(val)
        except (AttributeError,TypeError,ValueError): pass
    if prop.type=='ENUM':
        try: data['options']=[it.identifier for it in prop.enum_items if it.identifier]
        except Exception: data['options']=[]
    if prop.type=='POINTER':
        try: data['pointer_type']=prop.fixed_type.identifier
        except Exception: pass
    return data


def different_value(owner, name, prop):
    """One safe non-default value when RNA supplies enough information.

    Do not manufacture Blender datablocks or guess required pointer targets.
    """
    value=getattr(owner,name)
    kind=prop.type
    if kind=='BOOLEAN': return (not value),None
    if kind=='STRING': return (str(value) + '_NF_probe'),None
    if kind=='ENUM':
        options=[it.identifier for it in prop.enum_items if it.identifier]
        if not options and getattr(owner,'bl_idname',None)=='NodeSocketMenu':
            options=menu_choices(owner).get('values',[])
        for option in options:
            if option!=value:return option,None
        return None,'no_other_enum_option'
    if kind in ('INT','FLOAT'):
        is_array=bool(getattr(prop,'is_array',False))
        lo=getattr(prop,'hard_min',None)
        hi=getattr(prop,'hard_max',None)
        def scalar(x):
            choices=[(x+1 if kind=='INT' else x+0.5),
                     (x-1 if kind=='INT' else x-0.5), 1, 0, -1]
            for c in choices:
                if lo is not None and c < lo:continue
                if hi is not None and c > hi:continue
                if c != x:return c
            return None
        if is_array:
            seq=list(value)
            for index,old in enumerate(seq):
                if hasattr(old, '__iter__'):
                    nested=list(old)
                    for subindex, component in enumerate(nested):
                        candidate=scalar(component)
                        if candidate is not None:
                            nested[subindex]=candidate
                            seq[index]=nested
                            return seq,None
                else:
                    candidate=scalar(old)
                    if candidate is not None:
                        seq[index]=candidate
                        return seq,None
            return None,'no_different_value_in_bounds'
        v=scalar(value)
        return (v,None) if v is not None else (None,'no_different_value_in_bounds')
    if kind=='POINTER':
        return None,'requires_external_datablock'
    return None,'unsupported_property_kind'


def equal_values(a,b):
    if isinstance(a,(int,float)) and not isinstance(a,bool) and isinstance(b,(int,float)) and not isinstance(b,bool):
        return math.isclose(a,b,rel_tol=1e-6,abs_tol=1e-6)
    if type(a) is not type(b):return False
    if isinstance(a,dict):
        return a.keys()==b.keys() and all(equal_values(a[k],b[k]) for k in a)
    if isinstance(a,list):
        return len(a)==len(b) and all(equal_values(x,y) for x,y in zip(a,b))
    return a==b


def get_socket(node, spec):
    collection=getattr(node,spec['side'])
    if spec['index']>=len(collection):
        raise IndexError('socket index changed')
    socket=collection[spec['index']]
    if socket.identifier!=spec['identifier'] or socket.bl_idname!=spec['bl_idname']:
        raise ValueError('socket identity changed')
    return socket


def test_socket_defaults(tree, nid, state, order, stats, collection_setup=None):
    cfg=state['rna']
    menus=[{k:m[k] for k in ('side','index','identifier','value')}
           for m in state['menus']]
    node=new_configured_node(tree,nid,cfg,order,menus)
    record={'state_index':state['_index'],'sockets':[],'replay':{}}
    existing=set()
    if collection_setup is not None:
        record['collection_setup']={'name':collection_setup[0],'args':collection_setup[1]}
        for side in ('inputs','outputs'):
            existing.update((side,i,s.identifier,s.bl_idname) for i,s in enumerate(getattr(node,side)))
        getattr(node,collection_setup[0]).new(*collection_setup[1])
    accepted=[]
    try:
        for side in ('inputs','outputs'):
            # Snapshot list before any default-value mutation.
            specifications=[{'side':side,'index':i,'name':s.name,'identifier':s.identifier,
                             'bl_idname':s.bl_idname} for i,s in enumerate(getattr(node,side))]
            for spec in specifications:
                if collection_setup is not None and (side,spec['index'],spec['identifier'],spec['bl_idname']) in existing:
                    continue
                row=dict(spec)
                try:
                    socket=get_socket(node,spec)
                    prop=socket.bl_rna.properties.get('default_value')
                    if prop is None or not hasattr(socket,'default_value'):
                        row['status']='no_default_value'
                    else:
                        row['rna']=property_spec(prop)
                        row['original']=current_value(socket,'default_value')
                        row['ui_accessible']=(not socket.hide_value and socket.enabled
                                              and not socket.is_unavailable and not socket.is_inactive)
                        if prop.is_readonly:
                            row['status']='readonly'
                        elif socket.bl_idname=='NodeSocketMenu':
                            # Joint-state stage already verified selected Menu values;
                            # changing them here would change the state under test.
                            row['status']='configured_by_menu_state'
                        else:
                            candidate,reason=different_value(socket,'default_value',prop)
                            if reason:
                                row['status']='not_testable_with_scalar_fixture'
                                row['reason']=reason
                                # Baseline self-assignment is *not* counted as non-default support.
                            else:
                                row['requested']=encode(candidate)
                                try:
                                    socket.default_value=candidate
                                    row['actual']=current_value(socket,'default_value')
                                    row['status']='accepted' if equal_values(row['actual'],row['requested']) else 'coerced'
                                    if row['status']=='accepted':
                                        accepted.append({'socket':spec,'value':candidate})
                                except Exception as ex:
                                    row['status']='rejected';row['error']=error(ex)
                except Exception as ex:
                    row['status']='inspection_error';row['error']=error(ex)
                record['sockets'].append(row)
                stats['socket_'+row['status']]+=1
        # A setter may have invalidated values assigned before it.
        retained=[]
        for change in accepted:
            try:
                now=current_value(get_socket(node,change['socket']),'default_value')
                if equal_values(now,encode(change['value'])):retained.append(change)
                else:
                    record.setdefault('post_assignment_changes',[]).append({
                        'socket':change['socket'],'requested':encode(change['value']), 'actual':now})
            except Exception as ex:
                record.setdefault('post_assignment_changes',[]).append({
                    'socket':change['socket'],'error':error(ex)})
        expected=sockets(node)
        other=new_configured_node(tree,nid,cfg,order,menus)
        errors=[]
        if collection_setup is not None:
            getattr(other,collection_setup[0]).new(*collection_setup[1])
        try:
            for change in accepted:
                try:
                    get_socket(other,change['socket']).default_value=change['value']
                except Exception as ex:
                    errors.append({'socket':change['socket'],'error':error(ex)})
            reproduced=sockets(other)
            record['replay']={'status':'matched' if not errors and equal_values(expected,reproduced) else 'mismatched',
                              'assigned_socket_count':len(accepted),
                              'retained_after_all_writes':len(retained)}
            if errors:record['replay']['write_errors']=errors
            if not equal_values(expected,reproduced):
                record['replay']['expected']=expected
                record['replay']['actual']=reproduced
            stats['socket_replay_'+record['replay']['status']]+=1
        finally:
            tree.nodes.remove(other)
    except Exception as ex:
        record['replay']={'status':'error','error':error(ex)}
        stats['socket_replay_error']+=1
    finally:
        tree.nodes.remove(node)
    return record


def item_fields(item):
    fields=[]
    for p in item.bl_rna.properties:
        if p.identifier=='rna_type':continue
        row={'name':p.identifier,'rna':property_spec(p)}
        try:row['original']=current_value(item,p.identifier)
        except Exception as ex:row['read_error']=error(ex)
        fields.append(row)
    return fields


def get_item(coll, created, start_length):
    """Prefer the returned RNA item; fallback to append position when needed."""
    if created is not None and hasattr(created,'bl_rna'):return created
    return coll[start_length]


def test_item_properties(tree,nid,state,order,collection_name,args,baseline):
    menus=[{k:m[k] for k in ('side','index','identifier','value')} for m in state['menus']]
    out={'origin':'initial' if baseline else 'added','fields':[]}

    def make_item():
        node=new_configured_node(tree,nid,state['rna'],order,menus)
        try:
            coll=getattr(node,collection_name)
            if baseline:
                if not len(coll):
                    return node,None
                return node,coll[0]
            start=len(coll)
            return node,get_item(coll,coll.new(*args),start)
        except BaseException:
            tree.nodes.remove(node)
            raise

    node=None
    try:
        node,item=make_item()
        if item is None:return {'origin':'initial','status':'no_initial_items'}
        fields=item_fields(item)
        out['item_type']=item.bl_rna.identifier
    except Exception as ex:
        out['status']='error';out['error']=error(ex)
        return out
    finally:
        if node is not None:tree.nodes.remove(node)

    # Each property is tested on a fresh item: changing socket_type must not
    # silently influence the following property probe.
    for field in fields:
        row=dict(field)
        node=None
        try:
            if 'read_error' in row:
                row['status']='unreadable'
            elif field['rna']['readonly']:
                row['status']='readonly'
            else:
                node,item=make_item()
                prop=item.bl_rna.properties[field['name']]
                value,reason=different_value(item,field['name'],prop)
                if reason:
                    row['status']='not_testable_with_scalar_fixture';row['reason']=reason
                else:
                    row['requested']=encode(value)
                    try:
                        setattr(item,field['name'],value)
                        row['actual']=current_value(item,field['name'])
                        row['status']='accepted' if equal_values(row['actual'],row['requested']) else 'coerced'
                    except Exception as ex:
                        row['status']='rejected';row['error']=error(ex)
        except Exception as ex:
            row['status']='inspection_error';row['error']=error(ex)
        finally:
            if node is not None:tree.nodes.remove(node)
        out['fields'].append(row)
    out['status']='tested'
    return out


def test_collection_operations(tree,nid,state,order,collection_name,args):
    menus=[{k:m[k] for k in ('side','index','identifier','value')} for m in state['menus']]
    result={}
    for operation in ('remove','move','clear'):
        node=new_configured_node(tree,nid,state['rna'],order,menus)
        row={}
        try:
            coll=getattr(node,collection_name)
            if not callable(getattr(coll,operation,None)):
                row['status']='not_exposed'
            else:
                row['length_initial']=len(coll)
                if operation=='remove':
                    old_count=len(coll)
                    coll.new(*args)
                    row['length_before']=len(coll)
                    coll.remove(coll[-1])
                    row['length_after']=len(coll)
                    row['status']='accepted' if len(coll)==old_count else 'not_restored'
                    row['socket_state_after']=sockets(node)
                elif operation=='move':
                    while len(coll)<2:coll.new(*args)
                    row['length_before']=len(coll)
                    row['items_before']=[getattr(it,'name',None) for it in coll]
                    coll.move(0,1)
                    row['items_after']=[getattr(it,'name',None) for it in coll]
                    row['length_after']=len(coll)
                    row['status']='accepted'
                else:
                    coll.new(*args)
                    row['length_before']=len(coll)
                    coll.clear()
                    row['length_after']=len(coll)
                    row['status']='accepted'
        except Exception as ex:
            row['status']='rejected';row['error']=error(ex)
        finally:
            tree.nodes.remove(node)
        result[operation]=row
    # Whether Blender permits removal of pre-existing items is separate
    # from removal of a newly-added item.
    node=new_configured_node(tree,nid,state['rna'],order,menus)
    try:
        coll=getattr(node,collection_name)
        if len(coll) and callable(getattr(coll,'remove',None)):
            before=len(coll)
            try:
                coll.remove(coll[0])
                result['remove_initial']={'status':'accepted','length_before':before,
                                          'length_after':len(coll)}
            except Exception as ex:
                result['remove_initial']={'status':'rejected','error':error(ex)}
        else:
            result['remove_initial']={'status':'not_applicable'}
    finally:
        tree.nodes.remove(node)
    return result


def order_trial(tree,nid,state,order,collection_name,args):
    """Compare 2 construction orders and a 3rd when a Menu is present."""
    menu=[{k:m[k] for k in ('side','index','identifier','value')} for m in state['menus']]
    def choose_order(label):
        if label=='config_then_new':
            node=new_configured_node(tree,nid,state['rna'],order,menu)
            getattr(node,collection_name).new(*args)
        else:
            node=tree.nodes.new(nid)
            try:
                if label=='new_then_config':
                    getattr(node,collection_name).new(*args)
                    assign_rna(node,state['rna'],order)
                elif label=='rna_then_new_then_menu':
                    assign_rna(node,state['rna'],order)
                    getattr(node,collection_name).new(*args)
                else:raise ValueError(label)
                for m in menu:
                    if m['value'] is None:continue
                    sk=get_socket(node,{'side':m['side'],'index':m['index'],
                                        'identifier':m['identifier'],'bl_idname':'NodeSocketMenu'})
                    sk.default_value=m['value']
            except BaseException:
                tree.nodes.remove(node)
                raise
        return node
    results={}
    for label in (['config_then_new','new_then_config','rna_then_new_then_menu']
                  if menu else ['config_then_new','new_then_config']):
        node=None
        try:
            node=choose_order(label)
            results[label]={'status':'applied', 'sockets':sockets(node),
                            'length':len(getattr(node,collection_name)),
                            'items':[{'name':getattr(i,'name',None),
                                      'socket_type':getattr(i,'socket_type',None)}
                                     for i in getattr(node,collection_name)]}
        except Exception as ex:
            results[label]={'status':'rejected','error':error(ex)}
        finally:
            if node is not None:tree.nodes.remove(node)
    baseline=results['config_then_new']
    outcomes={k: ('matched' if v==baseline else 'different_or_rejected')
              for k,v in results.items() if k!='config_then_new'}
    out={'outcomes':outcomes}
    if any(v!='matched' for v in outcomes.values()):
        out['details']=results
    return out


def main(args=None):
    args=(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []) if args is None else args
    if len(args)!=3:raise SystemExit('Usage: blender -b --python probe_final_states.py -- RNA.json JOINT.json OUTPUT.json')
    rna_path,source_path,output_path=map(Path,args)
    rna=json.loads(rna_path.read_text(encoding='utf-8'))
    data=json.loads(source_path.read_text(encoding='utf-8'))
    if rna.get('blender_version')!=bpy.app.version_string or data.get('blender_version')!=bpy.app.version_string:
        raise ValueError('Input JSON Blender version does not match runtime')
    rna_states={node['bl_idname']:node['states'] for node in rna['nodes']}
    tree=bpy.data.node_groups.new('NodeForge final-state study (temporary)','GeometryNodeTree')
    out={'schema':SCHEMA,'blender_version':bpy.app.version_string,
         'source_schema':data.get('schema'),
         'scope':{
             'socket_default_test':'one changed value for each readable writable non-Menu default_value, followed by readback and replay',
             'dynamic_item_test':'editable item properties per accepted new() type, remove/move/clear for first accepted type in each state',
             'construction_order':'config -> new versus new -> config; RNA -> new -> Menu when Menu exists',
             'not_tested':['node group interface changes','external datablock pointer fixtures',
                           'linked zone pairing','compound multi-connection behavior',
                           'actual geometry and field values',
                           'every possible numeric value / string / dynamic item count']},
         'nodes':[],'errors':[]}
    stats=Counter()
    for index,node_data in enumerate(data['nodes']):
        nid=node_data['bl_idname']
        node_row={'bl_idname':nid,'states':[]}
        for state_index,state in enumerate(node_data['states']):
            if state['status']!='applied':continue
            state=dict(state)
            state['_index']=state_index
            # Some valid Blender configurations require a particular assignment
            # order (e.g. Compare data_type and operation). Use the verified one.
            source_rna_state=rna_states[nid][state['rna_state_index']]
            if source_rna_state['config'] != state['rna']:
                raise ValueError('RNA and joint-state input files disagree for '+nid)
            order=source_rna_state['used_assignment_order']
            entry={'state_index':state_index}
            try:
                entry['defaults']=test_socket_defaults(tree,nid,state,order,stats)
                entry['collections']=[]
                for col in state.get('collections',[]):
                    c={'name':col['name'],'accepted_types':[]}
                    good=[t['args'] for t in col.get('attempts',[]) if t['status']=='accepted']
                    for new_args in good:
                        p=test_item_properties(tree,nid,state,order,col['name'],new_args,False)
                        defaults=test_socket_defaults(tree,nid,state,order,stats,(col['name'],new_args))
                        c['accepted_types'].append({'args':new_args,'item':p,'new_socket_defaults':defaults})
                        stats['item_type_probes']+=1
                        stats['item_field_probes']+=len(p.get('fields',[]))
                    if good:
                        c['initial_item']=test_item_properties(tree,nid,state,order,col['name'],good[0],True)
                        c['operations']=test_collection_operations(tree,nid,state,order,col['name'],good[0])
                        c['construction_order']=order_trial(tree,nid,state,order,col['name'],good[0])
                        for val in c['construction_order']['outcomes'].values():stats['order_'+val]+=1
                        for name,r in c['operations'].items():stats['operation_'+name+'_'+r['status']]+=1
                    else:
                        c['status']='no_accepted_new_variant'
                    entry['collections'].append(c)
                    stats['collections_inspected']+=1
            except Exception as ex:
                entry['error']=error(ex)
                stats['state_errors']+=1
                tree.nodes.clear()
            node_row['states'].append(entry)
            stats['states_inspected']+=1
        out['nodes'].append(node_row)
        stats['nodes_inspected']+=1
        if (index+1)%25==0:
            print('FINAL_STATES',index+1,'/',len(data['nodes']),
                  'states',stats['states_inspected'],'collections',stats['collections_inspected'],flush=True)
    out['summary']=dict(stats)
    output_path.parent.mkdir(parents=True,exist_ok=True)
    output_path.write_text(json.dumps(out,ensure_ascii=False,separators=(',',':'),allow_nan=False),encoding='utf-8')
    print('DONE FINAL_STATES',bpy.app.version_string,dict(stats),flush=True)
    bpy.data.node_groups.remove(tree)

if __name__=='__main__':main()
