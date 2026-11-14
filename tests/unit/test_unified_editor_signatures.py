"""Editor projections for unified .nf module and definition imports."""

from pathlib import Path

import pytest

from NodeForge import packages
from NodeForge.editor.intelligence import SymbolIndex, cursor_assistance
from NodeForge.environment_resolution import resolve_environment

pytestmark = pytest.mark.unit


def _at(source, index):
    """Ask for editor assistance at a marked cursor position."""
    offset = source.index('|')
    before = source[:offset]
    text = before + source[offset + 1:]
    return cursor_assistance(text, before.count('\n'), len(before.split('\n')[-1]), index)


@pytest.fixture
def index(tmp_path, monkeypatch):
    """Provide one real Local directory and source module without global installs."""
    local = tmp_path / 'local'
    (local / 'terrain').mkdir(parents=True)
    (local / 'terrain' / 'geometry.nf').write_text('''def move(geo: Geometry, offset: Vector["Offset"] = vector(1, 2, 3)) -> Geometry:
    return transform(geo, translation=offset)
def _resize(geo: Geometry, scale: Float = 1.5) -> Geometry:
    return transform(geo, scale=scale)
''', encoding='utf-8')
    monkeypatch.setattr('NodeForge.local_sources._catalog_input_paths', lambda: ((local,), ()))
    packages.set_packages_dir_for_tests(tmp_path / 'packages')
    try:
        yield SymbolIndex(resolve_environment())
    finally:
        packages.set_packages_dir_for_tests(None)


def test_module_only_exposes_its_declarations(index):
    """Module selection never makes the file itself callable."""
    symbols = index.symbols('from local.terrain import geometry as geo')
    assert 'geo' not in symbols['']
    assert set(symbols['geo']) == {'move', '_resize'}
    signature = symbols['geo']['move'][0]
    assert [p.keyword for p in signature.parameters] == ['geo', 'offset']
    assert [p.default for p in signature.parameters] == [None, 'vector(1, 2, 3)']
    assert signature.parameters[1].display_label == 'Offset'
    assert [(o.name, o.typ) for o in signature.outputs] == [('Value', 'Geometry')]


def test_direct_member_alias_and_exact_parameter_keyword(index):
    """A directly imported member retains its definition's keyword names."""
    hint = _at('from local.terrain.geometry import move as go\nx = go(of|', index)
    assert [c.insert for c in hint.candidates] == ['offset=']
    assert hint.signatures[0].name == 'go'
    assert all(p.keyword != 'Offset' for p in hint.signatures[0].parameters)


def test_directory_member_completes_as_module_not_callable(index):
    """The editor can traverse directories without inventing file functions."""
    module = _at('from local import terrain as t\nx = t.ge|', index)
    assert [c.label for c in module.candidates] == ['geometry']
    member = _at('from local import terrain as t\nx = t.geometry.mo|', index)
    assert [c.label for c in member.candidates] == ['move']


def test_no_legacy_root_input_completion(index):
    """Old root-interface operations no longer appear as DSL callable completions."""
    names = index.symbols('') ['']
    assert 'input_float' not in names
    assert 'output' not in names
