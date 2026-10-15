"""Editor intelligence must agree with selected package and Local namespaces."""

from pathlib import Path

import pytest

from NodeForge import catalog, packages
from NodeForge.environment_resolution import resolve_environment
from NodeForge.editor.intelligence import SymbolIndex, cursor_assistance

pytestmark = pytest.mark.unit


@pytest.fixture
def editor_index(tmp_path, monkeypatch):
    """Build real frozen Local/package catalogs without touching user storage."""
    local = tmp_path / "local"
    (local / "terrain").mkdir(parents=True)
    (local / "terrain" / "move.nf").write_text(
        'scale = input_float("Scale XY", default=2.0)\n'
        'geometry = input_geometry("Geometry")\n'
        'output("Geometry", geometry)\n', encoding="utf-8"
    )
    (local / "simple.nf").write_text(
        'radius = input_float("Radius", default=1.0)\n'
        'output("Radius", radius)\n', encoding="utf-8"
    )
    monkeypatch.setattr("NodeForge.local_sources._catalog_input_paths", lambda: ((local,), ()))
    packages.set_packages_dir_for_tests(tmp_path / "packages")
    yield SymbolIndex(resolve_environment()), tmp_path
    packages.set_packages_dir_for_tests(None)


def help_at_caret(source, index):
    """Analyze a caret denoted by a vertical bar in a source snippet."""
    offset = source.index("|")
    before = source[:offset]
    text = source[:offset] + source[offset + 1:]
    return cursor_assistance(text, before.count("\n"), len(before.split("\n")[-1]), index)


def test_directory_members_and_import_alias(editor_index):
    """Local directory aliases expose selected members, not arbitrary flat names."""
    index, _ = editor_index
    hint = help_at_caret('from local import terrain as t\nx = t.mo|', index)
    assert [c.label for c in hint.candidates] == ["move"]
    hint = help_at_caret('from local.terrain import move as go\nx = go(|', index)
    assert hint.signatures[0].name == "go"
    assert [p.keyword for p in hint.signatures[0].parameters] == ["scale_xy", "geometry"]
    assert "scale_xy=" in [c.insert for c in hint.candidates]


def test_local_flat_keywords_and_active_parameter(editor_index):
    """Imported Local source labels supply public keywords and call position."""
    index, _ = editor_index
    hint = help_at_caret('from local import simple\nx = simple(ra|', index)
    assert [c.insert for c in hint.candidates] == ["radius="]
    hint = help_at_caret('from local import simple\nx = simple(radius=|', index)
    assert hint.active_parameter == 0
    assert not hint.candidates


def test_builtins_nested_calls_and_comments(editor_index):
    """Nested commas don't advance the outer call; comment text is ignored."""
    index, _ = editor_index
    hint = help_at_caret('x = vector(1, 2,|', index)
    assert hint.signatures and hint.active_parameter == 2
    hint = help_at_caret('x = vector(1, vector(2, 3, 4),|', index)
    assert hint.active_parameter == 2
    hint = help_at_caret('x = 2 # cube(|', index)
    assert not hint.candidates and not hint.signatures


def test_unsaved_local_def(editor_index):
    """A local def becomes available without saving or compiling source."""
    index, _ = editor_index
    source = 'def spin(angle: Float, offset: Vector):\n    return angle\nx = spin(of|'
    hint = help_at_caret(source, index)
    assert hint.signatures[0].name == 'spin'
    assert [c.insert for c in hint.candidates] == ['offset=']


def test_package_source_and_extensions(editor_index):
    """The installed math package contributes source and Extension API callables."""
    index, tmp_path = editor_index
    import os
    archive = os.environ.get('NODEFORGE_MATH_TEST_ZIP')
    if archive is None:
        pytest.skip('math archive not supplied')
    packages.install_package_zip(Path(archive), allow_python=True)
    index = SymbolIndex(resolve_environment())
    names = index.symbols('from packages import math')['math']
    assert 'layout_grid' in names
    assert 'sin' in names
    hint = help_at_caret('from packages import math\nx = math.la|', index)
    assert 'layout_grid' in [c.label for c in hint.candidates]
    hint = help_at_caret('from packages import math\nx = math.layout_grid(geometry, sp|', index)
    assert 'spacing=' in [c.insert for c in hint.candidates]
    hint = help_at_caret('from packages import math\nx = math.sin(|', index)
    assert hint.signatures and hint.signatures[0].parameters


