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
        'def move(geometry: Geometry, scale_xy: Float["Scale XY"] = 2.0) -> Geometry:\n'
        '    return transform(geometry, scale=scale_xy)\n', encoding="utf-8"
    )
    (local / "simple.nf").write_text(
        'def radius(radius: Float = 1.0) -> Float:\n'
        '    return radius\n', encoding="utf-8"
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
    hint = help_at_caret('from local import terrain as t\nx = t.move.mo|', index)
    assert [c.label for c in hint.candidates] == ["move"]
    hint = help_at_caret('from local.terrain.move import move as go\nx = go(|', index)
    assert hint.signatures[0].name == "go"
    assert [p.keyword for p in hint.signatures[0].parameters] == ["geometry", "scale_xy"]
    assert "scale_xy=" in [c.insert for c in hint.candidates]


def test_local_flat_keywords_and_active_parameter(editor_index):
    """Imported Local source labels supply public keywords and call position."""
    index, _ = editor_index
    hint = help_at_caret('from local.simple import radius\nx = radius(ra|', index)
    assert [c.insert for c in hint.candidates] == ["radius="]
    hint = help_at_caret('from local.simple import radius\nx = radius(radius=|', index)
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
    packages.install_package_zip(Path(archive))
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
    hint = help_at_caret('from local.simple import radius\nx = radius(radius=vector(x=1), |', index)
    assert hint.signatures and not hint.candidates
    hint = help_at_caret('from local.simple import radius\nx = radius("cu|', index)
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
        'def both(geo: Geometry, weight: Float = 2.0):\n'
        '    return geo, weight\n', encoding='utf-8'
    )
    index = SymbolIndex(resolve_environment())
    hint = help_at_caret('from local.two_outputs import both\nx = both(|', index)
    assert [(item.name, item.typ) for item in hint.signatures[0].outputs] == [
        ('Geo', 'Geometry'), ('Weight', 'Float')
    ]
    # Cached snapshots key on the source path and selected definition.
    assert len(index._source_cache) == 1
    help_at_caret('from local.two_outputs import both\nx = both(ge|', index)
    assert len(index._source_cache) == 1


def test_source_fallback_keeps_named_outputs_if_analysis_fails(editor_index):
    """Broken source dependencies must not suppress the signature and output labels."""
    index, tmp_path = editor_index
    (tmp_path / 'local' / 'broken.nf').write_text(
        'def broken(x: Float):\n'
        '    return non_existing_function(x), x\n', encoding='utf-8'
    )
    index = SymbolIndex(resolve_environment())
    hint = help_at_caret('from local.broken import broken\nx = broken(|', index)
    assert [(item.name, item.typ) for item in hint.signatures[0].outputs] == [
        ('Value 1', '?'), ('X', 'Float')
    ]


@pytest.mark.parametrize('statement, expected', [
    ('dummy = (result := unrelated())', [('Value 1', '?'), ('Value 2', '?')]),
    ("dummy = {'socket': (result := unrelated())}", [('Value 1', '?'), ('Value 2', '?')]),
    ('(result := unrelated())', [('Value 1', '?'), ('Value 2', '?')]),
    ('dummy = (other := unrelated())', [('Mesh', 'Geometry'), ('Mesh 2', 'Geometry')]),
])
def test_return_projection_discards_only_rebound_named_expr_targets(statement, expected):
    """Unsupported walrus expressions cannot retain obsolete socket metadata."""
    from NodeForge.editor.intelligence import _def_signatures
    source = ("def sample():\n"
              "    result = node('X', outputs={'out': (ID('Mesh'), Geometry)})\n"
              f"    {statement}\n"
              "    return result.out, result.out\n")
    signature = _def_signatures(source)['sample'][0]
    assert [(out.name, out.typ) for out in signature.outputs] == expected


def test_return_projection_drops_rebound_name_within_return_tuple():
    """A walrus rebinding in return invalidates a later socket access."""
    from NodeForge.editor.intelligence import _def_signatures, _return_preview
    source = ("def sample():\n"
              "    result = node('X', outputs={'out': (ID('Mesh'), Geometry)})\n"
              "    return (result := unrelated()), result.out\n")
    signature = _def_signatures(source)['sample'][0]
    assert [(out.name, out.typ) for out in signature.outputs] == [
        ('Value 1', '?'), ('Value 2', '?')]
    assert _return_preview(signature) == ''


