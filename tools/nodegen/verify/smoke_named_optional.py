"""Compile small named-parameter examples and verify native Blender socket defaults.

Uses the bundled Nodes result contracts. A failed case includes its NF source and traceback.
"""
from __future__ import annotations
import argparse
import json
import math
import os
from pathlib import Path
import shutil
import sys
import traceback
import bpy

if os.environ.get('NODEFORGE_CORE_PARENT'):
    sys.path.insert(0, os.environ['NODEFORGE_CORE_PARENT'])
from NodeForge import compiler, packages


def nf(call, results, grouped=False):
    names = 'result' if len(results) == 1 else ', '.join(f"result[{key!r}]" for key in results)
    content = call[:-1]
    separator = ', ' if not content.endswith('(') else ''
    return ("from packages import nodes as gen\n\n"
            f"def entry():\n    result = gen.{content}{separator}__inline__={not grouped})\n"
            f"    return {names}\n")


DEFAULT_KEYS = {'resample_curve': ('curve',), 'math': ('value',),
                'named_attribute': ('attribute', 'exists'), 'index': ('index',),
                'sample_index': ('value',), 'index_switch': ('output',),
                'menu_switch': ('output', 'A', 'B', 'C')}


# Each case is an independent physical-node test, not just Python signature introspection.
CASES = [
    {'name': 'resample_no_args_blender_baseline', 'node': 'GeometryNodeResampleCurve',
     'call': 'resample_curve()', 'values': {'Count': 10},
     'menu_values': {'Mode': 'Count'}, 'rna_values': {'keep_last_segment': True}},
    {'name': 'resample_blender_count_default', 'node': 'GeometryNodeResampleCurve',
     'call': "resample_curve(mode='Count')", 'values': {'Count': 10}},
    {'name': 'resample_explicit_count', 'node': 'GeometryNodeResampleCurve',
     'call': "resample_curve(mode='Count', count=23)", 'values': {'Count': 23}},
    {'name': 'resample_blender_length_default', 'node': 'GeometryNodeResampleCurve',
     'call': "resample_curve(mode='Length')", 'values': {'Length': 0.1}},
    {'name': 'resample_explicit_length', 'node': 'GeometryNodeResampleCurve',
     'call': "resample_curve(mode='Length', length=0.75)", 'values': {'Length': 0.75}},
    {'name': 'resample_selection', 'node': 'GeometryNodeResampleCurve',
     'call': "resample_curve(selection=False, mode='Count')", 'values': {'Selection': False}},
    {'name': 'math_named_inputs', 'node': 'ShaderNodeMath',
     'call': "math(value=2.0, value_001=3.0, operation='MULTIPLY')",
     'values': {'Value': 2.0, 'Value_001': 3.0}},
    {'name': 'math_omitted_input', 'node': 'ShaderNodeMath',
     'call': "math(value=2.0, operation='MULTIPLY')", 'values': {'Value_001': 0.5}},
    {'name': 'named_attribute_name_default', 'node': 'GeometryNodeInputNamedAttribute',
     'call': "named_attribute()", 'values': {'Name': ''}, 'results': ('attribute', 'exists')},
    {'name': 'named_attribute_explicit', 'node': 'GeometryNodeInputNamedAttribute',
     'call': "named_attribute(name='velocity')", 'values': {'Name': 'velocity'}, 'results': ('attribute', 'exists')},
    {'name': 'plain_index', 'node': 'GeometryNodeInputIndex',
     'call': 'index()', 'values': {}},
    {'name': 'builtin_and_package_index', 'node': 'GeometryNodeInputIndex',
     'source': 'from packages import nodes\n\ndef entry():\n    a = index()\n    b = nodes.index()\n    return a, b\n',
     'count': 2, 'values': {}},
    {'name': 'plain_index_grouped', 'node': 'GeometryNodeInputIndex',
     'call': 'index()', 'values': {}, 'grouped': True},
    {'name': 'sample_index_named', 'node': 'GeometryNodeSampleIndex',
     'call': "sample_index(index=7, data_type='FLOAT')", 'values': {'Index': 7}},
    {'name': 'index_dynamic_and_named', 'node': 'GeometryNodeIndexSwitch',
     'call': "index_switch(3.0, 4.0, 5.0, index=1, elements=3)",
     'values': {'Index': 1, 'Item_0': 3.0, 'Item_1': 4.0, 'Item_2': 5.0}},
    {'name': 'menu_dynamic', 'node': 'GeometryNodeMenuSwitch',
     'call': "menu_switch(1.5, 2.5, 3.5, elements='A,B,C', menu='C', data_type='FLOAT')",
     'values': {'A': 1.5, 'B': 2.5, 'C': 3.5}, 'results': ('output', 'A', 'B', 'C')},
    {'name': 'resample_grouped', 'node': 'GeometryNodeResampleCurve',
     'call': "resample_curve(count=29, mode='Count')", 'values': {'Count': 29}, 'grouped': True},
    {'name': 'inactive_length_rejected', 'node': 'GeometryNodeResampleCurve',
     'call': "resample_curve(mode='Count', length=0.75)", 'error': True},
    {'name': 'inactive_math_input_rejected', 'node': 'ShaderNodeMath',
     'call': "math(value_002=10.0, operation='ADD')", 'error': True},
]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--package', required=True)
    parser.add_argument('--out', required=True)
    args = parser.parse_args(sys.argv[sys.argv.index('--') + 1:] if '--' in sys.argv else [])
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    install = out / 'isolated_packages'
    shutil.rmtree(install, ignore_errors=True)
    packages.set_packages_dir_for_tests(install)
    packages.invalidate_caches()
    rows = []
    try:
        packages.install_package_directory(Path(args.package))
        for case in CASES:
            before = {g.as_pointer() for g in bpy.data.node_groups}
            src = case['source'] if 'source' in case else nf(case['call'], case.get('results', DEFAULT_KEYS[case['call'].split('(', 1)[0]]), case.get('grouped', False))
            record = {'name': case['name'], 'source': src, 'status': 'failed'}
            try:
                try:
                    root = compiler.create_expression_group(src, 'NF_Named_' + case['name'])
                except Exception as exc:
                    if case.get('error'):
                        record['status'] = 'passed'
                        record['expected_error'] = str(exc)
                    else:
                        raise
                else:
                    if case.get('error'):
                        raise AssertionError('Expected invalid input to be rejected, but compilation succeeded')
                    groups = [root]
                    groups += [n.node_tree for n in root.nodes
                               if n.bl_idname == 'GeometryNodeGroup' and n.node_tree]
                    found = [n for group in groups for n in group.nodes
                             if n.bl_idname == case['node']]
                    assert len(found) == case.get('count', 1), (case['name'], len(found))
                    native = found[0]
                    for key, expected in case['values'].items():
                        matches = [s for s in native.inputs if s.identifier == key]
                        if not matches:
                            matches = [s for s in native.inputs if s.name == key]
                        assert len(matches) == 1, (key, [s.name for s in native.inputs])
                        sock = matches[0]
                        assert not sock.is_linked, (key, 'unexpected link')
                        actual = sock.default_value
                        if isinstance(expected, float):
                            assert math.isclose(actual, expected, abs_tol=1e-5), (key, actual, expected)
                        else:
                            assert actual == expected, (key, actual, expected)
                    for key, expected in case.get('menu_values', {}).items():
                        matches = [s for s in native.inputs if s.identifier == key]
                        assert len(matches) == 1 and matches[0].default_value == expected, (key, expected)
                    for key, expected in case.get('rna_values', {}).items():
                        assert getattr(native, key) == expected, (key, expected)
                    assert all(link.is_valid for group in groups for link in group.links)
                    assert not any(n.bl_idname == 'ShaderNodeValue' for group in groups for n in group.nodes)
                    record['status'] = 'passed'
            except BaseException as exc:
                record.update(error=str(exc), traceback=traceback.format_exc())
            finally:
                for g in tuple(bpy.data.node_groups):
                    if g.as_pointer() not in before:
                        bpy.data.node_groups.remove(g, do_unlink=True)
            rows.append(record)
            print('NAMED_OPTIONAL', case['name'], record['status'], record.get('error', ''), flush=True)
    finally:
        packages.set_packages_dir_for_tests(None)
        packages.invalidate_caches()
    summary = {'blender': bpy.app.version_string, 'passed': sum(r['status'] == 'passed' for r in rows),
               'failed': sum(r['status'] != 'passed' for r in rows), 'cases': rows}
    (out / 'smoke.json').write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n')
    print('NAMED_OPTIONAL_RESULT', summary['passed'], len(rows), flush=True)

if __name__ == '__main__':
    main()
