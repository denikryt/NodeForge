"""Opt-in Text Editor completion UI; Blender events stay outside language services.

The draw handler presents passive hints, while one lightweight modal handler per
participating Text Editor handles only popup navigation/acceptance. Ordinary
text.insert keymaps remain untouched, unlike some third-party completers.
"""

from __future__ import annotations

from dataclasses import dataclass

import bpy
import blf
import gpu
from gpu_extras.batch import batch_for_shader

from .intelligence import Assistance, SymbolIndex, cursor_assistance


@dataclass
class _EditorState:
    """Track the last cursor snapshot and popup selection for one editor area."""

    key: tuple | None = None
    help: Assistance = Assistance()
    selection: int = 0
    suppressed: tuple | None = None
    error: str = ""


_STATES: dict[int, _EditorState] = {}
_RUNNING_AREAS: set[int] = set()
_SYMBOLS: SymbolIndex | None = None
_DRAW_HANDLE = None
_ENABLED = False


def _refresh_state(area) -> None:
    """Update assistance only when the text/caret changes; no compile side effects."""
    space = area.spaces.active
    if space.type != 'TEXT_EDITOR' or not bpy.context.window_manager.nodeforge_assist_enabled or space.text is None:
        _STATES.pop(area.as_pointer(), None)
        return
    text = space.text
    source = text.as_string()
    key = (text.as_pointer(), source, text.current_line_index, text.current_character)
    state = _STATES.setdefault(area.as_pointer(), _EditorState())
    if state.key == key:
        return
    state.key = key
    state.selection = 0
    try:
        global _SYMBOLS
        if _SYMBOLS is None:
            from ..environment_resolution import resolve_environment
            _SYMBOLS = SymbolIndex(resolve_environment())
        state.help = cursor_assistance(source, text.current_line_index, text.current_character, _SYMBOLS)
        state.error = ""
    except Exception as exc:
        state.help = Assistance()
        state.error = str(exc)
    for region in area.regions:
        if region.type == 'WINDOW':
            region.tag_redraw()


def _editor_tick():
    """Poll opt-in Text Editors without modifying Blender's insertion keymap."""
    if not _ENABLED:
        return None
    active = set()
    for window in bpy.context.window_manager.windows:
        for area in window.screen.areas:
            if area.type != 'TEXT_EDITOR' or not bpy.context.window_manager.nodeforge_assist_enabled:
                continue
            pointer = area.as_pointer()
            active.add(pointer)
            _refresh_state(area)
            if pointer in _RUNNING_AREAS:
                continue
            region = next((r for r in area.regions if r.type == 'WINDOW'), None)
            if region is None:
                continue
            try:
                with bpy.context.temp_override(window=window, area=area, region=region):
                    bpy.ops.nodeforge.editor_session('INVOKE_DEFAULT')
            except RuntimeError:
                continue
    for pointer in tuple(_STATES):
        if pointer not in active:
            _STATES.pop(pointer, None)
    _RUNNING_AREAS.intersection_update(active)
    return 0.25


def _rect(shader, x, y, width, height, color):
    """Draw one GPU rectangle in Text Editor region coordinates."""
    coords = ((x, y), (x + width, y), (x + width, y + height), (x, y + height))
    batch = batch_for_shader(shader, 'TRIS', {'pos': coords}, indices=((0, 1, 2), (0, 2, 3)))
    shader.bind()
    shader.uniform_float('color', color)
    batch.draw(shader)


