"""Pinned Inspector and generated Blender 5.2 Nodes package stay installable and in sync."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys

import pytest

from NodeForge import packages

pytestmark = pytest.mark.unit
ROOT = Path(__file__).resolve().parents[2]
PACKAGE = ROOT / 'packages' / 'nodes'
BUILD = ROOT / 'tools' / 'nodegen' / 'build.py'


def test_bundled_package_declares_generated_blender_owner():
    """Generated extensions are a normal system owner with direct package callables."""
    manifest = packages.validate_package_root(PACKAGE)
    records = packages.system_package_records_from_manifests((manifest,))
    assert manifest.package_id == 'nodeforge.nodes'
    assert manifest.import_name == 'nodes'
    assert [(r.system_id, r.package_id) for r in records] == [('blender', 'nodeforge.nodes')]
    assert not (PACKAGE / 'functions').exists()
    text = (PACKAGE / 'systems' / 'blender' / 'interface.py').read_text(encoding='utf-8')
    assert 'def resample_curve(' in text
    assert 'def set_position(' in text
    assert "'resample_curve': '.backend:resample_curve'" in text


def test_generated_bindings_match_inspector_source():
    """Regeneration preserves the manually managed manifest and exact bindings."""
    before = (PACKAGE / 'nodeforge_package.json').read_bytes()
    run = subprocess.run([sys.executable, str(BUILD), '--check', '--no-zip'],
                         capture_output=True, text=True, check=True)
    assert 'GENERATED 297 functions' in run.stdout
    assert (PACKAGE / 'nodeforge_package.json').read_bytes() == before
    assert (PACKAGE / 'systems/blender/interface.py').is_file()
    assert not list((ROOT / 'package_install').glob('nodeforge.nodes*.zip'))


def test_included_sources_install_without_special_package_logic(tmp_path):
    """Bundled sources use the stock versioned package directory installer."""
    packages.set_packages_dir_for_tests(tmp_path / 'inventory')
    try:
        assert packages.install_included_packages(ROOT) == ()
        manifest, = [record.manifest for record in packages.active_package_records()
                     if record.manifest.package_id == 'nodeforge.nodes']
        assert manifest.import_name == 'nodes'
        assert manifest.root != PACKAGE
        assert [record.system_id for record in packages.system_package_records()] == ['blender']
        state = packages.load_package_state()
        assert state['packages']['nodeforge.nodes']['installed_version'] == '1.6.0'
        assert json.loads((manifest.root / 'nodeforge_package.json').read_text())['nodeforge_min_version'] == '0.101.0'
        assert packages.install_included_packages(ROOT) == ()
    finally:
        packages.set_packages_dir_for_tests(None)
        packages.invalidate_caches()


def test_generator_can_target_an_alternate_package(tmp_path):
    """A custom destination receives fresh bindings without rewriting manifest metadata."""
    target = tmp_path / 'custom_nodes'
    run = subprocess.run([sys.executable, str(BUILD), '--output', str(target), '--no-zip',
                          '--results', str(tmp_path / 'results')],
                         capture_output=True, text=True, check=True)
    assert 'GENERATED 297 functions' in run.stdout
    assert (target / 'systems' / 'blender' / 'interface.py').read_bytes() == (
        PACKAGE / 'systems' / 'blender' / 'interface.py').read_bytes()
    assert (target / 'nodeforge_package.json').read_bytes() == (
        PACKAGE / 'nodeforge_package.json').read_bytes()
    coverage = json.loads((tmp_path / 'results' / 'coverage.json').read_text())
    assert coverage['generated'] == 297
    assert coverage['configurations'] == 2671
    assert coverage['excluded_zones']
    assert coverage['skipped']


@pytest.fixture(scope='module')
def generator_modules():
    """Load the build tool from its own source tree, not the installed library."""
    sys.path.insert(0, str(BUILD.parent))
    try:
        import build
        import generate_clean
        import inspect_contract
        yield build, generate_clean, inspect_contract
    finally:
        sys.path.remove(str(BUILD.parent))


def test_emitted_parser_definitions_and_phase_isolation(generator_modules):
    """Each real phase uses its own generated definitions without runtime generator imports."""
    import ast
    import inspect
    from NodeForge.extension_result_api import ResultStaticArg
    from NodeForge.nf_types import NFType
    from NodeForge.extensions.registry import ExtensionOwnerSession, capture_owner_code_snapshot
    build, generator, inspector = generator_modules
    root = PACKAGE / 'systems' / 'blender'
    assert sorted(p.name for p in root.glob('*.py')) == sorted(build.FILES)
    for filename, functions in [('backend.py', (generator.capture_entries, generator.menu_names, inspector.sanitized)),
                                ('semantic.py', (generator.capture_entries, generator.menu_names))]:
        parsed = {node.name: node for node in ast.parse((root / filename).read_text()).body
                  if isinstance(node, ast.FunctionDef)}
        for function in functions:
            assert ast.dump(parsed[function.__name__]) == ast.dump(ast.parse(inspect.getsource(function)).body[0])
    owner = ExtensionOwnerSession(capture_owner_code_snapshot(('system', 'nodeforge.nodes', 'blender'), root))
    families, _ = owner.normalize_interface()
    ids = {cid.name: cid for cid in families}
    assert families[ids['cylinder']][0].result.kind == 'NAMED_RESULTS'
    before = set(sys.modules)
    args = {'elements': ResultStaticArg(NFType.STRING, 'FLOAT:Weight,VECTOR:two words')}
    # Capture's domain is irrelevant to its result schema.
    assert owner.invoke_result_resolver(ids['capture_attribute'], args) == (
        ('geometry', NFType.GEOMETRY), ('selection', NFType.BOOL), ('Weight', NFType.FLOAT), ('two words', NFType.VECTOR))
    assert set(owner._phase_modules['SEMANTIC']) == {'semantic'}
    assert not owner._phase_modules['IMPLEMENTATION']
    with owner._mounted('IMPLEMENTATION'):
        owner._resolve_implementation_mounted(ids['capture_attribute'])
        backend = owner._phase_modules['IMPLEMENTATION']['backend']
        assert backend.capture_entries('FLOAT:Weight,VECTOR:two words', ('FLOAT', 'VECTOR')) == (
            ('FLOAT', 'Weight'), ('VECTOR', 'two words'))
        assert backend.menu_names('A, lower,Two Words') == ('A', 'lower', 'Two Words')
        assert backend.sanitized('Point Count') == 'point_count'
        assert owner._full_name('semantic') not in sys.modules
    assert set(owner._phase_modules['IMPLEMENTATION']) == {'backend'}
    assert not any(name.startswith(owner.snapshot.module_base) for name in set(sys.modules) - before)


@pytest.mark.parametrize('text,expected', [('', ()), ('FLOAT:a', (('FLOAT','a'),)),
    (' FLOAT:Weight , VECTOR:weight ', (('FLOAT','Weight'),('VECTOR','weight'))),
    ('FLOAT: a:b', (('FLOAT',' a:b'),)), ('FLOAT :a', None), ('FLOAT:', None),
    ('FLOAT:a,FLOAT:a', None), ('FLOAT:a,', None), ('BOGUS:a', None), ('a', None)])
def test_capture_parser_preserves_grammar(generator_modules, text, expected):
    """Capture parsing strips chunks, not the individual type and name fields."""
    _, generator, _ = generator_modules
    if expected is None:
        with pytest.raises(ValueError):
            generator.capture_entries(text, ('FLOAT','VECTOR'))
    else:
        assert generator.capture_entries(text, ('FLOAT','VECTOR')) == expected


@pytest.mark.parametrize('text,expected', [('', ()), (' A ,a,Two Words ', ('A','a','Two Words')),
    ('A,A', None), ('A,,B', None), (' ', None)])
def test_menu_parser_preserves_grammar(generator_modules, text, expected):
    """Menu names are stripped, ordered, case-sensitive and nonempty."""
    _, generator, _ = generator_modules
    if expected is None:
        with pytest.raises(ValueError):
            generator.menu_names(text)
    else:
        assert generator.menu_names(text) == expected


def test_name_only_result_variations_are_dynamic(generator_modules):
    """Equal NFTypes do not erase distinct native output names."""
    build, generator, inspector = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    for node, names in [('GeometryNodeAttributeDomainSize', {'point_count','instance_count','layer_count'}),
                        ('ShaderNodeTexVoronoi', {'distance','radius'})]:
        item = inspector.prepare_node(node, spec['nodes'][node])
        rule = generator.result_rule(item)
        assert rule['dynamic_result']
        assert names <= {name for schema in rule['map'].values() for name, _ in schema}
        for cfg in item['configurations']:
            values = {field['param']: cfg['rna' if field['kind']=='rna' else 'menus'][field['key']]
                      for field in item['fields']}
            assert rule['map'][tuple(values[key] for key in rule['deps'])] == generator.result_signature(item, cfg)


def test_schema_collision_is_fatal_and_build_does_not_publish(generator_modules, tmp_path, monkeypatch):
    """A mandatory same-layout collision is not a skipped unsupported node."""
    import shutil
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    node = 'GeometryNodeMeshCylinder'
    original = generator.prepare_node
    def colliding(name, data):
        prepared = original(name, data)
        if name == node:
            first, second = prepared['configurations'][0]['outputs'][:2]
            second['name'] = first['name'].upper()
        return prepared
    monkeypatch.setattr(generator, 'prepare_node', colliding)
    target = tmp_path / 'nodes'
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns('__pycache__'))
    owner = target / 'systems' / 'blender'
    before = {name: (owner / name).read_bytes() for name in build.FILES}
    with pytest.raises(generator.ResultSchemaError, match='GeometryNodeMeshCylinder state .*physical sockets'):
        generator.generate(spec, [node], owner)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    with pytest.raises(generator.ResultSchemaError, match='GeometryNodeMeshCylinder'):
        build.build(spec, target, archive=True)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    assert not list(tmp_path.glob('*.zip'))


@pytest.mark.parametrize('node, identifier', [('GeometryNodeInputIndex', 'Index'),
                                              ('GeometryNodeCaptureAttribute', 'Geometry'),
                                              ('GeometryNodeCaptureAttribute', 'Attribute')])
@pytest.mark.parametrize('field, value', [
    ('name', None), ('name', 42), ('name', ['invalid']), ('name', 'missing'),
    ('name', '\0'), ('name', '\ud800'),
    ('identifier', 'missing'), ('identifier', None), ('identifier', 42),
    ('identifier', ['invalid']), ('identifier', ''), ('identifier', '\0'),
    ('identifier', '\ud800'),
    ('bl_idname', 'missing'), ('bl_idname', None), ('bl_idname', 42),
    ('bl_idname', ['invalid']), ('bl_idname', ''), ('bl_idname', '\0'),
    ('bl_idname', '\ud800'),
])
def test_invalid_required_output_metadata_is_fatal(generator_modules, tmp_path, node, identifier, field, value):
    """Required output metadata is validated before conversion or dynamic probing."""
    import shutil
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    changed = 0
    for layout in spec['nodes'][node]['joint']['socket_states'].values():
        for socket in layout['outputs']:
            if socket.get('identifier') == identifier:
                if value == 'missing':
                    del socket[field]
                else:
                    socket[field] = value
                changed += 1
    assert changed
    target = tmp_path / 'nodes'
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns('__pycache__'))
    owner = target / 'systems/blender'
    before = {name: (owner / name).read_bytes() for name in build.FILES}
    problem = 'display label' if field == 'name' else field
    diagnostic = rf'{node} state .*{problem}.*physical sockets'
    with pytest.raises(generator.ResultSchemaError, match=diagnostic):
        generator.generate(spec, ['GeometryNodeInputPosition', node], owner)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    with pytest.raises(generator.ResultSchemaError, match=diagnostic):
        build.build(spec, target, archive=True)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    assert not list(tmp_path.glob('*.zip'))


@pytest.mark.parametrize('node', ['GeometryNodeMeshCylinder', 'GeometryNodeCaptureAttribute'])
def test_duplicate_required_output_identifiers_are_fatal(generator_modules, tmp_path, node):
    """Distinct named outputs cannot share one physical identifier in an active layout."""
    import shutil
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    for layout in spec['nodes'][node]['joint']['socket_states'].values():
        first, second = layout['outputs'][:2]
        second['identifier'] = first['identifier']
    target = tmp_path / 'nodes'
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns('__pycache__'))
    owner = target / 'systems/blender'
    before = {name: (owner / name).read_bytes() for name in build.FILES}
    diagnostic = rf'{node} state .*duplicate output identifier.*physical sockets'
    with pytest.raises(generator.ResultSchemaError, match=diagnostic):
        generator.generate(spec, [node], owner)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    with pytest.raises(generator.ResultSchemaError, match=diagnostic):
        build.build(spec, target, archive=True)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    assert not list(tmp_path.glob('*.zip'))


def test_unsupported_output_type_remains_a_skip(generator_modules, tmp_path):
    """An unrepresentable matrix socket does not require named result metadata."""
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    node = 'FunctionNodeCombineMatrix'
    for layout in spec['nodes'][node]['joint']['socket_states'].values():
        for socket in layout['outputs']:
            if socket['bl_idname'] == 'NodeSocketMatrix':
                socket.pop('name', None)
                socket.pop('identifier', None)
    report = generator.generate(spec, [node, 'GeometryNodeInputIndex'], tmp_path)
    assert report['summary'] == {'wrappers': 1, 'states': 1}
    assert report['skipped'] == [{'node': node, 'reason': 'no representable configurations'}]


@pytest.mark.parametrize('node, fault', [
    ('ShaderNodeValue', 'missing'), ('ShaderNodeValue', 'null'),
    ('ShaderNodeValue', 'duplicate'), ('FunctionNodeInputInt', 'duplicate'),
    ('ShaderNodeValue', 'wrong_identifier'), ('ShaderNodeValue', 'invalid_default'),
    ('ShaderNodeValue', 'missing_container'), ('ShaderNodeValue', 'null_record'),
])
def test_required_constant_baseline_is_fatal(generator_modules, tmp_path, node, fault):
    """Malformed baseline lookup data must not silently remove a constant or wrapper."""
    import shutil
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    baseline = spec['nodes'][node]['inventory']['baseline']
    target = tmp_path / 'nodes'
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns('__pycache__'))
    owner = target / 'systems/blender'
    before = {name: (owner / name).read_bytes() for name in build.FILES}
    prepared = generator.prepare_node(node, spec['nodes'][node])
    expected = ([{'param': 'value', 'type': 'Float', 'target': 'output', 'key': 'Value'}]
                if node == 'ShaderNodeValue' else
                [{'param': 'integer', 'type': 'Int', 'target': 'rna', 'key': 'integer'}])
    assert generator.constant_parameters(prepared, spec['nodes'][node]) == expected
    socket = baseline['outputs'][0]
    if fault == 'missing':
        del socket['identifier']
    elif fault == 'null':
        socket['identifier'] = None
    elif fault == 'duplicate':
        baseline['outputs'].append(dict(socket))
    elif fault == 'wrong_identifier':
        socket['identifier'] = 'NotTheExpectedOutput'
    elif fault == 'invalid_default':
        del socket['has_default_value']
    elif fault == 'missing_container':
        del baseline['outputs']
    else:
        baseline['outputs'][0] = None
    with pytest.raises(generator.ResultSchemaError, match=rf'{node} state baseline: .*physical sockets'):
        build.build(spec, target, archive=True)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    assert not list(tmp_path.glob('*.zip'))


@pytest.mark.parametrize('probe', [False, True], ids=['active-layout', 'capture-probe'])
@pytest.mark.parametrize('fault', ['missing', 'null-container', 'mapping', 'null-record', 'string-record'])
def test_invalid_output_records_are_fatal(generator_modules, tmp_path, probe, fault):
    """Output shape is validated before ordinary filtering or accepted probe inspection."""
    import shutil
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    node = 'GeometryNodeCaptureAttribute' if probe else 'GeometryNodeInputIndex'
    joint = spec['nodes'][node]['joint']
    applied = [st for st in joint['states'] if st['status'] == 'applied']
    if probe:
        trial = next(t for st in applied for c in st['collections'] if c['name'] == 'capture_items'
                     for t in c['attempts'] if t['status'] == 'accepted' and t['args'][0] == 'FLOAT')
        state = trial['after']
        assert state not in {st['socket_state_id'] for st in applied}
    else:
        state = applied[0]['socket_state_id']
    layout = joint['socket_states'][state]
    if fault == 'missing':
        del layout['outputs']
    elif fault == 'null-container':
        layout['outputs'] = None
    elif fault == 'mapping':
        layout['outputs'] = {'socket': layout['outputs'][0]}
    elif fault == 'null-record':
        layout['outputs'][0] = None
    else:
        layout['outputs'][0] = 'invalid'
    target = tmp_path / 'nodes'
    shutil.copytree(PACKAGE, target, ignore=shutil.ignore_patterns('__pycache__'))
    owner = target / 'systems/blender'
    before = {name: (owner / name).read_bytes() for name in build.FILES}
    diagnostic = rf'{node} state .*invalid output container or record.*physical sockets'
    with pytest.raises(generator.ResultSchemaError, match=diagnostic):
        generator.generate(spec, ['GeometryNodeInputPosition', node], owner)
    with pytest.raises(generator.ResultSchemaError, match=diagnostic):
        build.build(spec, target, archive=True)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    assert not list(tmp_path.glob('*.zip'))


def test_failed_constant_build_preserves_existing_archive_and_recovers(generator_modules, tmp_path):
    """A successful build, rejected build, and corrected rebuild preserve the constant API."""
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    target = tmp_path / 'nodes'
    report, archive = build.build(spec, target, archive=True)
    owner = target / 'systems/blender'
    before = {name: (owner / name).read_bytes() for name in build.FILES}
    archive_before = archive.read_bytes()
    sock = spec['nodes']['ShaderNodeValue']['inventory']['baseline']['outputs'][0]
    ident = sock.pop('identifier')
    with pytest.raises(generator.ResultSchemaError, match='ShaderNodeValue state baseline'):
        build.build(spec, target, archive=True)
    assert archive.read_bytes() == archive_before
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    sock['identifier'] = ident
    rebuilt, _ = build.build(spec, target, archive=True)
    assert rebuilt == report
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before


def test_known_unsupported_outcomes_are_not_schema_errors(generator_modules, tmp_path):
    """Empty outputs, unsupported constant sockets and varying options retain omission semantics."""
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    node = 'GeometryNodeInputIndex'
    for layout in spec['nodes'][node]['joint']['socket_states'].values():
        layout['outputs'] = []
    report = generator.generate(spec, [node, 'GeometryNodeListLength', 'ShaderNodeValue'], tmp_path)
    assert report['skipped'] == [
        {'node': node, 'reason': 'no representable configurations'},
        {'node': 'GeometryNodeListLength', 'reason': 'Option keys vary across otherwise representable states'},
    ]
    constant_node = spec['nodes']['ShaderNodeValue']
    item = generator.prepare_node('ShaderNodeValue', constant_node)
    sock = constant_node['inventory']['baseline']['outputs'][0]
    sock['has_default_value'] = False
    assert generator.constant_parameters(item, constant_node) == []
    sock['bl_idname'] = 'NodeSocketMatrix'
    sock.pop('identifier')
    assert generator.constant_parameters(item, constant_node) == []


def test_unexpected_preparation_error_aborts_generation(generator_modules, tmp_path, monkeypatch):
    """Unexpected preparation failures cannot be reported as unsupported nodes."""
    build, generator, _ = generator_modules
    spec = build.read_spec(build.DEFAULT_SPEC)
    target = tmp_path / 'nodes'
    _, archive = build.build(spec, target, archive=True)
    owner = target / 'systems/blender'
    before = {name: (owner / name).read_bytes() for name in build.FILES}
    archive_before = archive.read_bytes()
    original = generator.prepare_node

    def broken(name, inspected):
        """Inject an unexpected preparation failure into one supported node."""
        if name == 'ShaderNodeValue':
            raise RuntimeError('injected preparation failure')
        return original(name, inspected)

    monkeypatch.setattr(generator, 'prepare_node', broken)
    with pytest.raises(RuntimeError, match='injected preparation failure'):
        build.build(spec, target, archive=True)
    assert {name: (owner / name).read_bytes() for name in build.FILES} == before
    assert archive.read_bytes() == archive_before
