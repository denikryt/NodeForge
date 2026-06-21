"""NodeForge Blender addon package."""

bl_info = {
    "name": "NodeForge",
    "author": "nachitima",
    "version": (0, 46, 2),
    "blender": (5, 2, 0),
    "location": "Geometry Nodes Editor > Sidebar > NodeForge; Add Menu > Script > Compile Group",
    "description": "Compile a Python-like DSL into Geometry Nodes node groups. Packaged function library: editable .nf sources plus optional per-function native helpers.",
    "category": "Node",
}

from .ui import register, unregister  # noqa: F401

