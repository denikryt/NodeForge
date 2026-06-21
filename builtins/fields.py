"""Field input built-ins for NodeForge DSL."""

from ..errors import CompileError
from ..nodes import _position, _normal, _index, _id

NAMES = {"position", "normal", "index", "id"}


def compile_call(comp, expr, depth=0):
    name = expr.func.id
    x = depth * 240
    y = -depth * 90
    if expr.keywords:
        raise CompileError(f"{name}() does not support keyword arguments")
    if expr.args:
        raise CompileError(f"{name}() expects no arguments")
    if name == "position":
        return _position(comp.group, x, y)
    if name == "normal":
        return _normal(comp.group, x, y)
    if name == "index":
        return _index(comp.group, x, y)
    if name == "id":
        return _id(comp.group, x, y)
    raise CompileError(f"Unsupported field builtin: {name}")
