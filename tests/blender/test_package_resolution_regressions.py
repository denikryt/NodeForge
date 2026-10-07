"""Physical Examples materialization and snapshot-consistent Functions reload."""

from helpers import *

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from NodeForge import compiler, catalog, local_sources, packages, ui
from NodeForge.blender import library_groups
from NodeForge import storage
from NodeForge.errors import CompileError
from NodeForge.environment_resolution import resolve_environment


def _package(root, package_id, namespace, *, python=False):
    """Create one isolated installable package for the physical compiler path."""
    root.mkdir()
    (root / namespace).mkdir()
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': package_id, 'name': package_id,
        'version': '1.0.0', 'author': 'Tests', 'description': 'Package resolution regression',
        'nodeforge_min_version': '0.65.0', 'nodeforge_max_version': None,
        'contents': {namespace: namespace}, 'permissions': {'python': python},
    }))


def test_python_example_import_materializes_through_registry(tmp_path):
    root = tmp_path / 'example-package'
    _package(root, 'vendor.review', 'examples', python=True)
    owner = root / 'examples' / 'review_example'
    owner.mkdir()
    (owner / 'interface.py').write_text(
        'from typing import Annotated\nfrom NodeForge import Float, EvaluationMode\n'
        'EXTENSION_API = 2\nEXTENSIONS = {"review_example": ".backend:build"}\n'
        'def review_example(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'
    )
    (owner / 'backend.py').write_text('def build(context, value):\n    return value\n')
    packages.install_package_directory(root, allow_python=True)
    group = compile_group(
        'from examples import review_example\nx = input_float("X")\noutput("Value", review_example(x))\n',
        'NFTest_review_python_example',
    )
    check(group is not None, 'Python Example failed physical materialization')
    expect_compile_error(
        'from packages import review\nx = input_float("X")\noutput(review.review_example(x))\n',
        'NFTest_review_example_not_export',
    )


def test_functions_reload_uses_one_environment_without_live_rediscovery(tmp_path, monkeypatch):
    root = tmp_path / 'function-package'
    _package(root, 'vendor.review', 'functions')
    source = root / 'functions' / 'foo.nf'
    source.write_text('x = input_float("X")\noutput("Value", x + 1)\n')
    installed = packages.install_package_directory(root, allow_python=False)
    group = compiler.create_package_function_group('vendor.review', 'foo')
    (installed.root / 'functions' / 'foo.nf').write_text(
        'x = input_float("X")\noutput("Value", x + 2)\n'
    )
    snapshot = resolve_environment()
    calls = []
    def one_snapshot():
        calls.append(snapshot)
        assert len(calls) == 1
        return snapshot
    def forbidden(*args, **kwargs):
        pytest.fail('Reload repeated live catalog discovery')
    monkeypatch.setattr(compiler, 'resolve_environment', one_snapshot)
    monkeypatch.setattr(catalog, 'candidate_records_from_inputs', forbidden)
    updated = compiler.update_library_catalog_group(group, 'functions', 'foo')
    check(updated is group, 'Reload replaced the selected group identity')
    check(group.get('nodeforge_package_id') == 'vendor.review', 'Reload lost canonical owner')
    assert calls == [snapshot]
    with pytest.raises(CompileError, match='belongs to'):
        library_groups.resolve_reloadable_library_entry(group, 'functions', 'other', resolved_environment=snapshot)


