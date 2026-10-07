"""Blender-independent value-based records for NodeForge semantic lowering."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import TypeAlias

from ..compiler_identities import BindingId, CallSiteId, FunctionId, InputDeclarationId, InterfaceInputOrigin
from ..nf_types import NFType
from .group_context import GROUP_CONTEXT_SPECS, GroupContextSlot
from ..extension_contracts import ExtensionCallableId, PythonScalarKind, TypeSpec, validate_extension_argument_positions


def _is_ir_option_value(value) -> bool:
    """Return whether *value* is detached immutable data safe for Semantic IR."""
    if value is None or isinstance(value, (bool, int, float, str, NFType)):
        return True
    if isinstance(value, tuple):
        return all(_is_ir_option_value(item) for item in value)
    return False


def _is_raw_socket_selector(value) -> bool:
    """Return whether *value* is canonical detached raw socket selector data."""
    if isinstance(value, str):
        return bool(value)
    if type(value) is int:
        return value >= 0
    return (
        isinstance(value, tuple)
        and len(value) == 2
        and value[0] == "identifier"
        and isinstance(value[1], str)
        and bool(value[1])
    )


class IRFunctionMaterializationMode(str, Enum):
    """Select physical materialization semantics for one resolved reusable call."""

    SHARED = "SHARED"
    UNIQUE = "UNIQUE"


class IRCallableKind(str, Enum):
    """Identify a compiler-owned call target that Semantic IR can realize."""

    BUILTIN = "BUILTIN"
    SOURCE_FUNCTION = "SOURCE_FUNCTION"
    EXTENSION = "EXTENSION"


@dataclass(frozen=True)
class IRCallableTarget:
    """Store backend-independent identity for one compiler-owned callable."""

    kind: IRCallableKind
    name: str
    function_id: FunctionId | None = None
    extension_callable_id: ExtensionCallableId | None = None
    extension_overload_index: int | None = None

    def __post_init__(self) -> None:
        """Validate category-specific target identity without backend objects."""
        if not isinstance(self.kind, IRCallableKind):
            raise TypeError("kind must be an IRCallableKind")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("callable target name must be a non-empty string")
        if self.kind is IRCallableKind.BUILTIN:
            if self.function_id is not None or self.extension_callable_id is not None or self.extension_overload_index is not None:
                raise ValueError("builtin callable target cannot carry source/extension identity")
            return
        if self.kind is IRCallableKind.SOURCE_FUNCTION:
            if not isinstance(self.function_id, FunctionId):
                raise ValueError("source-function callable target requires FunctionId")
            if self.extension_callable_id is not None or self.extension_overload_index is not None:
                raise ValueError("source-function callable target cannot carry extension identity")
            return
        if self.kind is IRCallableKind.EXTENSION:
            if self.function_id is not None:
                raise ValueError("extension callable target cannot carry FunctionId")
            if not isinstance(self.extension_callable_id, ExtensionCallableId):
                raise TypeError("extension callable target requires ExtensionCallableId")
            if self.extension_overload_index is not None and (
                not isinstance(self.extension_overload_index, int)
                or isinstance(self.extension_overload_index, bool)
                or self.extension_overload_index < 0
            ):
                raise ValueError("extension overload index must be None or a non-negative integer")
            return
        raise ValueError("unsupported IR callable kind")


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
class IRCallOperandRef:
    """Reference one ordinary EXTENSION IRCall runtime operand from detached semantic state."""

    operand_index: int

    def __post_init__(self) -> None:
        """Require one non-negative physical operand index."""
        if not isinstance(self.operand_index, int) or isinstance(self.operand_index, bool) or self.operand_index < 0:
            raise ValueError("IRCallOperandRef.operand_index must be a non-negative integer")


def _detached_nf_type(value):
    """Infer the canonical NFType of one detached static semantic-state leaf."""
    from .compile_time import ConstVector
    if type(value) is bool:
        return NFType.BOOL
    if type(value) is int:
        return NFType.INT
    if type(value) is float:
        return NFType.FLOAT
    if type(value) is str:
        return NFType.STRING
    if isinstance(value, ConstVector):
        return NFType.VECTOR
    return None


def validate_extension_ir_state(type_spec: TypeSpec, state, arguments, registry) -> None:
    """Validate detached extension semantic state against TypeSpec and referenced IRValue types."""
    from .callable_contracts import source_argument_type_matches
    from ..extension_values import ExtensionValue

    def validate(spec, value):
        if spec.kind == "OPTIONAL":
            if value is None:
                return
            validate(spec.item, value)
            return
        if spec.kind == "PY_SCALAR":
            expected = {
                PythonScalarKind.BOOL: bool,
                PythonScalarKind.INT: int,
                PythonScalarKind.FLOAT: float,
                PythonScalarKind.STRING: str,
            }[spec.python_scalar_kind]
            if type(value) is not expected:
                raise TypeError("extension IR PY_SCALAR state has incompatible detached value")
            return
        if spec.kind == "NF_SET":
            if isinstance(value, IRCallOperandRef):
                if value.operand_index >= len(arguments):
                    raise ValueError("IRCallOperandRef references an out-of-range extension operand")
                actual = arguments[value.operand_index].value.typ
                if not any(source_argument_type_matches(expected, actual) for expected in spec.nf_types):
                    raise TypeError("IRCallOperandRef references a runtime operand incompatible with its TypeSpec")
                return
            actual = _detached_nf_type(value)
            if actual is None or not any(source_argument_type_matches(expected, actual) for expected in spec.nf_types):
                raise TypeError("extension IR static NF state is incompatible with its TypeSpec")
            return
        if spec.kind == "RECORD":
            if not isinstance(value, ExtensionValue):
                raise TypeError("extension IR RECORD state must be ExtensionValue")
            concrete = registry.type_spec(value.type_id)
            if not registry.is_nominal_subtype(concrete.id, spec.record_type):
                raise TypeError("extension IR RECORD state violates nominal TypeSpec")
            if not isinstance(value.storage, tuple) or len(value.storage) != len(concrete.fields):
                raise TypeError("extension IR RECORD state has invalid field storage")
            for (_name, field_spec), field_value in zip(concrete.fields, value.storage):
                validate(field_spec, field_value)
            return
        if spec.kind == "LIST":
            if not isinstance(value, tuple):
                raise TypeError("extension IR LIST state must use immutable tuple storage")
            for item in value:
                validate(spec.item, item)
            return
        if spec.kind == "TUPLE_FIXED":
            if not isinstance(value, tuple) or len(value) != len(spec.items):
                raise TypeError("extension IR fixed tuple state has invalid shape")
            for item_spec, item in zip(spec.items, value):
                validate(item_spec, item)
            return
        if spec.kind == "TUPLE_VAR":
            if not isinstance(value, tuple):
                raise TypeError("extension IR variadic tuple state must use tuple storage")
            for item in value:
                validate(spec.item, item)
            return
        if spec.kind == "DICT_STR":
            if not isinstance(value, tuple):
                raise TypeError("extension IR dict state must use immutable tuple storage")
            for pair in value:
                if not isinstance(pair, tuple) or len(pair) != 2 or type(pair[0]) is not str:
                    raise TypeError("extension IR dict state entry is malformed")
                validate(spec.item, pair[1])
            return
        raise TypeError(f"unsupported extension IR TypeSpec kind {spec.kind!r}")

    if not isinstance(type_spec, TypeSpec):
        raise TypeError("extension_state_type must be TypeSpec")
    validate(type_spec, state)


@dataclass(frozen=True)
class IRCallArgument:
    """Reference one already-emitted runtime operand of a compiler-owned call."""

    parameter_name: str | None
    value: IRValue
    parameter_index: int | None = None
    variadic_index: int | None = None

    def __post_init__(self) -> None:
        """Reject structural/backend operands and invalid contract positions."""
        if self.parameter_name is not None and not isinstance(self.parameter_name, str):
            raise TypeError("parameter_name must be a string or None")
        if not isinstance(self.value, IRValue):
            raise TypeError("IRCallArgument.value must be an IRValue")
        if self.parameter_index is not None and (
            not isinstance(self.parameter_index, int)
            or isinstance(self.parameter_index, bool)
            or self.parameter_index < 0
        ):
            raise ValueError("IRCallArgument.parameter_index must be None or a non-negative integer")
        if self.variadic_index is not None and (
            not isinstance(self.variadic_index, int)
            or isinstance(self.variadic_index, bool)
            or self.variadic_index < 0
        ):
            raise ValueError("IRCallArgument.variadic_index must be None or a non-negative integer")


@dataclass(frozen=True)
class IRStaticCallArgument:
    """Store one detached source-call argument by callable input position."""

    parameter_index: int
    value: object
    variadic_index: int | None = None

    def __post_init__(self) -> None:
        """Require one non-negative position and detached immutable value."""
        if (
            not isinstance(self.parameter_index, int)
            or isinstance(self.parameter_index, bool)
            or self.parameter_index < 0
        ):
            raise ValueError("IRStaticCallArgument.parameter_index must be a non-negative integer")
        if self.variadic_index is not None and (
            not isinstance(self.variadic_index, int)
            or isinstance(self.variadic_index, bool)
            or self.variadic_index < 0
        ):
            raise ValueError("IRStaticCallArgument.variadic_index must be None or a non-negative integer")
        if not _is_ir_option_value(self.value):
            raise TypeError("IRStaticCallArgument.value must be detached immutable IR data")


@dataclass(frozen=True)
class IRCall:
    """Represent one typed AST-free compiler-owned core call."""

    results: tuple[IRValue, ...]
    depth: int
    target: IRCallableTarget
    arguments: tuple[IRCallArgument, ...]
    options: tuple[tuple[str, object], ...] = ()
    raw_output_mode: IRRawNodeOutputMode | None = None
    materialization: IRFunctionMaterialization | None = None
    static_arguments: tuple[IRStaticCallArgument, ...] = ()
    extension_state_type: TypeSpec | None = None
    extension_state: object | None = None

    def __post_init__(self) -> None:
        """Validate call records before backend realization."""
        object.__setattr__(self, "results", tuple(self.results))
        object.__setattr__(self, "arguments", tuple(self.arguments))
        object.__setattr__(self, "options", tuple(self.options))
        object.__setattr__(self, "static_arguments", tuple(self.static_arguments))
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
        if not all(isinstance(item, IRStaticCallArgument) for item in self.static_arguments):
            raise TypeError("IRCall static_arguments must be IRStaticCallArgument records")
        if self.target.kind is IRCallableKind.BUILTIN:
            if self.materialization is not None or self.static_arguments:
                raise ValueError("builtin calls cannot carry source-function materialization/static arguments")
            if any(item.parameter_index is not None or item.variadic_index is not None for item in self.arguments):
                raise ValueError("builtin runtime arguments cannot carry source/extension positions")
        elif self.target.kind is IRCallableKind.SOURCE_FUNCTION:
            if self.target.function_id is None:
                raise ValueError("source-function IRCall requires target FunctionId")
            dynamic_positions = [item.parameter_index for item in self.arguments]
            if any(position is None for position in dynamic_positions):
                raise ValueError("source-function runtime arguments require parameter positions")
            if any(item.variadic_index is not None for item in (*self.arguments, *self.static_arguments)):
                raise ValueError("source-function arguments cannot carry extension variadic positions")
            static_positions = [item.parameter_index for item in self.static_arguments]
            all_positions = [int(position) for position in dynamic_positions] + static_positions
            if len(all_positions) != len(set(all_positions)):
                raise ValueError("source-function parameter positions must be unique across runtime/static arguments")
            if self.materialization is not None and self.materialization.callee != self.target.function_id:
                raise ValueError("source-function materialization must target IRCall FunctionId")
        elif self.target.kind is IRCallableKind.EXTENSION:
            if self.materialization is not None:
                raise ValueError("extension IRCall cannot carry source-function materialization")
            if self.extension_state_type is None:
                if self.extension_state is not None:
                    raise ValueError("extension_state requires extension_state_type")
                validate_extension_argument_positions(
                    (item.parameter_index, item.variadic_index)
                    for item in (*self.arguments, *self.static_arguments)
                )
            else:
                if not isinstance(self.extension_state_type, TypeSpec):
                    raise TypeError("extension_state_type must be TypeSpec")
                if self.target.extension_overload_index is not None:
                    raise ValueError("semantic-state extension IRCall cannot be overloaded")
                if self.static_arguments:
                    raise ValueError("semantic-state extension IRCall cannot carry public static arguments")
                if any(item.parameter_index is not None or item.variadic_index is not None for item in self.arguments):
                    raise ValueError("semantic-state extension dependency operands cannot carry public positions")
        else:
            raise ValueError("unsupported IRCall target kind")
        if self.target.kind is not IRCallableKind.EXTENSION and (self.extension_state_type is not None or self.extension_state is not None):
            raise ValueError("only EXTENSION IRCall may carry extension semantic state")
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
            inputs = option_map.get("inputs")
            if not isinstance(inputs, tuple):
                raise TypeError("raw node IRCall inputs metadata must be a tuple")
            input_selectors = []
            for input_item in inputs:
                if not isinstance(input_item, tuple) or len(input_item) != 2:
                    raise TypeError("raw node IRCall inputs metadata must contain (selector, spec) pairs")
                selector, _spec = input_item
                if not _is_raw_socket_selector(selector):
                    raise ValueError("raw node IRCall input selectors must be canonical socket selectors")
                input_selectors.append(selector)
            if len(input_selectors) != len(set(input_selectors)):
                raise ValueError("raw node IRCall input selectors must be unique")
            if self.raw_output_mode is IRRawNodeOutputMode.SINGLE_OUTPUT:
                if len(self.results) != 1:
                    raise ValueError("SINGLE_OUTPUT raw node IRCall requires exactly one result")
                if option_map.get("outputs") is not None:
                    raise ValueError("SINGLE_OUTPUT raw node IRCall cannot carry named outputs")
                if not _is_raw_socket_selector(option_map.get("output")):
                    raise ValueError("SINGLE_OUTPUT raw node IRCall requires output metadata")
                if not isinstance(option_map.get("typ"), NFType):
                    raise TypeError("SINGLE_OUTPUT raw node IRCall requires an NFType typ option")
            else:
                outputs = option_map.get("outputs")
                if not isinstance(outputs, tuple) or not outputs:
                    raise ValueError("NAMED_OUTPUTS raw node IRCall requires named output metadata")
                names = []
                selectors = []
                for output_item in outputs:
                    if not isinstance(output_item, tuple) or len(output_item) != 3:
                        raise TypeError("NAMED_OUTPUTS metadata must contain (alias, selector, NFType) triples")
                    output_name, selector, output_type = output_item
                    if not isinstance(output_name, str) or not output_name:
                        raise ValueError("NAMED_OUTPUTS aliases must be non-empty strings")
                    if not _is_raw_socket_selector(selector):
                        raise ValueError("NAMED_OUTPUTS selectors must be canonical socket selectors")
                    if not isinstance(output_type, NFType):
                        raise TypeError("NAMED_OUTPUTS types must be NFType members")
                    names.append(output_name)
                    selectors.append(selector)
                if len(names) != len(set(names)):
                    raise ValueError("NAMED_OUTPUTS raw node IRCall requires unique output aliases")
                if len(selectors) != len(set(selectors)):
                    raise ValueError("NAMED_OUTPUTS raw node IRCall requires unique output selectors")
                if len(outputs) != len(self.results):
                    raise ValueError("NAMED_OUTPUTS raw node IRCall result count must match declared outputs")
                if option_map.get("output") is not None or option_map.get("typ") is not None:
                    raise ValueError("NAMED_OUTPUTS raw node IRCall cannot carry single-output metadata")
        elif self.raw_output_mode is not None:
            raise ValueError("raw output mode is valid only for node() builtin calls")


@dataclass(frozen=True)
class IRContextRead:
    """Produce one typed value from compiler-owned contextual group state."""

    result: IRValue
    depth: int
    slot: GroupContextSlot

    def __post_init__(self) -> None:
        """Validate slot type and expression depth before backend lowering."""
        if not isinstance(self.result, IRValue):
            raise TypeError("IRContextRead.result must be an IRValue")
        if not isinstance(self.depth, int) or isinstance(self.depth, bool) or self.depth < 0:
            raise ValueError("IRContextRead.depth must be a non-negative integer")
        if not isinstance(self.slot, GroupContextSlot):
            raise TypeError("IRContextRead.slot must be a GroupContextSlot")
        if self.result.typ is not GROUP_CONTEXT_SPECS[self.slot].typ:
            raise TypeError("IRContextRead result type does not match context slot")


@dataclass(frozen=True)
class IRContextWrite:
    """Replace one compiler-owned contextual group value without a source result."""

    depth: int
    slot: GroupContextSlot
    value: IRValue

    def __post_init__(self) -> None:
        """Validate slot type and expression depth before backend lowering."""
        if not isinstance(self.depth, int) or isinstance(self.depth, bool) or self.depth < 0:
            raise ValueError("IRContextWrite.depth must be a non-negative integer")
        if not isinstance(self.slot, GroupContextSlot):
            raise TypeError("IRContextWrite.slot must be a GroupContextSlot")
        if not isinstance(self.value, IRValue):
            raise TypeError("IRContextWrite.value must be an IRValue")
        if self.value.typ is not GROUP_CONTEXT_SPECS[self.slot].typ:
            raise TypeError("IRContextWrite value type does not match context slot")


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
    """Produce one typed Object Info property with frontend-owned configuration."""

    result: IRValue
    depth: int
    value: IRValue
    property_name: str
    transform_space: str
    as_instance: bool

    def __post_init__(self) -> None:
        """Validate Object property type/configuration before Blender lowering."""
        from .constants import OBJECT_PROPERTY_TYPES, TYPE_OBJECT
        if self.property_name not in OBJECT_PROPERTY_TYPES:
            raise ValueError("unsupported Object property")
        if self.value.typ is not TYPE_OBJECT:
            raise TypeError("IRObjectProperty value must be Object")
        if self.result.typ is not OBJECT_PROPERTY_TYPES[self.property_name]:
            raise TypeError("IRObjectProperty result type does not match property contract")
        if self.transform_space not in {"ORIGINAL", "RELATIVE"}:
            raise ValueError("IRObjectProperty transform_space must be ORIGINAL or RELATIVE")
        if not isinstance(self.as_instance, bool):
            raise TypeError("IRObjectProperty as_instance must be bool")


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


@dataclass(frozen=True)
class IRAssign:
    """Bind one body-local runtime name to the result of an expression program."""

    binding_id: BindingId
    source_name: str
    value: IRProgram

    def __post_init__(self) -> None:
        """Require one compiler-owned binding and ordinary runtime result."""
        if not isinstance(self.binding_id, BindingId):
            raise TypeError("binding_id must be a BindingId")
        if not isinstance(self.source_name, str) or not self.source_name:
            raise ValueError("source_name must be a non-empty string")
        if not isinstance(self.value, IRProgram) or not isinstance(self.value.result, IRValue):
            raise TypeError("IRAssign value must be an IRProgram with an IRValue result")


@dataclass(frozen=True)
class IRInputDeclaration:
    """Declare one physical group input whose source target owns the runtime value."""

    target_binding_id: BindingId
    declaration_id: InputDeclarationId
    target_name: str
    display_name: str
    typ: NFType
    default: object | None = None

    def __post_init__(self) -> None:
        """Keep input identity separate from display-only interface metadata."""
        if not isinstance(self.target_binding_id, BindingId):
            raise TypeError("target_binding_id must be a BindingId")
        if not isinstance(self.declaration_id, InputDeclarationId):
            raise TypeError("declaration_id must be an InputDeclarationId")
        if not isinstance(self.target_name, str) or not self.target_name:
            raise ValueError("target_name must be a non-empty string")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("display_name must be a non-empty string")
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")
        if not _is_ir_option_value(self.default):
            raise TypeError("input default must be detached immutable IR data")


@dataclass(frozen=True)
class IROutput:
    """Publish one explicitly named body runtime value."""

    name: str
    value: IRProgram

    def __post_init__(self) -> None:
        """Require one non-empty display name and ordinary runtime program result."""
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("output name must be a non-empty string")
        if not isinstance(self.value, IRProgram) or not isinstance(self.value.result, IRValue):
            raise TypeError("IROutput value must be an IRProgram with an IRValue result")


@dataclass(frozen=True)
class IRFinalExpression:
    """Represent the one eligible body-final automatic output expression."""

    value: IRProgram

    def __post_init__(self) -> None:
        """Require an ordinary runtime result."""
        if not isinstance(self.value, IRProgram) or not isinstance(self.value.result, IRValue):
            raise TypeError("IRFinalExpression value must be an IRProgram with an IRValue result")



def _bindable_runtime_result_leaves(result: IRResult) -> tuple[IRValue, ...]:
    """Return runtime leaves recursively bindable by ``IRBindLeaves`` in source order."""
    if isinstance(result, IRValue):
        return (result,)
    if isinstance(result, IRTuple):
        return result.items
    if isinstance(result, IRNamedOutputs):
        return tuple(value for _name, value in result.items)
    if isinstance(result, IRArray):
        leaves = []
        for item in result.items:
            leaves.extend(_bindable_runtime_result_leaves(item))
        return tuple(leaves)
    raise TypeError("unsupported IR result")


@dataclass(frozen=True)
class IRLeafBinding:
    """Map one exact program-result leaf to one body-local destination BindingId."""

    source: IRValue
    destination: BindingId
    typ: NFType

    def __post_init__(self) -> None:
        """Require canonical source/destination/type metadata."""
        if not isinstance(self.source, IRValue):
            raise TypeError("IRLeafBinding.source must be an IRValue")
        if not isinstance(self.destination, BindingId):
            raise TypeError("IRLeafBinding.destination must be a BindingId")
        if not isinstance(self.typ, NFType):
            raise TypeError("IRLeafBinding.typ must be an NFType")
        if self.source.typ is not self.typ:
            raise TypeError("IRLeafBinding type must equal source type")


@dataclass(frozen=True)
class IRBindLeaves:
    """Evaluate one structural program once and bind selected runtime leaves."""

    value: IRProgram
    bindings: tuple[IRLeafBinding, ...]

    def __post_init__(self) -> None:
        """Validate exact result-leaf membership and one-to-one destinations."""
        if not isinstance(self.value, IRProgram):
            raise TypeError("IRBindLeaves.value must be an IRProgram")
        object.__setattr__(self, "bindings", tuple(self.bindings))
        if not self.bindings or not all(isinstance(binding, IRLeafBinding) for binding in self.bindings):
            raise TypeError("IRBindLeaves requires one or more IRLeafBinding records")
        leaves = _bindable_runtime_result_leaves(self.value.result)
        leaf_ids = {(leaf.id, leaf.typ) for leaf in leaves}
        if any((binding.source.id, binding.source.typ) not in leaf_ids for binding in self.bindings):
            raise ValueError("IRBindLeaves source must be an exact runtime leaf of value.result")
        destinations = [binding.destination for binding in self.bindings]
        sources = [(binding.source.id, binding.source.typ) for binding in self.bindings]
        if len(destinations) != len(set(destinations)):
            raise ValueError("IRBindLeaves destinations must be unique")
        if len(sources) != len(set(sources)):
            raise ValueError("IRBindLeaves source leaves must be unique")


@dataclass(frozen=True)
class IRDiscardExpression:
    """Evaluate one expression program exactly once and discard its result."""

    value: IRProgram

    def __post_init__(self) -> None:
        """Require a complete compiler-owned expression program."""
        if not isinstance(self.value, IRProgram):
            raise TypeError("IRDiscardExpression.value must be an IRProgram")


@dataclass(frozen=True)
class IRPanelDeclaration:
    """Declare one source-ordered native interface panel by canonical input origin."""

    member_origins: tuple[InterfaceInputOrigin, ...]
    name: str
    collapsed: bool

    def __post_init__(self) -> None:
        """Validate detached panel membership using interface provenance identity."""
        object.__setattr__(self, "member_origins", tuple(self.member_origins))
        allowed = (BindingId, InputDeclarationId)
        if not self.member_origins or not all(isinstance(item, allowed) for item in self.member_origins):
            raise TypeError("IRPanelDeclaration requires one or more interface input origins")
        if len(self.member_origins) != len(set(self.member_origins)):
            raise ValueError("IRPanelDeclaration member origins must be unique")
        if not isinstance(self.name, str) or not self.name:
            raise ValueError("IRPanelDeclaration name must be a non-empty string")
        if not isinstance(self.collapsed, bool):
            raise TypeError("IRPanelDeclaration.collapsed must be bool")


@dataclass(frozen=True)
class IRBranchMerge:
    """Describe one typed runtime binding merged at a structured branch exit."""

    binding_id: BindingId
    source_name: str
    typ: NFType

    def __post_init__(self) -> None:
        """Validate self-contained branch merge metadata."""
        if not isinstance(self.binding_id, BindingId):
            raise TypeError("binding_id must be a BindingId")
        if not isinstance(self.source_name, str) or not self.source_name:
            raise ValueError("source_name must be a non-empty string")
        if not isinstance(self.typ, NFType):
            raise TypeError("typ must be an NFType")


@dataclass(frozen=True)
class IRIf:
    """Represent one already-resolved runtime conditional and its binding merges."""

    condition: IRProgram
    true_body: "IRBody"
    false_body: "IRBody"
    merges: tuple[IRBranchMerge, ...]

    def __post_init__(self) -> None:
        """Validate only context-free structured conditional invariants."""
        object.__setattr__(self, "merges", tuple(self.merges))
        if not isinstance(self.condition, IRProgram) or not isinstance(self.condition.result, IRValue):
            raise TypeError("IRIf condition must be an IRProgram with an IRValue result")
        if self.condition.result.typ is not NFType.BOOL:
            raise TypeError("IRIf condition must have Bool type")
        if not isinstance(self.true_body, IRBody) or not isinstance(self.false_body, IRBody):
            raise TypeError("IRIf branches must be IRBody records")
        if not all(isinstance(item, IRBranchMerge) for item in self.merges):
            raise TypeError("IRIf merges must be IRBranchMerge records")
        ids = [item.binding_id for item in self.merges]
        names = [item.source_name for item in self.merges]
        if len(ids) != len(set(ids)):
            raise ValueError("IRIf merge BindingIds must be unique")
        if len(names) != len(set(names)):
            raise ValueError("IRIf merge source names must be unique")


_REPEAT_STATE_TYPES = frozenset({
    NFType.GEOMETRY, NFType.VECTOR, NFType.FLOAT, NFType.INT, NFType.BOOL, NFType.BUNDLE
})


@dataclass(frozen=True)
class IRRepeatState:
    """Describe one Repeat carried binding with one exact semantic type."""

    binding_id: BindingId
    source_name: str
    typ: NFType
    publish_to_parent: bool

    def __post_init__(self) -> None:
        """Validate context-free Repeat state metadata."""
        if not isinstance(self.binding_id, BindingId):
            raise TypeError("binding_id must be a BindingId")
        if not isinstance(self.source_name, str) or not self.source_name:
            raise ValueError("source_name must be a non-empty string")
        if self.typ not in _REPEAT_STATE_TYPES:
            raise TypeError("IRRepeatState uses an unsupported Repeat state type")
        if not isinstance(self.publish_to_parent, bool):
            raise TypeError("publish_to_parent must be bool")


@dataclass(frozen=True)
class IRRepeat:
    """Represent one structured runtime Repeat Zone with explicit carried state."""

    iterations: IRProgram
    iteration_binding_id: BindingId
    iteration_name: str
    states: tuple[IRRepeatState, ...]
    body: "IRBody"

    def __post_init__(self) -> None:
        """Validate self-contained Repeat structure before backend lowering."""
        object.__setattr__(self, "states", tuple(self.states))
        if not isinstance(self.iterations, IRProgram) or not isinstance(self.iterations.result, IRValue):
            raise TypeError("IRRepeat iterations must be an IRProgram with an IRValue result")
        if self.iterations.result.typ is not NFType.INT:
            raise TypeError("IRRepeat iterations must have Int type")
        if not isinstance(self.iteration_binding_id, BindingId):
            raise TypeError("iteration_binding_id must be a BindingId")
        if not isinstance(self.iteration_name, str) or not self.iteration_name:
            raise ValueError("iteration_name must be a non-empty string")
        if not self.states or not all(isinstance(item, IRRepeatState) for item in self.states):
            raise TypeError("IRRepeat requires at least one IRRepeatState")
        ids = [item.binding_id for item in self.states]
        names = [item.source_name for item in self.states]
        if self.iteration_binding_id in ids:
            raise ValueError("IRRepeat own iteration BindingId cannot also be carried state")
        if self.iteration_name in names:
            raise ValueError("IRRepeat own iteration name cannot also be a state name")
        if len(ids) != len(set(ids)) or len(names) != len(set(names)):
            raise ValueError("IRRepeat state IDs and names must be unique")
        if not isinstance(self.body, IRBody) or not self.body.statements:
            raise TypeError("IRRepeat body must be a non-empty IRBody")


IRBodyStatement: TypeAlias = (
    IRAssign | IRInputDeclaration | IROutput | IRFinalExpression | IRBindLeaves | IRDiscardExpression | IRPanelDeclaration | IRIf | IRRepeat
)


@dataclass(frozen=True)
class IRBody:
    """Store one ordered compiler-owned straight-line executable body."""

    statements: tuple[IRBodyStatement, ...]

    def __post_init__(self) -> None:
        """Freeze statement order and reject non-body records."""
        object.__setattr__(self, "statements", tuple(self.statements))
        allowed = (IRAssign, IRInputDeclaration, IROutput, IRFinalExpression, IRBindLeaves, IRDiscardExpression, IRPanelDeclaration, IRIf, IRRepeat)
        if not all(isinstance(statement, allowed) for statement in self.statements):
            raise TypeError("IRBody contains an unsupported statement record")


__all__ = [
    "IRCallableKind",
    "IRCallableTarget",
    "IRRawNodeOutputMode",
    "IRTuple",
    "IRNamedOutputs",
    "IRCallArgument",
    "IRCallOperandRef",
    "IRStaticCallArgument",
    "IRCall",
    "IRFunctionMaterializationMode",
    "IRFunctionMaterialization",
    "IRValue",
    "IRArray",
    "IRResult",
    "IROperation",
    "IRProgram",
    "IRContextRead",
    "IRContextWrite",
    "IRLiteral",
    "IRBinding",
    "IRUnary",
    "IRBinary",
    "IRBoolBinary",
    "IRCompare",
    "IRConditional",
    "IRVectorLiteral",
    "validate_extension_ir_state",
    "IRObjectProperty",
    "IRVectorComponent",
    "IRAssign",
    "IRInputDeclaration",
    "IROutput",
    "IRFinalExpression",
    "IRLeafBinding",
    "IRBindLeaves",
    "IRDiscardExpression",
    "IRPanelDeclaration",
    "IRBranchMerge",
    "IRIf",
    "IRRepeatState",
    "IRRepeat",
    "IRBodyStatement",
    "IRBody",
]
