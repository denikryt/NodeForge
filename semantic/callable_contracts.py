"""Pure source-callable and group-interface contracts for semantic compilation."""

from __future__ import annotations

import unicodedata
from dataclasses import dataclass

from ..compiler_identities import BindingId, FunctionId, InputDeclarationId, InterfaceInputOrigin
from ..errors import CompileError
from ..nf_types import NFType
from .numeric_semantics import normalize_float_constant, normalize_int_constant
from .ir import IRPanelDeclaration


@dataclass(frozen=True)
class GroupInputContract:
    """Describe one final public group input independently from Blender sockets."""

    source_name: str | None
    display_name: str
    typ: NFType
    default: object | None
    has_default: bool
    interface_origin: InterfaceInputOrigin | None

    def __post_init__(self) -> None:
        """Validate detached semantic input metadata."""
        if self.source_name is not None and not isinstance(self.source_name, str):
            raise TypeError("GroupInputContract.source_name must be a string or None")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("GroupInputContract.display_name must be a non-empty string")
        if not isinstance(self.typ, NFType):
            raise TypeError("GroupInputContract.typ must be an NFType")
        if not isinstance(self.has_default, bool):
            raise TypeError("GroupInputContract.has_default must be bool")
        if self.interface_origin is not None and not isinstance(
            self.interface_origin, (BindingId, InputDeclarationId)
        ):
            raise TypeError("GroupInputContract.interface_origin must be a compiler input identity or None")
        if self.has_default:
            if self.typ not in {NFType.FLOAT, NFType.INT, NFType.BOOL, NFType.VECTOR, NFType.STRING}:
                raise ValueError("Only scalar/vector/string group inputs may own semantic defaults")
        elif self.default is not None:
            raise ValueError("Group input without semantic default must store default=None")


@dataclass(frozen=True)
class GroupOutputContract:
    """Describe one final public group output independently from Blender sockets."""

    index: int
    display_name: str
    typ: NFType

    def __post_init__(self) -> None:
        """Validate ordered semantic output metadata."""
        if not isinstance(self.index, int) or isinstance(self.index, bool) or self.index < 0:
            raise ValueError("GroupOutputContract.index must be a non-negative integer")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("GroupOutputContract.display_name must be a non-empty string")
        if not isinstance(self.typ, NFType):
            raise TypeError("GroupOutputContract.typ must be an NFType")


@dataclass(frozen=True)
class GroupInterfaceContract:
    """Store final callable input/output order inferred by the semantic frontend."""

    inputs: tuple[GroupInputContract, ...]
    outputs: tuple[GroupOutputContract, ...]

    def __post_init__(self) -> None:
        """Freeze ordered members and require contiguous physical output positions."""
        object.__setattr__(self, "inputs", tuple(self.inputs))
        object.__setattr__(self, "outputs", tuple(self.outputs))
        if not all(isinstance(item, GroupInputContract) for item in self.inputs):
            raise TypeError("GroupInterfaceContract.inputs must contain GroupInputContract records")
        if not all(isinstance(item, GroupOutputContract) for item in self.outputs):
            raise TypeError("GroupInterfaceContract.outputs must contain GroupOutputContract records")
        if [item.index for item in self.outputs] != list(range(len(self.outputs))):
            raise ValueError("GroupInterfaceContract output indices must be contiguous final positions")
        origins = [item.interface_origin for item in self.inputs if item.interface_origin is not None]
        if len(origins) != len(set(origins)):
            raise ValueError("GroupInterfaceContract interface origins must be unique")


