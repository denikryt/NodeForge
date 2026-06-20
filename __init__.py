"""NodeForge Blender addon package."""

bl_info = {
    "name": "NodeForge",
    "author": "nachitima",
    "version": (0, 19, 0),
    "blender": (5, 2, 0),
    "location": "Geometry Nodes Editor > Sidebar > GN Script; Add Menu > Script > Expression/Vector Group",
    "description": "Compile a Python-like DSL into Geometry Nodes node groups. Refactored into modules with documented functions.",
    "category": "Node",
}

from .ui import register, unregister  # noqa: F401

