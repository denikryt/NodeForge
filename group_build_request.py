"""Blender-independent physical build request for prepared NodeForge groups."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping

from .semantic.group import SemanticGroupCompilation


@dataclass(frozen=True)
class BlenderGroupBuildRequest:
    """Carry one prepared group plus the physical state needed for publication."""

    prepared_compilation: SemanticGroupCompilation
    name: str
    existing_group: object | None = None
    helper_namespace: str | None = None
    function_group_cache: object | None = None
    function_group_transaction: object | None = None
    function_compilation_trace: object | None = None
    function_compilation_inputs: Mapping[str, object] | None = None
    function_instance_key: str | None = None
    source_callable_session: object | None = None
    preserve_if_equivalent: bool = False

    def __post_init__(self) -> None:
        """Freeze detached mapping data and validate the prepared artifact boundary."""
        if not isinstance(self.prepared_compilation, SemanticGroupCompilation):
            raise TypeError("prepared_compilation must be SemanticGroupCompilation")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("name must be a non-empty string")
        if self.helper_namespace is not None and not isinstance(self.helper_namespace, str):
            raise TypeError("helper_namespace must be a string or None")
        if self.function_compilation_inputs is not None:
            object.__setattr__(
                self,
                "function_compilation_inputs",
                MappingProxyType(dict(self.function_compilation_inputs)),
            )
        if self.function_instance_key is not None and not isinstance(self.function_instance_key, str):
            raise TypeError("function_instance_key must be a string or None")
        if not isinstance(self.preserve_if_equivalent, bool):
            raise TypeError("preserve_if_equivalent must be bool")


__all__ = ["BlenderGroupBuildRequest"]
