"""Blender integration of nested package calls and source-owned Library groups."""

import json
from pathlib import Path

import bpy
import pytest

from NodeForge import compiler, packages
from NodeForge.errors import CompileError


def _install_source_package(root: Path):
    """Install source functions that intentionally reuse a public leaf name."""
    root.mkdir(parents=True)
    for parent, multiplier in [('mesh', 2), ('curve', 3)]:
        directory = root/'functions'/parent
        directory.mkdir(parents=True)
        (directory/'primitives.nf').write_text(
            f'def shared(x: Float) -> Float:\n    return x * {multiplier}.0\n', encoding='utf8')
    (root/'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.nested', 'import_name': 'nested',
        'name': 'Nested', 'version': '1.0.0', 'author': 'Tests',
        'nodeforge_min_version': '0.65.0', 'contents': {'functions': 'functions'},
    }), encoding='utf8')
    return packages.install_package_directory(root)


def test_exact_source_add_reload_and_missing_source(tmp_path):
    """Two same-leaf source groups Reload by ModuleId rather than the leaf name."""
    installed = _install_source_package(tmp_path/'source')
    a = compiler.create_package_function_group('vendor.nested', 'mesh.primitives.shared')
    b = compiler.create_package_function_group('vendor.nested', 'curve.primitives.shared')
    try:
        first = a.get('nodeforge_module_definition')
        second = b.get('nodeforge_module_definition')
        assert first and second and first != second
        with pytest.raises(CompileError, match='Ambiguous callable'):
            compiler.create_package_function_group('vendor.nested', 'shared')
        other_before = [(node.bl_idname, node.name) for node in b.nodes]
        source = installed.root/'functions/mesh/primitives.nf'
        source.write_text('def shared(x: Float) -> Float:\n    return x + 7.0\n')
        compiler.update_library_catalog_group(a)
        assert a.get('nodeforge_module_definition') == first
        assert b.get('nodeforge_module_definition') == second
        assert other_before == [(node.bl_idname, node.name) for node in b.nodes]
        assert all(link.is_valid for link in a.links) and all(link.is_valid for link in b.links)
        source.unlink()
        with pytest.raises(CompileError, match='no longer exported'):
            compiler.update_library_catalog_group(a)
        assert a.get('nodeforge_module_definition') == first
    finally:
        bpy.data.node_groups.remove(a)
        bpy.data.node_groups.remove(b)


def test_native_extension_member_is_callable_but_has_no_library_add(tmp_path):
    """Programmatic native Add rejects before materializing any group."""
    source=tmp_path/'source';owner=source/'functions/mesh/native';owner.mkdir(parents=True)
    (owner/'interface.py').write_text('''from typing import Annotated
from NodeForge import Float, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"native": ".backend:build"}
def native(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
''')
    (owner/'backend.py').write_text('def build(context, value): return value\n')
    (source/'nodeforge_package.json').write_text(json.dumps({
        'schema_version':1,'id':'vendor.native','import_name':'native',
        'name':'Native','version':'1.0.0','author':'Tests',
        'nodeforge_min_version':'0.65.0', 'contents':{'functions':'functions'},
        'permissions':{'python':True},
    }))
    packages.install_package_directory(source)
    baseline=set(bpy.data.node_groups)
    with pytest.raises(CompileError, match='cannot be added as a standalone Library group'):
        compiler.create_package_function_group('vendor.native', 'mesh.native')
    assert set(bpy.data.node_groups)==baseline
    group=compiler.create_expression_group(
        'from packages import native\ndef wrapper(value: Float) -> Float:\n'
        '    return native.mesh.native(value)\n'
    )
    try:assert all(link.is_valid for link in group.links)
    finally:bpy.data.node_groups.remove(group)


