"""Explicit compilation roles and safe physical execution boundaries."""

from __future__ import annotations

from dataclasses import fields, is_dataclass
from enum import Enum

from ..errors import CompileError
from .ir import IRCall, IRCallableKind


class RuntimeRole(str, Enum):
    """Identify the semantic runtime role independent of materialization depth."""

    ROOT = 'ROOT'
    NESTED = 'NESTED'


def preflight_direct_base_operations(body):
    """Reject physical Extension API v2 calls before touching a user-owned tree.

    A backend extension receives unrestricted access to its target group and
    could erase unrelated nodes or links. A new-node journal cannot undo that
    mutation, so direct invocations are forbidden even with a resource journal.
    This scan follows effective semantic IR, not raw import names or unused
    source declarations.
    """
    def visit(value):
        """Walk typed IR by value without executing extension implementation code."""
        if isinstance(value, IRCall) and value.target.kind is IRCallableKind.EXTENSION:
            raise CompileError(
                f"Physical Extension API v2 call {value.target.name!r} "
                "is unsupported directly in an existing base GeometryNodeTree; "
                "call it inside a function group instead"
            )
        if is_dataclass(value) and not isinstance(value, type):
            for entry in fields(value):
                visit(getattr(value, entry.name))
        elif isinstance(value, (tuple, list)):
            for element in value:
                visit(element)
        elif isinstance(value, dict):
            for element in value.values():
                visit(element)

    visit(body)
