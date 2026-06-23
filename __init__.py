"""NodeForge Blender addon package."""

bl_info = {
    "name": "NodeForge",
    "author": "nachitima",
    "version": (0, 49, 20),
    "blender": (5, 2, 0),
    "location": "Geometry Nodes Editor > Sidebar > NodeForge; Add Menu > Script > Compile Group",
    "description": "Compile a Python-like DSL into Geometry Nodes node groups. Packaged function library: editable .nf sources plus optional per-function native helpers.",
    "category": "Node",
}



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


__all__ = ["bl_info", "register", "unregister"]

