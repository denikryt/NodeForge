"""Native Blender regressions for Local canonical ownership, reload and rollback."""

import bpy
import pytest

from helpers import compile_group
from NodeForge import compiler, local_sources
from NodeForge.errors import CompileError


@pytest.fixture
def local_tree(tmp_path, monkeypatch):
    """Isolate Local storage and clean every native datablock produced by the test."""
    root = tmp_path / 'Local'
    root.mkdir()
    monkeypatch.setattr(local_sources, '_default_local_catalog_dir', lambda: root)
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: tmp_path / 'registry.json')
    before = {group.as_pointer() for group in bpy.data.node_groups}
    yield root
    for group in list(bpy.data.node_groups):
        if group.as_pointer() not in before:
            bpy.data.node_groups.remove(group, do_unlink=True)


def write(root, relative, expression='x'):
    """Create one single-input reusable physical source."""
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'x = input_float("X")\noutput("Value", {expression})\n')
    return path


def children(group):
    """Return source-backed group nodes in native root node order."""
    return [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']


def metadata_values(group):
    """Snapshot ID properties by value, including nested Blender wrappers."""
    def freeze(value):
        """Recursively copy mutable property groups and array contents."""
        if hasattr(value, 'to_dict'):
            return {key: freeze(item) for key, item in value.to_dict().items()}
        if hasattr(value, 'to_list'):
            return tuple(freeze(item) for item in value.to_list())
        if isinstance(value, dict):
            return {key: freeze(item) for key, item in value.items()}
        if isinstance(value, (list, tuple)):
            return tuple(freeze(item) for item in value)
        return value
    return {key: freeze(value) for key, value in group.items()}


def test_same_origin_reuses_build_group_and_different_origins_do_not(local_tree):
    """Flat/direct/alias calls share a child while same-stem physical owners differ."""
    a = write(local_tree, 'terrain/noise.nf')
    root = compile_group('''from local import noise as flat
from local.terrain import noise as direct
from local import terrain as t
output(flat(1.0) + direct(2.0) + t.noise(3.0))
''', 'NFTest_local_namespace_same_origin')
    nodes = children(root)
    assert len(nodes) == 3
    assert len({node.node_tree.as_pointer() for node in nodes}) == 1
    assert nodes[0].node_tree['nodeforge_library_source_origin'] == local_sources._path_key(a)
    b = write(local_tree, 'procedural/noise.nf', 'x + 2.0')
    root = compile_group('''from local.terrain import noise
from local.procedural import noise as other
output(noise(1.0) + other(1.0))
''', 'NFTest_local_namespace_distinct_origins')
    nodes = children(root)
    assert len({node.node_tree.as_pointer() for node in nodes}) == 2
    assert {node.node_tree['nodeforge_library_source_origin'] for node in nodes} == {local_sources._path_key(a), local_sources._path_key(b)}
    assert len({node.node_tree['nodeforge_function_definition_owner'] for node in nodes}) == 2
    assert len({node.node_tree['nodeforge_function_compilation_fingerprint'] for node in nodes}) == 2


def test_directory_alias_helper_has_no_namespace_socket(local_tree):
    """Inherited frontend aliases stay outside the native helper interface."""
    write(local_tree, 'terrain/noise.nf')
    root = compile_group('''from local import terrain as t
def helper(x: Float):
    return t.noise(x)
output(helper(1.0))
''', 'NFTest_local_namespace_helper')
    helper = children(root)[0].node_tree
    inputs = [item.name for item in helper.interface.items_tree if item.item_type == 'SOCKET' and item.in_out == 'INPUT']
    assert 't' not in inputs and len(inputs) == 1
    assert children(helper)[0].node_tree.get('nodeforge_library_source_origin')


def test_reload_exact_origin_preserves_pointer_links_and_rejects_missing_legacy(local_tree):
    """Reload uses stored physical ownership and retains the native selected group."""
    a = write(local_tree, 'terrain/noise.nf')
    write(local_tree, 'procedural/noise.nf', 'x + 2.0')
    root = compile_group('from local.terrain import noise\noutput(noise(1.0))', 'NFTest_local_origin_reload')
    node = children(root)[0]
    selected = node.node_tree
    pointer = selected.as_pointer()
    link_count = len(root.links)
    a.write_text('x = input_float("X")\noutput("Value", x + 3.0)\n')
    compiler.update_library_catalog_group(selected)
    assert selected.as_pointer() == pointer and node.node_tree is selected
    assert len(root.links) == link_count
    assert selected['nodeforge_library_source_origin'] == local_sources._path_key(a)
    before = (selected.as_pointer(), len(selected.nodes), len(selected.links), metadata_values(selected))
    a.unlink()
    with pytest.raises(CompileError, match='unavailable'):
        compiler.update_library_catalog_group(selected)
    assert (selected.as_pointer(), len(selected.nodes), len(selected.links), metadata_values(selected)) == before
    del selected['nodeforge_library_source_origin']
    with pytest.raises(CompileError, match='no canonical source origin'):
        compiler.update_library_catalog_group(selected)
    assert selected.as_pointer() == pointer and node.node_tree is selected


def test_invalid_selection_and_backend_failure_leave_no_published_groups(local_tree, monkeypatch):
    """Ambiguity fails before lowering; native child failure rolls resources back."""
    write(local_tree, 'terrain/noise.nf')
    write(local_tree, 'procedural/noise.nf')
    before = {group.as_pointer() for group in bpy.data.node_groups}
    with pytest.raises(CompileError, match='Ambiguous callable'):
        compile_group('from local import noise\noutput(noise(1.0))', 'NFTest_local_ambiguity_atomic')
    assert {group.as_pointer() for group in bpy.data.node_groups} == before
    from NodeForge.blender import library_groups
    def fail_metadata(group, record):
        """Inject a publication-boundary failure after native child construction."""
        raise CompileError('injected Local publication failure')
    monkeypatch.setattr(library_groups, '_write_package_metadata', fail_metadata)
    with pytest.raises(CompileError, match='injected Local publication failure'):
        compile_group('from local.terrain import noise\noutput(noise(1.0))', 'NFTest_local_origin_rollback')
    assert {group.as_pointer() for group in bpy.data.node_groups} == before


def test_root_create_update_and_callbacks_capture_only_before_materialization(local_tree, monkeypatch):
    """Native create/update and callback paths use zero or one frontend-owned capture."""
    from NodeForge import environment_resolution as resolution
    from NodeForge.blender.group_backend import BlenderGroupBackend
    captures = []
    original_capture = resolution._capture_local_catalog
    def capture():
        """Record actual capture results for native compilation attempts."""
        selected = original_capture()
        captures.append(selected)
        return selected
    monkeypatch.setattr(resolution, '_capture_local_catalog', capture)
    original_publish = BlenderGroupBackend.create_or_update
    def publish(self, request, **kwargs):
        """Fail if physical lowering initiates any Local inventory capture."""
        def forbidden():
            """Reject discovery after semantic preparation has finished."""
            pytest.fail('Local capture during native materialization')
        with monkeypatch.context() as guarded:
            guarded.setattr(resolution, '_capture_local_catalog', forbidden)
            return original_publish(self, request, **kwargs)
    monkeypatch.setattr(BlenderGroupBackend, 'create_or_update', publish)
    root = compiler.create_expression_group('output("Value", 1.0)', 'NFTest_local_free_demand')
    pointer = root.as_pointer()
    compiler.update_expression_group(root, 'output("Value", 2.0)')
    assert root.as_pointer() == pointer and captures == []
    write(local_tree, 'terrain/noise.nf')
    text = 'from local import terrain as t\ndef helper(x: Float):\n    return t.noise(x)\noutput("Value", helper(1.0))'
    compiler.update_expression_group(root, text)
    assert len(captures) == 1 and root.as_pointer() == pointer
    compiler.update_expression_group(root, text)
    assert len(captures) == 2 and captures[0] is not captures[1]
    backend = compiler._new_group_backend()
    backend.compile_group_callback('output("Value", 3.0)', name='NFTest_local_free_callback')
    assert len(captures) == 2
    backend.compile_group_callback(text, name='NFTest_local_demand_callback')
    assert len(captures) == 3


def test_native_local_free_root_ignores_bad_registry_but_import_fails(local_tree, tmp_path, monkeypatch):
    """Broken Local state does not affect independent native roots or their updates."""
    registry = tmp_path / 'bad-registry.json'
    registry.write_text('{"version":999,"roots":[]}')
    monkeypatch.setattr(local_sources, '_local_sources_registry_path', lambda: registry)
    root = compiler.create_expression_group('output("Value", 1.0)', 'NFTest_bad_local_free')
    pointer = root.as_pointer()
    compiler.update_expression_group(root, 'output("Value", 2.0)')
    before = (pointer, len(root.nodes), len(root.links), metadata_values(root))
    with pytest.raises(CompileError, match='Unsupported Local source registry format'):
        compiler.update_expression_group(root, 'from local import *\noutput("Value", 3.0)')
    assert (root.as_pointer(), len(root.nodes), len(root.links), metadata_values(root)) == before
