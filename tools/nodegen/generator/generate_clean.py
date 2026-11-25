"""Generate compact, one-function-per-Blender-node Python Extension API v2 wrappers.

Uses the existing inspected configuration catalogue to identify *which* APIs and
result rules are needed, rather than copying the catalogue into executable code.
Original generation.json remains test input / audit evidence, not runtime code.
"""
from __future__ import annotations
import argparse
import itertools
import inspect
import re
import json
import sys
from pathlib import Path

from inspect_contract import (ResultSchemaError, UnsupportedNodeError, marker,
                              output_records, prepare_node, sanitized,
                              validate_output_identifiers, validate_output_types)


def capture_entries(elements, supported):
    """Parse capture collection entries, preserving exact item labels."""
    chunks = tuple(tuple(chunk.strip().split(':', 1)) for chunk in elements.split(',')) if elements else ()
    if any(len(pair) != 2 or pair[0] not in supported or not pair[1] for pair in chunks):
        raise ValueError('elements must be TYPE:Name entries with supported types')
    if len({pair[1] for pair in chunks}) != len(chunks):
        raise ValueError('Duplicate capture item names')
    return chunks


def menu_names(elements):
    """Parse ordered, unique Menu Switch labels."""
    names = tuple(chunk.strip() for chunk in elements.split(',')) if elements else ()
    if not all(names) or len(names) != len(set(names)):
        raise ValueError('elements must contain unique nonempty Menu names')
    return names


def result_signature(item, cfg):
    """Return the ordered base result schema, validating the complete observed layout."""
    outputs = cfg['outputs']
    dynamic = item['dynamic']
    collection_count = len(dynamic['initial_names']) if dynamic and dynamic['kind'] == 'menu' else 0
    base_count = len(outputs) - collection_count
    keys = tuple(sanitized(socket['name']) if index < base_count else socket['name']
                 for index, socket in enumerate(outputs))
    try:
        valid_keys = all(key and '\0' not in key and len(key.encode('utf-8')) <= 63 for key in keys)
    except UnicodeError:
        valid_keys = False
    if base_count <= 0 or len(keys) != len(set(keys)) or not valid_keys:
        raise ResultSchemaError(f"{item['node']} state {cfg['inspector_state']}: invalid output keys {keys!r}; "
                                f"physical sockets {outputs!r}")
    return tuple((key, socket['type'].upper()) for key, socket in zip(keys[:base_count], outputs))


def result_rule(item):
    """Find the smallest set of compile-time fields determining the ordered schema."""
    configs = item['configurations']
    signatures = [result_signature(item, cfg) for cfg in configs]
    variable = len(set(signatures)) > 1 or item['dynamic'] and item['dynamic']['kind'] in ('capture','menu','index')
    if not variable:
        return {'dynamic_result': False, 'static': signatures[0]}
    fields = item['fields']
    def values(c):
        return {f['param']: c['rna'][f['key']] if f['kind']=='rna' else c['menus'][f['key']] for f in fields}
    for size in range(len(fields)+1):
        for choice in itertools.combinations([f['param'] for f in fields], size):
            group = {}
            if all(group.setdefault(tuple(values(c)[f] for f in choice), signature) == signature
                   for c, signature in zip(configs, signatures)):
                return {'dynamic_result': True, 'deps': list(choice), 'map': group}
    raise ResultSchemaError(f"{item['node']}: Cannot resolve output schema for {configs!r}")


def literal(value):
    return repr(value)


def public_name(node_id, inspected):
    """Use Blender's UI label (e.g. 'Resample Curve' -> resample_curve)."""
    label = inspected['inventory'].get('label') or node_id
    return sanitized(label)


