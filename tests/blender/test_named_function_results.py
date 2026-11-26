"""Physical integration tests for explicit named source returns."""

import bpy
import pytest

from helpers import compile_entry_group


@pytest.mark.parametrize('inline', [False, True])
def test_source_named_projection_reuses_one_call_and_exact_labels(inline):
    """Both materialization modes route the declared leaves without extra calls."""
    source = f'''def make(value: Float, __inline__={inline}):
    return {{"first": value + 1, "Two Words": value + 2}}

def main(value: Float):
    r = make(value)
    alias = r
    return {{"a": r.first, "b": alias["Two Words"], "again": r["first"]}}
'''
    group = compile_entry_group(source, 'NamedReturn', 'main')
    output = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    assert [s.name for s in output.inputs if s.bl_idname != 'NodeSocketVirtual'] == ['a','b','again']
    a,b,again = [output.inputs[key].links[0].from_socket for key in ('a','b','again')]
    assert a == again and a != b
    calls = [n for n in group.nodes if n.bl_idname == 'GeometryNodeGroup']
    if inline:
        assert not calls
        assert a.node.bl_idname == b.node.bl_idname == 'ShaderNodeMath'
    else:
        assert len(calls) == 1
        assert a.node == b.node == calls[0]
        assert (a.name,b.name) == ('first','Two Words')


@pytest.mark.parametrize('static', [False, True])
@pytest.mark.parametrize('inline', [False, True])
def test_named_extension_results_match_keys_not_backend_order(tmp_path, static, inline):
    """Same-typed backend leaves are independently connected by exact key."""
    from NodeForge import packages
    from test_extension_dynamic_results import _write_package
    from helpers import compile_group
    root = tmp_path / 'fixture'
    _write_package(root)
    if static:
        owner = root / 'systems' / 'dynamic'
        interface = owner / 'interface.py'
        interface.write_text(interface.read_text().replace('-> DynamicResults:', '-> {"double": Float, "original": Float}:'))
        (owner / 'semantic.py').unlink()
    packages.install_package_directory(root)
    group = compile_group(f'''from packages import dynamic

def main(value: Float):
    r = foo(value, __inline__={inline})
    return {{"first": r.double, "second": r["original"]}}
''', 'NamedExtension')
    out = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    first, second = [out.inputs[key].links[0].from_socket for key in ('first','second')]
    if inline:
        assert first.node.bl_idname == 'ShaderNodeMath'
        assert second.node.bl_idname == 'NodeGroupInput'
    else:
        assert first.node == second.node and first.node.bl_idname == 'GeometryNodeGroup'
        assert (first.name,second.name) == ('double','original')
        child = first.node.node_tree
        child_out = next(n for n in child.nodes if n.bl_idname == 'NodeGroupOutput')
        assert child_out.inputs['double'].links[0].from_node.bl_idname == 'ShaderNodeMath'
        assert child_out.inputs['original'].links[0].from_node.bl_idname == 'NodeGroupInput'


@pytest.mark.parametrize('inline', [False, True])
def test_dynamic_semantic_then_backend_named_results(tmp_path, inline):
    """The resolved named schema survives compaction of semantic state."""
    from test_extension_boundary_matrix import _package
    from helpers import compile_group
    _package(tmp_path, name='named_state', interface='''from dataclasses import dataclass
from typing import Annotated
from NodeForge import DynamicResults, EvaluationMode, Float
EXTENSION_API = 2
EXTENSIONS = {'make': '.backend:make'}
@dataclass(frozen=True)
class _State:
    value: Float

def make(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> DynamicResults: ...
''', semantic='''from .interface import _State
from NodeForge.nf_types import NFType

def _result_types_make(args):
    return {'doubled': NFType.FLOAT, 'original': NFType.FLOAT}

def make(value) -> _State:
    return _State(value)
''', backend='''from NodeForge.nf_types import NFType

def make(context, state):
    assert context.result_names == ('doubled', 'original')
    node = context.group.nodes.new('ShaderNodeMath')
    node.operation = 'MULTIPLY'
    context.group.links.new(state.value.socket, node.inputs[0])
    node.inputs[1].default_value = 2.0
    return {'original': state.value, 'doubled': context.value(node.outputs[0], NFType.FLOAT)}
''')
    group = compile_group(f'''from packages import named_state

def main(value: Float):
    r = make(value, __inline__={inline})
    return {{'a': r.doubled, 'b': r.original}}
''', 'NamedState')
    out = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    a,b = [out.inputs[key].links[0].from_socket for key in ('a','b')]
    if inline:
        assert a.node.bl_idname == 'ShaderNodeMath' and b.node.bl_idname == 'NodeGroupInput'
    else:
        assert a.node == b.node and a.node.bl_idname == 'GeometryNodeGroup'
        assert (a.name,b.name) == ('doubled','original')
        assert [s.name for s in a.node.inputs] == ['Dependency_0']