def test_functions_browser_preserves_exact_canonical_selection(tmp_path):
    """The UI index exposes both same-named definitions and retains the selected path."""
    from types import SimpleNamespace
    from NodeForge import ui

    class Rows(list):
        """Minimal Blender collection with the UI's add/clear contract."""
        def add(self):
            """Append one mutable record as an actual CollectionProperty would."""
            row=SimpleNamespace(package_id='', package_name='', source_label='', root_path='')
            self.append(row)
            return row

    _install_source_package(tmp_path/'source')
    props=SimpleNamespace(function_items=Rows(), function_index=0)
    assert ui._refresh_catalog_items(props, 'functions') == 2
    assert {x.member_path for x in props.function_items} == {
        'curve.primitives.shared','mesh.primitives.shared',
    }
    assert all(x.materializable for x in props.function_items)
    selected=next(i for i,x in enumerate(props.function_items)
                  if x.member_path=='mesh.primitives.shared')
    props.function_index=selected
    assert ui._refresh_catalog_items(props, 'functions') == 2
    assert props.function_items[props.function_index].member_path=='mesh.primitives.shared'
    assert all(x.package_id=='vendor.nested' for x in props.function_items)


def test_native_ui_row_is_discoverable_but_non_materializable(tmp_path):
    """The Function Library retains native rows without advertising Add support."""
    from types import SimpleNamespace
    from NodeForge import ui

    source=tmp_path/'source';owner=source/'functions/mesh/native';owner.mkdir(parents=True)
    (owner/'interface.py').write_text('''from typing import Annotated
from NodeForge import Float, EvaluationMode
EXTENSION_API = 2
EXTENSIONS = {"native": ".backend:build"}
def native(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...
''')
    (owner/'backend.py').write_text('def build(context, value): return value\n')
    (source/'nodeforge_package.json').write_text(json.dumps({
        'schema_version':1,'id':'vendor.native','import_name':'native',
        'name':'Native','version':'1.0.0','author':'Tests',
        'nodeforge_min_version':'0.65.0', 'contents':{'functions':'functions'},
        'permissions':{'python':True},
    }))
    packages.install_package_directory(source)
    class Rows(list):
        """Blender collection stand-in for metadata-only selection tests."""
        def add(self):
            """Add one empty row."""
            row=SimpleNamespace();self.append(row);return row
    props=SimpleNamespace(function_items=Rows(),function_index=0)
    assert ui._refresh_catalog_items(props,'functions')==1
    row=props.function_items[0]
    assert row.member_path=='mesh.native'
    assert row.kind=='extension'
    assert row.materializable is False


def test_nested_source_owner_survives_blend_save_reopen_and_exact_reload(tmp_path):
    """Saved nested groups retain distinct ModuleIds across a native .blend round-trip."""
    installed = _install_source_package(tmp_path / 'source')
    mesh = compiler.create_package_function_group('vendor.nested', 'mesh.primitives.shared')
    curve = compiler.create_package_function_group('vendor.nested', 'curve.primitives.shared')
    mesh.use_fake_user = True
    curve.use_fake_user = True
    identities = {
        'mesh': (mesh.name, mesh['nodeforge_module_definition']),
        'curve': (curve.name, curve['nodeforge_module_definition']),
    }
    assert identities['mesh'][1] != identities['curve'][1]
    bpy.ops.wm.save_as_mainfile(filepath=str(tmp_path / 'nested_reload.blend'))
    bpy.ops.wm.open_mainfile(filepath=str(tmp_path / 'nested_reload.blend'))
    reloaded_mesh = bpy.data.node_groups[identities['mesh'][0]]
    reloaded_curve = bpy.data.node_groups[identities['curve'][0]]
    assert reloaded_mesh['nodeforge_module_definition'] == identities['mesh'][1]
    assert reloaded_curve['nodeforge_module_definition'] == identities['curve'][1]
    curve_before = [(n.bl_idname, n.name) for n in reloaded_curve.nodes]
    mesh_source = installed.root / 'functions/mesh/primitives.nf'
    mesh_source.write_text('def shared(x: Float) -> Float:\n    return x + 11.0\n', encoding='utf8')
    saved_ptr = reloaded_mesh.as_pointer()
    assert compiler.update_library_catalog_group(reloaded_mesh) is reloaded_mesh
    assert reloaded_mesh.as_pointer() == saved_ptr
    assert reloaded_mesh['nodeforge_module_definition'] == identities['mesh'][1]
    assert reloaded_curve['nodeforge_module_definition'] == identities['curve'][1]
    assert [(n.bl_idname, n.name) for n in reloaded_curve.nodes] == curve_before
    assert all(l.is_valid for l in reloaded_mesh.links)