def named_inputs(item):
    """Stable keyword parameters for physical inputs present in any observed state.

    Dynamic item sockets are intentionally kept variadic. A fixed socket ID is
    independent of the active RNA/Menu configuration, whereas Item_* represents
    a variable-size collection rather than a stable public parameter.
    """
    dynamic = item['dynamic']['kind'] if item['dynamic'] else None
    observed = {}
    for config in item['configurations']:
        per_state = set()
        for socket in config['inputs']:
            ident = socket['socket']
            if dynamic in ('index', 'menu') and ident.startswith('Item_'):
                continue
            if ident in per_state:
                raise UnsupportedNodeError(f"Ambiguous duplicate input socket identifier: {item['node']} {ident!r}")
            per_state.add(ident)
            observed.setdefault(ident, set()).add(socket['type'])
    field_names = {f['param'] for f in item['fields']}
    occupied = field_names | {'context'}
    if dynamic:
        occupied.update(('elements', 'items'))
    result = []
    for ident, types in observed.items():
        stem = sanitized(ident)
        # A configuration property keeps its Blender name. Disambiguate a
        # same-named socket by its type (e.g. primary_axis_vector).
        if stem in field_names:
            suffix = next(iter(types)).lower() if len(types) == 1 else 'input'
            stem = f'{stem}_{suffix}'
        name = stem
        number = 2
        while name in occupied:
            name = f'{stem}_{number}'
            number += 1
        occupied.add(name)
        result.append({'socket': ident, 'param': name, 'types': sorted(types)})
    return result


def constant_parameters(item, inspected):
    """Expose editable constants for zero-input, single-output source nodes.

    Prefer a matching writable RNA ``value`` property (e.g. Input Color);
    otherwise use the output socket's default_value (e.g. Value).
    Other nodes' output defaults are *not* user-facing constant parameters.
    """
    # A zero-input field node (Position, ID, Normal, etc.) also exposes an
    # editable fallback value on its *output*. That is not a constant-source
    # setting. Restrict this path to Blender's actual constant-input families.
    if not (item['node'].startswith('FunctionNodeInput') or
            item['node'] in {'ShaderNodeValue', 'ShaderNodeRGB'}):
        return []
    if item['dynamic'] or any(cfg['inputs'] for cfg in item['configurations']):
        return []
    signatures = {tuple((sock['socket'], sock['type']) for sock in cfg['outputs'])
                  for cfg in item['configurations']}
    if len(signatures) != 1:
        return []
    signature, = signatures
    if len(signature) != 1:
        return []
    ident, typ = signature[0]
    node = item['node']
    baseline = output_records(node, 'baseline', inspected['inventory'].get('baseline'))
    validate_output_types(node, 'baseline', baseline)
    supported = [sock for sock in baseline if marker(sock['bl_idname']) is not None]
    validate_output_identifiers(node, 'baseline', supported)
    matches = [sock for sock in supported if sock['identifier'] == ident]
    if len(matches) != 1:
        raise ResultSchemaError(f'{node} state baseline: missing required output identifier {ident!r}; '
                                f'physical sockets {baseline!r}')
    has_default = matches[0].get('has_default_value')
    if type(has_default) is not bool:
        raise ResultSchemaError(f'{node} state baseline: invalid has_default_value for output {ident!r}; '
                                f'physical sockets {baseline!r}')
    if not has_default:
        return []
    properties = inspected['inventory']['properties']
    # Blender's FunctionNodeInput* constant sources store their effective
    # value in RNA properties, not on the output socket. Setting the output
    # default alone can appear to work but leave the actual constant unchanged.
    kind_map = {
        'Color': ('FLOAT', 'COLOR'), 'Vector': ('FLOAT', 'XYZ'),
        'Rotation': ('FLOAT', 'EULER'), 'Float': ('FLOAT', None),
        'Int': ('INT', None), 'Bool': ('BOOLEAN', None),
        'String': ('STRING', None),
    }
    expected = kind_map.get(typ)
    if expected is not None:
        stem = sanitized(ident)
        candidates = [p for p in properties if not p.get('readonly')
                      and p['kind'] == expected[0]
                      and (expected[1] is None or p.get('subtype') == expected[1])
                      and p['name'] in (stem, 'value', stem + '_euler')]
        if len(candidates) == 1:
            prop = candidates[0]
            return [{'param': prop['name'] if prop['name']=='value' else stem,
                     'type': typ, 'target': 'rna', 'key': prop['name']}]
    # A writable socket default is not necessarily a constant source. E.g.,
    # contextual Geometry Nodes inputs can have fallback output defaults.
    # Require Inspector's independent UI-accessibility/assignment probe.
    if not any(sock.get('side') == 'outputs' and sock.get('identifier') == ident
               and sock.get('ui_accessible') and sock.get('status') == 'accepted'
               for state in inspected.get('final', {}).get('states', [])
               for sock in state.get('defaults', {}).get('sockets', [])):
        return []
    return [{'param': sanitized(ident), 'type': typ, 'target': 'output', 'key': ident}]


