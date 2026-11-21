# Extension API Reference

Extension API v2 is the supported Python interface for package-defined NodeForge callables. It is for package authors who need behavior that cannot be implemented as a source-backed `.nf` function.

This page documents the Python declaration and execution contracts. Package manifests, namespaces, ZIP layout, and installation are covered in the [Package Guide](PACKAGES.md).

## Owner model

A Python-backed owner is a directory whose public contract is declared in `interface.py`.

```text
systems/
└── scale_tools/
    ├── interface.py
    ├── semantic.py      # optional frontend processing
    └── backend.py       # physical Blender realization
```

`interface.py` is the declaration module. `semantic.py` provides frontend processing and is required for [invocation-dependent results](#invocation-dependent-results). A backend module may use any owner-local filename referenced by the declaration; `backend.py` is only a convention.

An owner is either source-backed or Python-backed. Do not place both `source.nf` and `interface.py` in the same owner directory.

Keep owner-local imports out of `interface.py`. Relative imports such as `from .helpers import ...` belong in `semantic.py`, backend modules, or modules imported by those files. The declaration module may import the Python standard library, the public `NodeForge` API, and dependencies permitted by the package.

## `interface.py`

Every v2 owner defines:

```python
EXTENSION_API = 2
EXTENSIONS = {...}
```

`EXTENSIONS` is the complete public callable inventory for that owner. Each key names a function declared directly in `interface.py`.

A callable with a physical backend maps to an owner-relative implementation reference:

```python
EXTENSIONS = {
    'scale_value': '.backend:scale_value',
}
```

The leading dot is required. The target must be a function in an owner-local module other than `interface.py` or `semantic.py`.

A semantic-only callable maps to `None`:

```python
EXTENSIONS = {
    'make_part': None,
}
```

A native owner under `functions/<name>/`, a nested directory such as
`functions/mesh/<name>/`, or `examples/<name>/` exports exactly one key matching
`<name>`. Owners in different category directories have independent backend
modules and frozen owner snapshots. An owner under `systems/<owner>/` may
export multiple callables. For the resulting public names and Library behavior,
see [Package Guide](PACKAGES.md#importing-package-callables) and
[Source-backed functions](PACKAGES.md#source-backed-functions).

Legacy v1 layouts such as `system.py`, `CONSTRUCTORS`, and native `BACKEND_BUILTINS` are not supported.

## Minimal backend-only extension

```python
# systems/scale_tools/interface.py
from typing import Annotated
from NodeForge import EvaluationMode, Float

EXTENSION_API = 2
EXTENSIONS = {
    'scale_value': '.backend:scale_value',
}


def scale_value(
    value: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
    *,
    factor: Annotated[Float, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = 2.0,
) -> Float:
    ...
```

```python
# systems/scale_tools/backend.py
from NodeForge.extension_api import ExtensionBackendContext, ExtensionBackendValue, NFType


def scale_value(context: ExtensionBackendContext, value, *, factor=2.0):
    node = context.group.nodes.new('ShaderNodeMath')
    node.operation = 'MULTIPLY'
    node.location = context.location

    context.group.links.new(value.socket, node.inputs[0])

    if isinstance(factor, ExtensionBackendValue):
        context.group.links.new(factor.socket, node.inputs[1])
    else:
        node.inputs[1].default_value = float(factor)

    return context.value(node.outputs[0], NFType.FLOAT)
```

Once exported by the package, the callable is used through the package namespace described in [Importing package callables](PACKAGES.md#importing-package-callables).

## Direct value contracts

The direct NodeForge type markers exported from `NodeForge` are:

```text
Float
Int
Bool
Vector
Color
Geometry
Material
Object
String
Bundle
Rotation
```

A direct parameter uses `typing.Annotated` with one marker, or a finite union of markers, plus exactly one `EvaluationMode`:

```python
from typing import Annotated
from NodeForge import EvaluationMode, Float, Int


def amount(
    value: Annotated[Float | Int, EvaluationMode.RUNTIME_ONLY],
) -> Float:
    ...
```

The marker classes are annotation tokens rather than runtime values to instantiate. A union accepts any listed NodeForge type and preserves the actual `NFType` of the selected argument.

### Evaluation modes

| Mode | Accepted representation |
| --- | --- |
| `EvaluationMode.COMPILE_TIME_ONLY` | Detached compile-time data only. |
| `EvaluationMode.RUNTIME_ONLY` | Runtime NodeForge value only; the backend receives an `ExtensionBackendValue`. |
| `EvaluationMode.COMPILE_TIME_OR_RUNTIME` | Detached data when known at compile time, otherwise the runtime representation. |

`RUNTIME_ONLY` parameters cannot have defaults. Compile-time and mixed parameters may default to detached `Bool`, signed-32 `Int`, `Float`, `String`, 3-component `Vector` or `Rotation`, or 4-component `Color` values. Vector, Rotation and Color defaults may be written as Python lists or tuples and are canonicalized by NodeForge. Color is linear RGBA `(R, G, B, A)` with finite nonnegative binary32 components, including alpha and valid HDR values. A Color-marked parameter receives an immutable RGBA tuple in its compile-time branch, or an `ExtensionBackendValue` in its runtime branch. Ordinary extension `list` and `tuple` semantic fields remain structural Python values.

A declared `Rotation` default is an XYZ Euler-angle triple in radians, normalized to an immutable three-float binary32 tuple. Python integer components may exceed signed Int32 when their numeric values are representable as finite binary32 floats. A uniquely declared `Rotation` parameter interprets an untyped numeric triple as Rotation; `Vector` declarations interpret it as Vector. A parameter typed `Rotation | Vector` cannot use an untyped numeric triple as a default or static argument because both alternatives match. A typed runtime reference uses its exact declared type. Backend overloads with equally matching Rotation and Vector candidates also reject an untyped triple as ambiguous. See [Writing Functions](WRITING_FUNCTIONS.md#parameters-and-sockets) for the `.nf` source-default limits and explicit runtime Euler conversion.

```python
from typing import Annotated
from NodeForge import Color, EvaluationMode

def tint(value: Annotated[Color, EvaluationMode.COMPILE_TIME_OR_RUNTIME] = (0.2, 0.3, 0.4, 0.75)) -> Color:
    ...
```

Use compile-time data for configuration that changes graph shape or node properties. Use runtime values for data that should remain connected as Geometry Nodes sockets or fields.

### Direct results

A backend-only callable may return one exact direct type:

```python
def length(value: Annotated[Vector, EvaluationMode.RUNTIME_ONLY]) -> Float:
    ...
```

or a fixed tuple:

```python
def split(value: Annotated[Float, EvaluationMode.RUNTIME_ONLY]) -> tuple[Float, Int]:
    ...
```

Variadic tuples such as `tuple[Float, ...]` and union results such as `Float | Int` are not executable direct-result contracts.

### Invocation-dependent results

Use `DynamicResults` when a call's output count or types depend on its arguments:

1. Set the return annotation in `interface.py` to `DynamicResults`, imported from `NodeForge`.
2. Define `_result_types_<public_name>(arguments)` in the same owner's `semantic.py`. Use the callable's key in `EXTENSIONS` as `<public_name>`.
3. Return the output types from the resolver and matching runtime values from the backend.

This extension forwards one or more values as a typed tuple:

```python
# interface.py
from typing import Annotated
from NodeForge import DynamicResults, EvaluationMode, Float, Int, Vector

EXTENSION_API = 2
EXTENSIONS = {'forward_values': '.backend:forward_values'}


def forward_values(
    *values: Annotated[Float | Int | Vector, EvaluationMode.RUNTIME_ONLY],
) -> DynamicResults:
    ...
```

```python
# semantic.py

def _result_types_forward_values(arguments):
    """Return each supplied value's actual type in argument order."""
    values = arguments['values']
    if not values:
        raise ValueError('forward_values requires at least one value')
    return tuple(value.typ for value in values)
```

```python
# backend.py

def forward_values(context, *values):
    """Forward the current invocation's typed runtime values."""
    return tuple(values)
```

Call it inside a grouped `.nf` function, using the installed package's import name:

```python
from packages import tools

def example(value: Float):
    single = tools.forward_values(value)
    direction, number = tools.forward_values(position(), index())
    return single[0], direction, number
```

A single result is still a tuple: use `[0]` or a one-element unpack target.

The resolver's `arguments` is a read-only mapping from parameter names to the following values. Defaults are included; variadic arguments retain their source order.

| Argument | Resolver value |
| --- | --- |
| Direct runtime value | `ResultRuntimeArg`: `.typ` is the actual `NFType`. |
| Direct compile-time value | `ResultStaticArg`: `.typ` and the canonical immutable `.value`. |
| Variadic parameter | A tuple of argument views, empty when no arguments were supplied. |
| Semantic record or list | A reconstructed semantic value with `RuntimeRef` runtime leaves. |

Import `ResultRuntimeArg` and `ResultStaticArg` from `NodeForge.extension_result_api`.

Resolver rules:

- Return a nonempty tuple or list of exact `NFType` members in output order, for example `(NFType.FLOAT, NFType.INT)`. Import `NFType` from `NodeForge.extension_api`.
- Use argument types and compile-time data to select outputs. Create Blender nodes and resources in the backend. Importing registered physical implementation modules from the resolver is rejected.
- For overloads, annotate every alternative with `DynamicResults` and provide one resolver for the public name.

The backend must return a Python tuple of `ExtensionBackendValue` objects, including for a single result. See [`context.result_types`](#extensionbackendcontext) for output validation and [Loading and reload behavior](#loading-and-reload-behavior) for resolver loading.

### Overloads

Use `typing.overload` for a finite set of direct-value signatures. Alternatives are tried in source order.

```python
from typing import Annotated, overload
from NodeForge import Bool, EvaluationMode, Float, Int

EXTENSION_API = 2
EXTENSIONS = {'select_value': '.backend:select_value'}

@overload
def select_value(
    cond: Annotated[Bool, EvaluationMode.RUNTIME_ONLY],
    a: Annotated[Int, EvaluationMode.RUNTIME_ONLY],
    b: Annotated[Int, EvaluationMode.RUNTIME_ONLY],
) -> Int:
    ...


@overload
def select_value(
    cond: Annotated[Bool, EvaluationMode.RUNTIME_ONLY],
    a: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
    b: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
) -> Float:
    ...


def select_value(*args, **kwargs):
    ...
```

Semantic record and list contracts described below are not overload alternatives.

## Semantic values

Frozen dataclasses declared directly in `interface.py` can define frontend-only structured values. They are useful when runtime references and compile-time options need to travel together without becoming a Geometry Nodes socket type.

```python
from dataclasses import dataclass
from typing import Annotated
from NodeForge import EvaluationMode, Float

EXTENSION_API = 2


@dataclass(frozen=True)
class Part:
    value: Float


EXTENSIONS = {
    'make_part': None,
    'consume_part': '.backend:consume_part',
}


def make_part(
    value: Annotated[Float, EvaluationMode.RUNTIME_ONLY],
) -> Part:
    ...


def consume_part(part: Part) -> Float:
    ...
```

A public record can appear in public parameters or results. A record whose class name begins with `_` is owner-private and can be used for normalized state passed from semantic processing to a backend.

Record identity is nominal and owner-local. Define the dataclass under its own name in `interface.py`; an imported lookalike class is not equivalent.

### Record fields

Record fields and private semantic state support this recursive schema:

```text
one NodeForge marker, or a finite marker union such as Float | Int
exact Python bool | int | float | str
another registered record
list[T]
tuple[T1, T2, ...]
tuple[T, ...]
dict[str, T]
T | None
```

Python containers are copied into compiler-owned detached state, so later package-side mutation cannot change an already stored semantic value.

### `RuntimeRef`

When `semantic.py` receives a direct runtime argument, the value is represented by a compiler-issued `RuntimeRef` rather than a Blender socket.

```python
from NodeForge import RuntimeRef


def make_part(value) -> Part:
    assert isinstance(value, RuntimeRef)
    return Part(value)
```

`RuntimeRef.typ` reports the canonical `NFType`. A reference belongs to the active semantic invocation and must not be fabricated or cached for later invocations.

### Semantic lists

A semantic callable may return or consume a declared `list[Record]`. Such lists can be stored in source variables and expanded into another extension call with caller-side `*` when the receiving contract accepts those elements.

Semantic lists are compiler semantic values, not mutable compile-time arrays; they do not expose structural mutation methods such as `append(...)`.

## `semantic.py`

If `semantic.py` defines an ordinary same-name function with a return annotation, that function provides frontend semantic behavior for the declared callable.

### Semantic-only callable

Use `None` in `EXTENSIONS` and return the declared semantic result:

```python
# semantic.py
from .interface import Part


def make_part(value) -> Part:
    return Part(value)
```

The result may be assigned and passed to another extension call:

```python
from packages import tools

def example(value: Float):
    part = tools.make_part(value)
    result = tools.consume_part(part)
    return result
```

Semantic records and lists may contain runtime dependencies, but they are not ordinary Geometry Nodes socket values. Return an ordinary socket value from a `.nf` function; semantic records cannot be exposed as Group Node outputs or carried through Repeat Zone state.

### Semantic-then-backend callable

Semantic processing may normalize public arguments into private owner state before physical realization.

```python
# interface.py
from dataclasses import dataclass
from NodeForge import Float


@dataclass(frozen=True)
class Part:
    value: Float


@dataclass(frozen=True)
class _BuildState:
    part: Part


EXTENSION_API = 2
EXTENSIONS = {'consume_part': '.backend:consume_part'}


def consume_part(part: Part) -> Float:
    ...
```

```python
# semantic.py
from .interface import _BuildState


def consume_part(part) -> _BuildState:
    return _BuildState(part)
```

```python
# backend.py
from NodeForge.extension_api import NFType


def consume_part(context, state):
    runtime_value = state.part.value
    return context.value(runtime_value.socket, NFType.FLOAT)
```

The backend receives current-session record classes. Runtime leaves inside semantic state are reconstructed as `ExtensionBackendValue` objects; static fields remain detached Python data.

## Backend API

A physical implementation is invoked as:

```python
implementation(context, *bound_args, **bound_kwargs)
```

Import the public backend types from:

```python
from NodeForge.extension_api import ExtensionBackendContext, ExtensionBackendValue, NFType
```

Runtime direct arguments are `ExtensionBackendValue` instances. Compile-time arguments are detached Python data selected by the parameter's evaluation mode.

### `ExtensionBackendValue`

A runtime value exposes:

- `value.socket` — the Blender output socket carrying the value;
- `value.typ` — its canonical `NFType`.

Do not construct compiler values or depend on compiler internals. Use the supplied socket with nodes created in the current backend context.

### `ExtensionBackendContext`

`context.group` is the candidate Geometry Nodes group for this compilation.

`context.location` is the `(x, y)` placement anchor for the extension call.

`context.result_types` is the read-only tuple of exact `NFType` members for this call's outputs. It is available for fixed and [invocation-dependent results](#invocation-dependent-results). Return values must match its count, order, and types. Each runtime leaf must use an output socket of the matching physical type in `context.group`; input sockets and sockets from other groups are rejected. Invalid backend results reject compilation before the candidate group is published.

`context.value(socket, typ)` validates and wraps an output socket from the current group as the declared NodeForge type:

```python
return context.value(node.outputs['Value'], NFType.FLOAT)
```

A backend may return an existing compatible `ExtensionBackendValue` when no new node is required.

### Generated Blender resources

Persistent Blender IDs created by a backend should be transaction-owned:

```python
mesh = context.new_generated_mesh(role='surface', name_hint='Terrain')
curve = context.new_generated_curve(role='path', name_hint='Guide')
obj = context.new_generated_object(mesh, role='preview', name_hint='Terrain')
```

Available helpers are:

- `new_generated_mesh(*, role, name_hint='')`;
- `new_generated_curve(*, role, name_hint='')`;
- `new_generated_object(data=None, *, role, name_hint='')`.

Resources created through these helpers participate in the active NodeForge transaction and can be rolled back when compilation fails. Semantic code must not create Blender resources.

## Loading and reload behavior

NodeForge snapshots `interface.py`, `semantic.py`, backend modules, and owner-local helpers as one extension owner. Source bytes determine freshness; filesystem timestamp changes alone do not.

For invocation-dependent results, contract loading imports `semantic.py` and checks the required resolver exports without running their bodies. Other semantic modules and physical implementation modules load when their phase is used. Do not rely on mutable module state being shared between frontend semantic execution and Blender-side realization.

A new root compilation resolves a new package environment snapshot and uses it consistently for nested calls.

Call Python-backed extensions that create Blender nodes inside a typed `.nf` function. **Compile Script** and **Update Script** reject those calls directly at module level in the existing base tree. The function supplies a separate node group for the extension's nodes. Mesh Compute callbacks use the dedicated entry-function and modifier workflow described in [Mesh Compute](MESH_COMPUTE.md).

## Validation

Package installation validates Extension API declarations before the candidate package becomes active. In addition to the contracts described above:

- `EXTENSION_API` must equal `2` and `EXTENSIONS` must be non-empty;
- `__unique__` is compiler-reserved and cannot be an extension parameter;
- public extension signatures do not support `**kwargs`;
- executable results use one exact NodeForge type, a fixed tuple of exact types, or [`DynamicResults`](#invocation-dependent-results).

Treat validation failures as declaration errors rather than compensating for malformed contracts inside backend code.
