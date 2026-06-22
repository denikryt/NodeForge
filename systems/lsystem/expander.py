"""Deterministic L-system string rewriting."""

from ...errors import CompileError

MAX_LSYSTEM_SYMBOLS = 200000


def expand(axiom: str, rules: dict[str, str], iterations: int) -> str:
    """Rewrite *axiom* by applying single-symbol rules for *iterations* passes."""
    stream = axiom
    if len(stream) > MAX_LSYSTEM_SYMBOLS:
        raise CompileError(f"L-system expansion exceeded MAX_LSYSTEM_SYMBOLS={MAX_LSYSTEM_SYMBOLS}")
    for _ in range(iterations):
        pieces = []
        total = 0
        for ch in stream:
            replacement = rules.get(ch, ch)
            total += len(replacement)
            if total > MAX_LSYSTEM_SYMBOLS:
                raise CompileError(f"L-system expansion exceeded MAX_LSYSTEM_SYMBOLS={MAX_LSYSTEM_SYMBOLS}")
            pieces.append(replacement)
        stream = "".join(pieces)
    return stream


__all__ = ["MAX_LSYSTEM_SYMBOLS", "expand"]
