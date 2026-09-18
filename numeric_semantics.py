"""Pure NodeForge scalar numeric type and compile-time value semantics."""

from __future__ import annotations

import math
import struct

from .errors import CompileError
from .nf_types import NFType, NUMERIC_NF_TYPES


INT_MIN = -2_147_483_648
INT_MAX = 2_147_483_647
FLOAT_MAX = 3.4028234663852886e38

_INT_PRESERVING_BINARY_OPS = frozenset({
    "ADD",
    "SUBTRACT",
    "MULTIPLY",
    "FLOOR_DIVIDE",
    "MODULO",
})
_FLOAT_RESULT_BINARY_OPS = frozenset({
    "ADD",
    "SUBTRACT",
    "MULTIPLY",
    "DIVIDE",
    "FLOOR_DIVIDE",
    "MODULO",
    "POWER",
})


def resolve_numeric_binary(
    operation: str,
    left_type: NFType,
    right_type: NFType,
) -> NFType | None:
    """Return the NodeForge scalar result type for one binary operation."""
    if left_type not in NUMERIC_NF_TYPES or right_type not in NUMERIC_NF_TYPES:
        return None
    if operation not in _FLOAT_RESULT_BINARY_OPS:
        return None
    if left_type is NFType.INT and right_type is NFType.INT:
        if operation in _INT_PRESERVING_BINARY_OPS:
            return NFType.INT
        return NFType.FLOAT
    return NFType.FLOAT


def resolve_numeric_unary_minus(operand_type: NFType) -> NFType | None:
    """Return the result type for numeric unary minus, or ``None`` if invalid."""
    if operand_type in NUMERIC_NF_TYPES:
        return operand_type
    return None


def normalize_int_constant(value: int) -> int:
    """Validate and return one statically known NodeForge signed-32 Int value."""
    if type(value) is not int or not INT_MIN <= value <= INT_MAX:
        raise CompileError(
            "NodeForge Int value must be in signed 32-bit range "
            "[-2147483648, 2147483647]"
        )
    return value


def _to_host_float(value: int | float) -> float:
    """Convert an exact numeric carrier to host float while rejecting Bool."""
    if type(value) not in {int, float}:
        raise CompileError("NodeForge Float value must be numeric")
    try:
        return float(value)
    except OverflowError as exc:
        raise CompileError("NodeForge Float value must be finite and binary32-representable") from exc


def normalize_float_constant(value: int | float) -> float:
    """Return the canonical finite binary32 carrier for a known NodeForge Float."""
    host = _to_host_float(value)
    if not math.isfinite(host) or abs(host) > FLOAT_MAX:
        raise CompileError("NodeForge Float value must be finite and binary32-representable")
    try:
        packed = struct.pack("!f", host)
    except (OverflowError, struct.error) as exc:
        raise CompileError("NodeForge Float value must be finite and binary32-representable") from exc
    result = struct.unpack("!f", packed)[0]
    if not math.isfinite(result):
        raise CompileError("NodeForge Float value must be finite and binary32-representable")
    return result


def try_normalize_float_operation_result(value: float) -> float | None:
    """Return a finite canonical binary32 operation result, or ``None`` on overflow."""
    try:
        host = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    if not math.isfinite(host) or abs(host) > FLOAT_MAX:
        return None
    try:
        result = struct.unpack("!f", struct.pack("!f", host))[0]
    except (OverflowError, struct.error):
        return None
    return result if math.isfinite(result) else None


def _require_int_pair(left: int, right: int) -> tuple[int, int]:
    """Validate one known Int operand pair before target-equivalent evaluation."""
    return normalize_int_constant(left), normalize_int_constant(right)


def _reject_int_floor_pair_trap(left: int, right: int) -> None:
    """Reject the Integer Math pair that traps in Blender 5.2 for // and %."""
    if left == INT_MIN and right == -1:
        raise CompileError(
            "NodeForge Int floor division/modulo is undefined for -2147483648 and -1"
        )


