"""Two-process `.blend` round-trip for root interface and external references.

Set NODEFORGE_ROOT_ROUNDTRIP_MODE=create|verify, FILE and REPORT environment vars.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys

import bpy

sys.path.insert(0, str(Path(__file__).resolve().parents[3]))

from NodeForge.compiler import compile_source_in_tree


FILE = Path(os.environ['NODEFORGE_ROOT_ROUNDTRIP_FILE'])
REPORT = Path(os.environ['NODEFORGE_ROOT_ROUNDTRIP_REPORT'])
ROOT_NAME = 'NFR5_roundtrip_root'
OBJECT_NAME = 'NFR5_roundtrip_owner'
OUTER_NAME = 'NFR5_roundtrip_outer'


def _interface(group):
    """Compare stable interface IDs, order and scalar defaults across reload."""
    return [(i.item_type, i.name, getattr(i, 'identifier', ''),
             getattr(i, 'in_out', ''), getattr(i, 'socket_type', ''),
             getattr(i, 'default_value', None))
            for i in group.interface.items_tree]


def _create():
    """Replace a linked manual root and write the authoritative datablock to disk."""
    group = bpy.data.node_groups.new(ROOT_NAME, 'GeometryNodeTree')
    group['ordinary_user_prop'] = 'persist'
    group.interface.new_socket(name='In', in_out='INPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Geometry', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    group.interface.new_socket(name='Passthrough', in_out='OUTPUT', socket_type='NodeSocketGeometry')
    panel = group.interface.new_panel('Extra')
    gain = group.interface.new_socket(name='Gain', in_out='INPUT', socket_type='NodeSocketFloat', parent=panel)
    gain.default_value = 3.5
    gin = group.nodes.new('NodeGroupInput')
    gout = group.nodes.new('NodeGroupOutput')
    gout.is_active_output = True
    group.links.new(gin.outputs['In'], gout.inputs['Passthrough'])
    old = group.nodes.new('GeometryNodeMeshCube')
    group.links.new(old.outputs['Mesh'], gout.inputs['Geometry'])
    owner = bpy.data.objects.new(OBJECT_NAME, bpy.data.meshes.new('NFR5_roundtrip_mesh'))
    bpy.context.scene.collection.objects.link(owner)
    mod = owner.modifiers.new('NodeForge', 'NODES')
    mod.node_group = group
    if hasattr(mod, 'properties') and hasattr(mod.properties, 'inputs'):
        getattr(mod.properties.inputs, gain.identifier).value = 9.25
    outer = bpy.data.node_groups.new(OUTER_NAME, 'GeometryNodeTree')
    outer.use_fake_user = True
    external = outer.nodes.new('GeometryNodeGroup')
    external.node_tree = group
    before = _interface(group)
    result = compile_source_in_tree('a = cube(4)\n', group)
    assert result.skipped_boundary_links == 0
    assert _interface(group) == before
    assert mod.node_group.as_pointer() == group.as_pointer()
    assert external.node_tree.as_pointer() == group.as_pointer()
    assert sum(n.bl_idname == 'GeometryNodeMeshCube' for n in group.nodes) == 1
    state = {'interface': before, 'root_name': group.name, 'root_user_prop': group['ordinary_user_prop'],
             'gain_identifier': gain.identifier, 'geometry_output': bool(gout.inputs['Geometry'].is_linked)}
    REPORT.write_text(json.dumps(state, indent=2))
    assert bpy.ops.wm.save_as_mainfile(filepath=str(FILE)) == {'FINISHED'}
    print('ROOT_UPDATE_SAVE_PASS')


def _verify():
    """Reload a saved root with unchanged modifier/external Group Node users."""
    state = json.loads(REPORT.read_text())
    group = bpy.data.node_groups[ROOT_NAME]
    assert _interface(group) == [tuple(row) for row in state['interface']]
    assert group['ordinary_user_prop'] == state['root_user_prop']
    owner = bpy.data.objects[OBJECT_NAME]
    modifier = owner.modifiers.get('NodeForge')
    assert modifier is not None and modifier.node_group.as_pointer() == group.as_pointer()
    if hasattr(modifier, 'properties') and hasattr(modifier.properties, 'inputs'):
        assert getattr(modifier.properties.inputs, state['gain_identifier']).value == 9.25
    outer = bpy.data.node_groups[OUTER_NAME]
    assert any(node.node_tree.as_pointer() == group.as_pointer() for node in outer.nodes
               if node.bl_idname == 'GeometryNodeGroup')
    assert sum(node.bl_idname == 'GeometryNodeMeshCube' for node in group.nodes) == 1
    assert len([node for node in group.nodes if node.bl_idname == 'NodeGroupInput']) == 1
    assert len([node for node in group.nodes if node.bl_idname == 'NodeGroupOutput']) == 1
    gin = next(n for n in group.nodes if n.bl_idname == 'NodeGroupInput')
    gout = next(n for n in group.nodes if n.bl_idname == 'NodeGroupOutput')
    assert any(link.from_node.as_pointer() == gin.as_pointer() and link.to_node.as_pointer() == gout.as_pointer()
               for link in group.links)
    assert gout.inputs['Geometry'].is_linked
    print('ROOT_UPDATE_REOPEN_PASS')


if os.environ['NODEFORGE_ROOT_ROUNDTRIP_MODE'] == 'create':
    _create()
else:
    _verify()
