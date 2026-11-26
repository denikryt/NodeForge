"""Inspect socket links for every saved RNA + Menu state (Blender background mode).

Run:
  blender -b -t 1 --factory-startup --python probe_connections.py -- JOINT.json RNA.json OUT.json

For each socket in each saved state, connect one matching-type source or sink,
record the observed states of BOTH nodes, remove the link, and check restoration.
For Menu outputs, try all discovered Menu inputs because they can supply different
sets of options. No node-specific rules, wrapper generation, or value evaluation.
"""
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path

import bpy
sys.path.insert(0, str(Path(__file__).resolve().parent))
from explore_joint_states import new_configured_node, keep_snapshot, menu_choices, error
from blender_rna import sockets

SCHEMA = 'nodeforge.blender_link_probe/1.0'


def menus(node):
    result=[]
    for side in ('inputs', 'outputs'):
        for index, sock in enumerate(getattr(node, side)):
            if sock.bl_idname == 'NodeSocketMenu':
                result.append({'side':side,'index':index,'identifier':sock.identifier,
                               'name':sock.name, **menu_choices(sock)})
    return result


def find_catalog(tree, node_ids):
    sources=defaultdict(list)
    sinks=defaultdict(list)
    source_families=defaultdict(list)
    sink_families=defaultdict(list)
    for nid in node_ids:
        node=None
        try:
            node=tree.nodes.new(nid)
            for side, table, families in (('outputs', sources, source_families), ('inputs', sinks, sink_families)):
                for idx, socket in enumerate(getattr(node, side)):
                    if socket.bl_idname!='NodeSocketVirtual' and not socket.is_unavailable:
                        spec={'node':nid,'index':idx,'name':socket.name,
                              'identifier':socket.identifier, 'socket_type':socket.bl_idname,
                              'data_type':socket.type}
                        table[socket.bl_idname].append(spec)
                        families[socket.type].append(spec)
        except Exception:
            pass
        finally:
            if node is not None:
                tree.nodes.remove(node)
    # Deterministic candidates, with no node-specific priority lists.
    for table in (sources, sinks, source_families, sink_families):
        for choices in table.values():
            choices.sort(key=lambda r: (r['node'], r['index']))
    return sources,sinks,source_families,sink_families


def record_link(tree, subject, side, socket_index, partner_spec, states):
    partner=None
    link=None
    entry={'direction':'incoming' if side=='inputs' else 'outgoing',
           'socket_index':socket_index,'partner':partner_spec}
    try:
        partner=tree.nodes.new(partner_spec['node'])
        subject_socket=getattr(subject,side)[socket_index]
        partner_socket=getattr(partner,'outputs' if side=='inputs' else 'inputs')[partner_spec['index']]
        entry['subject_before']=keep_snapshot(subject,states)
        entry['partner_before']=keep_snapshot(partner,states)
        has_menu=(any(s.bl_idname=='NodeSocketMenu' for s in subject.inputs) or
                  any(s.bl_idname=='NodeSocketMenu' for s in subject.outputs) or
                  any(s.bl_idname=='NodeSocketMenu' for s in partner.inputs) or
                  any(s.bl_idname=='NodeSocketMenu' for s in partner.outputs))
        if has_menu:
            entry['subject_menu_before']=menus(subject)
            entry['partner_menu_before']=menus(partner)
        if side=='inputs':
            link=tree.links.new(subject_socket,partner_socket)
        else:
            link=tree.links.new(partner_socket,subject_socket)
        entry['status']='created'
        entry['valid']=bool(link.is_valid)
        entry['subject_socket_is_linked']=bool(subject_socket.is_linked)
        entry['partner_socket_is_linked']=bool(partner_socket.is_linked)
        entry['subject_after']=keep_snapshot(subject,states)
        entry['partner_after']=keep_snapshot(partner,states)
        entry['subject_changed']=(entry['subject_before']!=entry['subject_after'])
        entry['partner_changed']=(entry['partner_before']!=entry['partner_after'])
        if has_menu:
            sm=menus(subject)
            pm=menus(partner)
            entry['subject_menu_changed']=(entry['subject_menu_before']!=sm)
            entry['partner_menu_changed']=(entry['partner_menu_before']!=pm)
            if entry['subject_menu_changed']:
                entry['subject_menu_after']=sm
            if entry['partner_menu_changed']:
                entry['partner_menu_after']=pm
        # Observe whether the single-link experiment leaves changes behind.
        tree.links.remove(link)
        link=None
        entry['subject_restored']=keep_snapshot(subject,states)==entry['subject_before']
        entry['partner_restored']=keep_snapshot(partner,states)==entry['partner_before']
        if has_menu:
            entry['subject_menu_restored']=menus(subject)==entry['subject_menu_before']
            entry['partner_menu_restored']=menus(partner)==entry['partner_menu_before']
    except Exception as ex:
        entry['status']='error'
        entry['error']=error(ex)
    finally:
        if link is not None:
            try:tree.links.remove(link)
            except Exception:pass
        if partner is not None:
            tree.nodes.remove(partner)
    return entry


