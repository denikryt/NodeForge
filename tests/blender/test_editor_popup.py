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
        self.edits = []

    def delete(self, *, type):
        assert type == 'PREVIOUS_CHARACTER'
        self.edits.append(('delete', type))
        index = self.current_character
        self.source = self.source[:index - 1] + self.source[index:]
        self.current_character -= 1

    def insert(self, *, text):
        self.edits.append(('insert', text))
        index = self.current_character
        self.source = self.source[:index] + text + self.source[index:]
        self.current_character += len(text)

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
    monkeypatch.setattr(ui, 'bpy', SimpleNamespace(
        context=SimpleNamespace(window_manager=window_manager),
        ops=SimpleNamespace(text=SimpleNamespace(delete=text.delete, insert=text.insert))))
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
    context = SimpleNamespace(area=area, region=area.regions[0])
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


def test_escape_suppresses_live_snapshot_if_timer_has_not_refreshed(popup_session):
    """A pre-Escape edit cannot reopen hints on a subsequent idle timer tick."""
    ui, operator, context, text, calls, event = popup_session
    state = ui._STATES[17]
    assert state.help.candidates
    text.source = 'nodes.c'
    text.current_character = len(text.source)
    assert state.key[1] == 'nodes.'
    assert operator.modal(context, event('ESC')) == {'RUNNING_MODAL'}
    assert state.suppressed == state.key == (101, 'nodes.c', 0, 7)
    assert not state.help.candidates
    ui._refresh_state(context.area)
    assert state.suppressed == state.key
    assert not state.help.candidates
    # The next editor action, rather than the idle tick, restores completion.
    assert operator.modal(context, event('BACK_SPACE')) == {'PASS_THROUGH'}
    assert state.suppressed is None and state.key is None
    ui._refresh_state(context.area)
    assert state.help.candidates


def test_non_text_clicks_do_not_reopen_dismissed_popup(popup_session):
    """Only the Text Editor WINDOW region may restart on a left click."""
    ui, operator, context, text, calls, event = popup_session
    state = ui._STATES[17]
    assert operator.modal(context, event('ESC')) == {'RUNNING_MODAL'}
    for region_type in ('HEADER', 'UI'):
        context.region = SimpleNamespace(type=region_type)
        assert operator.modal(context, event('LEFTMOUSE')) == {'PASS_THROUGH'}
        ui._refresh_state(context.area)
        assert state.suppressed == state.key
        assert not state.help.candidates
    context.region = None
    assert operator.modal(context, event('LEFTMOUSE')) == {'PASS_THROUGH'}
    assert state.suppressed == state.key
    context.region = SimpleNamespace(type='WINDOW')
    assert operator.modal(context, event('LEFTMOUSE')) == {'PASS_THROUGH'}
    assert state.suppressed is None and state.key is None
    ui._refresh_state(context.area)
    assert state.help.candidates


@pytest.mark.parametrize('key', ['RET', 'NUMPAD_ENTER', 'TAB'])
def test_completion_accepts_selected_candidate_once(popup_session, key):
    """All acceptance keys insert exactly the highlighted value without a newline."""
    ui, operator, context, text, calls, event = popup_session
    state = ui._STATES[17]
    state.help = Assistance(candidates=(Suggestion('mesh', 'mesh', 'function'),
                                        Suggestion('cube', 'cube', 'function')),
                            line=0, replace_start=0, replace_end=6)
    assert operator.modal(context, event('DOWN_ARROW')) == {'RUNNING_MODAL'}
    assert state.selection == 1
    assert operator.modal(context, event(key)) == {'RUNNING_MODAL'}
    assert text.source == 'cube' and '\n' not in text.source
    assert text.edits == [('delete', 'PREVIOUS_CHARACTER')] * 6 + [('insert', 'cube')]
    assert operator.modal(context, event(key)) == {'PASS_THROUGH'}
    assert len(text.edits) == 7


@pytest.mark.parametrize('key,kw', [
    ('RET', {}), ('NUMPAD_ENTER', {}), ('TAB', {}), ('RET', {'shift': True}),
    ('RET', {'ctrl': True}), ('NUMPAD_ENTER', {'alt': True}),
    ('RET', {'oskey': True}),
])
def test_native_keys_with_no_popup_or_modified_enter(popup_session, key, kw):
    """No popup or modified Enter leaves Blender free to handle the keystroke."""
    ui, operator, context, text, calls, event = popup_session
    state = ui._STATES[17]
    if kw:
        key_event = event(key)
        for name, value in kw.items():
            setattr(key_event, name, value)
        assert operator.modal(context, key_event) == {'PASS_THROUGH'}
    else:
        state.help = Assistance()
        assert operator.modal(context, event(key)) == {'PASS_THROUGH'}
    assert not text.edits
    assert text.source == 'nodes.'


