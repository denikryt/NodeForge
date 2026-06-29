"""Deterministic L-system token rewriting."""

from ...errors import CompileError
from .model import LSystemModule

MAX_LSYSTEM_SYMBOLS = 200000


def _legacy_modules(stream: str) -> tuple[LSystemModule, ...]:
    return tuple(LSystemModule(ch, (), ch) for ch in stream)


def _legacy_text(modules: tuple[LSystemModule, ...]) -> str:
    return "".join(module.raw or module.name for module in modules)


def expand(axiom, rules, iterations: int):
    """Rewrite *axiom* by applying one-token rules for *iterations* passes."""
    legacy = isinstance(axiom, str)
    stream = _legacy_modules(axiom) if legacy else tuple(axiom)
    parsed_rules = {k: (_legacy_modules(v) if isinstance(v, str) else tuple(v)) for k, v in rules.items()}
    if len(stream) > MAX_LSYSTEM_SYMBOLS:
        raise CompileError(f"L-system expansion exceeded MAX_LSYSTEM_SYMBOLS={MAX_LSYSTEM_SYMBOLS}")
    for _ in range(iterations):
        pieces = []
        total = 0
        for module in stream:
            replacement = parsed_rules.get(module.name, (module,))
            total += len(replacement)
            if total > MAX_LSYSTEM_SYMBOLS:
                raise CompileError(f"L-system expansion exceeded MAX_LSYSTEM_SYMBOLS={MAX_LSYSTEM_SYMBOLS}")
            pieces.extend(replacement)
        stream = tuple(pieces)
    return _legacy_text(stream) if legacy else stream


__all__ = ["MAX_LSYSTEM_SYMBOLS", "expand"]