def test_grouped_extension_named_rename_drops_external_link(tmp_path):
    """A rebuilt extension occurrence retains authority but not a renamed output link."""
    from NodeForge import packages, compiler
    from NodeForge.blender.interface import NAMED_RESULTS_PROP
    from NodeForge.blender.extension_group_materializer import EXTENSION_FINGERPRINT_PROP
    from test_extension_dynamic_results import _write_package
    from helpers import compile_group
    root = tmp_path / 'fixture'
    _write_package(root)
    owner = root / 'systems' / 'dynamic'
    interface = owner / 'interface.py'
    interface.write_text(interface.read_text().replace('EvaluationMode, Float', 'EvaluationMode, Float, Bool').replace('value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]', 'value: Annotated[Float, EvaluationMode.RUNTIME_ONLY], renamed: Annotated[Bool, EvaluationMode.COMPILE_TIME_ONLY] = False'))
    (owner / 'semantic.py').write_text('''from NodeForge.nf_types import NFType

def _result_types_foo(arguments):
    return {'new' if arguments['renamed'].value else 'old': NFType.FLOAT}
''')
    (owner / 'backend.py').write_text('''def foo(context, value, renamed):
    return {'new' if renamed else 'old': value}
''')
    packages.install_package_directory(root)
    def source(renamed):
        key = 'new' if renamed else 'old'
        return f'from packages import dynamic\n\ndef main(value: Float):\n    return foo(value, renamed={renamed}, __inline__=False).{key}\n'
    group = compile_group(source(False), 'NamedExtensionRename')
    child = next(n.node_tree for n in group.nodes if n.bl_idname == 'GeometryNodeGroup')
    assert child[NAMED_RESULTS_PROP]
    fingerprint = child[EXTENSION_FINGERPRINT_PROP]
    wrapper = bpy.data.node_groups.new('NamedExtensionExternal', 'GeometryNodeTree')
    instance = wrapper.nodes.new('GeometryNodeGroup')
    instance.node_tree = child
    target = wrapper.nodes.new('ShaderNodeMath')
    wrapper.links.new(instance.outputs['old'], target.inputs[0])
    compiler.update_expression_group(group, source(True))
    assert instance.node_tree == child
    assert list(instance.outputs.keys()) == ['new']
    assert not wrapper.links
    assert child[NAMED_RESULTS_PROP]
    assert child[EXTENSION_FINGERPRINT_PROP] != fingerprint


@pytest.mark.parametrize('before,after,expected', [
    ('a', 'b', []), ('a', 'a', ['a']), ('a', 'vector', []),
    ('ab', 'ba', ['a', 'b']), ('ab', 'b', ['b']),
    ('positional', 'Result', ['Result']), ('Result', 'positional', ['Result']),
    ('positional', 'a', []), ('a', 'positional', []),
    ('positional', 'Result', None), ('Result', 'positional', None),
])
def test_grouped_extension_named_output_update(tmp_path, before, after, expected):
    """A rebuilt extension occurrence matches exact physical output identity across shapes."""
    from NodeForge import packages, compiler
    from NodeForge.blender.interface import NAMED_RESULTS_PROP
    from test_extension_dynamic_results import _write_package
    from helpers import compile_group
    root = tmp_path / 'fixture'
    _write_package(root)
    installed = packages.install_package_directory(root)
    owner = installed.root / 'systems' / 'dynamic'
    def source(shape):
        """Write the current static interface and corresponding physical implementation."""
        keys = {'vector': ('a',), 'ab': ('a', 'b'), 'ba': ('b', 'a')}.get(shape, (shape,))
        typ = 'Vector' if shape == 'vector' else 'Float'
        annotation = 'Float' if shape == 'positional' else '{' + ', '.join(f'{key!r}: {typ}' for key in keys) + '}'
        (owner / 'interface.py').write_text(f'''from typing import Annotated
from NodeForge import EvaluationMode, Float, Vector
EXTENSION_API = 2
EXTENSIONS = {{'foo': '.backend:foo'}}
def foo(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> {annotation}: ...
''')
        (owner / 'semantic.py').unlink(missing_ok=True)
        body = 'return value' if shape == 'positional' else 'return {' + ', '.join(f'{key!r}: value' for key in keys) + '}'
        if shape == 'vector':
            body = "node = context.group.nodes.new('GeometryNodeInputPosition'); return {'a': context.value(node.outputs[0], NFType.VECTOR)}"
        (owner / 'backend.py').write_text('from NodeForge.nf_types import NFType\ndef foo(context, value):\n    ' + body + '\n')
        packages.invalidate_caches()
        projection = '' if shape == 'positional' else f'[{keys[0]!r}]'
        return f'from packages import dynamic\n\ndef main(value: Float):\n    return foo(value, __inline__=False){projection}\n'
    group = compile_group(source(before), 'NamedExtensionUpdate')
    child = next(n.node_tree for n in group.nodes if n.bl_idname == 'GeometryNodeGroup')
    assert (NAMED_RESULTS_PROP in child) == (before != 'positional')
    wrapper = bpy.data.node_groups.new('NamedExtensionExternal', 'GeometryNodeTree')
    instance = wrapper.nodes.new('GeometryNodeGroup')
    instance.node_tree = child
    instance.inputs[0].default_value = 7.0
    for output in instance.outputs:
        target = wrapper.nodes.new('ShaderNodeMath')
        wrapper.links.new(output, target.inputs[0])
    if expected is None:
        from NodeForge.blender import group_backend
        before_groups = {g.as_pointer() for g in bpy.data.node_groups}
        old_links = sorted(link.from_socket.name for link in wrapper.links)
        group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = True
        try:
            with pytest.raises(Exception, match='cutover'):
                compiler.update_expression_group(group, source(after))
        finally:
            group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = False
        expected, after = old_links, before
        assert {g.as_pointer() for g in bpy.data.node_groups} == before_groups
    else:
        compiler.update_expression_group(group, source(after))
    assert instance.node_tree == child
    assert sorted(link.from_socket.name for link in wrapper.links) == expected
    assert (NAMED_RESULTS_PROP in child) == (after != 'positional')
    assert instance.inputs[0].default_value == 7.0


@pytest.fixture
def bundled_nodes():
    """Install the exact generated owner from this repository into an isolated inventory."""
    from pathlib import Path
    from NodeForge import packages
    packages.install_package_directory(Path(__file__).resolve().parents[2] / 'packages' / 'nodes')


