"""Compile-time command tables for runtime L-systems."""

from __future__ import annotations

from dataclasses import dataclass, field
from math import cos, radians, sin

from ...errors import CompileError
from ...geometry import _is_const_number
from ...values import Value
from .model import LSystemModule

MOVE_MASK_ATTR = "nf_lsys_move_mask"
HEADING_INDEX_ATTR = "nf_lsys_heading_index"
DRAW_MASK_ATTR = "nf_lsys_draw_mask"
PATH_ID_ATTR = "nf_lsys_path_id"
PATH_DEPTH_ATTR = "nf_lsys_path_depth"
PARENT_ATTACH_INDEX_ATTR = "nf_lsys_parent_attach_index"
ANCHOR_MASK_ATTR = "nf_lsys_anchor_mask"
LOCAL_POSITION_ATTR = "nf_lsys_local_position"
WORLD_POSITION_ATTR = "nf_lsys_world_position"
MOVE_DISTANCE_STATIC_ATTR = "nf_lsys_move_distance_static"
MOVE_PARAM_INDEX_ATTR = "nf_lsys_move_param_index"
TURN_DEGREES_STATIC_ATTR = "nf_lsys_turn_degrees_static"
TURN_PARAM_INDEX_ATTR = "nf_lsys_turn_param_index"
TURN_SIGN_ATTR = "nf_lsys_turn_sign"
MARKER_ID_ATTRS = (
    "nf_lsys_marker_id_a",
    "nf_lsys_marker_id_b",
    "nf_lsys_marker_id_c",
    "nf_lsys_marker_id_d",
)
MARKER_MASK_ATTR = "nf_lsys_marker_mask"
MARKER_TANGENT_ATTR = "nf_lsys_marker_tangent"
MARKER_DEPTH_ATTR = "nf_lsys_marker_depth"
MARKER_PATH_ID_ATTR = "nf_lsys_marker_path_id"
MARKER_ITERATION_ATTR = "nf_lsys_marker_iteration"


@dataclass(frozen=True)
class BranchFreeCommandTable:
    """Linear command table consumed by the vectorized runtime backend."""

    vertices: tuple[tuple[float, float, float], ...]
    edges: tuple[tuple[int, int], ...]
    move_mask: tuple[float, ...]
    heading_index: tuple[float, ...]
    draw_mask: tuple[bool, ...]
    symbol_count: int
    draw_count: int
    move_distance_static: tuple[float, ...] = ()
    move_param_index: tuple[int, ...] = ()
    turn_degrees_static: tuple[float, ...] = ()
    turn_param_index: tuple[int, ...] = ()
    turn_sign: tuple[float, ...] = ()
    marker_id: tuple[tuple[int, int, int, int], ...] = ()
    marker_tangent: tuple[tuple[float, float, float], ...] = ()
    marker_mask: tuple[bool, ...] = ()
    marker_depth: tuple[int, ...] = ()
    marker_path_id: tuple[int, ...] = ()
    marker_iteration: tuple[int, ...] = ()
    runtime_params: tuple[tuple[str, Value], ...] = ()
    marker_param_static: dict[str, tuple[float, ...]] = field(default_factory=dict)
    marker_param_index: dict[str, tuple[int, ...]] = field(default_factory=dict)
    marker_count: int = 0

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def edge_count(self) -> int:
        return len(self.edges)


@dataclass(frozen=True)
class BranchPathRecord:
    """One logical linear turtle path in a branched runtime command table."""

    path_id: int
    parent_path_id: int
    parent_attach_point_index: int
    anchor_point_index: int
    depth: int
    initial_heading_index: int


