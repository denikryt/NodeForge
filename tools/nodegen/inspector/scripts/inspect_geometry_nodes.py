"""Independent RNA inventory: runtime discovery and one-at-a-time property probes."""
import bpy
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent))
from blender_rna import (base_props, encode, describe_property, sockets, schema_key)

SCHEMA = 'nodeforge.blender_rna_inventory/1.0'

def candidate_values(prop):
    if prop.get('read_error') or prop['readonly']:
        return []
    if prop['kind'] == 'BOOLEAN':
        return [not prop.get('default', False)]
    if prop['kind'] != 'ENUM':
        return []
    choices = [i['identifier'] for i in prop.get('declared_options', [])]
    if prop['is_enum_flag']:
        original = set(prop['default']) if isinstance(prop.get('default'), list) else set()
        return [sorted(original.symmetric_difference({value})) for value in choices]
    return [x for x in choices if x != prop.get('default')]


def trial(tree, bl_idname, prop, value, baseline):
    n = None
    result = {'property': prop, 'requested': value}
    try:
        n = tree.nodes.new(bl_idname)
        setattr(n, prop, set(value) if isinstance(value, list) else value)
        actual = encode(getattr(n, prop))
        result['actual'] = actual
        result['status'] = 'applied' if actual == value else 'coerced_or_ignored'
        updated = sockets(n)
        changed = schema_key(updated) != schema_key(baseline)
        result['schema_changed'] = changed
        if changed:
            result['sockets'] = updated
    except Exception as ex:
        result['status'] = 'rejected'
        result['error'] = type(ex).__name__ + ': ' + str(ex)[:240]
    finally:
        if n is not None:
            tree.nodes.remove(n)
    return result


def classify_rna_family(c):
    return [base.__name__ for base in c.__mro__ if base.__name__ in
            ('GeometryNode', 'FunctionNode', 'ShaderNode', 'NodeInternal', 'Node')]


def run(args=None):
    arguments = (sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else []) if args is None else args
    if len(arguments) != 1:
        raise SystemExit('Usage: blender -b --python inspect_geometry_nodes.py -- output.json')
    path = Path(arguments[0]); path.parent.mkdir(parents=True, exist_ok=True)
    tree = bpy.data.node_groups.new('NodeForge API introspection (temporary)', 'GeometryNodeTree')
    results, errors = [], []
    candidates = []
    for name in dir(bpy.types):
        try:
            typ = getattr(bpy.types, name)
            if isinstance(typ, type) and typ is not bpy.types.Node and issubclass(typ, bpy.types.Node):
                candidates.append((name, typ))
        except Exception:
            continue
    for index, (name, typ) in enumerate(sorted(candidates)):
        instance = None
        try:
            instance = tree.nodes.new(name)
            record = {
                'bl_idname': name,
                'rna_class': typ.bl_rna.identifier,
                'rna_base': typ.bl_rna.base.identifier if typ.bl_rna.base else None,
                'family': classify_rna_family(typ),
                'label': instance.bl_label,
                'description': typ.bl_rna.description,
                'baseline': sockets(instance),
                'properties': [],
            }
            for prop in instance.bl_rna.properties:
                if prop.identifier not in base_props and prop.identifier != 'rna_type':
                    record['properties'].append(describe_property(instance, prop))
            tree.nodes.remove(instance); instance = None
            variants = []
            for prop in record['properties']:
                for val in candidate_values(prop):
                    variants.append(trial(tree, name, prop['name'], val, record['baseline']))
            record['variants'] = variants
            results.append(record)
        except Exception as ex:
            errors.append({'bl_idname': name, 'reason': type(ex).__name__ + ': ' + str(ex)[:200]})
        finally:
            if instance is not None:
                tree.nodes.remove(instance)
        if (index + 1) % 100 == 0:
            print('INSPECTED', index+1, '/', len(candidates), flush=True)
    doc = {
        'schema': SCHEMA,
        'blender': {'version': bpy.app.version_string, 'version_tuple': list(bpy.app.version),
                    'build_hash': bpy.app.build_hash.decode('utf-8', 'replace') if isinstance(bpy.app.build_hash, bytes) else str(bpy.app.build_hash)},
        'discovery': {'method': 'registered bpy.types.Node subclasses then nodes.new() in GeometryNodeTree',
                      'candidate_classes': len(candidates), 'created_classes': len(results),
                      'not_creatable': len(errors), 'rejected_classes': errors,
                      'common_RNA_properties_excluded': sorted(base_props)},
        'probe_semantics': {
            'states': ['baseline', 'each ENUM or BOOLEAN property varied independently'],
            'not_covered': ['interactions of RNA properties', 'arbitrary collection mutations',
                            'menu socket item choices', 'paired zone operations',
                            'context-dependent UI operators', 'asset pointers',
                            'numeric / geometry evaluation', 'external add-on registrations'],
            'enum_warning': 'RNA declared options can include candidates refused by concrete node instances'
        },
        'nodes': sorted(results, key=lambda n: n['bl_idname']),
    }
    path.write_text(json.dumps(doc, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False), encoding='utf-8')
    print('RESULT', json.dumps({
        'version': bpy.app.version_string,
        'created': len(results), 'rejected_types': len(errors),
        'probes': sum(len(n['variants']) for n in results),
        'schema_changing': sum(v.get('schema_changed', False) for n in results for v in n['variants']),
        'probes_rejected': sum(v['status'] == 'rejected' for n in results for v in n['variants']),
        'dynamic_collections': sum(bool(p.get('collection_api', {}).get('functions'))
                                   for n in results for p in n['properties'])
    }), flush=True)


if __name__ == '__main__':
    run()