@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('call,key,physical', [
    ('cylinder()', 'mesh', 'GeometryNodeMeshCylinder'),
    ('sample_curve(data_type="FLOAT_VECTOR")', 'value', 'GeometryNodeSampleCurve'),
    ('named_attribute(data_type="FLOAT")', 'attribute', 'GeometryNodeInputNamedAttribute'),
    ('capture_attribute(elements={"Weight": 0.0})', 'Weight', 'GeometryNodeCaptureAttribute'),
    ('menu_switch(elements=["A","lower"], menu="lower", data_type="FLOAT")', 'lower', 'GeometryNodeMenuSwitch'),
])
def test_generated_named_projections_share_native_call(bundled_nodes, inline, call, key, physical):
    """Dot and bracket projection of the same generated occurrence share one socket."""
    from helpers import compile_group
    expression = call[:-1] + (', ' if not call.endswith('()') else '') + f'__inline__={inline})'
    group = compile_group(f'''from packages import nodes

def entry():
    r = nodes.{expression}
    return {{'a': r.{key}, 'b': r[{key!r}]}}
''', 'GeneratedNamedProjection')
    output = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    a, b = (output.inputs[k].links[0].from_socket for k in ('a','b'))
    assert a == b
    groups = [group] if inline else [a.node.node_tree]
    assert len([n for g in groups for n in g.nodes if n.bl_idname == physical]) == 1


@pytest.mark.parametrize('call,pattern', [
    ('capture_attribute(elements={"geometry": 1.0})', 'Dynamic result resolver'),
    ('capture_attribute(elements={"a":1.0,"a":2.0})', 'Duplicate structured dictionary key'),
    ('capture_attribute(elements={"":1.0})', 'non-empty string'),
    ('capture_attribute(elements={"' + 'x'*64 + '":1.0})', '63-byte'),
    ('menu_switch(elements=["output","B"], menu="B")', 'Dynamic result resolver'),
    ('menu_switch(elements=[], menu="A")', 'Dynamic result resolver'),
    ('menu_switch(elements=["A","A"], menu="A")', 'Dynamic result resolver'),
    ('capture_attribute(elements="FLOAT:Weight")', 'dictionary display'),
    ('menu_switch(elements="A,B")', 'list value'),
])
def test_generated_invalid_collection_schema_is_rejected_before_publication(bundled_nodes, call, pattern):
    """Invalid explicit keys and grammar fail before any native group is published."""
    from helpers import compile_group
    from NodeForge.errors import CompileError
    before = {g.as_pointer() for g in bpy.data.node_groups}
    with pytest.raises(CompileError, match=pattern):
        compile_group(f'from packages import nodes\n\ndef entry():\n    r = nodes.{call}\n    return r["geometry"]\n', 'InvalidGeneratedSchema')
    assert {g.as_pointer() for g in bpy.data.node_groups} == before


@pytest.mark.parametrize('inline', [False, True])
def test_package_source_named_results(tmp_path, inline):
    """Source exports and local functions share identical named projection rules."""
    from NodeForge import packages
    from test_qualified_package_callables import _write_source_package
    from helpers import compile_group
    root = tmp_path / 'named_package'
    _write_source_package(root, 'vendor.named', {'measure': '''def measure(value: Float):
    return {"first": value + 1, "Second Value": value + 2}
'''})
    packages.install_package_directory(root)
    group = compile_group(f'''from packages import named

def entry(value: Float):
    r = named.measure(value, __inline__={inline})
    return {{"a": r.first, "b": r["Second Value"]}}
''', 'NamedPackage')
    outlet = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    a, b = (outlet.inputs[key].links[0].from_socket for key in ('a', 'b'))
    if inline:
        assert a.node.bl_idname == b.node.bl_idname == 'ShaderNodeMath' and a.node != b.node
    else:
        assert a.node == b.node and a.node.bl_idname == 'GeometryNodeGroup'
        assert (a.name, b.name) == ('first', 'Second Value')


def test_catalogue_oracle_detects_same_type_output_swap(monkeypatch):
    """The independent verifier rejects a wrong physical source even for equal types."""
    from pathlib import Path
    import importlib.util
    path = Path(__file__).resolve().parents[2] / 'tools/nodegen/verify/validate_nodes.py'
    spec = importlib.util.spec_from_file_location('named_socket_verifier', path)
    verifier = importlib.util.module_from_spec(spec)
    # The standalone verifier inserts its generator directory into sys.path.
    import sys
    monkeypatch.setattr(sys, 'path', list(sys.path))
    spec.loader.exec_module(verifier)
    group = bpy.data.node_groups.new('SameTypeOutputSwap', 'GeometryNodeTree')
    try:
        native = group.nodes.new('GeometryNodeMeshCylinder')
        group.interface.new_socket(name='top', in_out='OUTPUT', socket_type='NodeSocketBool')
        group.interface.new_socket(name='bottom', in_out='OUTPUT', socket_type='NodeSocketBool')
        outlet = group.nodes.new('NodeGroupOutput')
        schema = [(key, native.outputs[label].identifier, label, 'NodeSocketBool')
                  for key, label in [('top', 'Top'), ('bottom', 'Bottom')]]
        group.links.new(native.outputs['Top'], outlet.inputs['top'])
        group.links.new(native.outputs['Bottom'], outlet.inputs['bottom'])
        assert verifier.check_output_links(group, native, schema) == schema
        group.links.new(native.outputs['Bottom'], outlet.inputs['top'])
        group.links.new(native.outputs['Top'], outlet.inputs['bottom'])
        with pytest.raises(AssertionError, match='wrong output provenance'):
            verifier.check_output_links(group, native, schema)
    finally:
        bpy.data.node_groups.remove(group, do_unlink=True)