def render_interface(items):
    types = sorted({t for i in items for inp in i['named_inputs'] for t in inp['types']}
                   | {t for i in items if i['dynamic'] for t in i['input_types']}
                   | {param['type'] for i in items for param in i['constants']})
    types += sorted({t.title() if t != 'BOOL' else 'Bool' for i in items
                     if not i['result_rule']['dynamic_result'] for _, t in i['result_rule']['static']})
    imports = sorted(set(['EvaluationMode', 'Bool', 'Int', 'String'] + types +
                         (['DynamicResults'] if any(i['result_rule']['dynamic_result'] for i in items) else [])))
    lines = ['"""One wrapper per Blender node. Fixed sockets are named and optional."""',
             'from typing import Annotated', f'from NodeForge import {", ".join(imports)}',
             'EXTENSION_API = 2',
             'EXTENSIONS = ' + repr({i['name']: '.backend:' + i['name'] for i in items}), '']
    for i in items:
        args = []
        if i['dynamic']:
            input_types = ' | '.join(i['input_types']) if i['input_types'] else 'Float'
            args.append(f'*items: Annotated[{input_types}, EvaluationMode.COMPILE_TIME_OR_RUNTIME]')
        elif i['named_inputs'] or i['fields'] or i['constants']:
            args.append('*')
        for inp in i['named_inputs']:
            annotation = ' | '.join(inp['types'])
            args.append(f"{inp['param']}: Annotated[{annotation}, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = None")
        for c in i['constants']:
            args.append(f"{c['param']}: Annotated[{c['type']}, EvaluationMode.COMPILE_TIME_ONLY] = None")
        for f in i['fields']:
            args.append(f"{f['param']}: Annotated[{f['marker']}, EvaluationMode.COMPILE_TIME_ONLY] = {f['default']!r}")
        if i['dynamic']:
            d = i['dynamic']; k = d['kind']; kind = 'Int' if k == 'index' else 'String'
            default = d['initial_count'] if k == 'index' else ','.join(d['initial_names']) if k == 'menu' else ''
            args.append(f"elements: Annotated[{kind}, EvaluationMode.COMPILE_TIME_ONLY] = {default!r}")
        sig = i['result_rule']
        if sig['dynamic_result']:
            return_type = 'DynamicResults'
        else:
            names = {'BOOL': 'Bool', 'INT': 'Int', 'FLOAT': 'Float', 'GEOMETRY': 'Geometry',
                     'VECTOR': 'Vector', 'COLOR': 'Color', 'ROTATION': 'Rotation',
                     'OBJECT': 'Object', 'MATERIAL': 'Material', 'STRING': 'String'}
            return_type = '{' + ', '.join(repr(key) + ': ' + names[t] for key, t in sig['static']) + '}'
        lines.append(f"def {i['name']}(")
        lines.extend(f'    {argument},' for argument in args)
        lines += [f') -> {return_type}:',
                  f'    """Build a {i["node"]}; omitted inputs keep Blender defaults."""',
                  '    ...', '']
    return '\n'.join(lines) + '\n'


BACKEND_HEADER = '''"""Physical Blender operations shared by the generated wrappers.

No Inspector state tables are needed at runtime. Blender is the authority for
RNA properties, Menu sockets, and the resulting physical socket layout.
"""
import keyword
import re
from NodeForge.extension_api import ExtensionBackendValue, NFType

'''