def test_later_known_assignment_recovers_socket_metadata_after_walrus():
    """A later explicit declaration can safely establish a new raw-node binding."""
    from NodeForge.editor.intelligence import _def_signatures
    source = ("def sample():\n"
              "    result = node('X', outputs={'out': (ID('Old'), Vector)})\n"
              "    dummy = (result := unrelated())\n"
              "    result = node('X', outputs={'out': (ID('Mesh'), Geometry)})\n"
              "    return result.out\n")
    signature = _def_signatures(source)['sample'][0]
    assert [(out.name, out.typ) for out in signature.outputs] == [('Mesh', 'Geometry')]


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
    packages.install_package_zip(Path(archive))
    index = SymbolIndex(resolve_environment())
    hint = help_at_caret('from packages import math\nx = math.circle_points(|', index)
    assert [(item.name, item.typ) for item in hint.signatures[0].outputs] == [
        ('Geometry', 'Geometry')
    ]


def _install_nested_editor_package(tmp_path):
    """Publish an isolated frozen package with duplicate leaves and a shared prefix."""
    import json

    root = tmp_path / 'nested_editor_pkg'
    files = {
        'mesh/primitives.nf': (
            'def cube(size: Float, bevel: Float = 0.0) -> Float:\n'
            '    return size + bevel\n'
        ),
        'mesh/sockets.nf': (
            "def dual(geo: Geometry):\n"
            "    out = node('X', outputs={'mesh': (ID('Mesh'), Geometry), 'uv': (ID('UV Map'), Vector)})\n"
            "    return out.mesh, out.uv\n"
        ),
        'curve/primitives.nf': (
            'def cube(radius: Float = 1.0) -> Float:\n'
            '    return radius\n'
        ),
        'mesh/operations.nf': (
            'def smooth(geometry: Geometry, iterations: Int = 2) -> Geometry:\n'
            '    return geometry\n'
        ),
        'utilities.nf': (
            'def identity(value: Float) -> Float:\n'
            '    return value\n'
        ),
        'utilities/field.nf': (
            'def accumulate(value: Float, group_id: Int = 0) -> Float:\n'
            '    return value\n'
        ),
    }
    (root / 'examples').mkdir(parents=True, exist_ok=True)
    (root / 'examples' / 'preview.nf').write_text(
        "def example():\n"
        "    out = node('X', outputs={'mesh': (ID('Mesh'), Geometry), 'uv': (ID('UV Map'), Vector)})\n"
        "    return out.uv, out.mesh\n", encoding='utf-8')
    for relative, content in files.items():
        path = root / 'functions' / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding='utf-8')
    (root / 'nodeforge_package.json').write_text(json.dumps({
        'schema_version': 1, 'id': 'vendor.editor', 'import_name': 'nodes',
        'name': 'Nested Editor', 'version': '1.0.0', 'author': 'Test',
        'description': 'Nested completion fixture',
        'nodeforge_min_version': '0.65.0',
        'contents': {'functions': 'functions', 'examples': 'examples'},
    }), encoding='utf-8')
    packages.install_package_directory(root)
    return root


def test_nested_package_completion_uses_canonical_owners_and_short_index(editor_index):
    """Every namespace segment completes; ambiguous leaves need exact paths."""
    _, tmp_path = editor_index
    _install_nested_editor_package(tmp_path)
    index = SymbolIndex(resolve_environment())
    prefix = 'from packages import nodes as n\nx = '
    symbols = index.symbols(prefix)
    assert {'mesh', 'curve', 'utilities', 'identity', 'smooth', 'accumulate'} <= set(symbols['n'])
    assert 'cube' not in symbols['n']
    assert {'primitives', 'operations'} <= set(symbols['n.mesh'])
    assert 'cube' in symbols['n.mesh.primitives']
    assert 'cube' in symbols['n.curve.primitives']
    assert 'field' in symbols['n.utilities']
    assert 'accumulate' in symbols['n.utilities.field']
    assert [candidate.label for candidate in help_at_caret(prefix + 'n.me|', index).candidates] == ['mesh']
    assert [candidate.label for candidate in help_at_caret(prefix + 'n.mesh.pr|', index).candidates] == ['primitives']
    assert [candidate.label for candidate in help_at_caret(prefix + 'n.mesh.primitives.cu|', index).candidates] == ['cube']
    assert 'cube' not in [item.label for item in help_at_caret(prefix + 'n.cu|', index).candidates]
    assert [item.label for item in help_at_caret(prefix + 'n.utilities.fi|', index).candidates] == ['field']