@pytest.mark.parametrize('semantic_state', [False, True])
@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('form', ['DIRECT', 'ONE', 'MULTI'])
def test_explicit_dynamic_result_forms(tmp_path, semantic_state, inline, form):
    """Both execution paths realize the explicit direct or named shape exactly once."""
    from test_extension_boundary_matrix import _package
    from helpers import compile_group
    from NodeForge.blender.interface import NAMED_RESULTS_PROP
    interface = '''from dataclasses import dataclass
from typing import Annotated
from NodeForge import Float, String, DynamicResults, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {'choose': '.backend:choose'}
@dataclass(frozen=True)
class _State:
    value: Float
    form: str

def choose(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY], form: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'DIRECT') -> DynamicResults: ...
'''
    semantic = '''from NodeForge.nf_types import NFType

def _result_types_choose(args):
    form = args['form'].value
    if form == 'DIRECT':
        return NFType.FLOAT
    if form == 'ONE':
        return {'value': NFType.FLOAT}
    return {'value': NFType.FLOAT, 'original': NFType.FLOAT}
'''
    if semantic_state:
        semantic += '''\nfrom .interface import _State

def choose(value, form='DIRECT') -> _State:
    return _State(value, form)
'''
    backend = 'from NodeForge.nf_types import NFType\n'
    backend += ('def choose(context, state):\n    value, form = state.value, state.form\n' if semantic_state else
                "def choose(context, value, form='DIRECT'):\n")
    backend += '''    node = context.group.nodes.new('ShaderNodeMath')
    node.operation = 'MULTIPLY'
    context.group.links.new(value.socket, node.inputs[0])
    node.inputs[1].default_value = 2.0
    result = context.value(node.outputs[0], NFType.FLOAT)
    if form == 'DIRECT':
        assert context.result_names is None
        return result
    if form == 'ONE':
        assert context.result_names == ('value',)
        return {'value': result}
    assert context.result_names == ('value', 'original')
    return {'original': value, 'value': result}
'''
    _package(tmp_path, name='explicit_shapes', interface=interface, semantic=semantic, backend=backend)
    value = 'r' if form == 'DIRECT' else 'r.value'
    other = 'r' if form == 'DIRECT' else 'r["value"]' if form == 'ONE' else 'r.original'
    group = compile_group(f'''from packages import explicit_shapes

def main(value: Float):
    r = choose(value, form={form!r}, __inline__={inline})
    return {{'a': {value}, 'b': {other}}}
''', 'ExplicitShapes')
    output = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    a,b = [output.inputs[key].links[0].from_socket for key in ('a','b')]
    assert (a == b) == (form != 'MULTI')
    if inline:
        child = group
    else:
        assert a.node == b.node and a.node.bl_idname == 'GeometryNodeGroup'
        child = a.node.node_tree
        assert (NAMED_RESULTS_PROP in child) == (form != 'DIRECT')
        assert tuple(a.node.outputs.keys()) == {'DIRECT': ('Result',), 'ONE': ('value',), 'MULTI': ('value', 'original')}[form]
    assert len([n for n in child.nodes if n.bl_idname == 'ShaderNodeMath']) == 1


@pytest.mark.parametrize('before,after,expected', [
    ('direct', 'direct', ['Result']), ('direct', 'named', []),
    ('named', 'direct', []), ('named', 'multi', ['value']),
    ('multi', 'direct', []), ('direct', 'multi', []),
    ('direct', 'same_key', ['Result']), ('same_key', 'direct', ['Result']),
    ('direct', 'named', None), ('named', 'direct', None),
])
def test_dynamic_result_shape_update(tmp_path, before, after, expected):
    """Invocation-dependent carrier changes retain authority and exact named link identity."""
    from test_extension_boundary_matrix import _package
    from helpers import compile_group
    from NodeForge import compiler
    from NodeForge.blender.interface import NAMED_RESULTS_PROP
    from NodeForge.blender.extension_group_materializer import EXTENSION_FINGERPRINT_PROP
    _package(tmp_path, name='shape_update', interface='''from typing import Annotated
from NodeForge import Float, String, EvaluationMode, DynamicResults
EXTENSION_API = 2
EXTENSIONS = {'choose': '.backend:choose'}
def choose(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY], shape: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY]) -> DynamicResults: ...
''', semantic='''from NodeForge.nf_types import NFType
def _result_types_choose(args):
    shape = args['shape'].value
    if shape == 'direct': return NFType.FLOAT
    if shape == 'same_key': return {'Result': NFType.FLOAT}
    if shape == 'named': return {'value': NFType.FLOAT}
    return {'value': NFType.FLOAT, 'other': NFType.FLOAT}
''', backend='''def choose(context, value, shape):
    if shape == 'direct': return value
    if shape == 'same_key': return {'Result': value}
    if shape == 'named': return {'value': value}
    return {'other': value, 'value': value}
''')
    def source(shape):
        projection = '' if shape == 'direct' else '.Result' if shape == 'same_key' else '.value'
        return f'from packages import shape_update\n\ndef main(value: Float):\n    return choose(value, shape={shape!r}, __inline__=False){projection}\n'
    group = compile_group(source(before), 'ShapeUpdate')
    child = next(n.node_tree for n in group.nodes if n.bl_idname == 'GeometryNodeGroup')
    old_fp = child[EXTENSION_FINGERPRINT_PROP]
    wrapper = bpy.data.node_groups.new('ShapeExternal', 'GeometryNodeTree')
    instance = wrapper.nodes.new('GeometryNodeGroup')
    instance.node_tree = child
    instance.inputs[0].default_value = 7.0
    for output in instance.outputs:
        wrapper.links.new(output, wrapper.nodes.new('ShaderNodeMath').inputs[0])
    if expected is None:
        from NodeForge.blender import group_backend
        pointers = {g.as_pointer() for g in bpy.data.node_groups}
        links = sorted(link.from_socket.name for link in wrapper.links)
        group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = True
        try:
            with pytest.raises(Exception, match='cutover'):
                compiler.update_expression_group(group, source(after))
        finally:
            group_backend._TEST_CUTOVER_FAIL_AFTER_RESET = False
        assert {g.as_pointer() for g in bpy.data.node_groups} == pointers
        assert instance.node_tree == child
        assert sorted(link.from_socket.name for link in wrapper.links) == links
        assert child[EXTENSION_FINGERPRINT_PROP] == old_fp
        assert (NAMED_RESULTS_PROP in child) == (before != 'direct')
        expected = []
    compiler.update_expression_group(group, source(after))
    assert instance.node_tree == child
    assert sorted(link.from_socket.name for link in wrapper.links) == expected
    assert instance.inputs[0].default_value == 7.0
    assert (NAMED_RESULTS_PROP in child) == (after != 'direct')
    assert (child[EXTENSION_FINGERPRINT_PROP] == old_fp) == (before == after)


