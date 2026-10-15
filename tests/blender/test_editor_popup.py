"""Blender-bound popup layout checks: no repeated parameters or clipped signature line."""

from NodeForge.editor.blender_ui import _popup_rows
from NodeForge.editor.intelligence import Assistance, Output, Parameter, Signature, Suggestion


def test_parameter_rows_show_single_keyword_and_typed_return():
    """Keyword candidates reuse signature rows instead of repeating label and detail."""
    sig = Signature('circle_points', (
        Parameter('Count', 'Int', '16', 'count'),
        Parameter('Radius', 'Float', '1.0', 'radius'),
        Parameter('Start Angle', 'Float', '0.0', 'start_angle'),
    ), (Output('Geometry', 'Geometry'),))
    candidates = tuple(Suggestion(p.keyword + '=', p.keyword + '=', 'parameter', p.label())
                       for p in sig.parameters)
    rows = _popup_rows(Assistance(candidates=candidates, signatures=(sig,)), 1, 600)
    labels = [row[0] for row in rows]
    assert labels == [
        'circle_points(', '  count: Int = 16', '  radius: Float = 1.0',
        '  start_angle: Float = 0.0', ')', '→ Geometry: Geometry',
    ]
    assert rows[2][1] is True


def test_many_parameters_fit_vertically_and_keep_focus_visible():
    """A long signature limits its parameter rows and centers the active field."""
    sig = Signature('large', tuple(Parameter(f'param{i}', 'Int', keyword=f'param{i}')
                                   for i in range(24)), (Output('Result', 'Float'),))
    rows = _popup_rows(Assistance(signatures=(sig,), active_parameter=17), 0, 240)
    labels = [row[0] for row in rows]
    assert labels[0].startswith('large(  ')
    assert '  param17: Int' in labels
    assert labels[-1] == '→ Result: Float'
    assert 21 * len(rows) + 8 <= 240


def test_multiple_outputs_remain_separate_rows():
    """Multiple result sockets retain their names, types, and public ordering."""
    sig = Signature('split', (Parameter('mesh', 'Geometry', keyword='mesh'),), (
        Output('Mesh', 'Geometry'), Output('Distance', 'Float'), Output('Valid', 'Bool'),
    ))
    rows = _popup_rows(Assistance(signatures=(sig,)), 0, 480)
    assert [row[0] for row in rows][-4:] == [
        '→ 3 outputs:', '  Mesh: Geometry', '  Distance: Float', '  Valid: Bool',
    ]
