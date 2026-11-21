"""Explicit compilation roles and safe physical execution boundaries."""

from __future__ import annotations

from enum import Enum


class RuntimeRole(str, Enum):
    """Identify the semantic runtime role independent of materialization depth."""

    ROOT = 'ROOT'
    NESTED = 'NESTED'
