"""Common Blender RNA and socket serialization helpers (Blender 5.2+).

No node inventory, class names, Blender version or Blender source paths are required.
The public inspection pipeline starts in inspect.py, not here.
No Blender version or node IDs are hardcoded.
"""
import bpy
import json
import math

# Common per-Node UI and editor-state props are not part of node-specific
# functional configuration. This set is derived dynamically from runtime RNA.
base_props = set()
for name in ('Node', 'NodeInternal'):
    kind = getattr(bpy.types, name, None)
    if kind is not None:
        base_props.update(p.identifier for p in kind.bl_rna.properties)


def encode(value, depth=0):
    if depth > 8:
        return {'__truncated__': 'depth_limit'}
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, float):
        if not math.isfinite(value):
            return {'__float__': str(value)}
        return value
    if isinstance(value, (set, frozenset)):
        return sorted([encode(v, depth + 1) for v in value], key=str)
    if isinstance(value, (list, tuple)):
        return [encode(v, depth + 1) for v in value[:64]]
    # bpy_struct pointers must not be represented with volatile 0x addresses.
    if hasattr(value, 'bl_rna'):
        try:
            return {'__rna_pointer__': value.bl_rna.identifier, 'name': getattr(value, 'name', None)}
        except Exception:
            return {'__rna_pointer__': type(value).__name__}
    try:
        return [encode(v, depth + 1) for v in list(value)[:64]]
    except Exception:
        return {'__python_type__': type(value).__name__}


def sock(socket, index):
    r = {
        'index': index, 'name': socket.name, 'identifier': socket.identifier,
        'bl_idname': socket.bl_idname, 'data_type': socket.type,
        'is_multi_input': bool(socket.is_multi_input),
        'is_inactive': bool(socket.is_inactive),
        'is_unavailable': bool(socket.is_unavailable),
        'enabled': bool(socket.enabled),
        'hide_value': bool(socket.hide_value),
        'has_default_value': hasattr(socket, 'default_value'),
    }
    if r['has_default_value']:
        try:
            r['default_value'] = encode(socket.default_value)
        except Exception as ex:
            r['default_value_error'] = type(ex).__name__ + ': ' + str(ex)[:180]
    return r


def sockets(node):
    return {'inputs': [sock(s, i) for i, s in enumerate(node.inputs)],
            'outputs': [sock(s, i) for i, s in enumerate(node.outputs)]}


def schema_key(s):
    # Structural signature deliberately excludes default values and labels.
    return tuple((side, sck['index'], sck['identifier'], sck['bl_idname'],
                  sck['is_unavailable'], sck['is_inactive'], sck['is_multi_input'])
                 for side in ('inputs', 'outputs') for sck in s[side])


def enum_options(prop):
    try:
        return [{'identifier': x.identifier, 'name': x.name, 'description': x.description}
                for x in prop.enum_items if x.identifier]
    except Exception:
        return []


def describe_function(fn):
    return {
        'name': fn.identifier,
        'description': getattr(fn, 'description', ''),
        'parameters': [
            {'name': p.identifier, 'kind': p.type,
             'required': bool(getattr(p, 'is_required', False)),
             'is_output': bool(getattr(p, 'is_output', False)),
             **({'enum_items': enum_options(p)} if p.type == 'ENUM' else {}),
             **({'pointer_type': p.fixed_type.identifier if p.fixed_type else None}
                if p.type == 'POINTER' else {})}
            for p in fn.parameters
        ]
    }


def describe_collection(coll):
    functions = []
    if hasattr(coll, 'bl_rna'):
        try:
            for fn in coll.bl_rna.functions:
                if callable(getattr(coll, fn.identifier, None)):
                    functions.append(describe_function(fn))
        except Exception:
            pass
    item_info = []
    try:
        for item in list(coll)[:3]:
            item_info.append({
                'rna_type': item.bl_rna.identifier if hasattr(item, 'bl_rna') else type(item).__name__,
                'attributes': [
                    {'name': p.identifier, 'kind': p.type, 'readonly': bool(p.is_readonly),
                     'value': encode(getattr(item, p.identifier, None))}
                    for p in item.bl_rna.properties
                    if p.identifier not in {'rna_type'}
                ] if hasattr(item, 'bl_rna') else []
            })
    except Exception as ex:
        item_info.append({'error': type(ex).__name__ + ': ' + str(ex)[:160]})
    try:
        size = len(coll)
    except Exception:
        size = None
    return {'initial_length': size, 'functions': sorted(functions, key=lambda f: f['name']),
            'example_items': item_info}


def describe_property(node, prop):
    result = {'name': prop.identifier, 'kind': prop.type,
              'readonly': bool(prop.is_readonly),
              'description': getattr(prop, 'description', ''),
              'is_hidden': bool(getattr(prop, 'is_hidden', False)),
              'is_enum_flag': bool(getattr(prop, 'is_enum_flag', False))}
    try:
        val = getattr(node, prop.identifier)
        result['default'] = encode(val)
        if prop.type == 'COLLECTION':
            result['collection_api'] = describe_collection(val)
    except Exception as ex:
        result['read_error'] = type(ex).__name__ + ': ' + str(ex)[:160]
    if prop.type == 'ENUM':
        result['declared_options'] = enum_options(prop)
    elif prop.type == 'POINTER':
        try:
            result['pointer_type'] = prop.fixed_type.identifier if prop.fixed_type else None
        except Exception:
            pass
    for name in ('hard_min', 'hard_max', 'soft_min', 'soft_max', 'subtype'):
        try:
            if prop.type in ('INT', 'FLOAT') and name != 'subtype' or name == 'subtype':
                val = getattr(prop, name, None)
                if val is not None:
                    result[name] = encode(val)
        except Exception:
            pass
    return result