@dataclass(frozen=True)
class SourceCallableParameter:
    """Describe one runtime group input exposed by a source-backed callable."""

    input_index: int
    source_name: str | None
    display_name: str
    keyword_key: str | None
    typ: NFType
    public: bool

    def __post_init__(self) -> None:
        """Validate the callable-position and source-facing metadata."""
        if not isinstance(self.input_index, int) or isinstance(self.input_index, bool) or self.input_index < 0:
            raise ValueError("SourceCallableParameter.input_index must be a non-negative integer")
        if self.source_name is not None and not isinstance(self.source_name, str):
            raise TypeError("SourceCallableParameter.source_name must be a string or None")
        if not isinstance(self.display_name, str) or not self.display_name:
            raise ValueError("SourceCallableParameter.display_name must be a non-empty string")
        if self.keyword_key is not None and not isinstance(self.keyword_key, str):
            raise TypeError("SourceCallableParameter.keyword_key must be a string or None")
        if not isinstance(self.typ, NFType):
            raise TypeError("SourceCallableParameter.typ must be an NFType")
        if not isinstance(self.public, bool):
            raise TypeError("SourceCallableParameter.public must be bool")
        if not self.public and self.keyword_key is not None:
            raise ValueError("Hidden source-call parameter cannot expose a caller keyword")


@dataclass(frozen=True)
class SourceCallableContract:
    """Store one typed source-backed function interface before materialization."""

    function_id: FunctionId
    parameters: tuple[SourceCallableParameter, ...]
    outputs: tuple[GroupOutputContract, ...]

    def __post_init__(self) -> None:
        """Freeze members and require one canonical FunctionId."""
        if not isinstance(self.function_id, FunctionId):
            raise TypeError("SourceCallableContract.function_id must be a FunctionId")
        object.__setattr__(self, "parameters", tuple(self.parameters))
        object.__setattr__(self, "outputs", tuple(self.outputs))
        if not all(isinstance(item, SourceCallableParameter) for item in self.parameters):
            raise TypeError("SourceCallableContract.parameters must contain SourceCallableParameter records")
        if not all(isinstance(item, GroupOutputContract) for item in self.outputs):
            raise TypeError("SourceCallableContract.outputs must contain GroupOutputContract records")
        positions = [item.input_index for item in self.parameters]
        if len(positions) != len(set(positions)):
            raise ValueError("SourceCallableContract parameter positions must be unique")


def normalize_callable_keyword(name: str) -> str | None:
    """Return the optional Unicode-aware keyword alias for one public label."""
    normalized = unicodedata.normalize("NFKC", name or "").casefold()
    key = "".join(character for character in normalized if character.isalnum())
    return key or None


def source_argument_type_matches(expected: NFType, actual: NFType) -> bool:
    """Return whether one source-call argument satisfies an input semantic type."""
    if expected is NFType.FLOAT:
        return actual in {NFType.FLOAT, NFType.INT}
    if expected is NFType.INT:
        return actual is NFType.INT
    return expected is actual


def canonicalize_group_input_default(typ: NFType, value: object) -> object:
    """Return the detached canonical default used for semantic/physical parity."""
    if typ is NFType.FLOAT:
        return normalize_float_constant(value)
    if typ is NFType.INT:
        return normalize_int_constant(value)
    if typ is NFType.BOOL:
        if type(value) is not bool:
            raise CompileError("Bool group input default must be Bool")
        return value
    if typ is NFType.VECTOR:
        try:
            values = tuple(value)
        except TypeError as exc:
            raise CompileError("Vector group input default must contain three numeric components") from exc
        if len(values) != 3:
            raise CompileError("Vector group input default must contain three numeric components")
        return tuple(normalize_float_constant(component) for component in values)
    if typ is NFType.STRING:
        if not isinstance(value, str):
            raise CompileError("String group input default must be String")
        return value
    raise CompileError(f"{typ} does not own a semantic group input default")



def bind_local_source_arguments(
    parameter_names: tuple[str, ...],
    positional_values: tuple[object, ...],
    keyword_values: tuple[tuple[str | None, object], ...],
    function_name: str,
) -> tuple[tuple[str, object], ...]:
    """Bind one local source call using the established Python-like plain-parameter rules."""
    names = tuple(parameter_names)
    if len(positional_values) > len(names):
        raise CompileError(f"{function_name}() got too many positional arguments")
    bound: dict[str, object] = {}
    source_order: list[tuple[str, object]] = []
    for index, value in enumerate(positional_values):
        name = names[index]
        bound[name] = value
        source_order.append((name, value))
    for keyword_name, value in keyword_values:
        if keyword_name is None:
            raise CompileError(f"{function_name}() does not support **kwargs")
        if keyword_name not in names:
            raise CompileError(f"{function_name}() got unknown keyword argument {keyword_name!r}")
        if keyword_name in bound:
            raise CompileError(f"{function_name}() got multiple values for argument {keyword_name!r}")
        bound[keyword_name] = value
        source_order.append((keyword_name, value))
    missing = [name for name in names if name not in bound]
    if missing:
        raise CompileError(f"{function_name}() missing arguments: {', '.join(missing)}")
    return tuple(source_order)