@pytest.mark.parametrize('mutation', ['source', 'caret', 'line', 'text', 'range', 'selection'])
def test_stale_completion_is_never_inserted(popup_session, mutation):
    """Snapshot and span checks happen before any destructive editor operation."""
    ui, operator, context, text, calls, event = popup_session
    state = ui._STATES[17]
    if mutation == 'source':
        text.source = 'nodesx'
    elif mutation == 'caret':
        text.current_character = 4
    elif mutation == 'line':
        text.source = 'nodes.\n'
        text.current_line_index = 1
        text.current_character = 0
    elif mutation == 'text':
        other = _FakeText(source='nodes.', pointer=202)
        context.area.spaces.active.text = other
    elif mutation == 'range':
        state.help = Assistance(candidates=state.help.candidates, line=0,
                                replace_start=-1, replace_end=6)
    elif mutation == 'selection':
        state.selection = 99
    assert operator.modal(context, event('RET')) == {'PASS_THROUGH'}
    assert not text.edits
    if mutation == 'text':
        assert not context.area.spaces.active.text.edits
    # The next timer tick is responsible for refreshing changed snapshots.
    if mutation in {'source', 'caret', 'line', 'text'}:
        ui._refresh_state(context.area)
        assert state.key != (101, 'nodes.', 0, 6)


def test_source_outputs_share_popup_rows_and_preview_with_overflow():
    """AST-derived outputs feed the existing row renderer without a parallel formatter."""
    from NodeForge.editor.intelligence import _def_signatures, _return_preview
    source = """def many():
    result = node('X', outputs={'a': (ID('Mesh'), Geometry), 'b': (ID('Top'), Bool), 'c': (ID('Bottom'), Bool), 'd': (ID('Side'), Bool), 'e': (ID('UV Map'), Vector)})
    return result.a, result.b, result.c, result.d, result.e
"""
    signature = _def_signatures(source)['many'][0]
    rows = _popup_rows(Assistance(signatures=(signature,)), 0, 500)
    assert [row[0] for row in rows][-6:] == [
        '→ 5 outputs:', '  Mesh: Geometry', '  Top: Bool', '  Bottom: Bool',
        '  Side: Bool', '  … +1 outputs']
    assert _return_preview(signature) == '→ (Geometry, Bool, Bool, Bool, Vector)'


def test_real_blender_text_editor_escape_and_enter_leave_node_graph_untouched():
    """Exercise modal acceptance using actual Text datablocks and bpy text operators."""
    import bpy
    from NodeForge.editor import blender_ui as ui

    area = bpy.context.screen.areas[0]
    old_type = area.type
    initially_enabled = ui._ENABLED
    before_groups = set(bpy.data.node_groups.keys())
    text = bpy.data.texts.new('NFTest_PopupRealEditor')
    other = bpy.data.texts.new('NFTest_PopupOtherText')
    pointer = area.as_pointer()
    try:
        if not initially_enabled:
            ui.register()
        bpy.context.window_manager.nodeforge_assist_enabled = True
        area.type = 'TEXT_EDITOR'
        area.spaces.active.text = text
        region = next(item for item in area.regions if item.type == 'WINDOW')
        operator = SimpleNamespace(area_pointer=pointer)
        ui._RUNNING_AREAS.add(pointer)

        def event(key, unicode='', **modifiers):
            data = dict(type=key, value='PRESS', unicode=unicode, ctrl=False,
                        alt=False, oskey=False, shift=False)
            data.update(modifiers)
            return SimpleNamespace(**data)

        with bpy.context.temp_override(area=area, region=region):
            bpy.ops.text.insert(text='cu')
            ui._refresh_state(area)
            state = ui._STATES[pointer]
            assert any(item.insert == 'cube' for item in state.help.candidates)
            assert ui.NODEFORGE_OT_editor_session.modal(operator, bpy.context, event('ESC')) == {'RUNNING_MODAL'}
            assert state.suppressed == state.key
            assert ui.NODEFORGE_OT_editor_session.modal(operator, bpy.context, event('BACK_SPACE')) == {'PASS_THROUGH'}
            bpy.ops.text.delete(type='PREVIOUS_CHARACTER')
            bpy.ops.text.insert(text='u')
            ui._refresh_state(area)
            assert state.help.candidates and state.suppressed is None
            assert ui.NODEFORGE_OT_editor_session.modal(operator, bpy.context, event('RET')) == {'RUNNING_MODAL'}
            assert text.as_string() == 'cube'

            # A native Text datablock switch before the next poll invalidates the hint.
            area.spaces.active.text = other
            bpy.ops.text.insert(text='cu')
            assert ui.NODEFORGE_OT_editor_session.modal(operator, bpy.context, event('RET')) == {'PASS_THROUGH'}
            assert other.as_string() == 'cu'
            ui._refresh_state(area)
            assert ui.NODEFORGE_OT_editor_session.modal(operator, bpy.context, event('NUMPAD_ENTER')) == {'RUNNING_MODAL'}
            assert other.as_string() == 'cube'
    finally:
        ui._STATES.pop(pointer, None)
        ui._RUNNING_AREAS.discard(pointer)
        bpy.context.window_manager.nodeforge_assist_enabled = False
        area.type = old_type
        bpy.data.texts.remove(text)
        bpy.data.texts.remove(other)
        if not initially_enabled:
            ui.unregister()
    assert set(bpy.data.node_groups.keys()) == before_groups


