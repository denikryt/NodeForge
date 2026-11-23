"""Follow up link-induced state changes and inspect paired-zone collections.

blender -b -t 1 --factory-startup --python probe_context_states.py -- \
    RNA.json JOINT.json CONNECTIONS.json OUTPUT.json

Pure Blender introspection: no node allowlist, no NodeForge dependency.
Only replay *observed* link-induced state changes from the existing link probe.
Test individual RNA and Menu alternatives in the linked context, and recheck
collection.new() for each resulting successfully configured context.
Discover paired nodes via pair_with_output() and a corresponding registered
Input/Output type; probe their collection.new() effects on BOTH nodes.
"""
import json
import sys
from collections import Counter
from pathlib import Path

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parent))
from explore_joint_states import (new_configured_node, menu_choices, enumerate_menus,
                                  collection_descriptions, error, keep_snapshot)
from blender_rna import sockets, encode, base_props

SCHEMA = 'nodeforge.blender_context_probe/1.0'
SOCKET_STATES = {}


def compact_sockets(node):
    """Changes of the physical interface, not data values or node coordinates."""
    return keep_snapshot(node, SOCKET_STATES)


def collect_rna(node):
    values = []
    for prop in node.bl_rna.properties:
        if prop.identifier in base_props or prop.identifier == 'rna_type' or prop.is_readonly:
            continue
        if prop.type == 'BOOLEAN':
            candidates = [False, True]
        elif prop.type == 'ENUM' and not prop.is_enum_flag:
            candidates = [x.identifier for x in prop.enum_items if x.identifier]
        else:
            continue
        try:
            initial = encode(getattr(node, prop.identifier))
        except Exception:
            continue
        for value in candidates:
            if value != initial:
                values.append({'name': prop.identifier, 'value': value, 'initial': initial})
    return values


def new_linked_nodes(tree, spec):
    subject = new_configured_node(tree, spec['node'], spec['rna'],
                                  spec['order'], spec['menus'])
    try:
        partner = tree.nodes.new(spec['partner']['node'])
        if spec['direction'] == 'incoming':
            source = partner.outputs[spec['partner']['index']]
            target = subject.inputs[spec['socket_index']]
        else:
            source = subject.outputs[spec['socket_index']]
            target = partner.inputs[spec['partner']['index']]
        link = tree.links.new(source, target)
        return subject, partner, link
    except BaseException:
        tree.nodes.remove(subject)
        raise


def clean_pair(tree):
    tree.nodes.clear()


def state_of(subject, partner):
    return {'subject_sockets': compact_sockets(subject),
            'partner_sockets': compact_sockets(partner),
            'subject_menus': enumerate_menus(subject),
            'partner_menus': enumerate_menus(partner)}


def mutation(tree, spec, role, kind, assignment, save_sockets=False):
    subject = partner = None
    result = {'role': role, 'kind': kind, 'assignment': assignment}
    try:
        subject, partner, link = new_linked_nodes(tree, spec)
        node = subject if role == 'subject' else partner
        before = state_of(subject, partner)
        if kind == 'rna':
            setattr(node, assignment['name'], assignment['value'])
            actual = encode(getattr(node, assignment['name']))
        elif kind == 'menu':
            sk = getattr(node, assignment['side'])[assignment['index']]
            if sk.bl_idname != 'NodeSocketMenu' or sk.identifier != assignment['identifier']:
                raise ValueError('Menu socket changed identity')
            sk.default_value = assignment['value']
            actual = encode(sk.default_value)
        else:
            raise ValueError(kind)
        result['actual'] = actual
        result['status'] = 'applied' if actual == assignment['value'] else 'coerced'
        result['link_valid_after'] = bool(link.is_valid)
        after = state_of(subject, partner)
        result['interface_changed'] = (before['subject_sockets'] != after['subject_sockets'] or
                                       before['partner_sockets'] != after['partner_sockets'])
        result['menus_changed'] = (before['subject_menus'] != after['subject_menus'] or
                                   before['partner_menus'] != after['partner_menus'])
        if save_sockets or result['interface_changed'] or result['menus_changed']:
            result['after'] = after
    except Exception as ex:
        result['status'] = 'rejected'
        result['error'] = error(ex)
    finally:
        clean_pair(tree)
    return result


