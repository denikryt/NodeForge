"""Generate exactly one Extension API v2 function per Blender node from Inspector JSON.

The finite RNA/Menu state table is copied from Blender Inspector; it is not
inferred from names or hard-coded mode lists. Unsupported physical sockets
are recorded as unrepresentable rather than silently retyped. This prototype
keeps dynamic item sockets positional and ordinary inputs keyword-only.
"""
import argparse
import json
import keyword
import re
from pathlib import Path

CANONICAL = {
    'NodeSocketFloat':'Float', 'NodeSocketInt':'Int', 'NodeSocketBool':'Bool',
    'NodeSocketVector':'Vector', 'NodeSocketColor':'Color', 'NodeSocketRotation':'Rotation',
    'NodeSocketGeometry':'Geometry', 'NodeSocketString':'String',
    'NodeSocketMaterial':'Material', 'NodeSocketObject':'Object',
}
BLOCKED = {'NodeSocketVector2D', 'NodeSocketVector4D', 'NodeSocketVectorXYZ2D',
           'NodeSocketIntVector3D', 'NodeSocketMenu','NodeSocketVirtual',
           'NodeSocketMatrix','NodeSocketCollection','NodeSocketImage','NodeSocketFont',
           'NodeSocketSound','NodeSocketClosure','NodeSocketBundle'}

class ResultSchemaError(ValueError):
    """A supported configuration cannot express an unambiguous result contract."""


def marker(bl):
    if bl in BLOCKED:
        return None
    for prefix, v in sorted(CANONICAL.items(), key=lambda pair:-len(pair[0])):
        if bl.startswith(prefix):return v
    return None

def good_socket(s):
    return not (s.get('is_unavailable') or s.get('is_inactive') or not s.get('enabled',True) or s['bl_idname']=='NodeSocketVirtual')

def sanitized(raw):
    v=re.sub('[^a-z0-9_]+','_',raw.lower()).strip('_')
    if not v or v[0].isdigit() or keyword.iskeyword(v):v='option_'+v
    return v

def valid_output_text(value, *, allow_empty=False):
    """Physical socket metadata must be an exact, UTF-8 encodable string."""
    if type(value) is not str or (not allow_empty and not value) or '\0' in value:
        return False
    try:
        value.encode('utf-8')
    except UnicodeError:
        return False
    return True


def validate_output_types(node_id, state, outputs):
    """Require physical type metadata before filtering unsupported output types."""
    if any(not valid_output_text(sock.get('bl_idname')) for sock in outputs):
        raise ResultSchemaError(f"{node_id} state {state}: invalid output bl_idname; "
                                f"physical sockets {outputs!r}")


def validate_output_schema(node_id, state, outputs):
    """Require exact, unambiguous physical identities and labels on supported outputs."""
    identifiers = set()
    for sock in outputs:
        ident = sock.get('identifier')
        label = sock.get('name')
        if not valid_output_text(ident) or ident in identifiers:
            raise ResultSchemaError(f"{node_id} state {state}: invalid or duplicate output identifier {ident!r}; "
                                    f"physical sockets {outputs!r}")
        if not valid_output_text(label, allow_empty=True):
            raise ResultSchemaError(f"{node_id} state {state}: invalid output display label {label!r}; "
                                    f"physical sockets {outputs!r}")
        identifiers.add(ident)


def detect_dynamic(node_id, node):
    collections=[c for st in node['joint']['states'] if st['status']=='applied' for c in st['collections']]
    names={c['name'] for c in collections}
    recognized={'index_switch_items':'index','capture_items':'capture','enum_items':'menu'}
    selected=[(name,kind) for name,kind in recognized.items() if name in names]
    if not selected:return None
    if len(selected)>1:raise ValueError('Multiple dynamic collections need an explicit recipe')
    name, kind=selected[0]
    desc=next(c for c in collections if c['name']==name)
    types={}
    if kind=='capture':
        # Derive enum token -> NFType using the observed physical socket created
        # by that enum token, not by guessing the token's meaning.
        def identity(sock):
            """Compare probe sockets without requiring labels for unsupported types."""
            ident = sock.get('identifier')
            label = sock.get('name')
            return (ident if type(ident) is str else None,
                    label if type(label) is str else None, sock['bl_idname'])
        for trial in desc['attempts']:
            if trial.get('status')!='accepted':continue
            before=node['joint']['socket_states'][trial['before']]['outputs']
            after=node['joint']['socket_states'][trial['after']]['outputs']
            for state, sockets in ((trial['before'], before), (trial['after'], after)):
                validate_output_types(node_id, state, sockets)
                validate_output_schema(node_id, state, [s for s in sockets if marker(s['bl_idname']) is not None])
            before_ids={identity(s) for s in before}
            extras=[s for s in after if identity(s) not in before_ids and s['bl_idname']!='NodeSocketVirtual']
            if len(extras)==1 and marker(extras[0]['bl_idname']):
                types[trial['args'][0]]=marker(extras[0]['bl_idname'])
    initial_names=[]
    if kind=='menu':
        st=next(st for st in node['joint']['states'] if st['status']=='applied' and st['collections'] and any(c['name']==name for c in st['collections']))
        snapshot=node['joint']['socket_states'][st['socket_state_id']]
        initial_names=[so['name'] for so in snapshot['inputs'] if so['identifier'].startswith('Item_') and so['bl_idname']!='NodeSocketVirtual']
    return {'name':name,'kind':kind,'initial_count':desc['length'],'initial_names':initial_names,'types':types}


