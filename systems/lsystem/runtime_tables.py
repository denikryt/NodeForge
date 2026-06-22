"""Compile-time command tables for runtime L-systems."""

from __future__ import annotations

from dataclasses import dataclass

from ...errors import CompileError

MOVE_MASK_ATTR = "nf_lsys_move_mask"
HEADING_INDEX_ATTR = "nf_lsys_heading_index"
DRAW_MASK_ATTR = "nf_lsys_draw_mask"
PATH_ID_ATTR = "nf_lsys_path_id"
PATH_DEPTH_ATTR = "nf_lsys_path_depth"
PARENT_ATTACH_INDEX_ATTR = "nf_lsys_parent_attach_index"
ANCHOR_MASK_ATTR = "nf_lsys_anchor_mask"
LOCAL_POSITION_ATTR = "nf_lsys_local_position"
WORLD_POSITION_ATTR = "nf_lsys_world_position"


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


def build_branch_free_command_table(stream: str) -> BranchFreeCommandTable:
    """Build a generated-mesh command table for a branch-free expanded stream.

    The table stores two point samples per input symbol and one edge per symbol.
    Ordered point-domain accumulation walks those samples in mesh order.
    """
    if "[" in stream or "]" in stream:
        raise CompileError("branch_free_vectorized_runtime_backend received a branched L-system stream")

    vertices: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    move_mask: list[float] = []
    heading_index: list[float] = []
    draw_mask: list[bool] = []
    heading = 0
    draw_count = 0

    for idx, ch in enumerate(stream):
        start_i = 2 * idx
        end_i = start_i + 1
        vertices.append((0.0, 0.0, 0.0))
        vertices.append((0.0, 0.0, 0.0))
        edges.append((start_i, end_i))

        start_heading = heading
        end_heading = heading
        end_move = 0.0
        edge_draw = False

        if ch == "+":
            end_heading = heading + 1
            heading = end_heading
        elif ch == "-":
            end_heading = heading - 1
            heading = end_heading
        elif ch in {"F", "f"}:
            end_move = 1.0
            edge_draw = ch == "F"
            if edge_draw:
                draw_count += 1
        else:
            # Grammar symbols are preserved by expansion and ignored by turtle emission.
            pass

        move_mask.extend((0.0, end_move))
        heading_index.extend((float(start_heading), float(end_heading)))
        draw_mask.append(edge_draw)

    if draw_count <= 0:
        raise CompileError("Branch-free runtime L-system backend requires at least one drawn F segment")

    return BranchFreeCommandTable(
        vertices=tuple(vertices),
        edges=tuple(edges),
        move_mask=tuple(move_mask),
        heading_index=tuple(heading_index),
        draw_mask=tuple(draw_mask),
        symbol_count=len(stream),
        draw_count=draw_count,
    )