BACKEND_HELPERS = '''_TYPES = {
    'NodeSocketFloat': NFType.FLOAT, 'NodeSocketInt': NFType.INT,
    'NodeSocketBool': NFType.BOOL, 'NodeSocketVector': NFType.VECTOR,
    'NodeSocketColor': NFType.COLOR, 'NodeSocketRotation': NFType.ROTATION,
    'NodeSocketGeometry': NFType.GEOMETRY, 'NodeSocketString': NFType.STRING,
    'NodeSocketObject': NFType.OBJECT, 'NodeSocketMaterial': NFType.MATERIAL,
}
_EXCLUDED = {'NodeSocketIntVector3D', 'NodeSocketVector2D', 'NodeSocketVector4D',
             'NodeSocketVectorXYZ2D', 'NodeSocketBundle', 'NodeSocketMenu',
             'NodeSocketVirtual', 'NodeSocketMatrix', 'NodeSocketClosure',
             'NodeSocketImage', 'NodeSocketCollection', 'NodeSocketFont', 'NodeSocketSound'}


def socket_type(socket):
    name = socket.bl_idname
    if name in _EXCLUDED:
        return None
    return next((typ for prefix, typ in _TYPES.items() if name.startswith(prefix)), None)


def active_sockets(sockets, *, dynamic=False, menu=False):
    return [sock for sock in sockets
            if sock.bl_idname != 'NodeSocketVirtual'
            and (not menu or sock.bl_idname != 'NodeSocketMenu')
            and sock.enabled and not getattr(sock, 'is_unavailable', False)
            and (dynamic or not getattr(sock, 'is_inactive', False))]


def make_node(context, node_id, properties, orders):
    # Test the small set of observed RNA assignment orders on fresh nodes.
    # Changing an earlier property can invalidate later enum assignments.
    fallback = None
    last_error = None
    for order in (orders or ((),)):
        node = context.group.nodes.new(node_id)
        node.location = context.location
        try:
            for name in order:
                value = properties[name]
                if node.bl_rna.properties[name].is_enum_flag:
                    value = set(filter(None, value.split(',')))
                setattr(node, name, value)
        except (TypeError, ValueError) as exc:
            last_error = exc
            context.group.nodes.remove(node)
            continue
        if all(getattr(node, name) == (set(filter(None, value.split(',')))
                if node.bl_rna.properties[name].is_enum_flag else value)
               for name, value in properties.items()):
            if fallback is not None:
                context.group.nodes.remove(fallback)
            return node
        # Blender can silently normalize an impossible RNA combination.
        # Preserve it for independent comparison against the Inspector state.
        if fallback is not None:
            context.group.nodes.remove(fallback)
        fallback = node
    if fallback is not None:
        return fallback
    raise ValueError(f'Blender rejected RNA configuration {properties!r}: {last_error}')


def set_collection(node, kind, elements, capture_types):
    if kind == 'index':
        if type(elements) is not int or elements < 0:
            raise ValueError('elements must be a nonnegative integer')
        items = node.index_switch_items
        while len(items) > elements: items.remove(items[-1])
        while len(items) < elements: items.new()
    elif kind == 'menu':
        names = menu_names(elements)
        if tuple(x.name for x in node.enum_items) != names:
            node.enum_items.clear()
            for name in names: node.enum_items.new(name)
        return names
    elif kind == 'capture':
        entries = capture_entries(elements, capture_types)
        for token, name in entries:
            node.capture_items.new(token, name)
        return tuple(name for _, name in entries)
    return ()


def deliver(context, socket, value):
    typ = socket_type(socket)
    if typ is None:
        raise TypeError(f'Unsupported Blender input type: {socket.bl_idname}')
    if isinstance(value, ExtensionBackendValue):
        if value.typ is not typ:
            raise TypeError(f'{socket.identifier} expects {typ}, got {value.typ}')
        context.group.links.new(value.socket, socket)
    else:
        socket.default_value = value


def materialize(context, node_id, named, items=(), rna=None, menus=(), orders=(),
                kind=None, elements=None, capture_types=None,
                constant_rna=None, constant_outputs=None):
    node = make_node(context, node_id, rna or {}, orders)
    requested_names = set_collection(node, kind, elements, capture_types) if kind else ()
    for key, value in menus:
        candidates = [sock for sock in node.inputs if sock.identifier == key]
        if len(candidates) != 1:
            raise ValueError(f'Missing or ambiguous Menu socket: {key!r}')
        candidates[0].default_value = value
        if candidates[0].default_value != value:
            raise ValueError(f'Blender rejected Menu value {value!r}')
    available = active_sockets(node.inputs, dynamic=bool(kind), menu=True)
    # An explicitly supplied named input must exist in the selected configuration.
    for key, value in named.items():
        if value is None:
            continue
        matches = [sock for sock in available if sock.identifier == key]
        if len(matches) != 1:
            raise ValueError(f'{node_id}: input {key!r} is unavailable or ambiguous in this configuration')
        deliver(context, matches[0], value)
    # Only collection-created sockets remain positional.
    variable_inputs = [sock for sock in available if sock.identifier not in named]
    if len(items) > len(variable_inputs):
        raise ValueError(f'{node_id} has {len(variable_inputs)} variable inputs; got {len(items)}')
    for socket, value in zip(variable_inputs, items):
        deliver(context, socket, value)
    # A missing optional constant is intentionally not assigned: Blender owns
    # its defaults. These are compile-time-only knobs, never runtime links.
    for key, value in (constant_rna or {}).items():
        if value is not None:
            setattr(node, key, value)
    for key, value in (constant_outputs or {}).items():
        if value is None:
            continue
        matches = [sock for sock in node.outputs if sock.identifier == key]
        if len(matches) != 1 or not hasattr(matches[0], 'default_value'):
            raise ValueError(f'{node_id}: missing or ambiguous writable output {key!r}')
        matches[0].default_value = value
    outputs = active_sockets(node.outputs, dynamic=bool(kind))
    if len(outputs) != len(context.result_types) or len(requested_names) > len(outputs):
        raise ValueError(f'{node_id}: unexpected output count; '
                         f'physical identifiers {[socket.identifier for socket in outputs]!r}')
    base_count = len(outputs) - len(requested_names)
    observed_names = tuple(sanitized(socket.name) if index < base_count else socket.name
                           for index, socket in enumerate(outputs))
    if tuple(observed_names[base_count:]) != requested_names:
        raise ValueError(f'{node_id}: collection output labels differ from {requested_names!r}')
    observed = tuple(zip(observed_names, (socket_type(socket) for socket in outputs)))
    expected = tuple(zip(context.result_names, context.result_types))
    if observed != expected:
        raise ValueError(f'{node_id}: output schema {observed!r} does not match {expected!r}; '
                         f'physical identifiers {[socket.identifier for socket in outputs]!r}')
    return {key: context.value(socket, typ) for (key, typ), socket in zip(observed, outputs)}

'''


