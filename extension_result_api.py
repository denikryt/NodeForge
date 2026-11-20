"""Blender-independent declarations and invocation types for dynamic extension results."""


class DynamicResults:
    """Annotation-only opt-in to invocation-dependent executable result tuples."""

    def __new__(cls, *args, **kwargs):
        """Reject runtime construction of the declaration-only result marker."""
        raise TypeError("DynamicResults is an annotation-only marker")