@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('call,physical,output', [
    ('position()', 'GeometryNodeInputPosition', 'Position'),
    ('sample_index(data_type="FLOAT")', 'GeometryNodeSampleIndex', 'Value'),
    ('index_switch(elements=3, data_type="FLOAT")', 'GeometryNodeIndexSwitch', 'Output'),
    ('domain_size(component="POINTCLOUD")', 'GeometryNodeAttributeDomainSize', 'Point Count'),
    ('domain_size(component="INSTANCES")', 'GeometryNodeAttributeDomainSize', 'Instance Count'),
])
def test_generated_direct_values_share_native_call(bundled_nodes, inline, call, physical, output):
    """Direct values can be bound and reused, preserving native socket identity."""
    from helpers import compile_group
    from NodeForge.blender.interface import NAMED_RESULTS_PROP
    expression = call[:-1] + (', ' if not call.endswith('()') else '') + f'__inline__={inline})'
    group = compile_group(f'''from packages import nodes

def entry():
    r = nodes.{expression}
    alias = r
    return {{'a': r, 'b': alias}}
''', 'GeneratedDirectReuse')
    outlet = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    a, b = (outlet.inputs[k].links[0].from_socket for k in ('a', 'b'))
    assert a == b
    if not inline:
        assert a.name == 'Result'
        child = a.node.node_tree
        assert NAMED_RESULTS_PROP not in child
        a = next(n for n in child.nodes if n.bl_idname == 'NodeGroupOutput').inputs['Result'].links[0].from_socket
    else:
        child = group
    assert a.node.bl_idname == physical and a.name == output
    assert len([n for n in child.nodes if n.bl_idname == physical]) == 1


@pytest.mark.parametrize('inline', [False, True])
def test_generated_wrong_scalar_physical_name(tmp_path, inline):
    """Same-typed native output with the wrong name is rejected before publication."""
    import shutil
    from pathlib import Path
    from NodeForge import packages
    from NodeForge.errors import CompileError
    from helpers import compile_group
    target = tmp_path / 'nodes'
    shutil.copytree(Path(__file__).resolve().parents[2] / 'packages/nodes', target)
    path = target / 'systems/blender/backend.py'
    source = path.read_text()
    start = source.index('\ndef domain_size(')
    end = source.index('\ndef ', start + 1)
    original = source[start:end]
    changed = original.replace("rna={'component': component}", "rna={'component': 'INSTANCES'}")
    assert original != changed
    path.write_text(source[:start] + changed + source[end:])
    packages.install_package_directory(target)
    before = {g.as_pointer() for g in bpy.data.node_groups}
    with pytest.raises(CompileError, match='output schema'):
        compile_group(f'from packages import nodes\n\ndef main():\n    return nodes.domain_size(component="POINTCLOUD", __inline__={inline})\n', 'WrongNativeName')
    assert before == {g.as_pointer() for g in bpy.data.node_groups}


