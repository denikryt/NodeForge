"""Tokenization and argument resolution for L-system module streams."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping

from ...errors import CompileError
from ...geometry import _is_const_number
from ...values import Value
from .model import LSystemMarker, LSystemModule, ModuleArg

BUILTIN_COMMANDS = frozenset(("F", "f", "+", "-", "[", "]"))
PARAMETERIZED_BUILTINS = frozenset(("F", "f", "+", "-"))
_IDENTIFIER_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_LEGACY_SYMBOL_RE = re.compile(r"^[A-Za-z0-9_]$")
_NUMERIC_LITERAL_RE = re.compile(r"^[+-]?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?$")
_DOMAIN = b"NodeForge/LSystem/MarkerIdentity/v1\0"


def validate_identifier(value: str, context: str) -> str:
    """Validate a public L-system identifier and return it unchanged."""
    if not isinstance(value, str):
        raise CompileError(f"{context} must be a compile-time string")
    if not _IDENTIFIER_RE.match(value):
        raise CompileError(f"{context} must match [A-Za-z_][A-Za-z0-9_]*")
    return value


def marker_identity(name: str) -> tuple[int, int, int, int]:
    """Return the stable four-component numeric identity for marker *name*."""
    digest = hashlib.sha256(_DOMAIN + name.encode("utf-8")).digest()
    out = []
    for idx in range(4):
        raw = int.from_bytes(digest[idx * 4:idx * 4 + 4], "big") & 0x7fffffff
        out.append(raw if raw != 0 else 1)
    return tuple(out)  # type: ignore[return-value]


def _parse_arg(text: str, params: Mapping[str, object], context: str) -> ModuleArg:
    if text == "":
        raise CompileError(f"{context} contains an empty module argument")
    if _NUMERIC_LITERAL_RE.match(text):
        return ModuleArg(source=text, value=float(text), is_runtime=False)
    if _IDENTIFIER_RE.match(text):
        if text not in params:
            raise CompileError(f"{context} references undeclared L-system parameter {text!r}")
        value = params[text]
        return ModuleArg(source=text, value=value, is_runtime=isinstance(value, Value), param_name=text)
    raise CompileError(f"{context} contains invalid module argument {text!r}")


def _read_parenthesized(value: str, start: int, context: str) -> tuple[str, int]:
    end = value.find(")", start + 1)
    if end < 0:
        raise CompileError(f"{context} contains an unclosed module argument list")
    inner = value[start + 1:end]
    if "(" in inner or ")" in inner:
        raise CompileError(f"{context} contains invalid nested module arguments")
    return inner, end + 1


def _parse_args(inner: str, params: Mapping[str, object], context: str) -> tuple[ModuleArg, ...]:
    if inner == "":
        raise CompileError(f"{context} contains an empty module argument list")
    if any(ch.isspace() for ch in inner):
        raise CompileError(f"{context} contains invalid whitespace in module arguments")
    pieces = inner.split(",")
    return tuple(_parse_arg(piece, params, context) for piece in pieces)


def _longest_marker_at(value: str, offset: int, marker_names: tuple[str, ...]) -> str | None:
    best = None
    for name in marker_names:
        if value.startswith(name, offset) and (best is None or len(name) > len(best)):
            best = name
    return best


def _declared_marker_inside_identifier(value: str, start: int, end: int, marker_names: tuple[str, ...]) -> bool:
    """Return True when compact legacy-prefix parsing can reach a marker before ``end``."""
    for offset in range(start + 1, end):
        if _longest_marker_at(value, offset, marker_names) is not None:
            return True
    return False


def parse_stream(value: str, *, params: Mapping[str, object], markers: Mapping[str, LSystemMarker], context: str) -> tuple[LSystemModule, ...]:
    """Parse an L-system string into declaration-aware module tokens."""
    if not isinstance(value, str):
        raise CompileError(f"{context} must be a compile-time string")
    marker_names = tuple(sorted(markers, key=len, reverse=True))
    modules: list[LSystemModule] = []
    i = 0
    n = len(value)
    while i < n:
        ch = value[i]
        if ch.isspace():
            raise CompileError(f"{context} contains invalid L-system symbol {ch!r}")
        # Parameterized built-in commands have first priority.
        if ch in PARAMETERIZED_BUILTINS and i + 1 < n and value[i + 1] == "(":
            inner, next_i = _read_parenthesized(value, i + 1, context)
            args = _parse_args(inner, params, context)
            if len(args) != 1:
                raise CompileError(f"{context} module {ch!r} expects exactly one argument")
            modules.append(LSystemModule(ch, args, value[i:next_i]))
            i = next_i
            continue
        # Declared multi-character marker modules use longest declared match.
        marker_name = _longest_marker_at(value, i, marker_names)
        if marker_name is not None:
            marker = markers[marker_name]
            next_i = i + len(marker_name)
            args: tuple[ModuleArg, ...] = ()
            raw_end = next_i
            if next_i < n and value[next_i] == "(":
                inner, raw_end = _read_parenthesized(value, next_i, context)
                args = _parse_args(inner, params, context)
            if len(args) != len(marker.parameter_names):
                raise CompileError(
                    f"{context} marker {marker_name!r} expects {len(marker.parameter_names)} argument(s)"
                )
            modules.append(LSystemModule(marker_name, args, value[i:raw_end]))
            i = raw_end
            continue
        if ch in BUILTIN_COMMANDS:
            modules.append(LSystemModule(ch, (), ch))
            i += 1
            continue
        # Unknown parenthesized identifier-like modules are invalid after the
        # one-character built-in fallback.  This preserves the compact syntax
        # contract where FLeaf(size) parses as F + Leaf(size) when only Leaf is
        # declared, while Leaf(size) without declaration remains an error.
        if ch.isalpha() or ch == "_":
            j = i
            while j < n and re.match(r"[A-Za-z0-9_]", value[j]):
                j += 1
            if j < n and value[j] == "(" and not _declared_marker_inside_identifier(value, i, j, marker_names):
                name = value[i:j]
                raise CompileError(f"{context} uses undeclared L-system module {name!r}")
        if _LEGACY_SYMBOL_RE.match(ch):
            modules.append(LSystemModule(ch, (), ch))
            i += 1
            continue
        raise CompileError(f"{context} contains invalid L-system symbol {ch!r}")
    return tuple(modules)


def parse_rule_predecessor(value: str) -> str:
    """Validate an ls_rule predecessor for this stage's one-symbol rule contract."""
    if not isinstance(value, str):
        raise CompileError("ls_rule() predecessor must be a compile-time string")
    if value == "":
        raise CompileError("ls_rule() predecessor cannot be empty")
    if len(value) != 1:
        raise CompileError("ls_rule() predecessor must be exactly one symbol")
    if value not in BUILTIN_COMMANDS and not _LEGACY_SYMBOL_RE.match(value):
        raise CompileError(f"ls_rule() predecessor contains invalid L-system symbol {value!r}")
    return value


def module_name(module: LSystemModule) -> str:
    """Return the rewrite-visible name of a parsed module."""
    return module.name


__all__ = [
    "BUILTIN_COMMANDS", "PARAMETERIZED_BUILTINS", "validate_identifier",
    "marker_identity", "parse_stream", "parse_rule_predecessor", "module_name",
]