def test_import_statement_completions(editor_index):
    """Complete Local/package import names before an import statement is valid."""
    index, _ = editor_index
    hint = help_at_caret('from local import te|', index)
    assert [item.insert for item in hint.candidates] == ['terrain']
    hint = help_at_caret('from local.terrain import mo|', index)
    assert [item.insert for item in hint.candidates] == ['move']
    hint = help_at_caret('from local import si|', index)
    assert [item.insert for item in hint.candidates] == ['simple']


def test_keyword_accounting_and_string_context(editor_index):
    """Nested keyword arguments cannot mark outer call inputs as already used."""
    index, _ = editor_index
    hint = help_at_caret('from local import simple\nx = simple(radius=vector(x=1), |', index)
    assert hint.signatures and not hint.candidates
    hint = help_at_caret('from local import simple\nx = simple("cu|', index)
    assert not hint.signatures and not hint.candidates


def test_ambiguous_local_file_omitted(editor_index):
    """An ambiguous flat Local name must not be presented as a callable."""
    index, tmp_path = editor_index
    (tmp_path / 'local' / 'terrain' / 'simple.nf').write_text('output("x", 1.0)\n', encoding='utf-8')
    index = SymbolIndex(resolve_environment())
    hint = help_at_caret('from local import sim|', index)
    assert 'simple' not in [item.label for item in hint.candidates]


def test_typed_single_and_multiple_local_outputs(editor_index):
    """Use semantic output contracts for the selected call, including output order."""
    index, tmp_path = editor_index
    (tmp_path / 'local' / 'two_outputs.nf').write_text(
        'geo = input_geometry("Geometry")\n'
        'weight = input_float("Weight", default=2.0)\n'
        'output("Shape", geo)\n'
        'output("Weight", weight)\n', encoding='utf-8'
    )
    index = SymbolIndex(resolve_environment())
    hint = help_at_caret('from local import two_outputs\nx = two_outputs(|', index)
    assert [(item.name, item.typ) for item in hint.signatures[0].outputs] == [
        ('Shape', 'Geometry'), ('Weight', 'Float')
    ]
    # Repeated requests must reuse the semantic result rather than prepare again.
    assert len(index._typed_cache) == 1
    help_at_caret('from local import two_outputs\nx = two_outputs(ge|', index)
    assert len(index._typed_cache) == 1


def test_source_fallback_keeps_named_outputs_if_analysis_fails(editor_index):
    """Broken source dependencies must not suppress the signature and output labels."""
    index, tmp_path = editor_index
    (tmp_path / 'local' / 'broken.nf').write_text(
        'x = input_float("X")\noutput("First", non_existing_function(x))\n'
        'output("Second", x)\n', encoding='utf-8'
    )
    index = SymbolIndex(resolve_environment())
    hint = help_at_caret('from local import broken\nx = broken(|', index)
    assert [(item.name, item.typ) for item in hint.signatures[0].outputs] == [
        ('First', '?'), ('Second', '?')
    ]


def test_tuple_extension_result_is_split_into_ordered_outputs():
    """Extension tuple results must show individual types, not one long type string."""
    from types import SimpleNamespace
    from NodeForge.editor.intelligence import _extension_signatures
    from NodeForge.extensions.contracts import TypeSpec
    from NodeForge.nf_types import NFType

    result = TypeSpec('TUPLE_FIXED', items=(
        TypeSpec('NF_SET', nf_types={NFType.GEOMETRY}),
        TypeSpec('NF_SET', nf_types={NFType.FLOAT}),
    ))
    signatures = _extension_signatures('separate', [SimpleNamespace(parameters=(), result=result)])
    assert [(item.name, item.typ) for item in signatures[0].outputs] == [
        ('Value 1', 'Geometry'), ('Value 2', 'Float')
    ]


def test_selected_math_source_return_type(editor_index):
    """math.circle_points produces a real Geometry contract, not a label guess."""
    import os
    archive = os.environ.get('NODEFORGE_MATH_TEST_ZIP')
    if not archive:
        pytest.skip('math archive not supplied')
    index, _ = editor_index
    packages.install_package_zip(Path(archive), allow_python=True)
    index = SymbolIndex(resolve_environment())
    hint = help_at_caret('from packages import math\nx = math.circle_points(|', index)
    assert [(item.name, item.typ) for item in hint.signatures[0].outputs] == [
        ('Geometry', 'Geometry')
    ]