@dataclass(frozen=True)
class BranchAwareCommandTable:
    """Path-decomposed command table consumed by the branched runtime backend."""

    vertices: tuple[tuple[float, float, float], ...]
    edges: tuple[tuple[int, int], ...]
    move_mask: tuple[float, ...]
    heading_index: tuple[float, ...]
    draw_mask: tuple[bool, ...]
    anchor_mask: tuple[bool, ...]
    path_id: tuple[int, ...]
    path_depth: tuple[int, ...]
    parent_attach_index: tuple[int, ...]
    paths: tuple[BranchPathRecord, ...]
    symbol_count: int
    draw_count: int
    max_branch_depth: int
    move_distance_static: tuple[float, ...] = ()
    move_param_index: tuple[int, ...] = ()
    turn_degrees_static: tuple[float, ...] = ()
    turn_param_index: tuple[int, ...] = ()
    turn_sign: tuple[float, ...] = ()
    marker_id: tuple[tuple[int, int, int, int], ...] = ()
    marker_tangent: tuple[tuple[float, float, float], ...] = ()
    marker_mask: tuple[bool, ...] = ()
    marker_depth: tuple[int, ...] = ()
    marker_path_id: tuple[int, ...] = ()
    marker_iteration: tuple[int, ...] = ()
    runtime_params: tuple[tuple[str, Value], ...] = ()
    marker_param_static: dict[str, tuple[float, ...]] = field(default_factory=dict)
    marker_param_index: dict[str, tuple[int, ...]] = field(default_factory=dict)
    marker_count: int = 0

    @property
    def vertex_count(self) -> int:
        return len(self.vertices)

    @property
    def edge_count(self) -> int:
        return len(self.edges)


@dataclass
class _PathState:
    path_id: int
    parent_path_id: int
    parent_attach_point_index: int
    anchor_point_index: int
    depth: int
    initial_heading_index: int
    current_point_index: int
    heading: int
    heading_static: float = 0.0
    heading_coeffs: dict[int, float] = field(default_factory=dict)


def _legacy_modules(stream: str) -> tuple[LSystemModule, ...]:
    return tuple(LSystemModule(ch, (), ch) for ch in stream)


def _runtime_slot(runtime_slots: list[tuple[str, Value]], name: str, value) -> int:
    if not isinstance(value, Value):
        return -1
    for idx, (slot_name, slot_value) in enumerate(runtime_slots):
        if slot_name == name and slot_value is value:
            return idx
    runtime_slots.append((name, value))
    return len(runtime_slots) - 1


def _number_or_slot(value, source: str, runtime_slots: list[tuple[str, Value]]) -> tuple[float, int]:
    if isinstance(value, Value):
        return 0.0, _runtime_slot(runtime_slots, source, value)
    if _is_const_number(value):
        return float(value), -1
    raise CompileError("L-system runtime table received non-numeric module argument")


def _effective_move(module: LSystemModule, step, runtime_slots: list[tuple[str, Value]]) -> tuple[float, int]:
    if module.args:
        arg = module.args[0]
        return _number_or_slot(arg.value, arg.param_name or arg.source, runtime_slots)
    return _number_or_slot(step, "__ls_step__", runtime_slots)


def _effective_turn(module: LSystemModule, angle_degrees, sign: float, runtime_slots: list[tuple[str, Value]]) -> tuple[float, int, float]:
    if module.args:
        arg = module.args[0]
        static, idx = _number_or_slot(arg.value, arg.param_name or arg.source, runtime_slots)
    else:
        static, idx = _number_or_slot(angle_degrees, "__ls_angle__", runtime_slots)
    if idx >= 0:
        return 0.0, idx, sign
    return sign * static, -1, 0.0


def _init_marker_arrays():
    return {
        "ids": [], "tangent": [], "mask": [], "depth": [], "path": [], "iteration": [],
        "param_static": {}, "param_index": {},
    }


def _tangent_from_degrees(degrees: float) -> tuple[float, float, float]:
    angle = radians(float(degrees))
    return (cos(angle), sin(angle), 0.0)


def _append_marker_attrs(arrays, *, marker=None, module=None, depth=0, path_id=0, tangent=(0.0, 0.0, 0.0), runtime_slots=None):
    is_marker = marker is not None
    arrays["ids"].append(marker.marker_identity if is_marker else (0, 0, 0, 0))
    arrays["tangent"].append(tuple(float(v) for v in tangent) if is_marker else (0.0, 0.0, 0.0))
    arrays["mask"].append(bool(is_marker))
    arrays["depth"].append(depth if is_marker else 0)
    arrays["path"].append(path_id if is_marker else 0)
    arrays["iteration"].append(0 if is_marker else -1)
    for attr_name in list(arrays["param_static"]):
        arrays["param_static"][attr_name].append(0.0)
        arrays["param_index"][attr_name].append(-1)
    if not is_marker:
        return
    for attr_name, arg in zip(marker.parameter_names, module.args):
        if attr_name not in arrays["param_static"]:
            arrays["param_static"][attr_name] = [0.0] * len(arrays["mask"])
            arrays["param_index"][attr_name] = [-1] * len(arrays["mask"])
        static, idx = _number_or_slot(arg.value, arg.param_name or arg.source, runtime_slots)
        arrays["param_static"][attr_name][-1] = static
        arrays["param_index"][attr_name][-1] = idx