def bind_imported_source_arguments(
    parameters: tuple[SourceCallableParameter, ...],
    positional_values: tuple[object, ...],
    keyword_values: tuple[tuple[str | None, object], ...],
    function_name: str,
) -> tuple[tuple[SourceCallableParameter, object], ...]:
    """Bind one imported source call by final callable position and normalized public label."""
    public = tuple(parameter for parameter in parameters if parameter.public)
    if len(positional_values) > len(public):
        raise CompileError(f"{function_name}() got too many positional arguments")
    bound: list[tuple[SourceCallableParameter, object]] = []
    used_positions: set[int] = set()
    for index, value in enumerate(positional_values):
        parameter = public[index]
        bound.append((parameter, value))
        used_positions.add(parameter.input_index)

    normalized_positions: dict[str, list[SourceCallableParameter]] = {}
    for parameter in public:
        if parameter.keyword_key is not None:
            normalized_positions.setdefault(parameter.keyword_key, []).append(parameter)
    for keyword_name, value in keyword_values:
        if keyword_name is None:
            raise CompileError(f"{function_name}() does not support **kwargs")
        key = normalize_callable_keyword(keyword_name)
        matches = normalized_positions.get(key, ())
        if not matches:
            raise CompileError(f"{function_name}() got unknown keyword argument {keyword_name!r}")
        if len(matches) > 1:
            raise CompileError(
                f"{function_name}() input {keyword_name!r} is ambiguous because multiple inputs share that label; "
                "use positional arguments"
            )
        parameter = matches[0]
        if parameter.input_index in used_positions:
            raise CompileError(f"{function_name}() got multiple values for input {keyword_name!r}")
        bound.append((parameter, value))
        used_positions.add(parameter.input_index)
    return tuple(bound)

def project_group_input_layout(
    base_inputs: tuple[GroupInputContract, ...],
    panels: tuple[IRPanelDeclaration, ...],
) -> tuple[GroupInputContract, ...]:
    """Project Blender's root-first/depth-first callable order from panel moves.

    Inputs not moved into a panel remain at the root in their base creation order.
    Each panel contributes its members in the exact semantic move order.  Repeated
    moves of one interface input follow Blender's final-parent behavior: the later
    move removes it from the earlier location and inserts it in the later panel.
    """
    base = tuple(base_inputs)
    by_origin = {item.interface_origin: item for item in base if item.interface_origin is not None}
    final_parent: dict[InterfaceInputOrigin, int] = {}
    panel_members: list[list[InterfaceInputOrigin]] = [[] for _ in panels]
    for panel_index, panel in enumerate(panels):
        for origin in panel.member_origins:
            if origin not in by_origin:
                raise CompileError("Internal error: panel references an unknown interface input origin")
            previous = final_parent.get(origin)
            if previous is not None:
                panel_members[previous].remove(origin)
            final_parent[origin] = panel_index
            panel_members[panel_index].append(origin)

    ordered = [item for item in base if item.interface_origin is None or item.interface_origin not in final_parent]
    for members in panel_members:
        ordered.extend(by_origin[origin] for origin in members)
    return tuple(ordered)


__all__ = [
    "GroupInputContract",
    "GroupInterfaceContract",
    "GroupOutputContract",
    "SourceCallableContract",
    "SourceCallableParameter",
    "bind_imported_source_arguments",
    "bind_local_source_arguments",
    "canonicalize_group_input_default",
    "normalize_callable_keyword",
    "project_group_input_layout",
    "source_argument_type_matches",
]
