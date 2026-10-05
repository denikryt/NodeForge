"""NodeForge Blender addon package."""

bl_info = {
    "name": "NodeForge",
    "author": "nachitima",
    "version": (0, 65, 1),
    "blender": (5, 2, 0),
    "location": "Geometry Nodes Editor > Sidebar > NodeForge; Add Menu > Script > Compile Group",
    "description": "Compile a Python-like DSL into Geometry Nodes node groups. Catalog library: reusable functions, bundled examples, and user-owned local DSL scripts.",
    "category": "Node",
}

from .evaluation_modes import EvaluationMode
from .extension_semantic_api import RuntimeRef
from .extension_annotations import (
    Bool, Bundle, Float, Geometry, Int, Material, Object, Rotation, String, Vector,
)



def register():
    """Register the NodeForge Blender add-on.

    The UI module imports bpy, so import it lazily to keep pure NodeForge
    submodules importable under ordinary CPython.
    """
    from .ui import register as _register

    return _register()


def unregister():
    """Unregister the NodeForge Blender add-on."""
    from .ui import unregister as _unregister

    return _unregister()


__all__ = [
    "bl_info", "register", "unregister", "EvaluationMode", "RuntimeRef",
    "Float", "Int", "Bool", "Vector", "Geometry", "Material",
    "Object", "String", "Bundle", "Rotation",
]