def build_branch_aware_command_table(stream: str) -> BranchAwareCommandTable:
    """Build a generated-mesh command table for branched runtime L-systems.

    Every logical path starts with one synthetic anchor point.  Child paths record
    a real parent attach point, so branches that start at a path origin never need
    an invalid Sample Index sentinel.
    """
    vertices: list[tuple[float, float, float]] = []
    edges: list[tuple[int, int]] = []
    move_mask: list[float] = []
    heading_index: list[float] = []
    draw_mask: list[bool] = []
    anchor_mask: list[bool] = []
    path_id_values: list[int] = []
    path_depth_values: list[int] = []
    parent_attach_values: list[int] = []
    paths: list[BranchPathRecord] = []
    draw_count = 0
    max_branch_depth = 0

    def add_point(state: _PathState, *, move: float, heading: int, anchor: bool) -> int:
        point_index = len(vertices)
        vertices.append((0.0, 0.0, 0.0))
        move_mask.append(float(move))
        heading_index.append(float(heading))
        anchor_mask.append(bool(anchor))
        path_id_values.append(state.path_id)
        path_depth_values.append(state.depth)
        parent_attach_values.append(state.parent_attach_point_index)
        return point_index

    def new_path(parent_path_id: int, parent_attach_point_index: int, depth: int, initial_heading: int) -> _PathState:
        path_id = len(paths)
        dummy = _PathState(
            path_id=path_id,
            parent_path_id=parent_path_id,
            parent_attach_point_index=parent_attach_point_index,
            anchor_point_index=-1,
            depth=depth,
            initial_heading_index=initial_heading,
            current_point_index=-1,
            heading=initial_heading,
        )
        anchor_index = add_point(dummy, move=0.0, heading=initial_heading, anchor=True)
        state = _PathState(
            path_id=path_id,
            parent_path_id=parent_path_id,
            parent_attach_point_index=parent_attach_point_index,
            anchor_point_index=anchor_index,
            depth=depth,
            initial_heading_index=initial_heading,
            current_point_index=anchor_index,
            heading=initial_heading,
        )
        # The anchor was written before the final state existed, but all fields are
        # identical except anchor/current point, which are not per-point attributes.
        paths.append(BranchPathRecord(
            path_id=path_id,
            parent_path_id=parent_path_id,
            parent_attach_point_index=parent_attach_point_index,
            anchor_point_index=anchor_index,
            depth=depth,
            initial_heading_index=initial_heading,
        ))
        return state

    current = new_path(parent_path_id=-1, parent_attach_point_index=-1, depth=0, initial_heading=0)
    stack: list[_PathState] = []

    for ch in stream:
        if ch == "[":
            stack.append(current)
            child = new_path(
                parent_path_id=current.path_id,
                parent_attach_point_index=current.current_point_index,
                depth=current.depth + 1,
                initial_heading=current.heading,
            )
            max_branch_depth = max(max_branch_depth, child.depth)
            current = child
            continue
        if ch == "]":
            if not stack:
                raise CompileError("L-system branch-aware runtime table received unmatched ]")
            current = stack.pop()
            continue

        start_index = current.current_point_index
        end_heading = current.heading
        end_move = 0.0
        edge_draw = False
        if ch == "+":
            end_heading = current.heading + 1
        elif ch == "-":
            end_heading = current.heading - 1
        elif ch in {"F", "f"}:
            end_move = 1.0
            edge_draw = ch == "F"
            if edge_draw:
                draw_count += 1
        else:
            # Grammar symbols are preserved by expansion and ignored by turtle emission.
            pass

        end_index = add_point(current, move=end_move, heading=end_heading, anchor=False)
        edges.append((start_index, end_index))
        draw_mask.append(edge_draw)
        current.current_point_index = end_index
        current.heading = end_heading

    if stack:
        raise CompileError("L-system branch-aware runtime table received unclosed [")
    if draw_count <= 0:
        raise CompileError("Branch-aware runtime L-system backend requires at least one drawn F segment")

    return BranchAwareCommandTable(
        vertices=tuple(vertices),
        edges=tuple(edges),
        move_mask=tuple(move_mask),
        heading_index=tuple(heading_index),
        draw_mask=tuple(draw_mask),
        anchor_mask=tuple(anchor_mask),
        path_id=tuple(path_id_values),
        path_depth=tuple(path_depth_values),
        parent_attach_index=tuple(parent_attach_values),
        paths=tuple(paths),
        symbol_count=len(stream),
        draw_count=draw_count,
        max_branch_depth=max_branch_depth,
    )


__all__ = [
    "ANCHOR_MASK_ATTR",
    "BranchAwareCommandTable",
    "BranchFreeCommandTable",
    "BranchPathRecord",
    "DRAW_MASK_ATTR",
    "HEADING_INDEX_ATTR",
    "LOCAL_POSITION_ATTR",
    "MOVE_MASK_ATTR",
    "PARENT_ATTACH_INDEX_ATTR",
    "PATH_DEPTH_ATTR",
    "PATH_ID_ATTR",
    "WORLD_POSITION_ATTR",
    "build_branch_aware_command_table",
    "build_branch_free_command_table",
]