def render_backend(items):
    lines = [BACKEND_HEADER, inspect.getsource(sanitized), inspect.getsource(capture_entries), inspect.getsource(menu_names), BACKEND_HELPERS]
    for i in items:
        fields = [f for f in i['fields'] if f['kind'] == 'rna']
        menus = [f for f in i['fields'] if f['kind'] == 'menu']
        orders = []
        for c in i['configurations']:
            order = tuple(c['rna_order'])
            if order not in orders:
                orders.append(order)
        params = ['context']
        if i['dynamic']:
            params.append('*items')
        elif i['named_inputs'] or i['fields'] or i['constants']:
            params.append('*')
        params += [f"{inp['param']}=None" for inp in i['named_inputs']]
        params += [f"{c['param']}=None" for c in i['constants']]
        params += [f"{f['param']}={f['default']!r}" for f in i['fields']]
        kwargs = []
        if i['dynamic']:
            d = i['dynamic']; k = d['kind']
            default = d['initial_count'] if k == 'index' else ','.join(d['initial_names']) if k == 'menu' else ''
            params += [f'elements={default!r}']
            kwargs.extend([f'kind={k!r}', 'elements=elements'])
            if k == 'capture':
                kwargs.append(f"capture_types={tuple(sorted(d['types']))!r}")
        props = '{' + ', '.join(repr(f['key']) + ': ' + f['param'] for f in fields) + '}'
        menu = ('(' + ', '.join('(' + repr(f['key']) + ', ' + f['param'] + ')' for f in menus)
                + (',' if len(menus) == 1 else '') + ')')
        if not menus:
            menu = '()'
        named = '{' + ', '.join(repr(inp['socket']) + ': ' + inp['param'] for inp in i['named_inputs']) + '}'
        constant_rna = '{' + ', '.join(repr(c['key']) + ': ' + c['param'] for c in i['constants'] if c['target']=='rna') + '}'
        constant_outputs = '{' + ', '.join(repr(c['key']) + ': ' + c['param'] for c in i['constants'] if c['target']=='output') + '}'
        lines.append(f"\ndef {i['name']}(")
        lines.extend(f'    {param},' for param in params)
        lines.extend(['):', '    return materialize('])
        expression_parts = ['context', repr(i['node']), f'named={named}',
                            'items=items' if i['dynamic'] else 'items=()',
                            f'rna={props}', f'menus={menu}', f'orders={orders!r}',
                            f'constant_rna={constant_rna}', f'constant_outputs={constant_outputs}'] + kwargs
        lines.extend(f'        {part},' for part in expression_parts)
        lines.append('    )')
    return '\n'.join(lines) + '\n'