def test_nested_package_signatures_are_owner_exact_and_frozen(editor_index):
    """Qualified overloads and unique shortcuts use their distinct saved source."""
    _, tmp_path = editor_index
    root = _install_nested_editor_package(tmp_path)
    index = SymbolIndex(resolve_environment())
    prefix = 'from packages import nodes as n\nx = '
    mesh = help_at_caret(prefix + 'n.mesh.primitives.cube(be|', index)
    assert [p.name for p in mesh.signatures[0].parameters] == ['size', 'bevel']
    assert 'bevel=' in [item.insert for item in mesh.candidates]
    curve = help_at_caret(prefix + 'n.curve.primitives.cube(ra|', index)
    assert [p.name for p in curve.signatures[0].parameters] == ['radius']
    assert 'radius=' in [item.insert for item in curve.candidates]
    shortcut = help_at_caret(prefix + 'n.smooth(it|', index)
    qualified = help_at_caret(prefix + 'n.mesh.operations.smooth(it|', index)
    assert shortcut.signatures == qualified.signatures
    assert [p.keyword for p in qualified.signatures[0].parameters] == ['geometry', 'iterations']
    assert help_at_caret(prefix + 'n.cube(|', index).signatures == ()
    # Environment source was frozen on selection: disk edits cannot change hints.
    (root / 'functions/mesh/primitives.nf').write_text('def cube(changed: Float):\n    return changed\n')
    again = help_at_caret(prefix + 'n.mesh.primitives.cube(be|', index)
    assert [p.name for p in again.signatures[0].parameters] == ['size', 'bevel']
    assert 'bevel=' in [item.insert for item in again.candidates]


@pytest.mark.parametrize('source, expected', [
    ("""def dual():
    result = node('GeometryNodeMeshCube', outputs={'r1': (ID('UV Map'), Vector), 'r0': (ID('Mesh'), Geometry)})
    return (result.r0, result.r1)
""", [('Mesh', 'Geometry'), ('UV Map', 'Vector')]),
    ("""def dual():
    result = node('GeometryNodeMeshCube', outputs={'r1': (ID('UV Map'), Vector), 'r0': (ID('Mesh'), Geometry)})
    return result.r0, result.r1
""", [('Mesh', 'Geometry'), ('UV Map', 'Vector')]),
    ("""def dual():
    result = node('X', outputs={'mesh': Geometry, 'uv_map': (3, Vector), 'direct': ('Normal', Vector)})
    return result.uv_map, result.mesh, result.direct
""", [('Uv Map', 'Vector'), ('Mesh', 'Geometry'), ('Normal', 'Vector')]),
    ("""def dual():
    output = node('X', outputs={'mesh': (ID('Mesh'), Geometry), 'bad': (ID(variable), Float)})
    return output.mesh, output.bad
""", [('Mesh', 'Geometry'), ('Value 2', '?')]),
    ("""def dual():
    data = node('X', outputs={'a': (ID('Mesh'), Geometry), 'b': (ID('Mesh'), Geometry)})
    return data.a, data.b
""", [('Mesh', 'Geometry'), ('Mesh 2', 'Geometry')]),
    ("""def dual(geo: Geometry, weight: Float):
    alias = geo
    return alias, weight
""", [('Alias', 'Geometry'), ('Weight', 'Float')]),
    ("""def dual():
    original = node('X', outputs={'mesh': (ID('Mesh'), Geometry)})
    alias = original
    copy = alias.mesh
    return copy, alias.mesh
""", [('Mesh', 'Geometry'), ('Mesh 2', 'Geometry')]),
    ("""def dual(geo: Geometry):
    return unknown(geo), geo
""", [('Value 1', '?'), ('Geo', 'Geometry')]),
    ("""def dual():
    result = node('X', outputs={'mesh': (ID('Mesh'), Geometry)})
    result = unknown()
    return result.mesh, result.missing
""", [('Value 1', '?'), ('Value 2', '?')]),
    ("""def dual():
    result = node('X', outputs={'mesh': (ID('Mesh'), Geometry)})
    if flag:
        result = unknown()
    return result.mesh, result.mesh
""", [('Value 1', '?'), ('Value 2', '?')]),
    ("""def dual(geo: Geometry):
    geo = random()
    return geo, geo
""", [('Geo', '?'), ('Geo 2', '?')]),
])
def test_source_return_projection_and_conservative_fallback(source, expected):
    """Literal raw selectors and simple assignments preserve only defensible metadata."""
    from NodeForge.editor.intelligence import _def_signatures
    signature = _def_signatures(source)['dual'][0]
    assert [(o.name, o.typ) for o in signature.outputs] == expected