def trial_collection(tree, spec, role, name, args, mutation_spec=None):
    """Fresh graph for every call, so collection mutations cannot leak to next trial."""
    row = {'role':role, 'collection':name, 'args':args}
    try:
        subject, partner, link = new_linked_nodes(tree, spec)
        node = subject if role == 'subject' else partner
        if mutation_spec:
            kind,assignment = mutation_spec
            if kind == 'rna':
                setattr(node, assignment['name'], assignment['value'])
            else:
                socket = getattr(node, assignment['side'])[assignment['index']]
                socket.default_value = assignment['value']
        coll=getattr(node,name)
        row['before'] = {'subject': compact_sockets(subject),'partner': compact_sockets(partner),
                         'length':len(coll)}
        item = coll.new(*args)
        row['status'] = 'accepted'
        row['item_type'] = getattr(getattr(item,'bl_rna',None),'identifier',type(item).__name__)
        row['after'] = {'subject':compact_sockets(subject),'partner':compact_sockets(partner),
                        'length':len(coll)}
        row['link_valid_after'] = bool(link.is_valid)
        row['changed_subject'] = row['before']['subject'] != row['after']['subject']
        row['changed_partner'] = row['before']['partner'] != row['after']['partner']
    except Exception as ex:
        row['status'] = 'rejected'
        row['error'] = error(ex)
    finally:
        clean_pair(tree)
    return row


def inspect_link_context(tree, spec, before, stats):
    out = {'subject':spec['node'], 'partner':spec['partner'],
           'state_index':spec['state_index'], 'direction':spec['direction'],
           'socket_index':spec['socket_index'], 'valid':None,
           'after_link':None, 'probes':{}, 'errors':[]}
    try:
        subject, partner, link = new_linked_nodes(tree,spec)
        out['valid']=bool(link.is_valid)
        out['after_link']=state_of(subject,partner)
        descriptors={}
        for role,node in (('subject',subject),('partner',partner)):
            menu_defs=enumerate_menus(node)
            menus=[]
            for m in menu_defs:
                for value in m.get('values',[]):
                    if value != m.get('initial'):
                        menus.append({'side':m['side'],'index':m['index'],
                                      'identifier':m['identifier'],'value':value})
            descriptors[role]={'rna':collect_rna(node),'menus':menus,
                               'collections':collection_descriptions(node)}
        clean_pair(tree)
        for role,defs in descriptors.items():
            results={'rna':[], 'menu':[], 'collections':[]}
            for key in ('rna','menus'):
                kind='menu' if key=='menus' else 'rna'
                for a in defs[key]:
                    trial=mutation(tree,spec,role,kind,a)
                    results['menu' if kind=='menu' else 'rna'].append(trial)
                    stats['linked_state_trials']+=1
                    if trial['status']=='applied':
                        # Re-discover collection API in this modified linked state.
                        try:
                            s,p,_=new_linked_nodes(tree,spec)
                            n=s if role=='subject' else p
                            if kind=='rna': setattr(n,a['name'],a['value'])
                            else: getattr(n,a['side'])[a['index']].default_value=a['value']
                            dyn=collection_descriptions(n)
                            clean_pair(tree)
                            if dyn:
                                crows=[]
                                for desc in dyn:
                                    crow={'name':desc['name'],'new_parameters':desc.get('new_parameters'),
                                          'attempts':[trial_collection(tree,spec,role,desc['name'],arg,(kind,a))
                                                      for arg in desc.get('candidate_args',[])]}
                                    crows.append(crow)
                                trial['collections']=crows
                                stats['linked_variant_collection_trials']+=sum(len(c['attempts']) for c in crows)
                        except Exception as ex:
                            trial['collection_probe_error']=error(ex)
                            clean_pair(tree)
            for desc in defs['collections']:
                crow={'name':desc['name'], 'new_parameters':desc.get('new_parameters'),
                      'attempts':[trial_collection(tree,spec,role,desc['name'],arg)
                                  for arg in desc.get('candidate_args',[])]}
                results['collections'].append(crow)
                stats['linked_baseline_collection_trials']+=len(crow['attempts'])
            out['probes'][role]=results
        stats['linked_contexts_inspected']+=1
    except Exception as ex:
        out['errors'].append(error(ex))
        stats['linked_context_errors']+=1
    finally:
        clean_pair(tree)
    return out


