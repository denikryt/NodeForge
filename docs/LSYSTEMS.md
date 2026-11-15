# L-System Reference

The `nodeforge.lsystem` package exposes L-system constructors through the package namespace:

```python
from packages import lsystem as ls
```

This page is the API and grammar reference. For a worked introduction with complete examples, use the [L-System Guide](LSYSTEMS_SCRIPTING.md).

## Compatibility

NodeForge L-System 4.1 requires NodeForge 0.90.2 or newer.

## `ls.system(part, ...)`

Builds an L-system and returns `Geometry`.

| Part | Required | Multiplicity | Purpose |
| --- | --- | --- | --- |
| `ls.axiom(...)` | yes | one | Initial module stream. |
| `ls.iterations(...)` | yes | one | Static or runtime rewrite count. |
| `ls.angle(...)` | yes | one | Default turtle rotation angle. |
| `ls.step(...)` | yes | one | Default forward distance. |
| `ls.rule(...)` | no | many | One-symbol rewrite rules. |
| `ls.param(...)` | no | many | Named numeric values used by parameterized modules. |
| `ls.marker(...)` | no | many | Modules that emit marker points. |

All parts are positional. Their order inside `ls.system(...)` does not affect declaration resolution, so a rule or axiom may reference parameters and markers declared later in the call.

Duplicate singleton parts, rule predecessors, parameter names, or marker names raise `CompileError`.

```python
def example():
    return ls.system(
        ls.axiom("X"),
        ls.rule("X", "F[+X][-X]FX"),
        ls.rule("F", "FF"),
        ls.iterations(4),
        ls.angle(25),
        ls.step(0.08),
    )
```

## Constructors

### `ls.axiom(value)`

Defines the module stream before the first rewrite iteration.

| Parameter | Type |
| --- | --- |
| `value` | compile-time `String` |

The string may contain turtle commands, one-character grammar symbols, declared markers, and parameterized modules.

```python
def example():
    return ls.system(
        ls.axiom("F[+F]F"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(1),
    )
```

### `ls.rule(symbol, replacement)`

Defines one parallel rewrite rule.

| Parameter | Type | Constraint |
| --- | --- | --- |
| `symbol` | compile-time `String` | Exactly one turtle command or one ASCII letter, digit, or `_`. |
| `replacement` | compile-time `String` | Module stream; may be empty. |

Each iteration rewrites every module from the same input generation. Symbols without a rule pass through unchanged; an empty replacement deletes its predecessor.

Rule strings may be assembled with f-strings when every interpolated value is a compile-time string.

```python
def example():
    return ls.system(
        ls.axiom("F"),
        ls.rule("F", "F[+F]F[-F]F"),
        ls.iterations(2),
        ls.angle(25),
        ls.step(0.2),
    )
```

### `ls.iterations(value)`

Sets the number of rewrite generations.

| Accepted value | Behavior |
| --- | --- |
| compile-time `Int` | Must be non-negative; expansion happens during compilation. |
| runtime `Int` | Expansion happens inside Geometry Nodes and may change topology without recompilation. |

Runtime values below `0` are treated as `0`.

Runtime iteration mode requires the axiom and every rule replacement to balance `[` and `]` independently. Rules may not rewrite `[` or `]` in this mode.

```python
def example(iterations: Int = 4):
    plant = ls.system(
        ls.axiom("F"),
        ls.iterations(iterations),
        ls.angle(25),
        ls.step(0.2),
    )
    return plant
```

`ls.angle(...)` and `ls.step(...)` both accept either a compile-time number or a runtime `Float` or `Int`.

### `ls.angle(value)`

Sets the default rotation angle, in degrees, for unparameterized `+`, `-`, `^`, `&`, `/`, and `\` commands. An explicit argument such as `+(30)` or `/(roll)` takes precedence for that module.

```python
def example(angle: Float = 25.0):
    plant = ls.system(
        ls.axiom("F+F"),
        ls.iterations(0),
        ls.angle(angle),
        ls.step(1),
    )
    return plant
