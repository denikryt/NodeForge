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