def paired_node_types(tree, ids):
    """Look for Blender's pairing method; matching output ID is derived, not listed."""
    pairs=[]
    registered=set(ids)
    for nid in ids:
        node=None
        try:
            node=tree.nodes.new(nid)
            method=getattr(node,'pair_with_output',None)
            if callable(method) and nid.endswith('Input'):
                oid=nid[:-len('Input')]+'Output'
                if oid in registered:
                    pairs.append((nid,oid))
        except Exception:
            pass
        finally:
            if node is not None:tree.nodes.remove(node)
    return pairs


def new_paired(tree, in_id, out_id):
    a=tree.nodes.new(in_id)
    b=tree.nodes.new(out_id)
    a.pair_with_output(b)
    return a,b


def paired_trial(tree,in_id,out_id,side,coll_name,args):
    row={'side':side,'collection':coll_name,'args':args}
    try:
        a,b=new_paired(tree,in_id,out_id)
        node=a if side=='input' else b
        coll=getattr(node,coll_name)
        before={'input':compact_sockets(a),'output':compact_sockets(b), 'length':len(coll)}
        item=coll.new(*args)
        after={'input':compact_sockets(a),'output':compact_sockets(b),'length':len(coll)}
        row.update({'status':'accepted','before':before,'after':after,
                    'item_type':getattr(getattr(item,'bl_rna',None),'identifier',type(item).__name__),
                    'input_changed':before['input']!=after['input'],
                    'output_changed':before['output']!=after['output']})
    except Exception as ex:
        row['status']='rejected';row['error']=error(ex)
    finally:
        tree.nodes.clear()
    return row


def inspect_pair(tree,in_id,out_id,stats):
    result={'input_node':in_id,'output_node':out_id,'collections':[], 'errors':[]}
    try:
        a = tree.nodes.new(in_id)
        b = tree.nodes.new(out_id)
        result['unpaired_sockets'] = {'input':compact_sockets(a),'output':compact_sockets(b)}
        a.pair_with_output(b)
        result['paired_sockets']={'input':compact_sockets(a),'output':compact_sockets(b)}
        for side,node in (('input',a),('output',b)):
            for desc in collection_descriptions(node):
                result['collections'].append({'side':side,'name':desc['name'],
                     'length':desc.get('length'),'new_parameters':desc.get('new_parameters'),
                     'candidate_error':desc.get('candidate_error'),
                     'candidate_args':desc.get('candidate_args',[])})
        tree.nodes.clear()
        for coll in result['collections']:
            coll['attempts']=[paired_trial(tree,in_id,out_id,coll['side'],coll['name'],args)
                              for args in coll['candidate_args']]
            stats['paired_collection_trials']+=len(coll['attempts'])
            stats['paired_accepted']+=sum(t['status']=='accepted' for t in coll['attempts'])
        stats['pairs_inspected']+=1
    except Exception as ex:
        result['errors'].append(error(ex))
        stats['pair_errors']+=1
    finally:
        tree.nodes.clear()
    return result


