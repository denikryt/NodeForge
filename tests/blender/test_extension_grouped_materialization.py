"""Physical grouped extension calls reuse typed analyzed Call IR."""

import tempfile
from pathlib import Path

import bpy
import pytest

from helpers import compile_group
from NodeForge import packages, compiler
from NodeForge.blender.extension_group_materializer import EXTENSION_AUTHORITY_PROP
from test_extension_dynamic_results import _write_package


def test_grouped_dynamic_results_have_distinct_occurrences_and_external_references():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / 'package'
        _write_package(directory)
        packages.install_package_directory(directory)
        source = '''from packages import dynamic

def nf_fixture(v: Float):
    first = foo(v, __inline__=False)
    second = foo(first.double, __inline__=False)
    return second.double, second.original
'''
        group = compile_group(source, 'NFExt_grouped_dynamic')
        group_nodes = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
        assert len(group_nodes) == 2
        group_a = group_nodes[0].node_tree
        group_b = group_nodes[1].node_tree
        assert group_a.as_pointer() != group_b.as_pointer()
        assert group_a.get(EXTENSION_AUTHORITY_PROP) != group_b.get(EXTENSION_AUTHORITY_PROP)
        for node in group_nodes:
            assert [s.name for s in node.inputs] == ['value']
            assert [s.name for s in node.outputs] == ['double', 'original']
            assert len([n for n in node.node_tree.nodes if n.bl_idname == 'ShaderNodeMath']) == 1
        before = {g.get(EXTENSION_AUTHORITY_PROP): g.as_pointer() for g in (group_a, group_b)}
        compiler.update_expression_group(group, source)
        fresh_nodes = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
        assert len(fresh_nodes) == 2
        assert {node.node_tree.get(EXTENSION_AUTHORITY_PROP): node.node_tree.as_pointer()
                for node in fresh_nodes} == before


def test_inline_dynamic_results_keep_direct_nodes_and_no_extension_group():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / 'package'
        _write_package(directory)
        packages.install_package_directory(directory)
        source = '''from packages import dynamic

def nf_fixture(v: Float):
    r = foo(v)
    return r.double, r.original
'''
        group = compile_group(source, 'NFExt_inline_dynamic')
        assert len([node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']) == 0
        assert len([node for node in group.nodes if node.bl_idname == 'ShaderNodeMath']) == 1


def test_grouped_semantic_state_exposes_only_compacted_dependency_socket():
    import builtins
    from test_extension_api_v2 import _write_semantic_extension_package
    builtins._nodeforge_semantic_backend_calls = []
    try:
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp) / 'package'
            _write_semantic_extension_package(directory)
            packages.install_package_directory(directory)
            group = compile_group('''from packages import semantic

def nf_fixture(value: Int):
    part = make_part(value)
    result = consume_part(part, __inline__=False)
    return result
''', 'NFExt_grouped_semantic')
            nodes = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
            assert len(nodes) == 1
            assert [s.name for s in nodes[0].inputs] == ['Dependency_0']
            assert [s.bl_idname for s in nodes[0].inputs] == ['NodeSocketInt']
            assert [s.name for s in nodes[0].outputs] == ['Result']
            assert builtins._nodeforge_semantic_backend_calls == ['consume_part']
    finally:
        delattr(builtins, '_nodeforge_semantic_backend_calls')


@pytest.mark.parametrize('bad,pattern', [
    ('short', 'exact result keys'), ('wrong_type', 'expected FLOAT'),
    ('input_socket', 'output sockets'), ('wrong_socket_type', 'physical socket type'),
])
def test_grouped_invalid_backend_result_rolls_back_owned_group_and_resources(bad,pattern):
    from NodeForge.errors import CompileError
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / 'package'
        _write_package(directory, bad=bad)
        packages.install_package_directory(directory)
        groups_before = {g.as_pointer() for g in bpy.data.node_groups}
        meshes_before = {m.as_pointer() for m in bpy.data.meshes}
        with pytest.raises(CompileError, match=pattern):
            compile_group('''from packages import dynamic

def nf_fixture(v: Float):
    r = foo(v, __inline__=False)
    return r.double, r.original
''', 'NFExt_grouped_bad_'+bad)
        assert {g.as_pointer() for g in bpy.data.node_groups} == groups_before
        assert {m.as_pointer() for m in bpy.data.meshes} == meshes_before


def test_singleton_dynamic_results_remain_named_when_grouped():
    with tempfile.TemporaryDirectory() as temp:
        directory = Path(temp) / 'package'
        _write_package(directory)
        semantic = directory / 'systems' / 'dynamic' / 'semantic.py'
        semantic.write_text(semantic.read_text().replace('{"double": NFType.FLOAT, "original": NFType.FLOAT}', '{"double": NFType.FLOAT}'))
        backend = directory / 'systems' / 'dynamic' / 'backend.py'
        body = backend.read_text()
        body = body.replace('assert context.result_types == (NFType.FLOAT, NFType.FLOAT)',
                            'assert context.result_types == (NFType.FLOAT,)')
        body = body.replace("return {'original': value, 'double': context.value(node.outputs[0], NFType.FLOAT)}",
                            "return {'double': context.value(node.outputs[0], NFType.FLOAT)}")
        backend.write_text(body)
        packages.install_package_directory(directory)
        group = compile_group('''from packages import dynamic

def nf_fixture(v: Float):
    return foo(v, __inline__=False).double
''', 'NFExt_grouped_singleton')
        grouped = [node for node in group.nodes if node.bl_idname == 'GeometryNodeGroup']
        assert len(grouped) == 1
        assert [s.name for s in grouped[0].outputs] == ['double']
