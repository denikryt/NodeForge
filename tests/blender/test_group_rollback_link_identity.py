"""Regression: nested group restoration must resolve live nodes and sockets."""
from itertools import product
from pathlib import Path

import bpy
import pytest

from NodeForge import compiler, packages
from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
from helpers import prepared_create_or_update
from test_update_group import _evaluate_group_geometry_vertices as evaluate


@pytest.fixture
def nodes_package():
    """Install the generated Nodes sources into the isolated inventory."""
    packages.install_package_directory(Path(__file__).resolve().parents[2] / 'packages' / 'nodes')


def _source(a, b, c, multiplier):
    """Build three dependent calls with independently selected materialization modes."""
    return f'''from packages import nodes

def main():
    a = nodes.math(value=2.0, value_001=3.0, operation="ADD", __inline__={a})
    b = nodes.math(value=a, value_001={multiplier}, operation="MULTIPLY", __inline__={b})
    c = nodes.math(value=a, value_001=b, operation="ADD", __inline__={c})
    return {{"geometry": point(vector(a, b, c))}}
'''


def _topology(tree):
    """Compare socket occurrences; interface identifiers may be regenerated."""
    return sorted((l.from_node.name, l.from_socket.name,
                   list(l.from_node.outputs).index(l.from_socket),
                   l.to_node.name, l.to_socket.name,
                   list(l.to_node.inputs).index(l.to_socket)) for l in tree.links)


@pytest.mark.parametrize('a,b,c', list(product((False, True), repeat=3)))
def test_equivalent_group_chain_update_preserves_geometry(nodes_package, a, b, c):
    """Repeated equivalent and changed updates preserve actual geometry and topology."""
    group = compiler.create_expression_group(_source(a, b, c, 4.0), 'RollbackChain')
    pointer = group.as_pointer()
    before = _topology(group)
    identities = {g.as_pointer() for g in bpy.data.node_groups}
    assert evaluate(group, 'rollback_chain') == [(5., 20., 25.)]
    for multiplier in (4., 4., 6., 6., 4., 4.):
        compiler.update_expression_group(group, _source(a, b, c, multiplier))
        assert evaluate(group, 'rollback_chain') == [(5., 5.*multiplier, 5.+5.*multiplier)]
        assert group.as_pointer() == pointer
        assert _topology(group) == before
        assert {g.as_pointer() for g in bpy.data.node_groups} == identities


@pytest.mark.parametrize('savepoint', [False, True])
def test_rollback_after_parent_cutover_resolves_recreated_nodes(nodes_package, savepoint):
    """A parent backup recreates nodes before child mutations are restored."""
    group = compiler.create_expression_group(_source(False, False, False, 4.), 'RollbackParent')
    # External child-group user must retain its overridden default and outgoing link.
    child_node = next(n for n in group.nodes if n.bl_idname == 'GeometryNodeGroup'
                      and len(n.inputs) == 1 and n.inputs[0].is_linked)
    child = child_node.node_tree
    wrapper = bpy.data.node_groups.new('RollbackExternalUser', 'GeometryNodeTree')
    instance = wrapper.nodes.new('GeometryNodeGroup')
    instance.node_tree = child
    instance.inputs[0].default_value = 7.0
    math = wrapper.nodes.new('ShaderNodeMath')
    wrapper.links.new(instance.outputs[0], math.inputs[1])
    before = _topology(group)
    external = _topology(wrapper)
    identities = {g.as_pointer() for g in bpy.data.node_groups}
    pointer = group.as_pointer()
    assert evaluate(group, 'rollback_parent') == [(5.,20.,25.)]
    tx = BlenderGroupBuildTransaction()
    checkpoint = tx.savepoint()
    prepared_create_or_update(_source(False, False, False, 6.), group.name,
                              existing_group=group,
                              build_options={'local_helper_transaction': tx})
    assert evaluate(group, 'rollback_parent') == [(5.,30.,35.)]
    if savepoint:
        tx.rollback_to_savepoint(checkpoint)
        tx.commit()
    else:
        tx.rollback()
    assert evaluate(group, 'rollback_parent') == [(5.,20.,25.)]
    assert _topology(group) == before
    assert _topology(wrapper) == external
    assert instance.node_tree == child
    assert instance.inputs[0].default_value == 7.0
    assert group.as_pointer() == pointer
    assert {g.as_pointer() for g in bpy.data.node_groups} == identities
    compiler.update_expression_group(group, _source(False, False, False, 4.))
    assert evaluate(group, 'rollback_parent') == [(5.,20.,25.)]
