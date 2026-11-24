"""The generated public Nodes API exposes plain Blender property names."""
from __future__ import annotations

import ast
import gzip
import json
from pathlib import Path
import sys

import pytest

ROOT = Path(__file__).resolve().parents[2]
TOOL = ROOT / 'tools' / 'nodegen'
OWNER = ROOT / 'packages' / 'nodes' / 'systems' / 'blender'
pytestmark = pytest.mark.unit


@pytest.fixture(scope='module')
def generated(tmp_path_factory):
    """Generate the full pinned catalogue independently of the committed files."""
    sys.path.insert(0, str(TOOL / 'generator'))
    try:
        from generate_clean import generate
        with gzip.open(TOOL / 'data' / 'blender-5.2.spec.json.gz', 'rt') as source:
            spec = json.load(source)
        candidates = [name for name in sorted(spec['nodes'])
                      if not any(part in name for part in ('Simulation', 'Repeat', 'Foreach', 'Bake'))]
        output = tmp_path_factory.mktemp('plain_node_parameters')
        report = generate(spec, candidates, output)
        return report, output
    finally:
        sys.path.remove(str(TOOL / 'generator'))


def test_generated_property_names_are_plain_and_consistent(generated):
    """Every public parameter, backend mapping and resolver uses the same name."""
    report, output = generated
    assert report['summary'] == {'wrappers': 297, 'states': 2671}
    functions = {}
    for filename in ('interface.py', 'backend.py', 'semantic.py'):
        text = (output / filename).read_text()
        assert text == (OWNER / filename).read_text()
        functions[filename] = {f.name: f for f in ast.parse(text).body
                               if isinstance(f, ast.FunctionDef)}
    count = 0
    for wrapper in report['wrappers']:
        name = wrapper['name']
        declaration = functions['interface.py'][name]
        backend = functions['backend.py'][name]
        public = {a.arg for a in declaration.args.kwonlyargs}
        assert not any(param.startswith('rna_') for param in public)
        assert public == {a.arg for a in backend.args.kwonlyargs}
        materialize = backend.body[0].value
        props = next(k.value for k in materialize.keywords if k.arg == 'rna')
        actual = {k.value: v.id for k, v in zip(props.keys, props.values)}
        expected = {f['key']: f['param'] for f in wrapper['fields'] if f['kind'] == 'rna'}
        assert actual == expected
        assert all(key == value for key, value in expected.items())
        count += len(expected)
        resolver = functions['semantic.py'].get('_result_types_' + name)
        if resolver:
            lookup = resolver.body[0].value
            assert set(ast.literal_eval(lookup.args[1])) <= public
    assert count == 157


def test_same_named_axis_properties_and_inputs_are_distinct(generated):
    """Axis selectors and vector sockets remain independently addressable."""
    report, _ = generated
    axes = next(w for w in report['wrappers'] if w['name'] == 'axes_to_rotation')
    assert {f['param']: f['key'] for f in axes['fields']} == {
        'primary_axis': 'primary_axis', 'secondary_axis': 'secondary_axis'}
    assert {inp['param']: inp['socket'] for inp in axes['named_inputs']} == {
        'primary_axis_vector': 'Primary Axis', 'secondary_axis_vector': 'Secondary Axis'}