def inspect_node(tree, joint_node, rna_node, sources, sinks, source_families, sink_families):
    nid=joint_node['bl_idname']
    row={'bl_idname':nid,'states':[],'socket_states':{},'errors':[]}
    sums=Counter()
    for index,state in enumerate(joint_node['states']):
        if state['status']!='applied':
            continue
        node=None
        srow={'state_index':index,'rna_state_index':state['rna_state_index'],
              'socket_state_id':state['socket_state_id'],'connections':[]}
        try:
            rna_state=rna_node['states'][state['rna_state_index']]
            menu_assignments=[{'side':m['side'],'index':m['index'],
                               'identifier':m['identifier'],'value':m['value']}
                              for m in state['menus']]
            node=new_configured_node(tree,nid,state['rna'],
                                     rna_state['used_assignment_order'],menu_assignments)
            baseline=keep_snapshot(node,row['socket_states'])
            srow['observed_before']=baseline
            # A failed restoration does not contaminate subsequent tests:
            # rebuild the configured node before the next experiment if necessary.
            for side, catalog, families in (('inputs', sources, source_families), ('outputs', sinks, sink_families)):
                initial_sockets=sockets(node)[side]
                for index_socket, meta in enumerate(initial_sockets):
                    su={'direction':'incoming' if side=='inputs' else 'outgoing',
                        'socket_index':index_socket,'socket_name':meta['name'],
                        'socket_identifier':meta['identifier'],
                        'socket_type':meta['bl_idname'],'is_unavailable':meta['is_unavailable']}
                    choices=catalog.get(meta['bl_idname'],[])
                    su['partner_match']='exact_socket_id'
                    if not choices and meta['data_type'] not in ('CUSTOM','VIRTUAL'):
                        choices=families.get(meta['data_type'],[])
                        su['partner_match']='same_data_type'
                    if meta['bl_idname']=='NodeSocketVirtual':
                        su['status']='virtual_socket'
                        srow['connections'].append(su)
                        sums['virtual_sockets']+=1
                        continue
                    if not choices:
                        su['status']='no_matching_socket_in_catalog'
                        srow['connections'].append(su)
                        sums['no_matching_partner']+=1
                        continue
                    # Only Menu outputs need multiple sinks: their available choices
                    # are often supplied by the connected consumer.
                    if side=='outputs' and meta['bl_idname']=='NodeSocketMenu':
                        selected=choices
                    else:
                        selected=choices[:1]
                    if su['partner_match']=='same_data_type':
                        sums['fallback_socket_matches']+=1
                    su['tests']=[]
                    for partner_spec in selected:
                        trial=record_link(tree,node,side,index_socket,partner_spec,row['socket_states'])
                        su['tests'].append(trial)
                        sums['attempts']+=1
                        sums['links_created']+=trial['status']=='created'
                        sums['links_valid']+=trial.get('valid',False)
                        sums['subject_changed']+=trial.get('subject_changed',False)
                        sums['partner_changed']+=trial.get('partner_changed',False)
                        sums['menu_changes']+=(trial.get('subject_menu_changed',False) or trial.get('partner_menu_changed',False))
                        sums['errors']+=trial['status']=='error'
                    su['status']='tested'
                    srow['connections'].append(su)
                    if keep_snapshot(node,row['socket_states'])!=baseline:
                        # An error or a non-restoring connection must not affect
                        # the following socket tests in this state.
                        tree.nodes.remove(node)
                        node=new_configured_node(tree,nid,state['rna'],
                                         rna_state['used_assignment_order'],menu_assignments)
                        sums['subject_reset_after_test']+=1
            srow['status']='tested'
            sums['states_tested']+=1
        except Exception as ex:
            srow['status']='error'
            srow['error']=error(ex)
            row['errors'].append({'state_index':index,'error':error(ex)})
            sums['states_failed']+=1
        finally:
            if node is not None:
                tree.nodes.remove(node)
        row['states'].append(srow)
    row['summary']=dict(sums)
    return row


