"""Public root Compile publishes Python backends only through private candidates."""

import tempfile
from pathlib import Path

import bpy
import pytest

from NodeForge import compiler, packages
from NodeForge.errors import CompileError
from NodeForge.blender.generated_resources import read_group_manifest
from NodeForge.blender.extension_group_materializer import EXTENSION_AUTHORITY_PROP
from NodeForge.function_instances import COMPOSITION_ROOT_ID_PROP
from test_extension_dynamic_results import _write_package


def _package(temp, *, bad=''):
    directory = Path(temp) / 'package'
    _write_package(directory, bad=bad)
    packages.install_package_directory(directory)


def test_root_inline_dynamic_backend_publishes_without_append_and_stable_identity():
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp)
        root = bpy.data.node_groups.new('NFExt_public_inline', 'GeometryNodeTree')
        old = root.nodes.new('GeometryNodeMeshCube')
        code = 'from packages import dynamic\nr = foo(1.0)\na = r.double\nb = r.original\n'
        first = compiler.compile_source_in_tree(code, root)
        assert first.outcome == 'ROOT_REPLACEMENT'
        assert not any(n.bl_idname == 'GeometryNodeMeshCube' for n in root.nodes)
        assert sum(n.bl_idname == 'ShaderNodeMath' for n in root.nodes) == 1
        assert not any(n.bl_idname == 'GeometryNodeGroup' for n in root.nodes)
        root_uuid = root[COMPOSITION_ROOT_ID_PROP]
        manifest = read_group_manifest(root)
        assert manifest['owner_group_uuid'] == root_uuid
        assert len(manifest['resources']) == 1
        root.name = 'NFExt_public_inline_renamed'
        second = compiler.compile_source_in_tree(code, root)
        assert second.outcome == 'ROOT_REPLACEMENT'
        assert root[COMPOSITION_ROOT_ID_PROP] == root_uuid
        assert len([n for n in root.nodes if n.bl_idname == 'ShaderNodeMath']) == 1
        assert len(read_group_manifest(root)['resources']) == 1


def test_root_grouped_extension_has_per_root_authority_and_preserves_manual_input():
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp)
        roots = [bpy.data.node_groups.new('NFExt_public_grouped'+str(i), 'GeometryNodeTree') for i in range(2)]
        for root in roots:
            root.interface.new_socket(name='Value', in_out='INPUT', socket_type='NodeSocketFloat')
            inlet = root.nodes.new('NodeGroupInput')
            inlet.label = 'Keep input'
        code = 'from packages import dynamic\nr = foo(1.0, __inline__=False)\na = r.double\nb = r.original\n'
        first = []
        for root in roots:
            compiler.compile_source_in_tree(code, root, module_context='text:same')
            groups = [n for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
            assert len(groups) == 1
            assert [n.label for n in root.nodes if n.bl_idname == 'NodeGroupInput'] == ['Keep input']
            first.append(groups[0].node_tree)
        assert first[0].as_pointer() != first[1].as_pointer()
        assert first[0][EXTENSION_AUTHORITY_PROP] != first[1][EXTENSION_AUTHORITY_PROP]
        for root, prior in zip(roots, first):
            compiler.compile_source_in_tree(code, root, module_context='text:same')
            group, = [n.node_tree for n in root.nodes if n.bl_idname == 'GeometryNodeGroup']
            assert group.as_pointer() == prior.as_pointer()


def test_failed_root_backend_keeps_old_root_and_no_owner_stamp():
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp, bad='short')
        root = bpy.data.node_groups.new('NFExt_public_backend_failure', 'GeometryNodeTree')
        manual = root.nodes.new('GeometryNodeMeshCube')
        pointer = manual.as_pointer()
        before_groups = {g.as_pointer() for g in bpy.data.node_groups}
        before_meshes = {m.as_pointer() for m in bpy.data.meshes}
        with pytest.raises(CompileError, match='exact result keys'):
            compiler.compile_source_in_tree('from packages import dynamic\nr = foo(1.0)\na = r.double\nb = r.original', root)
        assert root.nodes[0].as_pointer() == pointer and len(root.nodes) == 1
        assert COMPOSITION_ROOT_ID_PROP not in root
        assert {g.as_pointer() for g in bpy.data.node_groups} == before_groups
        assert {m.as_pointer() for m in bpy.data.meshes} == before_meshes