def test_direct_single_source_output_and_annotation_labels():
    """Direct raw-node sockets use their declared name; explicit Type labels win."""
    from NodeForge.editor.intelligence import _def_signatures, _return_preview
    source = """def circle() -> Geometry:
    return node('GeometryNodeMeshCircle', output=ID('Mesh'), typ=Geometry)
def labeled() -> Geometry['Result']:
    return node('GeometryNodeMeshCircle', output=ID('Mesh'), typ=Geometry)
def unnamed(geo: Geometry) -> Geometry:
    return transform(geo)
def unknown():
    return other_function()
"""
    signatures = _def_signatures(source)
    assert [(o.name, o.typ) for o in signatures['circle'][0].outputs] == [('Mesh', 'Geometry')]
    assert [(o.name, o.typ) for o in signatures['labeled'][0].outputs] == [('Result', 'Geometry')]
    assert [(o.name, o.typ) for o in signatures['unnamed'][0].outputs] == [('Value', 'Geometry')]
    assert signatures['unknown'][0].outputs[0].typ == '?'
    assert _return_preview(signatures['circle'][0]) == '→ Geometry'
    assert _return_preview(signatures['unknown'][0]) == ''


def test_unsaved_function_source_survives_incomplete_call(editor_index):
    """An unfinished expression below a full declaration retains its output metadata."""
    index, _ = editor_index
    source = """def local_mesh():
    value = node('X', outputs={'mesh': (ID('Mesh'), Geometry), 'uv': (ID('UV Map'), Vector)})
    return value.mesh, value.uv
x = local_mesh(|"""
    hint = help_at_caret(source, index)
    assert [(o.name, o.typ) for o in hint.signatures[0].outputs] == [
        ('Mesh', 'Geometry'), ('UV Map', 'Vector')
    ]


def test_real_nodes_package_outputs(editor_index):
    """The supplied nodeforge.nodes package exposes actual raw-node selector labels."""
    import os
    from NodeForge.editor.intelligence import _return_preview
    source_root = os.environ.get('NODEFORGE_NODES_TEST_DIR')
    if source_root is None:
        pytest.skip('nodeforge.nodes fixture directory not supplied')
    index, _ = editor_index
    packages.install_package_directory(Path(source_root))
    index = SymbolIndex(resolve_environment())
    prefix = 'from packages import nodes as n\nx = n.mesh.primitives.'
    expected = {
        'mesh_cube': [('Mesh', 'Geometry'), ('UV Map', 'Vector')],
        'mesh_cone': [('Mesh', 'Geometry'), ('Top', 'Bool'), ('Bottom', 'Bool'),
                      ('Side', 'Bool'), ('UV Map', 'Vector')],
        'mesh_circle': [('Mesh', 'Geometry')],
    }
    for function, outputs in expected.items():
        hint = help_at_caret(prefix + function + '(|', index)
        assert [(o.name, o.typ) for o in hint.signatures[0].outputs] == outputs
    members = help_at_caret(prefix + 'mesh_cu|', index)
    assert [(s.label, s.detail) for s in members.candidates if s.label == 'mesh_cube'] == [
        ('mesh_cube', '→ (Geometry, Vector)')]



