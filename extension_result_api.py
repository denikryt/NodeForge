"""Blender-independent declarations and invocation types for dynamic extension results."""


class DynamicResults:
    """Annotation-only opt-in to invocation-dependent executable result tuples."""

    def __new__(cls, *args, **kwargs):
        """Reject runtime construction of the declaration-only result marker."""
        raise TypeError("DynamicResults is an annotation-only marker")


from dataclasses import dataclass
from .nf_types import NFType


@dataclass(frozen=True)
class ResultRuntimeArg:
    """Expose the actual NF type of one already-acquired runtime argument."""

    typ: NFType

    def __post_init__(self) -> None:
        """Require an exact runtime type token, rather than a declaration marker."""
        if type(self.typ) is not NFType:
            raise TypeError("ResultRuntimeArg.typ must be an exact NFType")


@dataclass(frozen=True)
class ResultStaticArg:
    """Expose one detached canonical compile-time value and its selected NF type."""

    typ: NFType
    value: object

    def __post_init__(self) -> None:
        """Reject mutable or noncanonical value carriers crossing the resolver boundary."""
        if type(self.typ) is not NFType:
            raise TypeError("ResultStaticArg.typ must be an exact NFType")
        def detached(item):
            """Admit only recursively immutable canonical literal storage."""
            return type(item) in (str, bool, int, float) or (
                type(item) is tuple and all(detached(part) for part in item)
            )
        if not detached(self.value):
            raise TypeError("ResultStaticArg.value must be detached immutable literal storage")
