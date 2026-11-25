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
    ('sample_index(data_type="FLOAT")', 'value', 'GeometryNodeSampleIndex'),
    ('cylinder()', 'mesh', 'GeometryNodeMeshCylinder'),
    ('sample_curve(data_type="FLOAT_VECTOR")', 'value', 'GeometryNodeSampleCurve'),
    ('named_attribute(data_type="FLOAT")', 'attribute', 'GeometryNodeInputNamedAttribute'),
    ('capture_attribute(elements="FLOAT:Weight")', 'Weight', 'GeometryNodeCaptureAttribute'),
    ('menu_switch(elements="A,lower", menu="lower", data_type="FLOAT")', 'lower', 'GeometryNodeMenuSwitch'),
    ('index_switch(elements=3, data_type="FLOAT")', 'output', 'GeometryNodeIndexSwitch'),
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


@pytest.mark.parametrize('call', [
    'capture_attribute(elements="FLOAT:geometry")',
    'capture_attribute(elements="FLOAT:a,FLOAT:a")',
    'capture_attribute(elements="FLOAT:")',
    'capture_attribute(elements="FLOAT:' + 'x'*64 + '")',
    'menu_switch(elements="output,B", menu="B")',
    'menu_switch(elements="", menu="A")',
    'menu_switch(elements="A,A", menu="A")',
])
def test_generated_invalid_collection_schema_is_rejected_before_publication(bundled_nodes, call):
    """Invalid explicit keys and grammar fail before any native group is published."""
    from helpers import compile_group
    from NodeForge.errors import CompileError
    before = {g.as_pointer() for g in bpy.data.node_groups}
    with pytest.raises(CompileError, match='Dynamic result resolver'):
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
