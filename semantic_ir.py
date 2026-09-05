"""Blender-independent value-based records for NodeForge semantic lowering."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from .compiler_identities import BindingId, CallSiteId, FunctionId
from .nf_types import NFType


def _is_ir_option_value(value) -> bool:
    """Return whether *value* is detached immutable data safe for Semantic IR."""
    if value is None or isinstance(value, (bool, int, float, str, NFType)):
        return True
    if isinstance(value, tuple):
        return all(_is_ir_option_value(item) for item in value)
    return False


class IRFunctionMaterializationMode(str, Enum):
    """Select physical materialization semantics for one resolved reusable call."""

    SHARED = "SHARED"
    UNIQUE = "UNIQUE"


class IRCallableKind(str, Enum):
    """Identify a compiler-owned call target that Semantic IR can realize."""

    BUILTIN = "BUILTIN"
    OBJECT_INFO = "OBJECT_INFO"


@dataclass(frozen=True)
class IRCallableTarget:
    """Store backend-independent identity for one migrated core callable."""

    kind: IRCallableKind
    name: str

    def __post_init__(self) -> None:
        """Validate target identity without admitting handler objects."""
        if not isinstance(self.kind, IRCallableKind):
            raise TypeError("kind must be an IRCallableKind")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("callable target name must be a non-empty string")


class IRRawNodeOutputMode(str, Enum):
    """Preserve the source-selected raw-node result protocol."""

    SINGLE_OUTPUT = "SINGLE_OUTPUT"
    NAMED_OUTPUTS = "NAMED_OUTPUTS"


@dataclass(frozen=True)
class IRFunctionMaterialization:
    """Describe how one resolved reusable function call is materialized."""

    callee: FunctionId
    mode: IRFunctionMaterializationMode
    call_site: CallSiteId | None = None

    def __post_init__(self):
        """Validate the semantic materialization invariants."""
        if not isinstance(self.callee, FunctionId):
            raise TypeError("callee must be a FunctionId")
        if self.mode is IRFunctionMaterializationMode.SHARED:
            if self.call_site is not None:
                raise ValueError("shared function materialization cannot carry a CallSiteId")
            return
        if self.mode is IRFunctionMaterializationMode.UNIQUE:
            if not isinstance(self.call_site, CallSiteId):
                raise ValueError("unique function materialization requires a CallSiteId")
            if self.call_site.callee != self.callee:
                raise ValueError("function materialization CallSiteId must target the same callee")
            return
        raise ValueError("unsupported function materialization mode")


@dataclass(frozen=True)
class IRValue:
    """Identify one typed semantic value inside a single :class:`IRProgram`."""

    id: int
    typ: NFType

    def __post_init__(self):
        """Reject non-canonical semantic type identities."""
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")


@dataclass(frozen=True)
class IRArray:
    """Store one immutable compiler-structural source array result."""

    items: tuple["IRResult", ...]


@dataclass(frozen=True)
class IRTuple:
    """Store one fixed structural tuple returned by a migrated call."""

    items: tuple[IRValue, ...]

    def __post_init__(self) -> None:
        """Require one or more typed IR members."""
        object.__setattr__(self, "items", tuple(self.items))
        if not self.items or not all(isinstance(item, IRValue) for item in self.items):
            raise TypeError("IRTuple requires one or more IRValue members")


@dataclass(frozen=True)
class IRNamedOutputs:
    """Store declared raw-node outputs without collapsing one-entry ``outputs=``."""

    items: tuple[tuple[str, IRValue], ...]

    def __post_init__(self) -> None:
        """Require one or more unique named IR values."""
        object.__setattr__(self, "items", tuple(self.items))
        names = [name for name, _ in self.items]
        if not self.items or len(names) != len(set(names)):
            raise ValueError("IRNamedOutputs requires one or more unique names")
        if any(not isinstance(name, str) or not name for name in names):
            raise ValueError("IRNamedOutputs names must be non-empty strings")
        if any(not isinstance(value, IRValue) for _, value in self.items):
            raise TypeError("IRNamedOutputs members must be IRValue records")

    def get(self, name: str) -> IRValue:
        """Return one already-emitted member by declared name."""
        for item_name, value in self.items:
            if item_name == name:
                return value
        raise KeyError(name)


IRResult: TypeAlias = IRValue | IRArray | IRTuple | IRNamedOutputs


@dataclass(frozen=True)
class IRCallArgument:
    """Reference one already-emitted runtime operand of a migrated call."""

    parameter_name: str | None
    value: IRValue

    def __post_init__(self) -> None:
        """Reject structural/backend operands in runtime call slots."""
        if self.parameter_name is not None and not isinstance(self.parameter_name, str):
            raise TypeError("parameter_name must be a string or None")
        if not isinstance(self.value, IRValue):
            raise TypeError("IRCallArgument.value must be an IRValue")


@dataclass(frozen=True)
class IRCall:
    """Represent one typed AST-free compiler-owned core call."""

    results: tuple[IRValue, ...]
    depth: int
    target: IRCallableTarget
    arguments: tuple[IRCallArgument, ...]
    options: tuple[tuple[str, object], ...] = ()
    raw_output_mode: IRRawNodeOutputMode | None = None

    def __post_init__(self) -> None:
        """Validate call records before backend realization."""
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "options", tuple(self.options))
        if not isinstance(self.depth, int) or isinstance(self.depth, bool) or self.depth < 0:
            raise ValueError("IRCall.depth must be a non-negative integer")
        if not self.results or not all(isinstance(item, IRValue) for item in self.results):
            raise TypeError("IRCall requires one or more IRValue results")
        if len({item.id for item in self.results}) != len(self.results):
            raise ValueError("IRCall result IDs must be unique")
        if not isinstance(self.target, IRCallableTarget):
            raise TypeError("IRCall.target must be an IRCallableTarget")
        if not all(isinstance(item, IRCallArgument) for item in self.arguments):
            raise TypeError("IRCall arguments must be IRCallArgument records")
        option_names = []
        for option in self.options:
            if not isinstance(option, tuple) or len(option) != 2:
                raise TypeError("IRCall options must be (name, value) tuples")
            name, value = option
            if not isinstance(name, str) or not name:
                raise ValueError("IRCall option names must be non-empty strings")
            if not _is_ir_option_value(value):
                raise TypeError("IRCall option values must be detached immutable IR data")
            option_names.append(name)
        if len(option_names) != len(set(option_names)):
            raise ValueError("IRCall option names must be unique")
        if self.target.kind is not IRCallableKind.BUILTIN and self.raw_output_mode is not None:
            raise ValueError("raw output mode is valid only for builtin calls")
        if self.target.kind is IRCallableKind.BUILTIN and self.target.name == "node":
            if not isinstance(self.raw_output_mode, IRRawNodeOutputMode):
                raise ValueError("raw node IRCall requires an explicit raw output mode")
            option_map = dict(self.options)
            if self.raw_output_mode is IRRawNodeOutputMode.SINGLE_OUTPUT:
                if len(self.results) != 1:
                    raise ValueError("SINGLE_OUTPUT raw node IRCall requires exactly one result")
                if option_map.get("outputs") is not None:
                    raise ValueError("SINGLE_OUTPUT raw node IRCall cannot carry named outputs")
                if not isinstance(option_map.get("output"), str) or not option_map.get("output"):
                    raise ValueError("SINGLE_OUTPUT raw node IRCall requires output metadata")
                if not isinstance(option_map.get("typ"), NFType):
                    raise TypeError("SINGLE_OUTPUT raw node IRCall requires an NFType typ option")
            else:
                outputs = option_map.get("outputs")
                if not isinstance(outputs, tuple) or not outputs:
                    raise ValueError("NAMED_OUTPUTS raw node IRCall requires named output metadata")
                names = []
                for output_item in outputs:
                    if not isinstance(output_item, tuple) or len(output_item) != 2:
                        raise TypeError("NAMED_OUTPUTS metadata must contain (name, NFType) pairs")
                    output_name, output_type = output_item
                    if not isinstance(output_name, str) or not output_name:
                        raise ValueError("NAMED_OUTPUTS names must be non-empty strings")
                    if not isinstance(output_type, NFType):
                        raise TypeError("NAMED_OUTPUTS types must be NFType members")
                    names.append(output_name)
                if len(names) != len(set(names)):
                    raise ValueError("NAMED_OUTPUTS raw node IRCall requires unique output names")
                if len(outputs) != len(self.results):
                    raise ValueError("NAMED_OUTPUTS raw node IRCall result count must match declared outputs")
                if option_map.get("output") is not None or option_map.get("typ") is not None:
                    raise ValueError("NAMED_OUTPUTS raw node IRCall cannot carry single-output metadata")
        elif self.raw_output_mode is not None:
            raise ValueError("raw output mode is valid only for node() builtin calls")


@dataclass(frozen=True)
class IRLiteral:
    """Produce one scalar runtime literal."""

    result: IRValue
    depth: int
    value: object


@dataclass(frozen=True)
class IRBinding:
    """Produce one value from an existing canonical runtime binding slot."""

    result: IRValue
    depth: int
    binding_id: BindingId


@dataclass(frozen=True)
class IRUnary:
    """Produce one value from a typed unary operation."""

    result: IRValue
    depth: int
    op: str
    operand: IRValue


@dataclass(frozen=True)
class IRBinary:
    """Produce one value from a typed binary operation."""

    result: IRValue
    depth: int
    op: str
    left: IRValue
    right: IRValue


@dataclass(frozen=True)
class IRBoolBinary:
    """Produce one Bool value from two ordered Bool operands."""

    result: IRValue
    depth: int
    op: str
    left: IRValue
    right: IRValue


@dataclass(frozen=True)
class IRCompare:
    """Produce one Bool value from a typed comparison."""

    result: IRValue
    depth: int
    op: str
    left: IRValue
    right: IRValue


@dataclass(frozen=True)
class IRConditional:
    """Produce one value by selecting between two same-typed values."""

    result: IRValue
    depth: int
    condition: IRValue
    true_value: IRValue
    false_value: IRValue


@dataclass(frozen=True)
class IRVectorLiteral:
    """Produce one Vector value from normalized compile-time components."""

    result: IRValue
    depth: int
    components: tuple[float, float, float]


@dataclass(frozen=True)
class IRObjectProperty:
    """Produce one typed Object Info property from an Object runtime value."""

    result: IRValue
    depth: int
    value: IRValue
    property_name: str


@dataclass(frozen=True)
class IRVectorComponent:
    """Produce one Float value from a Vector component."""

    result: IRValue
    depth: int
    value: IRValue
    component: str


IROperation: TypeAlias = (
    IRLiteral
    | IRBinding
    | IRUnary
    | IRBinary
    | IRBoolBinary
    | IRCompare
    | IRConditional
    | IRVectorLiteral
    | IRObjectProperty
    | IRVectorComponent
    | IRCall
)


@dataclass(frozen=True)
class IRProgram:
    """Store one ordered expression program and its final program-local result."""

    operations: tuple[IROperation, ...]
    result: IRResult


__all__ = [
    "IRCallableKind",
    "IRCallableTarget",
    "IRRawNodeOutputMode",
    "IRTuple",
    "IRNamedOutputs",
    "IRCallArgument",
    "IRCall",
    "IRFunctionMaterializationMode",
    "IRFunctionMaterialization",
    "IRValue",
    "IRArray",
    "IRResult",
    "IROperation",
    "IRProgram",
    "IRLiteral",
    "IRBinding",
    "IRUnary",
    "IRBinary",
    "IRBoolBinary",
    "IRCompare",
    "IRConditional",
    "IRVectorLiteral",
    "IRObjectProperty",
    "IRVectorComponent",
]
