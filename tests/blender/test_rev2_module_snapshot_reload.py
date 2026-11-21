"""Rev2 F03/F05/F10: selected sources use their own final CT revision."""

from helpers import *
from NodeForge import local_sources
from NodeForge.function_instances import FUNCTION_INSTANCE_KEY_PROP
from test_module_compile_time_fingerprints import _evaluate_float_output


def _source_file(tmp_path, monkeypatch, contents):
    root = tmp_path / 'local'
    root.mkdir()
    path = root / 'values.nf'
    path.write_text(contents, encoding='utf-8')
    monkeypatch.setattr(local_sources, '_catalog_input_paths', lambda: ((root,), ()))
    return path


def test_local_final_module_default_and_selected_unique_reload_preserve_identity(tmp_path, monkeypatch):
    """Caller K does not replace source K; explicit unique reload keeps pointer and key."""
    code = '''K = 1.0

def grow(value: Float = K) -> Float:
    return value + K

K = 9.0
'''
    path = _source_file(tmp_path, monkeypatch, code)
    base = bpy.data.node_groups.new('NF-Rev2-local-final-default', 'GeometryNodeTree')
    caller = '''from local import values
K = 100.0
shared = values.grow()
unique = values.grow(__unique__=True)
'''
    compile_root(caller, base, module_context='test:rev2-local-caller')
    shared, unique = [n for n in base.nodes if n.bl_idname == 'GeometryNodeGroup']
    assert shared.inputs[0].default_value == unique.inputs[0].default_value == 9.0
    assert not shared.inputs[0].is_linked and not unique.inputs[0].is_linked
    shared_group, unique_group = shared.node_tree, unique.node_tree
    identity = (
        unique_group.as_pointer(), unique_group['nodeforge_module_definition'],
        unique_group[FUNCTION_INSTANCE_KEY_PROP],
    )
    assert identity[2] and shared_group.as_pointer() != identity[0]
    path.write_text(code.replace('K = 9.0', 'K = 8.0'), encoding='utf-8')
    assert compiler.update_library_catalog_group(unique_group) is unique_group
    assert (
        unique_group.as_pointer(), unique_group['nodeforge_module_definition'],
        unique_group[FUNCTION_INSTANCE_KEY_PROP],
    ) == identity
    assert unique.inputs[0].default_value == 8.0
    assert shared.inputs[0].default_value == 9.0
    assert round(_evaluate_float_output(base, unique.outputs[0]), 5) == 16.0


def test_invalid_unused_imported_declaration_stops_before_physical_publication(tmp_path, monkeypatch):
    """Module preflight validates unused definitions before adding a caller Group Node."""
    _source_file(tmp_path, monkeypatch, '''def good(x: Float) -> Float:
    return x + 1

def broken(x):
    return x
''')
    base = bpy.data.node_groups.new('NF-Rev2-invalid-imported-preflight', 'GeometryNodeTree')
    cube = base.nodes.new('GeometryNodeMeshCube')
    transform = base.nodes.new('GeometryNodeTransform')
    base.links.new(cube.outputs['Mesh'], transform.inputs['Geometry'])
    before_nodes = {n.as_pointer() for n in base.nodes}
    before_links = {(l.from_node.as_pointer(), l.to_node.as_pointer()) for l in base.links}
    before_groups = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='explicit type annotation'):
        compile_root(
            'from local import values\nresult=values.good(2.0)\n',
            base, module_context='test:rev2-imported-invalid',
        )
    assert {n.as_pointer() for n in base.nodes} == before_nodes
    assert {(l.from_node.as_pointer(), l.to_node.as_pointer()) for l in base.links} == before_links
    assert {group.as_pointer() for group in bpy.data.node_groups} == before_groups
