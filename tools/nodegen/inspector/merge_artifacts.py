#!/usr/bin/env python3
"""Assemble independently generated Blender inspector JSON files without discarding data.

Runs with plain Python (no bpy). The original stage JSON files are not modified.
All source records are carried across unchanged; validation issues are reported,
not corrected or silently dropped.
"""
import argparse
import hashlib
import json
import os
from collections import Counter
from pathlib import Path

SCHEMA = 'nodeforge.blender_inspector_merged/1.0'
STAGES = ('inventory', 'rna', 'joint', 'connections', 'type_pairs', 'context', 'final')
NODE_STAGES = ('inventory', 'rna', 'joint', 'connections', 'final')


def load_sources(folder):
    sources, files = {}, {}
    for name in STAGES:
        file = folder / (name + '.json')
        if not file.is_file():
            raise ValueError(f'Missing input file: {file}')
        raw = file.read_bytes()
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError(f'{file}: expected a JSON object')
        sources[name] = data
        files[name] = {'file': file.name, 'sha256': hashlib.sha256(raw).hexdigest(),
                       'bytes': len(raw)}
    return sources, files


def blender_version(stage, document):
    if stage == 'inventory':
        return document.get('blender', {}).get('version')
    return document.get('blender_version')


def index_nodes(stage, document):
    rows = document.get('nodes')
    if not isinstance(rows, list):
        raise ValueError(f'Stage {stage}: missing nodes array')
    seen = {}
    for row in rows:
        if not isinstance(row, dict) or not isinstance(row.get('bl_idname'), str):
            raise ValueError(f'Stage {stage}: invalid node record')
        nid = row['bl_idname']
        if nid in seen:
            raise ValueError(f'Stage {stage}: duplicate node {nid!r}')
        seen[nid] = row
    return seen


def baseline_signature(sockets):
    if not isinstance(sockets, dict):
        return None
    return tuple((side, index, item.get('identifier'), item.get('bl_idname'),
                  item.get('is_inactive'), item.get('is_unavailable'), item.get('is_multi_input'))
                 for side in ('inputs', 'outputs')
                 for index, item in enumerate(sockets.get(side, [])))