@pytest.mark.parametrize('fault', ['marker', 'shape'])
def test_catalogue_oracle_rejects_wrong_direct_group_shape(bundled_nodes, monkeypatch, fault):
    """The oracle derives direct shape independently rather than trusting the actual marker."""
    from pathlib import Path
    import importlib.util
    import sys
    from helpers import compile_group
    from NodeForge.blender.interface import NAMED_RESULTS_PROP
    path = Path(__file__).resolve().parents[2] / 'tools/nodegen/verify/validate_nodes.py'
    spec = importlib.util.spec_from_file_location('direct_socket_verifier', path)
    verifier = importlib.util.module_from_spec(spec)
    monkeypatch.setattr(sys, 'path', list(sys.path))
    spec.loader.exec_module(verifier)
    root = compile_group('from packages import nodes\n\ndef main():\n    return {"point_count": nodes.domain_size(component="POINTCLOUD", __inline__=False)}\n', 'OracleDirect')
    child = next(n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup')
    native = next(n for n in child.nodes if n.bl_idname == 'GeometryNodeAttributeDomainSize')
    schema = [('point_count', native.outputs['Point Count'].identifier, 'Point Count', 'NodeSocketInt')]
    assert verifier.check_output_links(root, native, schema, grouped=True) == schema
    if fault == 'marker':
        child[NAMED_RESULTS_PROP] = True
    else:
        next(s for s in child.interface.items_tree if s.item_type == 'SOCKET' and s.in_out == 'OUTPUT').name = 'point_count'
    with pytest.raises(AssertionError, match='wrong named result marker|wrong grouped result shape'):
        verifier.check_output_links(root, native, schema, grouped=True)


@pytest.mark.parametrize('inline', [False, True])
def test_generated_direct_nested_repeat_evaluates_real_geometry(bundled_nodes, inline):
    """Direct Float, Vector and Geometry results compose across runtime repeat and source calls."""
    from helpers import compile_entry_group
    group = compile_entry_group(f'''from packages import nodes

def shifted(v: Vector, __inline__={inline}):
    return nodes.vector_math(vector=v, operation="LENGTH", __inline__={inline})

def entry(count: Int=2):
    g = nodes.mesh_line(count=3, start_location=vector(0, 0, 0), offset=vector(1, 0, 0), __inline__={inline})
    delta = shifted(vector(0, 3, 4))
    for i in repeat_range(count):
        g = nodes.set_position(geometry=g, offset=vector(0, delta, 0), __inline__={inline})
    return g
''', 'GeneratedDirectEvaluation', 'entry')
    mesh = bpy.data.meshes.new('DirectEvalMesh')
    obj = bpy.data.objects.new('DirectEvalObject', mesh)
    bpy.context.collection.objects.link(obj)
    try:
        obj.modifiers.new('Evaluate', 'NODES').node_group = group
        evaluated = obj.evaluated_get(bpy.context.evaluated_depsgraph_get())
        result = evaluated.to_mesh()
        try:
            assert sorted(tuple(round(float(c), 5) for c in v.co) for v in result.vertices) == [(0, 10, 0), (1, 10, 0), (2, 10, 0)]
        finally:
            evaluated.to_mesh_clear()
    finally:
        bpy.data.objects.remove(obj, do_unlink=True)
        bpy.data.meshes.remove(mesh)


@pytest.mark.parametrize('mode', ['inline', 'grouped'])
def test_generated_collection_shapes_against_native_reference(bundled_nodes, monkeypatch, tmp_path, mode):
    """Compare all collection types and domains plus boundary-size labels and counts."""
    from pathlib import Path
    import sys
    tool = Path(__file__).resolve().parents[2] / 'tools/nodegen'
    monkeypatch.setattr(sys, 'path', [str(tool), str(tool / 'verify'), *sys.path])
    import build
    from validate_nodes import run_case
    spec = build.read_spec(build.DEFAULT_SPEC)
    report = build.generate(spec, ['GeometryNodeCaptureAttribute', 'GeometryNodeIndexSwitch', 'GeometryNodeMenuSwitch'], tmp_path)
    rows = []
    for item in report['wrappers']:
        kind = item['dynamic']['kind']
        for cfg in item['configurations']:
            if kind == 'capture':
                types = item['dynamic']['types']
                labels = ['Weight', 'weight', 'Two, Words: value', 'é'*31+'a', '方向', 'class']
                elements = list(zip(types,labels))
                variants = [([], None), (elements, None)]
            elif kind == 'menu':
                variants = [(['é'*31+'a'], 'é'*31+'a'), (['A','Two, Words','lower'], 'lower')]
            else:
                variants = [(0,None), (1,None), (16,None)]
            # Menu override and generated configuration can normalize separately;
            # each comparison is against a fresh independently configured node.
            for elements, menu in variants:
                for runtime in (False, True):
                    rows.append(run_case(item,cfg,mode,runtime,elements,menu))
    failures = [r for r in rows if r['status'] == 'failed']
    assert not failures, [(r['node'],r['state'],r['elements'],r.get('error')) for r in failures]


@pytest.mark.parametrize('semantic_state', [False, True])
@pytest.mark.parametrize('inline', [False, True])
@pytest.mark.parametrize('typ', ['Float', 'Vector', 'Geometry'])
def test_direct_dynamic_typed_forwarding_and_second_call_rollback(tmp_path, semantic_state, inline, typ):
    """A failing second occurrence rolls back both calls; retry forwards typed leaves."""
    from test_extension_boundary_matrix import _package
    from helpers import compile_group
    from NodeForge.errors import CompileError
    interface = f'''from dataclasses import dataclass
from typing import Annotated
from NodeForge import {typ}, String, DynamicResults, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {{'forward': '.backend:forward'}}
@dataclass(frozen=True)
class _State:
    value: {typ}
    form: str

def forward(value: Annotated[{typ}, EvaluationMode.RUNTIME_ONLY], form: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY] = 'DIRECT') -> DynamicResults: ...
'''
    semantic = f'''from NodeForge.nf_types import NFType

def _result_types_forward(args):
    return NFType.{typ.upper()}
'''
    if semantic_state:
        semantic += '''\nfrom .interface import _State

def forward(value, form='DIRECT') -> _State:
    return _State(value, form)
'''
    backend = ('def forward(context, state):\n    value, form = state.value, state.form\n' if semantic_state else
               "def forward(context, value, form='DIRECT'):\n")
    backend += '''    context.new_generated_mesh(role='forward-probe', name_hint='Probe')
    assert context.result_names is None
    return {'value': value} if form == 'BAD' else value
'''
    _package(tmp_path, name='typed_forward', interface=interface, semantic=semantic, backend=backend)
    source = f'''from packages import typed_forward

def main(value: {typ}):
    first = forward(value, __inline__={inline})
    second = forward(first, form='BAD', __inline__={inline})
    return second
'''
    groups = {g.as_pointer() for g in bpy.data.node_groups}
    meshes = {m.as_pointer() for m in bpy.data.meshes}
    with pytest.raises(CompileError, match='runtime leaves'):
        compile_group(source, 'DirectTypedRetry')
    assert {g.as_pointer() for g in bpy.data.node_groups} == groups
    assert {m.as_pointer() for m in bpy.data.meshes} == meshes
    group = compile_group(source.replace("form='BAD'", "form='DIRECT'"), 'DirectTypedRetry')
    output = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    socket = next(s for s in output.inputs if s.bl_idname != 'NodeSocketVirtual')
    assert socket.links[0].from_socket.bl_idname == 'NodeSocket' + typ
    assert all(link.is_valid for link in group.links)
    calls = [n for n in group.nodes if n.bl_idname == 'GeometryNodeGroup']
    assert len(calls) == (0 if inline else 2)


@pytest.mark.parametrize('semantic_state', [False, True])
@pytest.mark.parametrize('inline', [False, True])
def test_mixed_dynamic_chain_update_and_failure_retry(tmp_path, semantic_state, inline):
    """Preserve dependent direct/named links and evaluated geometry on update and retry."""
    from collections import Counter
    import json
    from NodeForge import compiler
    from NodeForge.errors import CompileError
    from helpers import compile_group
    from test_extension_boundary_matrix import _package
    from test_group_rollback_link_identity import _topology
    from test_update_group import _evaluate_group_geometry_vertices as evaluate

    events = tmp_path / 'calls.jsonl'
    interface = '''from dataclasses import dataclass
from typing import Annotated
from NodeForge import Float, String, DynamicResults, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {'choose': '.backend:choose'}
@dataclass(frozen=True)
class _State:
    value: Float
    form: str

def choose(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY], form: Annotated[String, EvaluationMode.COMPILE_TIME_ONLY]) -> DynamicResults: ...
'''
    common = f'''from pathlib import Path
import json
from NodeForge.nf_types import NFType

def record(event, form):
    with Path({str(events)!r}).open('a') as f:
        f.write(json.dumps([event, form]) + '\\n')
'''
    semantic = common + '''
def _result_types_choose(args):
    form = args['form'].value
    record('resolve', form)
    if form == 'ONE': return {'value': NFType.FLOAT}
    if form == 'MULTI': return {'value': NFType.FLOAT, 'original': NFType.FLOAT}
    return NFType.FLOAT
'''
    if semantic_state:
        semantic += '''
from .interface import _State

def choose(value, form) -> _State:
    record('semantic', form)
    return _State(value, form)
'''
    backend = common + 'from NodeForge.extension_api import ExtensionBackendValue\nimport bpy\n'
    backend += ('def choose(context, state):\n    value, form = state.value, state.form\n' if semantic_state else
                'def choose(context, value, form):\n')
    backend += '''    record('backend', form)
    context.new_generated_mesh(role='mixed-rollback', name_hint='Probe')
    node = context.group.nodes.new('ShaderNodeMath')
    node.operation = 'MULTIPLY'
    context.group.links.new(value.socket, node.inputs[0])
    node.inputs[1].default_value = 2.0
    result = context.value(node.outputs[0], NFType.FLOAT)
    if form == 'ONE':
        assert context.result_names == ('value',)
        return {'value': result}
    if form == 'MULTI':
        assert context.result_names == ('value', 'original')
        return {'original': value, 'value': result}
    assert context.result_names is None
    if form == 'BAD_DICT': return {'value': result}
    if form == 'BAD_LIST': return [result]
    if form == 'BAD_TYPE': return ExtensionBackendValue(node.outputs[0], NFType.INT)
    if form == 'BAD_INPUT': return ExtensionBackendValue(node.inputs[0], NFType.FLOAT)
    if form == 'BAD_PHYSICAL':
        other = context.group.nodes.new('GeometryNodeInputPosition')
        return ExtensionBackendValue(other.outputs[0], NFType.FLOAT)
    if form == 'BAD_FOREIGN':
        return ExtensionBackendValue(bpy.data.node_groups['NFTest_mixed_foreign'].nodes[0].outputs[0], NFType.FLOAT)
    return result
'''
    _package(tmp_path, name='mixed_rollback', interface=interface, semantic=semantic, backend=backend)
    source = f'''from packages import mixed_rollback

def main():
    a = choose(3.0, form="DIRECT", __inline__={inline})
    one = choose(4.0, form="ONE", __inline__={inline})
    multi = choose(one.value, form="MULTI", __inline__={inline})
    alias = one
    return {{"geometry": point(vector(a + alias.value, multi.value, multi.original))}}
'''
    foreign = bpy.data.node_groups.new('NFTest_mixed_foreign', 'GeometryNodeTree')
    foreign.nodes.new('ShaderNodeMath')
    group = compile_group(source, 'MixedDynamicRollback')
    expected = Counter((event, form) for event in ('resolve', 'backend') + (('semantic',) if semantic_state else ())
                       for form in ('DIRECT', 'ONE', 'MULTI'))
    assert Counter(tuple(json.loads(line)) for line in events.read_text().splitlines()) == expected

    def snapshot():
        """Compare live topology, owned resources and evaluated output, not RNA handles."""
        vertices = evaluate(group, 'mixed_dynamic_rollback')
        return (group.as_pointer(), _topology(group), vertices,
                {g.as_pointer() for g in bpy.data.node_groups},
                {m.as_pointer() for m in bpy.data.meshes})

    before = snapshot()
    assert before[2] == [(14., 16., 8.)]
    compiler.update_expression_group(group, source)
    assert snapshot() == before
    for bad in ('BAD_DICT', 'BAD_LIST', 'BAD_TYPE', 'BAD_INPUT', 'BAD_PHYSICAL', 'BAD_FOREIGN'):
        invalid = source.replace('form="ONE"', f'form={bad!r}').replace('choose(one.value,', 'choose(one,').replace('a + alias.value', 'a + alias')
        events.write_text('')
        with pytest.raises(CompileError):
            compiler.update_expression_group(group, invalid)
        assert ['backend', bad] in [json.loads(line) for line in events.read_text().splitlines()]
        assert snapshot() == before
        compiler.update_expression_group(group, source)
        assert snapshot() == before
    invalid = source.replace('form="MULTI"', 'form="BAD_DICT"').replace('multi.value, multi.original', 'multi, one.value')
    events.write_text('')
    with pytest.raises(CompileError):
        compiler.update_expression_group(group, invalid)
    assert ['backend', 'BAD_DICT'] in [json.loads(line) for line in events.read_text().splitlines()]
    assert snapshot() == before
    compiler.update_expression_group(group, source)
    assert snapshot() == before


@pytest.mark.parametrize('inline', [True, False])
def test_structured_capture_mixed_defaults_and_evaluated_geometry(bundled_nodes, inline):
    """Exact keys, selected types and per-leaf defaults survive grouped materialization."""
    from helpers import compile_group
    from test_update_group import _evaluate_group_geometry_vertices as evaluate
    root = compile_group(f'''from packages import nodes

def main():
    geo = nodes.mesh_line(count=3, start_location=vector(0,0,0), offset=vector(1,0,0))
    v = vector(1,2,3)
    r = nodes.capture_attribute(geometry=geo, elements={{"X, y: z": position().x, "weight": 0.75, "rest": v, "selected": index() > 0, "tint": (0.25, 0.5, 0.75, 1.0), "idx": index()}}, __inline__={inline})
    return nodes.set_position(geometry=r.geometry, position=vector(r["X, y: z"], r.weight, r.idx))
''', 'StructuredCaptureEvaluation')
    groups = [root] + [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
    capture, = [n for g in groups for n in g.nodes if n.bl_idname == 'GeometryNodeCaptureAttribute']
    assert [i.name for i in capture.capture_items] == ['X, y: z','weight','rest','selected','tint','idx']
    assert [i.data_type for i in capture.capture_items] == ['FLOAT','FLOAT','FLOAT_VECTOR','BOOLEAN','FLOAT_COLOR','INT']
    assert [s.is_linked for s in list(capture.inputs)[2:-1]] == [True,False,False,True,False,True]
    assert capture.inputs['weight'].default_value == .75
    assert tuple(capture.inputs['rest'].default_value) == (1.,2.,3.)
    assert tuple(capture.inputs['tint'].default_value) == (.25,.5,.75,1.)
    assert not any(n.bl_idname == 'ShaderNodeValue' for n in capture.id_data.nodes)
    assert sorted(evaluate(root,'StructuredCaptureEval')) == [(0.,.75,0.),(1.,.75,1.),(2.,.75,2.)]


@pytest.mark.parametrize('inline', [True, False])
def test_structured_menu_ct_list_and_omitted_defaults(bundled_nodes, inline):
    """A compile-time list preserves comma/colon labels and unprovided native defaults."""
    from helpers import compile_group
    root = compile_group(f'''from packages import nodes

def main(x: Float):
    labels = ["A, x", "B: y", "Third"]
    r = nodes.menu_switch(x, 0.75, elements=labels, menu="B: y", data_type="FLOAT", __inline__={inline})
    return {{"value":r.output, "chosen":r["B: y"]}}
''', 'StructuredMenuLabels')
    groups = [root] + [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
    menu, = [n for g in groups for n in g.nodes if n.bl_idname == 'GeometryNodeMenuSwitch']
    assert [it.name for it in menu.enum_items] == ['A, x','B: y','Third']
    assert menu.inputs['Menu'].default_value == 'B: y'
    assert menu.inputs['A, x'].is_linked and not menu.inputs['B: y'].is_linked
    assert menu.inputs['B: y'].default_value == .75
    assert not menu.inputs['Third'].is_linked


@pytest.mark.parametrize('inline', [True, False])
def test_collection_oracle_rejects_same_type_input_swap(bundled_nodes, monkeypatch, inline):
    """Independent input provenance detects equal-typed operands routed to wrong items."""
    import sys
    from pathlib import Path
    from helpers import compile_group
    tool = Path(__file__).resolve().parents[2] / 'tools/nodegen'
    monkeypatch.setattr(sys, 'path', [str(tool), str(tool / 'verify'), *sys.path])
    from validate_nodes import check_input_links
    root = compile_group(f'''from packages import nodes

def main(p0: Float, p1: Float):
    r = nodes.capture_attribute(elements={{"left":p0, "right":p1}}, __inline__={inline})
    return {{"left":r.left, "right":r.right}}
''', 'CollectionInputOracle')
    child = root if inline else next(n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup')
    native = next(n for n in child.nodes if n.bl_idname == 'GeometryNodeCaptureAttribute')
    slots = list(native.inputs)[2:-1]
    expected = [(s.identifier,s.name,s.bl_idname,f'p{i}') for i,s in enumerate(slots)]
    assert check_input_links(root,native,expected,grouped=not inline)
    sources = [s.links[0].from_socket for s in slots]
    for socket in slots:
        child.links.remove(socket.links[0])
    for socket,source in zip(slots,reversed(sources)):
        child.links.new(source,socket)
    with pytest.raises(AssertionError,match='Input provenance mismatch'):
        check_input_links(root,native,expected,grouped=not inline)


@pytest.mark.parametrize('inline', [True, False])
@pytest.mark.parametrize('data_type,decl,values,expected', [
    ('RGBA', '', '(0.25,0.5,0.75,1.0)', (.25,.5,.75,1.)),
    ('VECTOR', '', 'vector(1,2,3)', (1.,2.,3.)),
    ('ROTATION', 'rotation: Rotation', 'rotation', None),
])
def test_structured_menu_selected_input_types(bundled_nodes, inline, data_type, decl, values, expected):
    """Changing the label carrier preserves ordinary selected Color/Vector/Rotation delivery."""
    from helpers import compile_group
    root = compile_group(f'''from packages import nodes

def main({decl}):
    r = nodes.menu_switch({values}, elements=["One"], menu="One", data_type="{data_type}", __inline__={inline})
    return r.output
''', 'StructuredMenuTyped')
    groups = [root] + [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
    menu, = [n for g in groups for n in g.nodes if n.bl_idname == 'GeometryNodeMenuSwitch']
    socket = menu.inputs['One']
    if expected is None:
        assert socket.is_linked and socket.bl_idname == 'NodeSocketRotation'
    else:
        assert not socket.is_linked and tuple(socket.default_value) == expected
