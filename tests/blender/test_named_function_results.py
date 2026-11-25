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
