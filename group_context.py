"""Compiler-owned contextual values available while lowering a group body."""

from dataclasses import dataclass
from enum import Enum
from typing import Iterable

from .nf_types import NFType


class GroupContextSlot(str, Enum):
    """Identify a typed contextual value shared across group-body operations."""

    CURRENT_GEOMETRY = "current_geometry"
    GRID_UV = "grid_uv"


@dataclass(frozen=True)
class GroupContextSpec:
    """Describe the runtime type required by a group-context slot."""

    typ: NFType


GROUP_CONTEXT_SPECS = {
    GroupContextSlot.CURRENT_GEOMETRY: GroupContextSpec(NFType.GEOMETRY),
    GroupContextSlot.GRID_UV: GroupContextSpec(NFType.VECTOR),
}


@dataclass
class GroupContextAvailabilityCursor:
    """Track context slots available at the current semantic traversal point."""

    available_slots: set[GroupContextSlot]

    def snapshot(self) -> frozenset[GroupContextSlot]:
        """Return an immutable snapshot of the currently available slots."""
        return frozenset(self.available_slots)

    def replace(self, slots: Iterable[GroupContextSlot]) -> None:
        """Replace the available slots with the values from *slots*."""
        self.available_slots = set(slots)

    def mark_available(self, slot: GroupContextSlot) -> None:
        """Mark *slot* as available for subsequent semantic operations."""
        self.available_slots.add(slot)