def merge_documents(docs, file_info=None):
    missing = set(STAGES).difference(docs)
    if missing:
        raise ValueError(f'Missing stages: {sorted(missing)}')
    versions = {name: blender_version(name, docs[name]) for name in STAGES}
    if len(set(versions.values())) != 1 or not next(iter(versions.values())):
        raise ValueError(f'Blender versions differ (refusing to mix): {versions}')
    builds = {'inventory': docs['inventory'].get('blender', {}).get('build_hash'),
              'rna': docs['rna'].get('blender_build_hash')}
    if all(builds.values()) and len(set(builds.values())) > 1:
        raise ValueError(f'Blender builds differ (refusing to mix): {builds}')

    index = {name: index_nodes(name, docs[name]) for name in NODE_STAGES}
    all_ids = set().union(*(set(group) for group in index.values()))
    issues = []
    def issue(code, node, **kwargs):
        issues.append({'code': code, 'node': node, **kwargs})

    # Independent RNA inventory and RNA state explorer are intentionally both retained.
    inventory_ids = set(index['inventory'])
    for stage in NODE_STAGES:
        for nid in sorted(all_ids - set(index[stage])):
            issue('missing_in_stage', nid, stage=stage)

    for nid in sorted(inventory_ids & set(index['rna'])):
        inventory, rna = index['inventory'][nid], index['rna'][nid]
        inventory_props = {p['name'] for p in inventory.get('properties', [])}
        rna_props = set(rna.get('discrete_properties', {}))
        if inventory_props != rna_props:
            issue('rna_property_inventory_diff', nid,
                  inventory_only=sorted(inventory_props-rna_props),
                  rna_only=sorted(rna_props-inventory_props))
        inventory_sockets = inventory.get('baseline')
        rna_sockets = rna.get('topologies', {}).get(rna.get('baseline_topology_id'))
        if baseline_signature(inventory_sockets) != baseline_signature(rna_sockets):
            issue('baseline_socket_diff', nid)

    for nid in sorted(set(index['rna']) & set(index['joint'])):
        rna = index['rna'][nid]
        for pos, state in enumerate(index['joint'][nid].get('states', [])):
            k = state.get('rna_state_index')
            if not isinstance(k, int) or not 0 <= k < len(rna.get('states', [])):
                issue('invalid_rna_reference', nid, joint_state=pos, rna_state_index=k)
            elif state.get('rna') != rna['states'][k].get('config'):
                issue('rna_state_config_diff', nid, joint_state=pos, rna_state_index=k)

    for stage in ('connections', 'final'):
        for nid in sorted(set(index['joint']) & set(index[stage])):
            count = len(index['joint'][nid].get('states', []))
            for pos, state in enumerate(index[stage][nid].get('states', [])):
                k = state.get('state_index')
                if not isinstance(k, int) or not 0 <= k < count:
                    issue('invalid_joint_reference', nid, stage=stage, item=pos, joint_state_index=k)

    context = docs['context']
    contexts = context.get('link_contexts', [])
    for pos, row in enumerate(contexts):
        nid = row.get('subject')
        if nid not in index['joint']:
            issue('unknown_context_subject', nid, context_index=pos)
            continue
        k = row.get('state_index')
        if not isinstance(k, int) or not 0 <= k < len(index['joint'][nid].get('states', [])):
            issue('invalid_context_state_reference', nid, context_index=pos, joint_state_index=k)
        reused = row.get('reused_context')
        if reused is not None and (not isinstance(reused, int) or not 0 <= reused < pos):
            issue('invalid_context_reuse_reference', nid, context_index=pos, reused_context=reused)

    for pos, pair in enumerate(context.get('pairs', [])):
        for key in ('input_node', 'output_node'):
            if pair.get(key) not in all_ids:
                issue('unknown_paired_node', pair.get(key), pair_index=pos, role=key)

    nodes = {nid: {stage: index[stage][nid] for stage in NODE_STAGES if nid in index[stage]}
             for nid in sorted(all_ids)}
    # All original document fields remain present exactly once in the merged JSON:
    # node rows under nodes[nid][stage], all other fields under stages[stage].
    stages = {}
    for stage in STAGES:
        d = docs[stage]
        header = {k: v for k, v in d.items() if k != 'nodes'}
        stages[stage] = {'header': header}
        if stage in NODE_STAGES:
            stages[stage]['node_order'] = [row['bl_idname'] for row in d['nodes']]

    return {
        'schema': SCHEMA,
        'blender_version': next(iter(versions.values())),
        'node_count': len(nodes),
        'nodes': nodes,
        'stages': stages,
        'cross_checks': {'issue_count': len(issues), 'issues': issues,
                         'codes': dict(Counter(i['code'] for i in issues))},
        'source_files': file_info or {},
        'references': {
            'joint.states[*].rna_state_index': 'nodes[nid].rna.states (same node)',
            'connections.states[*].state_index': 'nodes[nid].joint.states (same node)',
            'final.states[*].state_index': 'nodes[nid].joint.states (same node)',
            'context.link_contexts[*].state_index': 'nodes[subject].joint.states',
            'context.link_contexts[*].reused_context': 'stages.context.header.link_contexts',
            'socket_state_id': 'scope-specific socket_states table inside the originating stage/node',
        },
    }


def restore_stage(merged, stage):
    """Losslessness test helper: rebuild the original JSON object for a stage."""
    record = merged['stages'][stage]
    doc = dict(record['header'])
    if stage in NODE_STAGES:
        doc['nodes'] = [merged['nodes'][nid][stage] for nid in record['node_order']]
    return doc


def write_json(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + '.partial')
    try:
        with tmp.open('w', encoding='utf-8') as fp:
            json.dump(obj, fp, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
        os.replace(tmp, path)
    finally:
        if tmp.exists():
            tmp.unlink()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--input', type=Path, required=True, help='Directory containing seven raw stage JSON files')
    ap.add_argument('--output', type=Path, required=True, help='Merged JSON output')
    args = ap.parse_args()
    folder = args.input.resolve()
    docs, file_info = load_sources(folder)
    merged = merge_documents(docs, file_info)
    # Verify in memory that every input object can be reproduced from combined output.
    for stage in STAGES:
        if restore_stage(merged, stage) != docs[stage]:
            raise RuntimeError(f'Merge lost source data in stage {stage}')
    write_json(args.output.resolve(), merged)
    print('Merged Blender', merged['blender_version'], '| nodes', merged['node_count'],
          '| cross-check issues', merged['cross_checks']['issue_count'], '| output', args.output)


if __name__ == '__main__':
    main()
