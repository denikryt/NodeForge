"""Blender-bound popup layout checks: no repeated parameters or clipped signature line."""

import pytest
from types import SimpleNamespace

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


class _FakeText:
    """Minimal Text snapshot for testing modal state transitions without a UI window."""

    def __init__(self, source='nodes.', pointer=101):
        self.source = source
        self.pointer = pointer
        self.current_line_index = 0
        self.current_character = len(source)

    def as_pointer(self):
        return self.pointer

    def as_string(self):
        return self.source


@pytest.fixture
def popup_session(monkeypatch):
    """Run the real modal handler against an area-scoped, opt-in fake Text Editor."""
    from types import SimpleNamespace
    from NodeForge.editor import blender_ui as ui

    text = _FakeText()
    area = SimpleNamespace(
        as_pointer=lambda: 17,
        spaces=SimpleNamespace(active=SimpleNamespace(type='TEXT_EDITOR', text=text)),
        regions=[SimpleNamespace(type='WINDOW', tag_redraw=lambda: None)],
        tag_redraw=lambda: None,
    )
    window_manager = SimpleNamespace(nodeforge_assist_enabled=True)
    monkeypatch.setattr(ui, 'bpy', SimpleNamespace(context=SimpleNamespace(window_manager=window_manager)))
    monkeypatch.setattr(ui, '_ENABLED', True)
    monkeypatch.setattr(ui, '_SYMBOLS', object())
    monkeypatch.setattr(ui, '_STATES', {})
    monkeypatch.setattr(ui, '_RUNNING_AREAS', {17})
    candidate = Suggestion('mesh', 'mesh', 'function')
    calls = []

    def assistance(source, line, character, index):
        calls.append((source, line, character))
        return Assistance(candidates=(candidate,), line=line, replace_start=0, replace_end=character)

    monkeypatch.setattr(ui, 'cursor_assistance', assistance)
    context = SimpleNamespace(area=area)
    operator = SimpleNamespace(area_pointer=17)
    operator.modal = lambda ctx, event: ui.NODEFORGE_OT_editor_session.modal(operator, ctx, event)
    ui._refresh_state(area)

    def event(kind, *, unicode='', value='PRESS', ctrl=False, alt=False, oskey=False):
        return SimpleNamespace(type=kind, value=value, unicode=unicode, ctrl=ctrl,
                               alt=alt, oskey=oskey, shift=False)

    return ui, operator, context, text, calls, event


def test_dismissed_popup_stays_hidden_without_editor_input(popup_session):
    """Escape consumes only the active popup; repeated Escape/idle do not unlock it."""
    ui, operator, context, text, calls, event = popup_session
    state = ui._STATES[17]
    assert operator.modal(context, event('ESC')) == {'RUNNING_MODAL'}
    assert state.suppressed == state.key
    for tick in range(3):
        ui._refresh_state(context.area)
        assert state.suppressed == state.key
        assert operator.modal(context, event('MOUSEMOVE')) == {'PASS_THROUGH'}
        assert operator.modal(context, event('ESC')) == {'PASS_THROUGH'}
        assert operator.modal(context, event('A', unicode='a', value='RELEASE')) == {'PASS_THROUGH'}
        assert operator.modal(context, event('LEFT_SHIFT')) == {'PASS_THROUGH'}
    assert len(calls) == 1
    assert operator.modal(SimpleNamespace(area=SimpleNamespace(as_pointer=lambda: 18)),
                          event('BACK_SPACE')) == {'PASS_THROUGH'}
    assert state.suppressed == state.key


def test_edit_after_escape_recomputes_even_when_snapshot_returns_identical(popup_session):
    """Backspace/retype events unlock the popup before the next 250 ms poll."""
    ui, operator, context, text, calls, event = popup_session
    state = ui._STATES[17]
    old_key = state.key
    assert operator.modal(context, event('ESC')) == {'RUNNING_MODAL'}
    assert operator.modal(context, event('BACK_SPACE')) == {'PASS_THROUGH'}
    assert state.key is None and state.suppressed is None and not state.help.candidates
    # Blender handles two edits between ticks, returning to the identical source/caret.
    text.source = 'nodes'
    text.current_character = 5
    text.source = 'nodes.'
    text.current_character = 6
    ui._refresh_state(context.area)
    assert state.key == old_key and state.help.candidates
    assert len(calls) == 2
    assert operator.modal(context, event('ESC')) == {'RUNNING_MODAL'}
    assert operator.modal(context, event('PERIOD', unicode='.')) == {'PASS_THROUGH'}
    assert state.key is None


def test_caret_navigation_and_shortcuts_restart_but_noop_events_do_not(popup_session):
    """Mouse placement/navigation and editing shortcuts restart without consuming input."""
    ui, operator, context, text, calls, event = popup_session
    state = ui._STATES[17]
    for trigger in (event('LEFTMOUSE'), event('RIGHT_ARROW'), event('HOME'),
                    event('Z', ctrl=True), event('V', oskey=True), event('RET')):
        ui._refresh_state(context.area)
        operator.modal(context, event('ESC'))
        assert operator.modal(context, trigger) == {'PASS_THROUGH'}
        assert state.key is None and state.suppressed is None
    ui._refresh_state(context.area)
    operator.modal(context, event('ESC'))
    assert operator.modal(context, event('F5')) == {'PASS_THROUGH'}
    assert state.suppressed == state.key