```

### `ls.step(value)`

Sets the default forward distance for unparameterized `F` and `f`. `F(0.5)` or `F(length)` supplies a per-module distance instead.

```python
def example(step: Float = 0.2):
    plant = ls.system(
        ls.axiom("FF"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(step),
    )
    return plant
```

### `ls.param(name, value)`

Binds a numeric value to a name used inside parameterized commands or markers.

| Parameter | Type | Constraint |
| --- | --- | --- |
| `name` | compile-time `String` | Identifier matching `[A-Za-z_][A-Za-z0-9_]*`. |
| `value` | compile-time number or runtime `Float` or `Int` | Numeric value exposed to the grammar. |

A module argument is either a numeric literal or one declared parameter name. NodeForge expressions are not parsed inside the L-system string; compute an expression in the surrounding script and bind its result with `ls.param(...)`.

```python
def example(segment_length: Float = 0.5):
    plant = ls.system(
        ls.axiom("F(length)F"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(0.2),
        ls.param("length", segment_length),
    )
    return plant
```

### `ls.marker(name, *parameter_names)`

Declares a marker module. Reaching that module emits a point at the current turtle position without moving or rotating the turtle.

| Parameter | Type | Constraint |
| --- | --- | --- |
| `name` | compile-time `String` | Identifier that is not a built-in turtle command. |
| `parameter_names` | compile-time `String` values | Unique attribute names for marker arguments. |

Marker parameter names cannot use the reserved `nf_lsys_` prefix.

```python
def example(size: Float["Leaf Size"] = 0.25):
    plant = ls.system(
        ls.axiom("FLeaf(size)"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(0.5),
        ls.param("size", size),
        ls.marker("Leaf", "size"),
    )
    return plant
```

Every marker point stores:

- `nf_lsys_marker_tangent` — turtle Heading;
- `nf_lsys_marker_up` — turtle Up;
- one point attribute for each declared marker parameter.

### `ls.points(geometry, marker="Name")`

Extracts points belonging to one declared marker.

| Parameter | Type | Constraint |
| --- | --- | --- |
| `geometry` | `Geometry` | Geometry carrying L-system marker attributes. |
| `marker` | compile-time `String` | Required keyword selecting the marker name. |

Returns `Geometry` containing only the selected marker points. Marker identity is stored in numeric point attributes, so filtering continues to work after operations such as assignment, `transform(...)`, and `join(...)` when Blender preserves those attributes.

```python
def extract_leaves(plant: Geometry) -> Geometry:
    return ls.points(plant, marker="Leaf")
```

## Rewrite and grammar rules

An L-system starts from the axiom, applies all matching rewrite rules in parallel for each iteration, and then sends the final module stream to the turtle interpreter.

Ordinary grammar symbols are one ASCII letter, digit, or `_`. They may participate in rewriting but are ignored during turtle drawing if they remain in the final stream. Declared markers may use multi-character identifiers.

Whitespace, unsupported punctuation, Unicode grammar symbols, undeclared parameter names, malformed argument lists, unmatched `]`, and unclosed `[` raise `CompileError` when the invalid structure is known during compilation.

## Turtle coordinate frame

The turtle starts at the world origin with this local frame:

| Axis | Initial direction | Meaning |
| --- | --- | --- |
| Heading | `+X` | Forward direction used by `F` and `f`. |
| Left | `+Y` | Local pitch axis. |
| Up | `+Z` | Local yaw axis. |

Yaw, pitch, and roll update the local frame, so rotation order matters. `[` stores position and all three orientation axes; `]` restores the saved state.

## Turtle commands

Rotation arguments are measured in degrees.

| Module | Meaning |
| --- | --- |
| `F` | Move by `ls.step(...)` and draw a segment. |
| `F(length)` | Move by `length` and draw a segment. |
| `f` | Move by `ls.step(...)` without drawing. |
| `f(length)` | Move by `length` without drawing. |
| `+` | Yaw left around local Up by `ls.angle(...)`. |
| `+(angle)` | Yaw left by `angle`. |
| `-` | Yaw right around local Up by `ls.angle(...)`. |
| `-(angle)` | Yaw right by `angle`. |
| `^` | Pitch up around local Left by `ls.angle(...)`. |
| `^(angle)` | Pitch up by `angle`. |
| `&` | Pitch down around local Left by `ls.angle(...)`. |
| `&(angle)` | Pitch down by `angle`. |
| `/` | Roll around local Heading by the positive default angle. |
| `/(angle)` | Roll around local Heading by positive `angle`. |
| `\` | Roll around local Heading by the negative default angle. |
| `\(angle)` | Roll around local Heading by negative `angle`. |
| `[` | Save the complete turtle state and begin a branch. |
| `]` | Restore the most recently saved state. |

A parameterized command accepts exactly one numeric literal or one `ls.param(...)` name.

## Runtime values

These constructor arguments may remain runtime values:

| Constructor | Runtime type | Effect |
| --- | --- | --- |
| `ls.iterations(...)` | `Int` | Changes rewrite count and topology. |
| `ls.angle(...)` | `Float` or `Int` | Changes default rotations. |
| `ls.step(...)` | `Float` or `Int` | Changes default forward distance. |
| `ls.param(...)` | `Float` or `Int` | Changes commands or markers that reference the parameter. |

The axiom, rule strings, parameter names, marker declarations, and rule set are compile-time declarations.

## Limits

| Limit | Applies to | Result when exceeded |
| --- | --- | --- |
| `200000` expanded modules | compile-time `ls.iterations(...)` | `CompileError` |
| branch depth `32` | compile-time-expanded planar branched systems using runtime angle or step values | `CompileError` |

Runtime `Int` iterations are evaluated inside Geometry Nodes and are not covered by the compile-time `200000`-module guard. Constrain user-facing runtime iteration inputs to practical ranges.

A compiled system must have a possible drawable segment or marker for the selected backend. A compile-time-expanded path that resolves to no drawable content raises `CompileError`.