def main(args=None):
    args=(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else []) if args is None else args
    if len(args)!=3:
        raise SystemExit('Usage: blender -b -t 1 --factory-startup --python probe_connections.py -- JOINT.json RNA.json OUT.json')
    joint_p,rna_p,out_p=map(Path,args)
    joint=json.loads(joint_p.read_text())
    rna=json.loads(rna_p.read_text())
    if joint['blender_version']!=bpy.app.version_string or rna['blender_version']!=bpy.app.version_string:
        raise ValueError('Blender and source JSON versions must match')
    tree=bpy.data.node_groups.new('NodeForge connection inspector temporary','GeometryNodeTree')
    ids=[n['bl_idname'] for n in joint['nodes']]
    sources,sinks,source_families,sink_families=find_catalog(tree,ids)
    source_rna={n['bl_idname']:n for n in rna['nodes']}
    doc={'schema':SCHEMA,'blender_version':bpy.app.version_string,
         'source_joint_schema':joint['schema'],
         'method':'one exact-id or same-data-type source for each input and sink for each output; all sink variants for Menu outputs; each recorded RNA/Menu state',
         'limitations':['one source/sink representative per ordinary socket type, not all possible node pairs',
                        'same-data-type fallback may not be accepted by Blender',
                        'does not test implicit cross-data-type conversions',
                        'Menu values requiring a linked graph may remain unresolved',
                        'zone pairing, external resources, evaluated outputs not tested',
                        'invalid link or unchanged UI does not establish computational semantics'],
         'socket_type_catalog':{
            'sources':{t:len(v) for t,v in sources.items()},
            'sinks':{t:len(v) for t,v in sinks.items()}},
         'nodes':[], 'errors':[]}
    sums=Counter()
    for pos,node in enumerate(joint['nodes']):
        try:
            row=inspect_node(tree,node,source_rna[node['bl_idname']],sources,sinks,source_families,sink_families)
            doc['nodes'].append(row)
            for key,value in row['summary'].items():sums[key]+=value
            sums['nodes_tested']+=1
        except Exception as ex:
            doc['errors'].append({'node':node['bl_idname'],'error':error(ex)})
        if (pos+1)%20==0:
            print('LINKS',pos+1,'/',len(joint['nodes']),dict(sums),flush=True)
    sums['nodes_failed']=len(doc['errors'])
    doc['summary']=dict(sums)
    out_p.parent.mkdir(parents=True,exist_ok=True)
    out_p.write_text(json.dumps(doc,ensure_ascii=False,separators=(',',':')),encoding='utf-8')
    print('DONE',bpy.app.version_string,dict(sums),flush=True)
    bpy.data.node_groups.remove(tree)

if __name__=='__main__':main()