def test_package_source_output_metadata_is_frozen_and_example_visible(editor_index):
    """Package, aliases, and Examples show the same source-backed types and order."""
    _, tmp_path = editor_index
    root = _install_nested_editor_package(tmp_path)
    index = SymbolIndex(resolve_environment())
    qualified = help_at_caret('from packages import nodes as n\nx = n.mesh.sockets.dual(|', index)
    assert [(o.name, o.typ) for o in qualified.signatures[0].outputs] == [
        ('Mesh', 'Geometry'), ('UV Map', 'Vector')]
    short = help_at_caret('from packages import nodes as n\nx = n.dual(|', index)
    assert short.signatures[0].outputs == qualified.signatures[0].outputs
    example = help_at_caret('from examples import preview as ex\nx = ex.example(|', index)
    assert [(o.name, o.typ) for o in example.signatures[0].outputs] == [
        ('UV Map', 'Vector'), ('Mesh', 'Geometry')]
    (root / 'functions/mesh/sockets.nf').write_text('def dual(changed: Float):\n    return changed\n')
    frozen = help_at_caret('from packages import nodes as n\nx = n.mesh.sockets.dual(|', index)
    assert frozen.signatures[0].outputs == qualified.signatures[0].outputs


def test_plain_expr_and_docstring_do_not_erase_known_parameter_types():
    """Non-binding expressions preserve types, while assignment expressions do not."""
    from NodeForge.editor.intelligence import _def_signatures
    source = """def stable(geo: Geometry, weight: Float):
    '''A source-docstring does not rebind parameters.'''
    inspect(geo)
    return geo, weight

def mutated(geo: Geometry):
    inspect((geo := other()))
    return geo, geo
"""
    signatures = _def_signatures(source)
    assert [(o.name, o.typ) for o in signatures['stable'][0].outputs] == [
        ('Geo', 'Geometry'), ('Weight', 'Float')]
    assert [(o.name, o.typ) for o in signatures['mutated'][0].outputs] == [
        ('Geo', '?'), ('Geo 2', '?')]


def test_named_source_signature_and_receiver_completion(editor_index):
    """Preserve exact keys and expose only Python-spelling-safe fields."""
    index, _ = editor_index
    prefix = ('def measure(value: Float):\n'
              '    return {"value": value, "class": value, "two words": value, '
              '"K": value, "K": value, "unknown": value * 2}\n')
    signature = help_at_caret(prefix + 'x = measure(|', index).signatures[0]
    assert signature.result_kind == 'named'
    assert [o.key for o in signature.outputs] == ['value', 'class', 'two words', 'K', 'K', 'unknown']
    assert signature.outputs[0].typ == 'Float'
    for tail in ('x = measure(1.0).|', 'r = measure(1.0)\nx = r.|',
                 'r = measure(1.0)\na = r\nx = a.|'):
        assert {c.insert for c in help_at_caret(prefix + tail, index).candidates} == {'value', 'K', 'unknown'}
    assert not help_at_caret(prefix + 'r = measure(1.0)\nr = 3\nx = r.|', index).candidates
    assert not help_at_caret(prefix + 'r = measure(1.0)\nif True:\n    r = 3\nx = r.|', index).candidates
    singleton = help_at_caret('def f(x: Float):\n    return {"value": x}\nx = f(|', index).signatures[0]
    assert singleton.result_kind == 'named' and singleton.outputs[0].key == 'value'


def test_bundled_named_extension_receiver_completion(editor_index):
    """Use declared schemas and the compiler's dynamic result resolver, without bpy."""
    packages.install_package_directory(Path(__file__).resolve().parents[2] / 'packages/nodes')
    index = SymbolIndex(resolve_environment())
    prefix = 'from packages import nodes as n\n'
    assert {c.insert for c in help_at_caret(prefix + 'x = n.cylinder().|', index).candidates} == {'mesh', 'top', 'side', 'bottom', 'uv_map'}
    signature = help_at_caret(prefix + 'x = n.face_group_boundaries(|', index).signatures[0]
    assert signature.result_kind == 'named' and signature.outputs[0].key == 'boundary_edges'
    assert help_at_caret(prefix + 'x = n.domain_size(|', index).signatures[0].result_kind == 'dynamic'
    assert {c.insert for c in help_at_caret(prefix + 'x = n.domain_size(component="POINTCLOUD").|', index).candidates} == {'point_count'}
    assert {c.insert for c in help_at_caret(prefix + 'r = n.domain_size(component="INSTANCES")\nx = r.|', index).candidates} == {'instance_count'}
    assert {c.insert for c in help_at_caret(prefix + 'def f(g: Geometry):\n    r = n.domain_size(geometry=g, component="POINTCLOUD")\n    x = r.|', index).candidates} == {'point_count'}
    assert not help_at_caret(prefix + 'x = n.domain_size(component=unknown).|', index).candidates
