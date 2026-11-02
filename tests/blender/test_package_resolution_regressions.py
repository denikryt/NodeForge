"""Physical Examples materialization and snapshot-consistent Functions reload."""

from helpers import *

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from NodeForge import compiler, catalog, local_sources, packages, ui
from NodeForge.blender import library_groups, storage
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
    packages.install_package_directory(root)
    group = compile_group(
        'from examples import review_example\nx = input_float("X")\noutput("Value", review_example(x))\n',
        'NFTest_review_python_example',
    )
    check(group is not None, 'Python Example failed physical materialization')
    expect_compile_error(
        'from packages import review\nx = input_float("X")\noutput(review.review_example(x))\n',
        'NFTest_review_example_not_export',
    )




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
    packages.install_package_directory(root)
    source = 'from packages import review\nx=input_float("X")\n'
    if style == 'local':
        source += 'def helper(x):\n    return review.native_value(x)\noutput(helper(x))\n'
    else:
        callee = 'native_value' if style == 'bare' else 'review.native_value'
        source += f'output({callee}(x))\n'
    assert compile_group(source, 'NFTest_native_function_' + style) is not None




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
