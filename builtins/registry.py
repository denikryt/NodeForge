"""Central dispatch table for NodeForge DSL built-ins."""

from . import math, vector, fields, geometry, instancing, io, runtime

_MODULES = (math, vector, fields, geometry, instancing, io)
_RUNTIME_NAMES = set(runtime.NAMES)

_HANDLERS = {}
for _module in _MODULES:
    for _name in _module.NAMES:
        _HANDLERS[_name] = _module.compile_call

BUILTIN_NAMES = set(_HANDLERS) | _RUNTIME_NAMES
CALLABLE_BUILTIN_NAMES = set(_HANDLERS)
RUNTIME_BUILTIN_NAMES = _RUNTIME_NAMES


def has_builtin(name: str) -> bool:
    return name in BUILTIN_NAMES


def has_callable_builtin(name: str) -> bool:
    return name in CALLABLE_BUILTIN_NAMES


def compile_call(comp, expr, depth=0):
    name = expr.func.id
    handler = _HANDLERS.get(name)
    if handler is None:
        raise KeyError(name)
    return handler(comp, expr, depth)