def _popup_rows(hint: Assistance, selection: int, region_height: int):
    """Build compact, scroll-windowed rows without duplicating keyword parameters."""
    rows = []
    parameter_candidates = hint.candidates and all(item.kind == "parameter" for item in hint.candidates)
    if hint.signatures:
        signature = hint.signatures[0]
        outputs = signature.outputs
        output_lines = min(len(outputs), 4)
        footer_lines = 1 + (1 if len(outputs) <= 1 else output_lines + 2)
        other_candidates = () if parameter_candidates else hint.candidates[:8]
        available = max(1, (region_height - 28) // 21 - footer_lines - 1 - len(other_candidates))
        visible = min(12, available, len(signature.parameters))

        focus = hint.active_parameter
        if parameter_candidates and hint.candidates:
            selected_keyword = hint.candidates[selection].insert.removesuffix("=")
            for i, parameter in enumerate(signature.parameters):
                if parameter.keyword == selected_keyword:
                    focus = i
                    break
        start = max(0, min(focus - visible // 2, len(signature.parameters) - visible))
        end = start + visible
        title = f"{signature.name}("
        if start or end < len(signature.parameters):
            title += f"  {start + 1}-{end}/{len(signature.parameters)}"
        rows.append((title, False, "heading"))
        for index in range(start, end):
            parameter = signature.parameters[index]
            # Display the actual insertable spelling rather than repeating the
            # human-readable source label after a second keyword prefix.
            name = parameter.keyword or parameter.name
            label = name
            if parameter.typ:
                label += f": {parameter.typ}"
            if parameter.default is not None:
                label += f" = {parameter.default}"
            selected = (parameter_candidates and parameter.keyword is not None
                        and hint.candidates[selection].insert == parameter.keyword + "=")
            role = "active" if index == hint.active_parameter else "parameter"
            rows.append(("  " + label, selected, role))
        rows.append((")", False, "heading"))
        if len(outputs) == 1:
            result = outputs[0]
            rows.append((f"→ {result.name}: {result.typ}", False, "result"))
        elif len(outputs) > 1:
            rows.append((f"→ {len(outputs)} outputs:", False, "result"))
            for output in outputs[:output_lines]:
                rows.append((f"  {output.name}: {output.typ}", False, "result"))
            if len(outputs) > output_lines:
                rows.append((f"  … +{len(outputs) - output_lines} outputs", False, "muted"))
        else:
            rows.append(("→ type unknown", False, "muted"))
        if len(hint.signatures) > 1:
            rows.append((f"+{len(hint.signatures) - 1} overload(s)", False, "muted"))
        if parameter_candidates:
            # Keywords unique to another overload must remain selectable.
            visible_keys = {param.keyword for param in signature.parameters}
            for index, item in enumerate(hint.candidates):
                if item.insert.removesuffix("=") not in visible_keys:
                    rows.append((item.label + "  (other overload)", index == selection, "option"))
    else:
        other_candidates = hint.candidates[:8]

    if not parameter_candidates:
        start = max(0, min(selection - 4, len(hint.candidates) - 8))
        if len(hint.candidates) > 8:
            rows.append((f"Suggestions {start + 1}-{min(start + 8, len(hint.candidates))} / {len(hint.candidates)}", False, "muted"))
        for index in range(start, min(start + 8, len(hint.candidates))):
            item = hint.candidates[index]
            label = item.label + (f"   {item.detail}" if item.detail else "")
            rows.append((label, index == selection, "option"))
    return rows


def _fit_text(label: str, max_width: float) -> str:
    """Clip individual long fields by their rendered width, preserving layout."""
    if blf.dimensions(0, label)[0] <= max_width:
        return label
    ellipsis = "…"
    low, high = 0, len(label)
    while low < high:
        mid = (low + high + 1) // 2
        if blf.dimensions(0, label[:mid] + ellipsis)[0] <= max_width:
            low = mid
        else:
            high = mid - 1
    return label[:low] + ellipsis


def _draw_overlay():
    """Draw parameter rows and typed results beside the Blender text cursor."""
    context = bpy.context
    area = context.area
    region = context.region
    if area is None or region is None:
        return
    state = _STATES.get(area.as_pointer())
    if state is None or state.suppressed == state.key:
        return
    hint = state.help
    if not hint.candidates and not hint.signatures and not state.error:
        return
    space = area.spaces.active
    text = space.text
    if text is None:
        return
    x, y = space.region_location_from_cursor(text.current_line_index, text.current_character)
    if x < 0 or y < 0:
        return
    blf.size(0, 12)
    rows = _popup_rows(hint, state.selection, region.height)
    if state.error:
        rows.insert(0, ("NodeForge: " + state.error, False, "muted"))
    if not rows:
        return
    width = min(520, max(180, region.width - 12))
    row_height = 21
    height = row_height * len(rows) + 8
    x = min(max(6, x), max(6, region.width - width - 6))
    y = max(6, min(y - height - 8, region.height - height - 6))
    shader = gpu.shader.from_builtin('UNIFORM_COLOR')
    gpu.state.blend_set('ALPHA')
    try:
        _rect(shader, x, y, width, height, (0.08, 0.10, 0.14, 0.96))
        for i, (label, selected, role) in enumerate(rows):
            baseline = y + height - row_height * (i + 1) - 1
            if selected:
                _rect(shader, x + 3, baseline - 4, width - 6, row_height, (0.21, 0.35, 0.52, 0.90))
            if role == "active":
                color = (0.74, 0.88, 1.0, 1.0)
            elif role in {"heading", "result"}:
                color = (0.65, 0.82, 1.0, 1.0)
            elif role == "muted":
                color = (0.65, 0.68, 0.73, 1.0)
            else:
                color = (0.94, 0.94, 0.94, 1.0)
            blf.position(0, x + 9, baseline, 0)
            blf.color(0, *color)
            blf.draw(0, _fit_text(label, width - 18))
    finally:
        gpu.state.blend_set('NONE')


class NODEFORGE_OT_editor_session(bpy.types.Operator):
    """Receive popup navigation while allowing normal Text Editor keys through."""

    bl_idname = 'nodeforge.editor_session'
    bl_label = 'NodeForge Completion Session'
    bl_options = {'INTERNAL'}

    def invoke(self, context, event):
        """Start one modal listener per Text Editor area."""
        self.area_pointer = context.area.as_pointer()
        if self.area_pointer in _RUNNING_AREAS:
            return {'CANCELLED'}
        _RUNNING_AREAS.add(self.area_pointer)
        context.window_manager.modal_handler_add(self)
        return {'RUNNING_MODAL'}

    def modal(self, context, event):
        """Accept Tab, navigate Up/Down, dismiss Escape; pass every other event."""
        pointer = self.area_pointer
        if not _ENABLED or pointer not in _RUNNING_AREAS:
            _RUNNING_AREAS.discard(pointer)
            return {'CANCELLED'}
        if context.area is None or context.area.as_pointer() != pointer:
            return {'PASS_THROUGH'}
        space = context.area.spaces.active
        if space.type != 'TEXT_EDITOR' or not bpy.context.window_manager.nodeforge_assist_enabled or space.text is None:
            _RUNNING_AREAS.discard(pointer)
            _STATES.pop(pointer, None)
            return {'CANCELLED'}
        state = _STATES.get(pointer)
        if state is None or not state.help.candidates or state.suppressed == state.key:
            return {'PASS_THROUGH'}
        if event.value != 'PRESS':
            return {'PASS_THROUGH'}
        if event.type in {'UP_ARROW', 'DOWN_ARROW'}:
            count = len(state.help.candidates)
            state.selection = (state.selection + (1 if event.type == 'DOWN_ARROW' else -1)) % count
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'ESC':
            state.suppressed = state.key
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        if event.type == 'TAB' and not (event.ctrl or event.alt or event.oskey):
            hint = state.help
            text = space.text
            if text.current_line_index != hint.line or text.current_character != hint.replace_end:
                return {'PASS_THROUGH'}
            suggestion = hint.candidates[state.selection]
            for _ in range(hint.replace_end - hint.replace_start):
                bpy.ops.text.delete(type='PREVIOUS_CHARACTER')
            bpy.ops.text.insert(text=suggestion.insert)
            state.suppressed = state.key
            context.area.tag_redraw()
            return {'RUNNING_MODAL'}
        return {'PASS_THROUGH'}


class NODEFORGE_OT_refresh_editor_symbols(bpy.types.Operator):
    """Refresh installed package/Local catalogs for text completion."""

    bl_idname = 'nodeforge.refresh_editor_symbols'
    bl_label = 'Refresh NodeForge Completions'

    def execute(self, context):
        """Discard captured environment and cursor assistance snapshots."""
        global _SYMBOLS
        _SYMBOLS = None
        for state in _STATES.values():
            state.key = None
            state.suppressed = None
        return {'FINISHED'}


def _draw_header(self, context):
    """Expose one window-manager toggle and explicit catalog refresh."""
    layout = self.layout
    layout.prop(context.window_manager, 'nodeforge_assist_enabled', text='NodeForge', toggle=True)
    if context.window_manager.nodeforge_assist_enabled:
        layout.operator('nodeforge.refresh_editor_symbols', text='', icon='FILE_REFRESH')


def register():
    """Register Text Editor UI without replacing any global editing keymaps."""
    global _ENABLED, _DRAW_HANDLE
    bpy.types.WindowManager.nodeforge_assist_enabled = bpy.props.BoolProperty(
        name='NodeForge Completion', description='Show NodeForge call and argument suggestions', default=False)
    bpy.utils.register_class(NODEFORGE_OT_editor_session)
    bpy.utils.register_class(NODEFORGE_OT_refresh_editor_symbols)
    bpy.types.TEXT_HT_header.append(_draw_header)
    _DRAW_HANDLE = bpy.types.SpaceTextEditor.draw_handler_add(_draw_overlay, (), 'WINDOW', 'POST_PIXEL')
    _ENABLED = True
    if not bpy.app.background:
        bpy.app.timers.register(_editor_tick, first_interval=0.2)


def unregister():
    """Remove handlers, timer and runtime state when disabling NodeForge."""
    global _ENABLED, _DRAW_HANDLE, _SYMBOLS
    _ENABLED = False
    if bpy.app.timers.is_registered(_editor_tick):
        bpy.app.timers.unregister(_editor_tick)
    if _DRAW_HANDLE is not None:
        bpy.types.SpaceTextEditor.draw_handler_remove(_DRAW_HANDLE, 'WINDOW')
        _DRAW_HANDLE = None
    bpy.types.TEXT_HT_header.remove(_draw_header)
    bpy.utils.unregister_class(NODEFORGE_OT_refresh_editor_symbols)
    bpy.utils.unregister_class(NODEFORGE_OT_editor_session)
    del bpy.types.WindowManager.nodeforge_assist_enabled
    _STATES.clear()
    _RUNNING_AREAS.clear()
    _SYMBOLS = None