def test_real_blender_escape_with_stale_snapshot_and_header_click():
    """Dismiss the current Text state, not a cached one; ignore header clicks."""
    import bpy
    from NodeForge.editor import blender_ui as ui

    area = bpy.context.screen.areas[0]
    old_type = area.type
    initially_enabled = ui._ENABLED
    manager = bpy.context.window_manager
    old_assist = getattr(manager, 'nodeforge_assist_enabled', False)
    pointer = area.as_pointer()
    text = bpy.data.texts.new('NFTest_PopupStaleEscape')
    operator = SimpleNamespace(area_pointer=pointer)

    def event(name):
        return SimpleNamespace(type=name, value='PRESS', unicode='', ctrl=False,
                               alt=False, oskey=False, shift=False)

    try:
        if not initially_enabled:
            ui.register()
        manager.nodeforge_assist_enabled = True
        area.type = 'TEXT_EDITOR'
        area.spaces.active.text = text
        window_region = next(region for region in area.regions if region.type == 'WINDOW')
        header_region = next(region for region in area.regions if region.type == 'HEADER')
        ui._RUNNING_AREAS.add(pointer)

        with bpy.context.temp_override(area=area, region=window_region):
            bpy.ops.text.insert(text='cu')
            ui._refresh_state(area)
            state = ui._STATES[pointer]
            assert state.help.candidates
            assert state.key[1] == 'cu'
            bpy.ops.text.insert(text='b')
            assert state.key[1] == 'cu' and text.as_string() == 'cub'
            assert ui.NODEFORGE_OT_editor_session.modal(operator, bpy.context,
                                                        event('ESC')) == {'RUNNING_MODAL'}
            assert state.key[1] == 'cub' and state.suppressed == state.key
            ui._refresh_state(area)
            assert state.suppressed == state.key
            assert not state.help.candidates

        with bpy.context.temp_override(area=area, region=header_region):
            assert ui.NODEFORGE_OT_editor_session.modal(operator, bpy.context,
                                                        event('LEFTMOUSE')) == {'PASS_THROUGH'}
        with bpy.context.temp_override(area=area, region=window_region):
            ui._refresh_state(area)
            assert state.suppressed == state.key
            assert not state.help.candidates
            assert ui.NODEFORGE_OT_editor_session.modal(operator, bpy.context,
                                                        event('LEFTMOUSE')) == {'PASS_THROUGH'}
            ui._refresh_state(area)
            assert state.suppressed is None
            assert state.help.candidates
    finally:
        ui._STATES.pop(pointer, None)
        ui._RUNNING_AREAS.discard(pointer)
        manager.nodeforge_assist_enabled = old_assist
        area.type = old_type
        bpy.data.texts.remove(text)
        if not initially_enabled:
            ui.unregister()


def test_raw_node_declared_selectors_match_blender_mesh_cube_sockets():
    """The supplied Blender 5.2 Mesh Cube exposes the declared Mesh/UV Map sockets."""
    import bpy
    from NodeForge.editor.intelligence import _def_signatures
    signatures = _def_signatures("""def mesh_cube():
    result = node('GeometryNodeMeshCube', outputs={'mesh': (ID('Mesh'), Geometry), 'uv': (ID('UV Map'), Vector)})
    return result.mesh, result.uv
""")
    tree = bpy.data.node_groups.new('NFTest_CompletionSocketNames', 'GeometryNodeTree')
    try:
        native = tree.nodes.new('GeometryNodeMeshCube')
        expected = [(socket.name, {'NodeSocketGeometry': 'Geometry',
                                   'NodeSocketVector': 'Vector'}[socket.bl_idname])
                    for socket in native.outputs if socket.name in {'Mesh', 'UV Map'}]
        assert [(o.name, o.typ) for o in signatures['mesh_cube'][0].outputs] == expected
        assert [socket.identifier for socket in native.outputs if socket.name in {'Mesh', 'UV Map'}] == [
            'Mesh', 'UV Map']
    finally:
        bpy.data.node_groups.remove(tree)