@pytest.mark.parametrize('style', ['bare', 'qualified', 'local'])
def test_native_function_materializes_source_calls(tmp_path, style):
    root = tmp_path / 'native-package'
    _package(root, 'vendor.review', 'functions', python=True)
    owner = root / 'functions' / 'native_value'
    owner.mkdir()
    (owner / 'interface.py').write_text(
        'from typing import Annotated\nfrom NodeForge import Float, EvaluationMode\n'
        'EXTENSION_API = 2\nEXTENSIONS = {"native_value": ".backend:build"}\n'
        'def native_value(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> Float: ...\n'
    )
    (owner / 'backend.py').write_text('def build(context, value):\n    return value\n')
    packages.install_package_directory(root, allow_python=True)
    source = 'from packages import review\nx=input_float("X")\n'
    if style == 'local':
        source += 'def helper(x):\n    return review.native_value(x)\noutput(helper(x))\n'
    else:
        callee = 'native_value' if style == 'bare' else 'review.native_value'
        source += f'output({callee}(x))\n'
    assert compile_group(source, 'NFTest_native_function_' + style) is not None


@pytest.mark.parametrize('namespace', ['functions', 'examples'])
@pytest.mark.parametrize('missing_owner', [False, True])
def test_ui_reload_uses_one_snapshot_and_preserves_owner(tmp_path, monkeypatch, namespace, missing_owner):
    from types import SimpleNamespace
    from NodeForge import ui
    root = tmp_path / 'ui-package'
    _package(root, 'vendor.review', namespace)
    (root / namespace / 'foo.nf').write_text('x=input_float("X")\noutput("Value", x+1)\n')
    installed = packages.install_package_directory(root, allow_python=False)
    if namespace == 'functions':
        group = compiler.create_package_function_group('vendor.review', 'foo')
        other = tmp_path / 'other-package'
        _package(other, 'vendor.other', namespace)
        (other / namespace / 'foo.nf').write_text('output("Value", 99)\n')
        packages.install_package_directory(other, allow_python=False)
    else:
        group = compiler.create_library_catalog_group(namespace, 'foo')
    group.name = 'NFTest_custom_name'
    old_name = group.name
    if missing_owner:
        (installed.root / namespace / 'foo.nf').unlink()
    else:
        (installed.root / namespace / 'foo.nf').write_text('x=input_float("X")\noutput("Value", x+2)\n')
    snapshot = resolve_environment()
    calls = []
    def one_snapshot():
        calls.append(snapshot)
        assert len(calls) == 1
        return snapshot
    def forbidden(*args, **kwargs):
        pytest.fail('UI reload performed preliminary live discovery')
    reports = []
    node = SimpleNamespace(node_tree=group, name='')
    operator = SimpleNamespace(report=lambda level, message: reports.append((level, message)))
    monkeypatch.setattr(compiler, 'resolve_environment', one_snapshot)
    monkeypatch.setattr(catalog, 'candidate_records_from_inputs', forbidden)
    monkeypatch.setattr(ui, '_selected_group_node', lambda context: node)
    result = ui.NODEFORGE_OT_reload_selected_library_group.execute(operator, SimpleNamespace())
    assert calls == [snapshot]
    assert node.node_tree is group
    assert group.name == old_name
    assert group.get('nodeforge_package_id') == 'vendor.review'
    if missing_owner:
        assert result == {'CANCELLED'}
        assert reports[-1][0] == {'ERROR'}
        assert 'unavailable' in reports[-1][1]
    else:
        assert result == {'FINISHED'}
        assert node.name == old_name
        assert reports[-1][0] == {'INFO'}


