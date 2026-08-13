"""Compiler-owned Geometry Builder DSL state."""

from .constants import TYPE_GEOMETRY
from .compile_time import CompileTimeObject, reject_compile_time_object
from .errors import CompileError
from .geometry import _empty_geometry, _join_geometry
from .values import Value


class GeometryBuilder(CompileTimeObject):
    """Script-local procedural Geometry accumulator.

    A builder is owned by one source binding and exists only while compiling a
    single DSL source. Outside runtime Repeat Zones it stores pending Geometry
    values until a snapshot is needed. Inside Repeat Zones the active state is
    owned by runtime descriptors, not by this mutable object.
    """

    def __init__(self, binding_name):
        object.__setattr__(self, "binding_name", binding_name)
        object.__setattr__(self, "parts", [])
        object.__setattr__(self, "current", None)
        object.__setattr__(self, "in_runtime_state", False)


    def __eq__(self, other):
        """Use identity equality for mutable builder ownership."""
        return self is other

    def __hash__(self):
        """Use identity hashing so multiple builders remain distinct state owners."""
        return id(self)

    def usage_error(self, context):
        """Return the controlled escape diagnostic for this builder."""
        if context in {"output() value", "final expression", "final auto-output"}:
            return "geometry_builder object cannot be output directly; use builder.geometry"
        return "geometry_builder cannot escape script scope"

    def _check_geometry(self, value, message="builder.add(...) expects Geometry"):
        reject_compile_time_object(value, "geometry_builder value")
        if isinstance(value, list) or not isinstance(value, Value) or value.typ != TYPE_GEOMETRY:
            raise CompileError(message)
        return value

    def materialize(self, comp, x=0, y=0):
        """Return the current accumulated Geometry snapshot."""
        if self.current is None:
            self.current = _join_geometry(comp.group, list(self.parts), x, y)
            self.parts.clear()
        elif self.parts:
            self.current = _join_geometry(comp.group, [self.current] + list(self.parts), x, y)
            self.parts.clear()
        return self.current

    def add_value(self, comp, value, x=0, y=0):
        """Append one compile-time Geometry value after validation."""
        value = self._check_geometry(value)
        if self.current is None:
            self.parts.append(value)
        else:
            self.current = _join_geometry(comp.group, [self.current, value], x, y)

    def extend_values(self, comp, values, x=0, y=0):
        """Append already validated Geometry values in order."""
        checked = [self._check_geometry(v, "builder.extend(...) expects an array of Geometry values") for v in values]
        for value in checked:
            self.add_value(comp, value, x, y)

    def _runtime_visible_value(self, comp):
        """Return the nearest Repeat-owned Geometry value, or None outside runtime state."""
        frame, descriptor = comp.runtime_frame_for_builder(self)
        if descriptor is None:
            return None
        return descriptor.current_get(frame)

    def snapshot_for_runtime(self, comp, x=0, y=0):
        """Return the current Geometry used as a newly entered Repeat state value."""
        runtime_value = self._runtime_visible_value(comp)
        if runtime_value is not None:
            return runtime_value
        return self.materialize(comp, x, y)

    def set_runtime_value(self, value):
        """Commit a Repeat Zone output value back to the builder after lowering."""
        self._check_geometry(value)
        self.current = value
        self.parts.clear()
        self.in_runtime_state = False

    def geometry_value(self, comp, x=0, y=0):
        """Resolve `.geometry` from the nearest owning Repeat frame when present."""
        runtime_value = self._runtime_visible_value(comp)
        if runtime_value is not None:
            return runtime_value
        return self.materialize(comp, x, y)


def is_geometry_builder(value):
    """Return True when *value* is a GeometryBuilder instance."""
    return isinstance(value, GeometryBuilder)


def validate_geometry_builder_constructor(call):
    """Reject unsupported geometry_builder(...) constructor arguments."""
    if call.args:
        raise CompileError("geometry_builder() expects no arguments")
    if call.keywords:
        raise CompileError("geometry_builder() does not support keyword arguments")


__all__ = ["GeometryBuilder", "is_geometry_builder", "validate_geometry_builder_constructor"]