def evaluate_int_floor_divide(left: int, right: int) -> int:
    """Evaluate Integer Math ``DIVIDE_FLOOR`` semantics for known Int operands."""
    left, right = _require_int_pair(left, right)
    if right == 0:
        return 0
    _reject_int_floor_pair_trap(left, right)
    return normalize_int_constant(left // right)


def evaluate_int_floored_modulo(left: int, right: int) -> int:
    """Evaluate Integer Math ``FLOORED_MODULO`` semantics for known Int operands."""
    left, right = _require_int_pair(left, right)
    if right == 0:
        return 0
    _reject_int_floor_pair_trap(left, right)
    return normalize_int_constant(left % right)


def evaluate_float_basic(
    operation: str,
    left: int | float,
    right: int | float,
) -> float | None:
    """Evaluate canonical Float ADD/SUBTRACT/MULTIPLY, or return ``None`` on overflow."""
    a = normalize_float_constant(left)
    b = normalize_float_constant(right)
    if operation == "ADD":
        raw = a + b
    elif operation == "SUBTRACT":
        raw = a - b
    elif operation == "MULTIPLY":
        raw = a * b
    else:
        raise ValueError("evaluate_float_basic supports ADD, SUBTRACT and MULTIPLY only")
    return try_normalize_float_operation_result(raw)


def evaluate_float_divide(left: int | float, right: int | float) -> float | None:
    """Evaluate Blender Float DIVIDE semantics with a binary32 result boundary."""
    a = normalize_float_constant(left)
    b = normalize_float_constant(right)
    if b == 0.0:
        return normalize_float_constant(0.0)
    return try_normalize_float_operation_result(a / b)


def evaluate_float_floor_divide(left: int | float, right: int | float) -> float | None:
    """Evaluate the runtime ``DIVIDE -> FLOOR`` topology at binary32 boundaries."""
    quotient = evaluate_float_divide(left, right)
    if quotient is None:
        return None
    return try_normalize_float_operation_result(float(math.floor(quotient)))


def evaluate_float_floored_modulo(left: int | float, right: int | float) -> float | None:
    """Evaluate Blender ``FLOORED_MODULO`` using the characterized binary32 steps."""
    a = normalize_float_constant(left)
    b = normalize_float_constant(right)
    if b == 0.0:
        return normalize_float_constant(0.0)

    quotient = evaluate_float_divide(a, b)
    if quotient is None:
        return None
    floored = try_normalize_float_operation_result(float(math.floor(quotient)))
    if floored is None:
        return None
    product = evaluate_float_basic("MULTIPLY", floored, b)
    if product is None:
        return None
    return evaluate_float_basic("SUBTRACT", a, product)


def evaluate_float_negate(value: int | float) -> float | None:
    """Mirror the existing runtime ``0.0 - operand`` Float unary-minus topology."""
    return evaluate_float_basic("SUBTRACT", normalize_float_constant(0.0), value)


def evaluate_float_comparison(
    operation: str,
    left: int | float,
    right: int | float,
) -> bool:
    """Evaluate the canonical Compare(FLOAT) contract on canonical operands."""
    a = normalize_float_constant(left)
    b = normalize_float_constant(right)
    if operation == "LESS_THAN":
        return a < b
    if operation == "LESS_EQUAL":
        return a <= b
    if operation == "GREATER_THAN":
        return a > b
    if operation == "GREATER_EQUAL":
        return a >= b
    if operation == "EQUAL":
        return a == b
    if operation == "NOT_EQUAL":
        return a != b
    raise ValueError(f"Unsupported Float comparison operation: {operation}")


__all__ = [
    "FLOAT_MAX",
    "INT_MAX",
    "INT_MIN",
    "evaluate_float_basic",
    "evaluate_float_comparison",
    "evaluate_float_divide",
    "evaluate_float_floor_divide",
    "evaluate_float_floored_modulo",
    "evaluate_float_negate",
    "evaluate_int_floor_divide",
    "evaluate_int_floored_modulo",
    "normalize_float_constant",
    "normalize_int_constant",
    "resolve_numeric_binary",
    "resolve_numeric_unary_minus",
    "try_normalize_float_operation_result",
]