def test_groups_only_after_root_extension_keeps_root_and_manifest_unchanged():
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp)
        root = bpy.data.node_groups.new('NFExt_group_only_after_root', 'GeometryNodeTree')
        compiler.compile_source_in_tree('from packages import dynamic\nr = foo(1.0)\na = r.double\nb = r.original', root)
        before_nodes = tuple(n.as_pointer() for n in root.nodes)
        before_manifest = root.get('nodeforge_generated_resources_manifest_v1')
        before_uuid = root[COMPOSITION_ROOT_ID_PROP]
        assert compiler.compile_source_in_tree('FLAG = False', root).outcome == 'GROUPS_ONLY'
        assert tuple(n.as_pointer() for n in root.nodes) == before_nodes
        assert root[COMPOSITION_ROOT_ID_PROP] == before_uuid
        assert root.get('nodeforge_generated_resources_manifest_v1') == before_manifest


def test_transfer_failure_leaves_manual_graph_and_discards_attempt_nodes(monkeypatch):
    from NodeForge.blender import root_subgraph_transfer, root_update
    root = bpy.data.node_groups.new('NFExt_root_transfer_failure', 'GeometryNodeTree')
    old = root.nodes.new('GeometryNodeMeshCube')
    old_pointer = old.as_pointer()
    before = {group.as_pointer() for group in bpy.data.node_groups}
    original = root_subgraph_transfer._copy_node_properties
    calls = 0

    def fail_second(src, dst, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 2:
            raise RuntimeError('Extension copier failed after one node')
        return original(src, dst, **kwargs)

    monkeypatch.setattr(root_subgraph_transfer, '_copy_node_properties', fail_second)
    with pytest.raises(root_update.RootUpdateError, match='Extension copier failed') as info:
        compiler.compile_source_in_tree('a = cube(1)\nb = cube(2)', root)
    assert not info.value.may_have_mutated
    assert len(root.nodes) == 1 and root.nodes[0].as_pointer() == old_pointer
    assert COMPOSITION_ROOT_ID_PROP not in root
    assert {group.as_pointer() for group in bpy.data.node_groups} == before


def test_root_backend_can_clear_private_candidate_without_touching_manual_input(tmp_path):
    """An absent, unrequested Group Input is not a backend-visible root copy."""
    from test_unified_runtime_capability_preflight import _install_capability_fixture
    _install_capability_fixture(tmp_path)
    root = bpy.data.node_groups.new('NFExt_root_manual_input_not_cloned', 'GeometryNodeTree')
    root.interface.new_socket(name='Value', in_out='INPUT', socket_type='NodeSocketFloat')
    inlet = root.nodes.new('NodeGroupInput')
    old = root.nodes.new('GeometryNodeMeshCube')
    before = inlet.as_pointer()
    result = compiler.compile_source_in_tree('from packages import danger\nx = unsafe_wipe(1.0)', root)
    assert result.outcome == 'ROOT_REPLACEMENT'
    assert inlet.as_pointer() == before
    assert inlet in root.nodes[:]
    assert old not in root.nodes[:]
    assert sum(node.bl_idname == 'NodeGroupInput' for node in root.nodes) == 1


def test_postcommit_root_resource_cleanup_exception_keeps_undo_signal(monkeypatch):
    import warnings
    from NodeForge.blender import generated_resources, root_update
    with tempfile.TemporaryDirectory() as tmp:
        _package(tmp)
        root = bpy.data.node_groups.new('NFExt_postcommit_root_resource', 'GeometryNodeTree')
        compiler.compile_source_in_tree('from packages import dynamic\nr = foo(1.0)\na = r.double\nb = r.original', root)
        uuid_before = root[COMPOSITION_ROOT_ID_PROP]
        old_manifest = read_group_manifest(root)
        assert old_manifest and old_manifest['resources']

        def fail_retirement(*args, **kwargs):
            raise RuntimeError('Extension failed after root publication')

        monkeypatch.setattr(root_update, 'cleanup_previous_after_commit', fail_retirement, raising=False)
        # Imported inside replace_prepared_root: patch the actual owner module.
        monkeypatch.setattr(generated_resources, 'cleanup_previous_after_commit', fail_retirement)
        with warnings.catch_warnings():
            warnings.simplefilter('error', RuntimeWarning)
            with pytest.raises(root_update.RootUpdateError, match='postcommit cleanup') as info:
                compiler.compile_source_in_tree('a = cube(1)', root)
        assert info.value.may_have_mutated
        assert root[COMPOSITION_ROOT_ID_PROP] == uuid_before
        assert len([n for n in root.nodes if n.bl_idname == 'GeometryNodeMeshCube']) == 1
        assert not read_group_manifest(root)['resources']


def test_groups_only_before_first_root_compile_does_not_stamp_composition_identity():
    root = bpy.data.node_groups.new('NFExt_root_group_only_no_identity', 'GeometryNodeTree')
    manual = root.nodes.new('GeometryNodeMeshCube')
    before = manual.as_pointer()
    assert compiler.compile_source_in_tree('FLAG = False', root).outcome == 'GROUPS_ONLY'
    assert COMPOSITION_ROOT_ID_PROP not in root
    assert root.nodes[0].as_pointer() == before


@pytest.mark.parametrize('inline', [True, False])
@pytest.mark.parametrize('kind,expr,expected', [
    ('GeometryNodeMenuSwitch', 'nodes.menu_switch(1.0, 2.0, 3.0, elements=["One","Two","Three"], menu="Three", data_type="FLOAT", __inline__={inline}).output', (1., 2., 3.)),
    ('GeometryNodeIndexSwitch', 'nodes.index_switch(1.0, 2.0, 3.0, elements=3, index=2, data_type="FLOAT", __inline__={inline})', (1., 2., 3.)),
    ('GeometryNodeCaptureAttribute', 'nodes.capture_attribute(geometry=cube(1), elements={{"Geometry": 0.75, "Selection": True}}, __inline__={inline})', (.75, True)),
])
def test_standalone_collection_root_and_group_recompilation(inline, kind, expr, expected):
    """First root transfer and equivalent group updates keep collection defaults and slots."""
    from test_update_group import _evaluate_group_geometry_vertices as evaluate
    packages.install_package_directory(Path(__file__).resolve().parents[2] / 'packages' / 'nodes')
    call = expr.format(inline=inline)
    if kind == 'GeometryNodeCaptureAttribute':
        # Capture is a field evaluated on its own geometry, so use Set Position.
        tail = 'geo = set_position(r.geometry, vector(r.Geometry, 0.0, 0.0))'
    else:
        tail = 'geo = point(vector(r, 0.0, 0.0))'
    # Use the root compile route independently of standalone function publication.
    root = bpy.data.node_groups.new('NF_collection_root', 'GeometryNodeTree')
    root_code = f'from packages import nodes\nr = {call}\n{tail}\n'
    function_code = f'from packages import nodes\n\ndef main():\n    r = {call}\n    {tail}\n    return {{"geometry": geo}}\n'

    def inspect(tree):
        """Read actual defaults and physical slot identities, not wrapper metadata."""
        children = [node.node_tree for node in tree.nodes if node.bl_idname == 'GeometryNodeGroup']
        native, = [node for candidate in [tree, *children] for node in candidate.nodes if node.bl_idname == kind]
        offset = 2 if kind == 'GeometryNodeCaptureAttribute' else 1
        assert tuple(socket.default_value for socket in list(native.inputs)[offset:-1]) == expected
        if kind == 'GeometryNodeMenuSwitch':
            assert tuple(item.name for item in native.enum_items) == ('One', 'Two', 'Three')
            assert native.inputs[0].default_value == 'Three'
        elif kind == 'GeometryNodeIndexSwitch':
            assert len(native.index_switch_items) == 3
            assert native.inputs[0].default_value == 2
        else:
            assert [(item.name, item.data_type) for item in native.capture_items] == [('Geometry','FLOAT'), ('Selection','BOOLEAN')]
            assert native.inputs[1].default_value is True
        return native.id_data.as_pointer()

    compiler.compile_source_in_tree(root_code, root)
    root_owner = inspect(root)
    compiler.compile_source_in_tree(root_code, root)
    assert inspect(root) == root_owner
    group = compiler.create_expression_group(function_code, 'NF_collection_group')
    owner = inspect(group)
    vertices = evaluate(group, 'nf_collection_before')
    assert vertices and all(v == ((.75, 0., 0.) if kind == 'GeometryNodeCaptureAttribute' else (3., 0., 0.)) for v in vertices)
    compiler.update_expression_group(group, function_code)
    assert inspect(group) == owner
    assert evaluate(group, 'nf_collection_after') == vertices


@pytest.mark.parametrize('savepoint', [False, True])
def test_collection_transaction_restores_changed_child_and_parent(savepoint):
    """Rollback of a real parent cutover preserves collection values and group inventory."""
    from NodeForge.blender.group_backend import BlenderGroupBuildTransaction
    from helpers import prepared_create_or_update
    from test_update_group import _evaluate_group_geometry_vertices as evaluate
    packages.install_package_directory(Path(__file__).resolve().parents[2] / 'packages' / 'nodes')
    source = '''from packages import nodes

def main():
    first = nodes.menu_switch(1.0, 2.0, 3.0, data_type="FLOAT", elements=["One","Two","Three"], menu="Three", __inline__=False).output
    second = nodes.index_switch(1.0, first, 2.0, data_type="FLOAT", elements=3, index=1, __inline__=False)
    return {"geometry": point(vector(first, second, 0.0))}
'''
    group = compiler.create_expression_group(source,'NF_collection_tx')
    before = evaluate(group,'collection_tx_before')
    assert before == [(3.,3.,0.)]
    inventory = {g.as_pointer() for g in bpy.data.node_groups}
    changed = source.replace('2.0, 3.0','2.0, 4.0')
    tx = BlenderGroupBuildTransaction()
    checkpoint = tx.savepoint()
    prepared_create_or_update(changed,group.name,existing_group=group,build_options={'local_helper_transaction':tx})
    assert evaluate(group,'collection_tx_changed') == [(4.,4.,0.)]
    if savepoint:
        tx.rollback_to_savepoint(checkpoint)
        tx.commit()
    else:
        tx.rollback()
    assert evaluate(group,'collection_tx_restored') == before
    assert inventory == {g.as_pointer() for g in bpy.data.node_groups}
    compiler.update_expression_group(group,changed)
    assert evaluate(group,'collection_tx_retry') == [(4.,4.,0.)]


def test_collection_save_reopen_and_recompile(tmp_path):
    """Reopened grouped Menu/Index graphs preserve selected labels and values on update."""
    import subprocess
    project = Path(__file__).resolve().parents[2]
    script = tmp_path / 'collection_reopen.py'
    script.write_text(f'''import sys
from pathlib import Path
sys.path.insert(0, {str(project.parent)!r})
import bpy
from NodeForge import compiler, packages
packages.set_packages_dir_for_tests(Path({str(tmp_path / 'packages-subprocess')!r}))
packages.install_package_directory(Path({str(project / 'packages/nodes')!r}))
source = """from packages import nodes

def main():
    a = nodes.menu_switch(1.0, 2.0, 3.0, elements=['One','Two','Three'], menu='Three', data_type='FLOAT', __inline__=False).output
    b = nodes.index_switch(1.0, a, 2.0, elements=3, index=1, data_type='FLOAT', __inline__=False)
    return {{'geometry': point(vector(a,b,0.0))}}
"""
group = compiler.create_expression_group(source,'NF_collection_reopen')
group.use_fake_user = True
assert bpy.ops.wm.save_as_mainfile(filepath={str(tmp_path/'collection.blend')!r}) == {{'FINISHED'}}
assert bpy.ops.wm.open_mainfile(filepath={str(tmp_path/'collection.blend')!r}) == {{'FINISHED'}}
group=bpy.data.node_groups['NF_collection_reopen']
children=[n.node_tree for n in group.nodes if n.bl_idname=='GeometryNodeGroup']
identities={{g.as_pointer() for g in children}}
compiler.update_expression_group(group,source)
children=[n.node_tree for n in group.nodes if n.bl_idname=='GeometryNodeGroup']
assert identities=={{g.as_pointer() for g in children}}
menu,=[n for g in children for n in g.nodes if n.bl_idname=='GeometryNodeMenuSwitch']
index,=[n for g in children for n in g.nodes if n.bl_idname=='GeometryNodeIndexSwitch']
assert [i.name for i in menu.enum_items]==['One','Two','Three'] and menu.inputs[0].default_value=='Three'
assert len(index.index_switch_items)==3 and index.inputs[2].is_linked
''', encoding='utf-8')
    proc = subprocess.run([bpy.app.binary_path,'-b','-t','1','--factory-startup','--python-exit-code','1','--python',str(script)], capture_output=True,text=True,timeout=60)
    assert proc.returncode == 0, proc.stdout + proc.stderr