SEMANTIC_HEADER='''"""Only invocation-dependent output schemas require semantic resolvers."""
from NodeForge.nf_types import NFType
from NodeForge.extension_result_api import ResultStaticArg, validate_named_result_key


'''

SEMANTIC_HELPERS = '''def static_value(arguments, param):
    value = arguments[param]
    if not isinstance(value, ResultStaticArg):
        raise TypeError(f'{param} must be compile-time')
    return value.value


def output_types(arguments, parameters, signatures):
    key = tuple(static_value(arguments, name) for name in parameters)
    try:
        return signatures[key]
    except KeyError:
        raise ValueError(f'Unsupported result configuration: {key!r}') from None

def with_collection_results(base, entries):
    """Append explicit collection keys without normalizing or overwriting them."""
    output = dict(base)
    for key, typ in entries:
        validate_named_result_key(key)
        if key in output:
            raise ValueError(f'Duplicate output key {key!r}')
        output[key] = typ
    return output


'''


def render_semantic(items):
    dynamic=[i for i in items if i['result_rule']['dynamic_result']]
    if not dynamic: return ''
    lines = [SEMANTIC_HEADER, inspect.getsource(capture_entries), inspect.getsource(menu_names), SEMANTIC_HELPERS]
    for i in dynamic:
        rule=i['result_rule']; mapping=rule['map']; kind=i['dynamic']['kind'] if i['dynamic'] else None
        lines.append(f"_SIGNATURE_{i['name']} = {{")
        for key,v in sorted(mapping.items(), key=lambda entry:repr(entry[0])):
            lines.append(f"    {key!r}: {{" + ", ".join(repr(name) + ": NFType." + typ for name, typ in v) + "},")
        lines.append('}')
        lines.append(f'\ndef _result_types_{i["name"]}(arguments):')
        lines.append(f"    base = output_types(arguments, {rule['deps']!r}, _SIGNATURE_{i['name']})")
        if kind=='capture':
            typemap={k:v.upper() for k,v in i['dynamic']['types'].items()}
            lines.extend([f"    types = {{{', '.join(repr(k)+': NFType.'+v for k,v in sorted(typemap.items()))}}}",
                          "    entries = capture_entries(static_value(arguments, 'elements'), types)",
                          "    return with_collection_results(base, ((name, types[token]) for token, name in entries))"])
        elif kind=='menu':
            lines.extend(["    names = menu_names(static_value(arguments, 'elements'))",
                          f"    selected = static_value(arguments, {next(f['param'] for f in i['fields'] if f['kind'] == 'menu' and f['key'] == 'Menu')!r})",
                          "    if selected not in names:",
                          "        raise ValueError('Invalid Menu selection / elements')",
                          "    return with_collection_results(base, ((name, NFType.BOOL) for name in names))"])
        elif kind=='index':
            lines.extend(["    count = static_value(arguments, 'elements')",
                          "    if type(count) is not int or count < 0:",
                          "        raise ValueError('elements must be nonnegative')",
                          '    return base'])
        else:
            lines.append('    return base')
        lines.append('')
    return '\n'.join(lines)+'\n'