def build_branch_free_command_table(stream, *, angle_degrees=90.0, step=1.0, markers=None) -> BranchFreeCommandTable:
    """Build a generated-mesh command table for a branch-free expanded stream."""
    modules = _legacy_modules(stream) if isinstance(stream, str) else tuple(stream)
    if any(module.name in {"[", "]"} for module in modules):
        raise CompileError("branch_free_vectorized_runtime_backend received a branched L-system stream")
    markers = markers or {}
    vertices: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    move_mask: list[float] = []
    heading_index: list[float] = []
    draw_mask: list[bool] = []
    move_distance_static: list[float] = []
    move_param_index: list[int] = []
    turn_degrees_static: list[float] = []
    turn_param_index: list[int] = []
    turn_sign: list[float] = []
    runtime_slots: list[tuple[str, Value]] = []
    marker_arrays = _init_marker_arrays()
    heading = 0
    heading_static_degrees = 0.0
    draw_count = 0
    marker_count = 0

    def add_point(*, move_old=0.0, heading_old=0, marker_tangent=(0.0, 0.0, 0.0), move_static=0.0, move_idx=-1, turn_static=0.0, turn_idx=-1, turn_s=0.0, marker=None, module=None):
        vertices.append((0.0, 0.0, 0.0))
        move_mask.append(float(move_old))
        heading_index.append(float(heading_old))
        move_distance_static.append(float(move_static))
        move_param_index.append(int(move_idx))
        turn_degrees_static.append(float(turn_static))
        turn_param_index.append(int(turn_idx))
        turn_sign.append(float(turn_s))
        _append_marker_attrs(marker_arrays, marker=marker, module=module, depth=0, path_id=0, tangent=marker_tangent, runtime_slots=runtime_slots)

    for module in modules:
        name = module.name
        if name in markers:
            add_point(heading_old=heading, marker_tangent=_tangent_from_degrees(heading_static_degrees), marker=markers[name], module=module)
            marker_count += 1
            continue
        start_i = len(vertices)
        add_point(heading_old=heading)
        start_heading = heading
        end_heading = heading
        old_move = 0.0
        edge_draw = False
        mv_static = 0.0
        mv_idx = -1
        t_static = 0.0
        t_idx = -1
        t_sign = 0.0
        if name == "+":
            t_static, t_idx, t_sign = _effective_turn(module, angle_degrees, 1.0, runtime_slots)
            end_heading = heading + 1
            heading = end_heading
            heading_static_degrees += t_static
        elif name == "-":
            t_static, t_idx, t_sign = _effective_turn(module, angle_degrees, -1.0, runtime_slots)
            end_heading = heading - 1
            heading = end_heading
            heading_static_degrees += t_static
        elif name in {"F", "f"}:
            mv_static, mv_idx = _effective_move(module, step, runtime_slots)
            old_move = 1.0
            edge_draw = name == "F"
            if edge_draw:
                draw_count += 1
        add_point(move_old=old_move, heading_old=end_heading, move_static=mv_static, move_idx=mv_idx, turn_static=t_static, turn_idx=t_idx, turn_s=t_sign)
        edges.append((start_i, start_i + 1))
        draw_mask.append(edge_draw)

    if draw_count <= 0 and marker_count <= 0:
        raise CompileError("Branch-free runtime L-system backend requires at least one drawn F segment")

    return BranchFreeCommandTable(
        vertices=tuple(vertices), edges=tuple(edges), move_mask=tuple(move_mask), heading_index=tuple(heading_index),
        draw_mask=tuple(draw_mask), symbol_count=len(modules), draw_count=draw_count,
        move_distance_static=tuple(move_distance_static), move_param_index=tuple(move_param_index),
        turn_degrees_static=tuple(turn_degrees_static), turn_param_index=tuple(turn_param_index), turn_sign=tuple(turn_sign),
        marker_id=tuple(marker_arrays["ids"]), marker_tangent=tuple(marker_arrays["tangent"]), marker_mask=tuple(marker_arrays["mask"]), marker_depth=tuple(marker_arrays["depth"]),
        marker_path_id=tuple(marker_arrays["path"]), marker_iteration=tuple(marker_arrays["iteration"]),
        runtime_params=tuple(runtime_slots), marker_param_static={k: tuple(v) for k, v in marker_arrays["param_static"].items()},
        marker_param_index={k: tuple(v) for k, v in marker_arrays["param_index"].items()}, marker_count=marker_count,
    )


