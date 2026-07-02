"""Registry marker for the geometry_builder() DSL constructor."""

from ..errors import CompileError
from ..geometry_builder import validate_geometry_builder_constructor

NAMES = {"geometry_builder"}


def compile_call(comp, expr, depth=0):
    """Reject expression-position geometry_builder() calls.

    The statement compiler owns successful construction so each builder has one
    simple source binding and cannot be created as an escaping temporary.
    """
    validate_geometry_builder_constructor(expr)
    raise CompileError("geometry_builder() must be assigned to a simple name")