def prefer_blender_defaults(prepared, inspected):
    """Use bpy baseline property/Menu values when those form a supported state."""
    props = {prop['name']: prop.get('default') for prop in inspected['inventory']['properties']}
    menus = {sock['identifier']: sock.get('default_value')
             for sock in inspected['inventory']['baseline']['inputs']
             if sock['bl_idname'] == 'NodeSocketMenu'}
    old = [field['default'] for field in prepared['fields']]
    for field in prepared['fields']:
        options = props if field['kind'] == 'rna' else menus
        if field['key'] in options and options[field['key']] is not None:
            value = options[field['key']]
            if isinstance(value, list):
                value = ','.join(sorted(value))
            field['default'] = value
    expected = {f['param']: f['default'] for f in prepared['fields']}
    def match(cfg):
        return all((cfg['rna'] if f['kind'] == 'rna' else cfg['menus'])[f['key']] == expected[f['param']]
                   for f in prepared['fields'])
    if not any(match(cfg) for cfg in prepared['configurations']):
        for field, original in zip(prepared['fields'], old):
            field['default'] = original


def generate(spec, requested, out):
    prepared=[];skipped=[]
    for name in requested:
        try:
            p=prepare_node(name,spec['nodes'][name])
            if not p['configurations']:
                skipped.append({'node':name,'reason':'no representable configurations'})
                continue
            p['name'] = public_name(name, spec['nodes'][name])
            p['result_rule']=result_rule(p)
            p['named_inputs']=named_inputs(p)
            p['constants']=constant_parameters(p, spec['nodes'][name])
            occupied={inp['param'] for inp in p['named_inputs']} | {f['param'] for f in p['fields']} | {'elements'}
            for constant in p['constants']:
                candidate=constant['param']; suffix=2
                while candidate in occupied:
                    candidate=f"{constant['param']}_{suffix}"; suffix+=1
                constant['param']=candidate
                occupied.add(candidate)
            prefer_blender_defaults(p, spec['nodes'][name])
            prepared.append(p)
        except UnsupportedNodeError as e:
            skipped.append({'node':name,'reason':str(e)})
    # Blender UI labels are unique for this sample; explicitly reject ambiguous APIs.
    name_to_ids = {}
    for item in prepared:
        name_to_ids.setdefault(item['name'], []).append(item['node'])
    collisions = {name: ids for name, ids in name_to_ids.items() if len(ids) > 1}
    if collisions:
        raise ValueError(f'Duplicate public function names: {collisions}')
    output = Path(out)
    output.mkdir(parents=True, exist_ok=True)
    for filename, content in [('interface.py', render_interface(prepared)),
                              ('backend.py', render_backend(prepared)),
                              ('semantic.py', render_semantic(prepared))]:
        (output / filename).write_text(content, encoding='utf-8')
    # Full inspected states stay in the returned audit data; no per-state tables
    # or package metadata are generated into the runtime library.
    report = {
        'schema': 'nodeforge.blender_node_wrappers/1',
        'blender_version': spec['blender_version'],
        'wrappers': [{k: v for k, v in p.items() if k != 'result_rule'} for p in prepared],
        'skipped': skipped,
        'summary': {'wrappers': len(prepared),
                    'states': sum(len(p['configurations']) for p in prepared)},
    }
    return report


def main():
    """Generate the Python extension owner files from an Inspector catalogue."""
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--spec', required=True)
    ap.add_argument('--out', required=True, help='Output directory containing interface.py and backend.py')
    ap.add_argument('--nodes', nargs='+', required=True)
    ap.add_argument('--report', help='Optional path for the generation audit JSON')
    args = ap.parse_args()
    report = generate(json.loads(Path(args.spec).read_text()), args.nodes, args.out)
    if args.report:
        Path(args.report).write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n')
    print(report['summary'], 'skipped:', report['skipped'])


if __name__ == '__main__':
    main()