def main(args=None):
    SOCKET_STATES.clear()
    args=(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []) if args is None else args
    if len(args)!=4:
        raise SystemExit('Usage: ... -- RNA.json JOINT.json CONNECTIONS.json OUTPUT.json')
    rna_p, joint_p, links_p, output_p=map(Path,args)
    rna=json.loads(rna_p.read_text());joint=json.loads(joint_p.read_text());links=json.loads(links_p.read_text())
    for src in (rna,joint,links):
        if src['blender_version'] != bpy.app.version_string:
            raise ValueError('Blender version does not match input JSON')
    node_ids=[n['bl_idname'] for n in joint['nodes']]
    rna_map={n['bl_idname']:n for n in rna['nodes']}
    joint_map={n['bl_idname']:n for n in joint['nodes']}
    tree=bpy.data.node_groups.new('NF link-context probe temporary','GeometryNodeTree')
    out={'schema':SCHEMA,'blender_version':bpy.app.version_string,
         'scope':{'link_contexts':'replays observed post-link structural/Menu changes; individual RNA and Menu alternatives, then collection.new; identical observed interfaces and Menu option sets reuse the same probe',
                  'pairing':'pairs discovered via pair_with_output; each collection.new candidate checked in a fresh paired graph',
                  'limitations':['not every simultaneous multi-link configuration',
                                 'not the cross product of post-link RNA and Menu alternatives',
                                 'interface-based reuse does not prove identical computational behavior',
                                 'not geometry evaluation','not external resource mutation']},
         'link_contexts':[], 'pairs':[], 'socket_states':SOCKET_STATES, 'errors':[]}
    stats=Counter()
    checked_contexts={}
    for nidx, link_node in enumerate(links['nodes']):
        nid=link_node['bl_idname']
        j=joint_map[nid];r=rna_map[nid]
        for link_state in link_node['states']:
            ix=link_state['state_index']; st=j['states'][ix]
            if st['status']!='applied': continue
            rs=r['states'][st['rna_state_index']]
            menu_values=[{'side':m['side'],'index':m['index'],
                          'identifier':m['identifier'],'value':m['value']} for m in st['menus']]
            for socket_row in link_state['connections']:
                for trial in socket_row.get('tests',[]):
                    if trial.get('status')!='created' or not trial.get('valid'):
                        continue
                    # Existing states have already been explored in isolation;
                    # retry only the contexts that actually changed in Blender.
                    if not any(trial.get(k) for k in ('subject_changed','partner_changed',
                                                     'subject_menu_changed','partner_menu_changed')):
                        continue
                    spec={'node':nid,'rna':st['rna'],'order':rs['used_assignment_order'],
                          'menus':menu_values,'state_index':ix,
                          'direction':trial['direction'],
                          'socket_index':trial['socket_index'],'partner':trial['partner']}
                    # Same observed physical interface + same available Menu
                    # options + same link endpoints: the interface-level probe
                    # would repeat. Keep a reference for every original state.
                    def options(which):
                        obs=trial.get(which+'_menu_after') if trial.get(which+'_menu_changed') else trial.get(which+'_menu_before')
                        return [(m['side'],m['index'],m['identifier'],m['status'],m.get('values',[]))
                                for m in (obs or [])]
                    context_key=json.dumps([nid,trial['direction'],trial['socket_index'],
                                            trial['partner']['node'],trial['partner']['index'],
                                            trial.get('subject_after'),trial.get('partner_after'),
                                            options('subject'),options('partner')],
                                           sort_keys=True,ensure_ascii=False)
                    context_no=len(out['link_contexts'])
                    if context_key in checked_contexts:
                        out['link_contexts'].append({'reused_context':checked_contexts[context_key],
                            'subject':nid,'state_index':ix,
                            'partner':trial['partner'], 'socket_index':trial['socket_index'],
                            'direction':trial['direction']})
                        stats['linked_contexts_reused']+=1
                    else:
                        checked_contexts[context_key]=context_no
                        out['link_contexts'].append(inspect_link_context(tree,spec,trial,stats))
                    stats['linked_contexts_selected']+=1
        if (nidx+1)%50==0:
            print('CONTEXT',nidx+1,'/',len(links['nodes']),dict(stats),flush=True)
    for in_id,out_id in paired_node_types(tree,node_ids):
        out['pairs'].append(inspect_pair(tree,in_id,out_id,stats))
    out['summary']=dict(stats)
    output_p.parent.mkdir(parents=True,exist_ok=True)
    output_p.write_text(json.dumps(out,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    bpy.data.node_groups.remove(tree)
    print('DONE',bpy.app.version_string,dict(stats),flush=True)

if __name__=='__main__':main()
