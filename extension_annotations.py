"""Public annotation-only type markers for declarative Python extensions."""

from __future__ import annotations

from .nf_types import NFType


class _NFAnnotationMeta(type):
    """Prevent annotation markers from becoming runtime DSL values."""

    def __call__(cls, *args, **kwargs):
        """Reject runtime construction of annotation-only marker classes."""
        raise TypeError("NodeForge annotation markers are not runtime values")


class Float(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Float extension value."""

    __nodeforge_nf_type__ = NFType.FLOAT


class Int(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Int extension value."""

    __nodeforge_nf_type__ = NFType.INT


class Bool(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Bool extension value."""

    __nodeforge_nf_type__ = NFType.BOOL


class Vector(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Vector extension value."""

    __nodeforge_nf_type__ = NFType.VECTOR


class Geometry(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Geometry extension value."""

    __nodeforge_nf_type__ = NFType.GEOMETRY


class Material(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Material extension value."""

    __nodeforge_nf_type__ = NFType.MATERIAL


class Object(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Object extension value."""

    __nodeforge_nf_type__ = NFType.OBJECT


class String(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge String extension value."""

    __nodeforge_nf_type__ = NFType.STRING


class Bundle(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Bundle extension value."""

    __nodeforge_nf_type__ = NFType.BUNDLE


class Rotation(metaclass=_NFAnnotationMeta):
    """Annotate one NodeForge Rotation extension value."""

    __nodeforge_nf_type__ = NFType.ROTATION


MARKER_TO_NF_TYPE = {
    Float: NFType.FLOAT,
    Int: NFType.INT,
    Bool: NFType.BOOL,
    Vector: NFType.VECTOR,
    Geometry: NFType.GEOMETRY,
    Material: NFType.MATERIAL,
    Object: NFType.OBJECT,
    String: NFType.STRING,
    Bundle: NFType.BUNDLE,
    Rotation: NFType.ROTATION,
}


__all__ = [
    "Bool",
    "Bundle",
    "Float",
    "Geometry",
    "Int",
    "MARKER_TO_NF_TYPE",
    "Material",
    "Object",
    "Rotation",
    "String",
    "Vector",
]
