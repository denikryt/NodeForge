"""Explicit catalog Add must materialize individual .nf definitions, not entire files."""

import json

import pytest

from NodeForge import compiler, packages
from NodeForge.catalog import _path_key

pytestmark = pytest.mark.blender


def _manifest(directory):
    """Create a minimal installed package containing two independent function exports."""
    root = directory / 'source_package'
    (root / 'functions').mkdir(parents=True)
    (root / 'functions' / 'shapes.nf').write_text('''def grow(x: Float) -> Float:
    return x + 1

def shrink(x: Float) -> Float:
    return x - 1

# Import and Add must never execute this top-level expression.
poison = unknown_runtime_name()
''', encoding='utf-8')
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.add', 'import_name': 'addpkg',
        'name': 'Add fixture', 'version': '1.0.0', 'author': 'Tests',
        'description': 'Source functions', 'contents': {'functions': 'functions'},
        'nodeforge_min_version': '0.65.0', 'nodeforge_max_version': None,
        'permissions': {'python': False},
    }), encoding='utf-8')
    return root


def test_package_add_selects_individual_definition_and_reuses_owner(tmp_path):
    """Adding both source members shares exact exported module ownership."""
    packages.set_packages_dir_for_tests(tmp_path / 'packages')
    try:
        packages.install_package_directory(_manifest(tmp_path))
        grow = compiler.create_package_function_group('vendor.add', 'grow')
        shrink = compiler.create_package_function_group('vendor.add', 'shrink')
        assert grow is not shrink
        assert grow.get('nodeforge_module_name') == 'grow'
        assert shrink.get('nodeforge_module_name') == 'shrink'
        assert compiler.create_package_function_group('vendor.add', 'grow') is grow
        assert all('unknown_runtime_name' not in node.name for node in grow.nodes)
        with pytest.raises(Exception, match='Unknown package function'):
            compiler.create_package_function_group('vendor.add', 'nonexistent')
    finally:
        packages.set_packages_dir_for_tests(None)


def test_local_add_requires_unambiguous_entry(tmp_path, monkeypatch):
    """Single-definition Add succeeds; a file with two entries cannot pick one arbitrarily."""
    from NodeForge import local_sources
    root = tmp_path / 'local'
    root.mkdir()
    single = root / 'single.nf'
    single.write_text('def lift(x: Float) -> Float:\n    return x + 1\n', encoding='utf-8')
    multiple = root / 'multiple.nf'
    multiple.write_text('def left(x: Float) -> Float:\n    return x\ndef right(x: Float) -> Float:\n    return x\n', encoding='utf-8')
    monkeypatch.setattr(local_sources, '_catalog_input_paths', lambda: ((root,), ()))
    group, modifier = compiler.create_selected_library_group_auto('local', _path_key(single))
    assert not modifier and group.get('nodeforge_module_name') == 'lift'
    with pytest.raises(Exception, match='2 entry definitions'):
        compiler.create_selected_library_group_auto('local', _path_key(multiple))


def test_package_module_definition_selected_reload_in_place(tmp_path):
    """Reload uses an exported def identity, not the containing .nf library file."""
    from NodeForge.environment_resolution import resolve_environment
    packages.set_packages_dir_for_tests(tmp_path / 'packages')
    try:
        packages.install_package_directory(_manifest(tmp_path))
        group = compiler.create_package_function_group('vendor.add', 'grow')
        pointer = group.as_pointer()
        other = compiler.create_package_function_group('vendor.add', 'shrink')
        old_other_pointer = other.as_pointer()
        assert compiler.update_library_catalog_group(group) is group
        assert group.as_pointer() == pointer
        export = resolve_environment().package_by_id('vendor.add').find('grow')
        path = export.record.source_path
        path.write_text(path.read_text(encoding='utf-8').replace('return x + 1', 'return x + 3'), encoding='utf-8')
        assert compiler.update_library_catalog_group(group) is group
        assert group.as_pointer() == pointer
        assert other.as_pointer() == old_other_pointer
        assert compiler.create_package_function_group('vendor.add', 'grow') is group
    finally:
        packages.set_packages_dir_for_tests(None)