def prepare_node(node_id, node):
    states=[s for s in node['joint']['states'] if s['status']=='applied']
    if not states:raise ValueError('No applied states')
    dynamic=detect_dynamic(node_id,node)
    valid=[]; unsupported=[]
    for st in states:
        layout=node['joint']['socket_states'][st['socket_state_id']]
        # Inactive is not unsupported: e.g. Index Switch's Item_1 or Menu Switch's B
        # can still hold a constant. Only permanently disabled/unavailable sockets are excluded.
        select=lambda sock: (not sock.get('is_unavailable') and sock.get('enabled',True)
                             and (dynamic is not None or not sock.get('is_inactive')))
        inputs=[sock for sock in layout['inputs'] if select(sock) and sock['bl_idname'] not in ('NodeSocketVirtual','NodeSocketMenu')]
        outputs=[sock for sock in layout['outputs'] if select(sock)]
        validate_output_types(node_id, node['joint']['states'].index(st), outputs)
        outputs=[sock for sock in outputs if sock['bl_idname']!='NodeSocketVirtual']
        bad=[{'side':side,'identifier':sock.get('identifier'),'bl_idname':sock['bl_idname']}
             for side,sockets in (('inputs',inputs),('outputs',outputs))
             for sock in sockets if marker(sock['bl_idname']) is None]
        if bad or not outputs:
            unsupported.append({'index':node['joint']['states'].index(st),'rna':st['rna'],
                                'menu':{m['identifier']:m['value'] for m in st['menus']},
                                'reason':'unsupported_socket_type' if bad else 'no_outputs','sockets':bad})
        else:
            validate_output_schema(node_id, node['joint']['states'].index(st), outputs)
            valid.append((st, inputs, outputs))
    if not valid:
        return {'node':node_id,'name':sanitized(node_id),'fields':[],'configurations':[],
                'unsupported':unsupported,'input_types':[],'total_states':len(states),'dynamic':dynamic}
    rna_keys=sorted({k for st,_,_ in valid for k in st['rna']})
    flag_fields = {prop['name'] for prop in node['inventory']['properties']
                   if prop.get('is_enum_flag')}
    def as_argument(key, value):
        if key in flag_fields and isinstance(value, (list, tuple, set)):
            return ','.join(sorted(value))
        return value
    menu_keys=sorted({m['identifier'] for st,_,_ in valid for m in st['menus']})
    if any(set(st['rna'])!=set(rna_keys) or {m['identifier'] for m in st['menus']}!=set(menu_keys) for st,_,_ in valid):
        raise ValueError('Option keys vary across otherwise representable states')
    fields=[{'kind':'rna','key':k,'param':sanitized(k),
             'marker':'Bool' if all(type(st['rna'][k]) is bool for st,_,_ in valid) else 'Int'
               if all(type(st['rna'][k]) is int for st,_,_ in valid) else 'String'} for k in rna_keys]
    fields += [{'kind':'menu','key':k,'param':sanitized(k),'marker':'String'} for k in menu_keys]
    if len({f['param'] for f in fields})!=len(fields):raise ValueError('Duplicate names')
    configurations=[];input_types=set(); seen=set()
    for st,inputs,outputs in valid:
        menus={m['identifier']:m['value'] for m in st['menus']}
        key=tuple(as_argument(k, st['rna'][k]) for k in rna_keys)+tuple(menus[k] for k in menu_keys)
        if key in seen:continue
        seen.add(key)
        def contract(sock):return {'socket':sock['identifier'],'by':'identifier',
                                   'type':marker(sock['bl_idname']),'physical':sock['bl_idname']}
        converted_inputs=list(map(contract,inputs))
        converted_outputs=[dict(contract(sock), name=sock['name']) for sock in outputs]
        input_types.update(a['type'] for a in converted_inputs)
        configurations.append({'key':list(key),'inspector_state':node['joint']['states'].index(st),
                               'rna':{k:as_argument(k,v) for k,v in st['rna'].items()},'menus':menus,
                               'rna_order':node['rna']['states'][st['rna_state_index']].get('used_assignment_order',rna_keys),
                               'inputs':converted_inputs,'outputs':converted_outputs})
    for f in fields:
        init=configurations[0]; f['default']=init['rna'][f['key']] if f['kind']=='rna' else init['menus'][f['key']]
    if dynamic:
        if dynamic['kind']=='capture':input_types.update(dynamic['types'].values())
        elif dynamic['kind'] in ('index','menu'):
            input_types.update(a['type'] for c in configurations for a in c['outputs'] if a['type']!='Bool')
        # Capture allows actual runtime typed values to be added to the node;
        # unsupported item types are not silently replaced with supported ones.
        dynamic['parameter']='elements'
    return {'node':node_id,'name':sanitized(node_id),'fields':fields,
            'configurations':configurations,'unsupported':unsupported,
            'input_types':sorted(input_types),'total_states':len(states),'dynamic':dynamic}