def build_branch_aware_command_table(stream, *, angle_degrees=90.0, step=1.0, markers=None) -> BranchAwareCommandTable:
    """Build a generated-mesh command table for branched runtime L-systems."""
    modules = _legacy_modules(stream) if isinstance(stream, str) else tuple(stream)
    markers = markers or {}
    vertices: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    move_mask: list[float] = []
    heading_index: list[float] = []
    draw_mask: list[bool] = []
    anchor_mask: list[bool] = []
    path_id_values: list[int] = []
    path_depth_values: list[int] = []
    parent_attach_values: list[int] = []
    move_distance_static: list[float] = []
    move_param_index: list[int] = []
    turn_degrees_static: list[float] = []
    turn_param_index: list[int] = []
    turn_sign: list[float] = []
    runtime_slots: list[tuple[str, Value]] = []
    marker_arrays = _init_marker_arrays()
    paths: list[BranchPathRecord] = []
    draw_count = 0
    marker_count = 0
    max_branch_depth = 0

    def add_point(state: _PathState, *, move: float, heading: int, anchor: bool, move_static=0.0, move_idx=-1, turn_static=0.0, turn_idx=-1, turn_s=0.0, marker=None, module=None) -> int:
        point_index = len(vertices)
        vertices.append((0.0, 0.0, 0.0))
        move_mask.append(float(move)); heading_index.append(float(heading)); anchor_mask.append(bool(anchor))
        path_id_values.append(state.path_id); path_depth_values.append(state.depth); parent_attach_values.append(state.parent_attach_point_index)
        move_distance_static.append(float(move_static)); move_param_index.append(int(move_idx)); turn_degrees_static.append(float(turn_static)); turn_param_index.append(int(turn_idx)); turn_sign.append(float(turn_s))
        _append_marker_attrs(marker_arrays, marker=marker, module=module, depth=state.depth, path_id=state.path_id, tangent=_tangent_from_degrees(state.heading_static), runtime_slots=runtime_slots)
        return point_index

    def new_path(parent_path_id: int, parent_attach_point_index: int, depth: int, initial_heading: int, *, heading_static=0.0, heading_coeffs=None) -> _PathState:
        path_id = len(paths)
        heading_coeffs = dict(heading_coeffs or {})
        dummy = _PathState(path_id, parent_path_id, parent_attach_point_index, -1, depth, initial_heading, -1, initial_heading, heading_static, dict(heading_coeffs))
        anchor_index = add_point(dummy, move=0.0, heading=initial_heading, anchor=True, turn_static=heading_static)
        state = _PathState(path_id, parent_path_id, parent_attach_point_index, anchor_index, depth, initial_heading, anchor_index, initial_heading, heading_static, dict(heading_coeffs))
        paths.append(BranchPathRecord(path_id, parent_path_id, parent_attach_point_index, anchor_index, depth, initial_heading))
        for slot_idx, coeff in sorted(heading_coeffs.items()):
            if coeff:
                state.current_point_index = add_point(state, move=0.0, heading=initial_heading, anchor=False, turn_idx=slot_idx, turn_s=coeff)
        return state

    current = new_path(parent_path_id=-1, parent_attach_point_index=-1, depth=0, initial_heading=0)
    stack: list[_PathState] = []
    for module in modules:
        name = module.name
        if name == "[":
            stack.append(current)
            child = new_path(
                current.path_id,
                current.current_point_index,
                current.depth + 1,
                current.heading,
                heading_static=current.heading_static,
                heading_coeffs=current.heading_coeffs,
            )
            max_branch_depth = max(max_branch_depth, child.depth)
            current = child
            continue
        if name == "]":
            if not stack:
                raise CompileError("L-system branch-aware runtime table received unmatched ]")
            current = stack.pop()
            continue
        if name in markers:
            add_point(current, move=0.0, heading=current.heading, anchor=False, marker=markers[name], module=module)
            marker_count += 1
            continue
        start_index = current.current_point_index
        end_heading = current.heading
        old_move = 0.0
        edge_draw = False
        mv_static = 0.0; mv_idx = -1; t_static = 0.0; t_idx = -1; t_sign = 0.0
        if name == "+":
            t_static, t_idx, t_sign = _effective_turn(module, angle_degrees, 1.0, runtime_slots)
            end_heading = current.heading + 1
            if t_idx >= 0:
                current.heading_coeffs[t_idx] = current.heading_coeffs.get(t_idx, 0.0) + t_sign
            else:
                current.heading_static += t_static
        elif name == "-":
            t_static, t_idx, t_sign = _effective_turn(module, angle_degrees, -1.0, runtime_slots)
            end_heading = current.heading - 1
            if t_idx >= 0:
                current.heading_coeffs[t_idx] = current.heading_coeffs.get(t_idx, 0.0) + t_sign
            else:
                current.heading_static += t_static
        elif name in {"F", "f"}:
            mv_static, mv_idx = _effective_move(module, step, runtime_slots)
            old_move = 1.0; edge_draw = name == "F"
            if edge_draw: draw_count += 1
        end_index = add_point(current, move=old_move, heading=end_heading, anchor=False, move_static=mv_static, move_idx=mv_idx, turn_static=t_static, turn_idx=t_idx, turn_s=t_sign)
        edges.append((start_index, end_index)); draw_mask.append(edge_draw)
        current.current_point_index = end_index; current.heading = end_heading
    if stack:
        raise CompileError("L-system branch-aware runtime table received unclosed [")
    if draw_count <= 0 and marker_count <= 0:
        raise CompileError("Branch-aware runtime L-system backend requires at least one drawn F segment")
    return BranchAwareCommandTable(
        vertices=tuple(vertices), edges=tuple(edges), move_mask=tuple(move_mask), heading_index=tuple(heading_index), draw_mask=tuple(draw_mask),
        anchor_mask=tuple(anchor_mask), path_id=tuple(path_id_values), path_depth=tuple(path_depth_values), parent_attach_index=tuple(parent_attach_values),
        paths=tuple(paths), symbol_count=len(modules), draw_count=draw_count, max_branch_depth=max_branch_depth,
        move_distance_static=tuple(move_distance_static), move_param_index=tuple(move_param_index), turn_degrees_static=tuple(turn_degrees_static),
        turn_param_index=tuple(turn_param_index), turn_sign=tuple(turn_sign), marker_id=tuple(marker_arrays["ids"]), marker_tangent=tuple(marker_arrays["tangent"]), marker_mask=tuple(marker_arrays["mask"]),
        marker_depth=tuple(marker_arrays["depth"]), marker_path_id=tuple(marker_arrays["path"]), marker_iteration=tuple(marker_arrays["iteration"]),
        runtime_params=tuple(runtime_slots), marker_param_static={k: tuple(v) for k, v in marker_arrays["param_static"].items()},
        marker_param_index={k: tuple(v) for k, v in marker_arrays["param_index"].items()}, marker_count=marker_count,
    )


__all__ = [
    "ANCHOR_MASK_ATTR", "BranchAwareCommandTable", "BranchFreeCommandTable", "BranchPathRecord", "DRAW_MASK_ATTR",
    "HEADING_INDEX_ATTR", "LOCAL_POSITION_ATTR", "MOVE_MASK_ATTR", "PARENT_ATTACH_INDEX_ATTR", "PATH_DEPTH_ATTR", "PATH_ID_ATTR", "WORLD_POSITION_ATTR",
    "MOVE_DISTANCE_STATIC_ATTR", "MOVE_PARAM_INDEX_ATTR", "TURN_DEGREES_STATIC_ATTR", "TURN_PARAM_INDEX_ATTR", "TURN_SIGN_ATTR", "MARKER_ID_ATTRS", "MARKER_MASK_ATTR",
    "MARKER_TANGENT_ATTR", "MARKER_DEPTH_ATTR", "MARKER_PATH_ID_ATTR", "MARKER_ITERATION_ATTR", "build_branch_aware_command_table", "build_branch_free_command_table",
]
