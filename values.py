"""Small data containers used during expression compilation."""

class Value:
    """Typed reference to a Blender node socket produced by the compiler."""
    def __init__(self, socket, typ):
        """Store the Blender socket and NodeForge semantic type for this value."""
        self.socket = socket
        self.typ = typ

__all__ = ["Value"]
