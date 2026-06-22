"""Compile-time command tables for branch-free runtime L-systems."""

from __future__ import annotations

from dataclasses import dataclass

from ...errors import CompileError

MOVE_MASK_ATTR = "nf_lsys_move_mask"
HEADING_INDEX_ATTR = "nf_lsys_heading_index"
DRAW_MASK_ATTR = "nf_lsys_draw_mask"


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


__all__ = [
    "BranchFreeCommandTable",
    "MOVE_MASK_ATTR",
    "HEADING_INDEX_ATTR",
    "DRAW_MASK_ATTR",
    "build_branch_free_command_table",
]