@pytest.fixture
def save_setup(tmp_path, monkeypatch):
    text = bpy.data.texts.new("NFTest_SaveText.nf")
    text.write('  output("Text", 1.0)\n\n')
    selected_group = bpy.data.node_groups.new("NFTest_SelectedSaveGroup", "GeometryNodeTree")
    selected_group[storage.SOURCE_PROP] = 'output("Group", 2.0)'
    props = SimpleNamespace(text_block=text, local_browser_path="", local_overwrite=False)
    dialogs = []
    context = SimpleNamespace(
        scene=SimpleNamespace(gn_script_mvp=props),
        active_node=SimpleNamespace(node_tree=selected_group),
        window_manager=SimpleNamespace(invoke_props_dialog=lambda op: dialogs.append(op) or {'RUNNING_MODAL'}),
    )
    reports = []
    operator = SimpleNamespace(script_name="saved", overwrite=False,
                               report=lambda level, message: reports.append((level, message)))
    original_catalog_dir = catalog.catalog_dir
    monkeypatch.setattr(local_sources, 'ensure_local_catalog_dir', lambda: tmp_path)
    monkeypatch.setattr(ui, '_refresh_catalog_items', lambda props, namespace: None)

    def forbidden(*args, **kwargs):
        pytest.fail("Save to Local queried an implicit or group source")

    for name in ('_selected_group_node', '_extract_group_source', '_source_from_props'):
        monkeypatch.setattr(ui, name, forbidden)
    try:
        yield SimpleNamespace(text=text, props=props, context=context, operator=operator,
                              reports=reports, path=tmp_path/'saved.nf', dialogs=dialogs)
    finally:
        bpy.data.texts.remove(text)
        bpy.data.node_groups.remove(selected_group)


def test_save_to_local_writes_exact_text_even_when_group_is_selected(save_setup):
    setup = save_setup
    assert ui.NODEFORGE_OT_save_to_local.poll(setup.context)
    assert ui.NODEFORGE_OT_save_to_local.execute(setup.operator, setup.context) == {'FINISHED'}
    assert setup.path.read_text() == setup.text.as_string()
    assert setup.props.local_script_name == 'saved'
    assert not hasattr(setup.props, 'local_source_kind')


@pytest.mark.parametrize('source', [None, '', ' \n\t'])
def test_save_to_local_rejects_missing_or_empty_text_without_group_fallback(save_setup, source):
    setup = save_setup
    if source is None:
        setup.props.text_block = None
        assert not ui.NODEFORGE_OT_save_to_local.poll(setup.context)
    else:
        setup.text.clear()
        setup.text.write(source)
    assert ui.NODEFORGE_OT_save_to_local.execute(setup.operator, setup.context) == {'CANCELLED'}
    assert not setup.path.exists()
    assert setup.reports[-1][0] == {'ERROR'}
    assert not hasattr(setup.props, 'local_script_name')


def test_save_to_local_rejects_missing_scene_properties(save_setup):
    setup = save_setup
    setup.context.scene.gn_script_mvp = None
    assert not ui.NODEFORGE_OT_save_to_local.poll(setup.context)
    assert ui.NODEFORGE_OT_save_to_local.execute(setup.operator, setup.context) == {'CANCELLED'}
    assert not setup.path.exists()


def test_save_to_local_invocation_uses_text_name_and_overwrite_setting(save_setup):
    setup = save_setup
    setup.operator.script_name = ''
    setup.props.local_overwrite = True
    assert ui.NODEFORGE_OT_save_to_local.invoke(setup.operator, setup.context, None) == {'RUNNING_MODAL'}
    assert setup.operator.script_name == 'NFTest_SaveText'
    assert setup.operator.overwrite is True
    assert setup.dialogs == [setup.operator]
    assert not hasattr(setup.operator, 'source_kind')


def test_save_to_local_preserves_existing_file_when_text_is_missing(save_setup):
    setup = save_setup
    setup.path.write_text('original contents')
    setup.operator.overwrite = True
    setup.props.text_block = None
    assert ui.NODEFORGE_OT_save_to_local.execute(setup.operator, setup.context) == {'CANCELLED'}
    assert setup.path.read_text() == 'original contents'


def test_save_to_local_scene_and_operator_rna_have_no_source_selector():
    import NodeForge
    was_registered = hasattr(bpy.types.Scene, 'gn_script_mvp')
    if not was_registered:
        NodeForge.register()
    try:
        assert 'local_source_kind' not in ui.GNSCRIPT_MVP_Properties.bl_rna.properties
        assert 'source_kind' not in ui.NODEFORGE_OT_save_to_local.bl_rna.properties
    finally:
        if not was_registered:
            NodeForge.unregister()
