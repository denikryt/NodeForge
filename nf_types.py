"""Canonical runtime semantic types used by the NodeForge compiler."""

from enum import Enum


class NFType(Enum):
    """Identify one NodeForge runtime semantic value type."""

    FLOAT = "FLOAT"
    VECTOR = "VECTOR"
    BOOL = "BOOL"
    GEOMETRY = "GEOMETRY"
    INT = "INT"
    MATERIAL = "MATERIAL"
    OBJECT = "OBJECT"
    STRING = "STRING"
    BUNDLE = "BUNDLE"
    ROTATION = "ROTATION"

    def __str__(self) -> str:
        """Return the stable human-readable semantic type token."""
        return self.value

    def __repr__(self) -> str:
        """Return the historical quoted type-token representation."""
        return repr(self.value)


NUMERIC_NF_TYPES = frozenset({NFType.FLOAT, NFType.INT})


def serialize_nf_type(typ: NFType) -> str:
    """Serialize one canonical type to its stable external token."""
    if not isinstance(typ, NFType):
        raise TypeError("typ must be an NFType")
    return typ.value


def deserialize_nf_type(token: str) -> NFType:
    """Deserialize one exact stable type token to its canonical type."""
    if not isinstance(token, str):
        raise TypeError("token must be a string")
    try:
        return NFType(token)
    except ValueError as exc:
        raise ValueError(f"Unknown NodeForge type token: {token!r}") from exc


__all__ = ["NFType", "NUMERIC_NF_TYPES", "serialize_nf_type", "deserialize_nf_type"]
