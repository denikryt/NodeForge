"""Turtle interpretation for L-system command streams."""

import math

from ...errors import CompileError
from ...geometry import _is_const_number
from ...nodes import _combine_xyz_mixed, _is_number_type, _math, _value
from ...values import Value
from .model import TurtlePoint, TurtleSegment


def _as_numeric_value(group, value, x, y, label):
    """Return *value* as a numeric Value, creating constants when necessary."""
    if isinstance(value, Value):
        if not _is_number_type(value.typ):
            raise CompileError(f"{label} must be numeric")
        return value
    if _is_const_number(value):
        return _value(group, value, x, y)
    raise CompileError(f"{label} must be numeric")


def _add(group, a, b, x, y):
    """Add static numbers directly or emit a math node for runtime values."""
    if _is_const_number(a) and _is_const_number(b):
        return float(a) + float(b)
    return _math(group, "ADD", [_as_numeric_value(group, a, x - 80, y, "add operand"), _as_numeric_value(group, b, x - 80, y - 40, "add operand")], x, y)


def _sub(group, a, b, x, y):
    """Subtract static numbers directly or emit a math node for runtime values."""
    if _is_const_number(a) and _is_const_number(b):
        return float(a) - float(b)
    return _math(group, "SUBTRACT", [_as_numeric_value(group, a, x - 80, y, "subtract operand"), _as_numeric_value(group, b, x - 80, y - 40, "subtract operand")], x, y)


def _mul(group, a, b, x, y):
    """Multiply static numbers directly or emit a math node for runtime values."""
    if _is_const_number(a) and _is_const_number(b):
        return float(a) * float(b)
    return _math(group, "MULTIPLY", [_as_numeric_value(group, a, x - 80, y, "multiply operand"), _as_numeric_value(group, b, x - 80, y - 40, "multiply operand")], x, y)


def _cos(group, value, x, y):
    """Compute cosine statically or emit a math node for runtime headings."""
    if _is_const_number(value):
        return math.cos(float(value))
    return _math(group, "COSINE", [_as_numeric_value(group, value, x - 80, y, "cos angle")], x, y)


def _sin(group, value, x, y):
    """Compute sine statically or emit a math node for runtime headings."""
    if _is_const_number(value):
        return math.sin(float(value))
    return _math(group, "SINE", [_as_numeric_value(group, value, x - 80, y, "sin angle")], x, y)


def _point_to_vector(group, point: TurtlePoint, x, y):
    """Create a Vector Value for a turtle point."""
    return _combine_xyz_mixed(group, [point.x, point.y, point.z], x, y)


def _angle_radians(group, angle_degrees, x, y):
    """Convert a DSL angle in degrees to radians."""
    return _mul(group, angle_degrees, math.pi / 180.0, x, y)


def interpret(group, stream: str, *, angle_degrees, step, x=0, y=0) -> list[TurtleSegment]:
    """Interpret an expanded L-system stream into drawn turtle segments."""
    angle = _angle_radians(group, angle_degrees, x - 180, y - 80)
    pos = TurtlePoint(0.0, 0.0, 0.0)
    heading = 0.0
    stack = []
    segments = []
    op_index = 0
    for ch in stream:
        px = x + (op_index % 8) * 180
        py = y - (op_index // 8) * 280
        if ch == "+":
            heading = _add(group, heading, angle, px, py)
            op_index += 1
        elif ch == "-":
            heading = _sub(group, heading, angle, px, py)
            op_index += 1
        elif ch == "[":
            stack.append((pos, heading))
        elif ch == "]":
            pos, heading = stack.pop()
        elif ch in {"F", "f"}:
            dx = _mul(group, step, _cos(group, heading, px, py - 40), px, py - 80)
            dy = _mul(group, step, _sin(group, heading, px, py - 120), px, py - 160)
            next_pos = TurtlePoint(_add(group, pos.x, dx, px, py - 200), _add(group, pos.y, dy, px, py - 240), pos.z)
            if ch == "F":
                segments.append(TurtleSegment(pos, next_pos))
            pos = next_pos
            op_index += 1
    return segments


__all__ = ["interpret", "_point_to_vector"]
