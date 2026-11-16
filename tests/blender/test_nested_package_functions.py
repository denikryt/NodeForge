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
