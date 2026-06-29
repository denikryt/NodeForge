# DSL Built-ins Reference

Built-ins are callable DSL primitives registered by `builtins/registry.py`.

Types used below:

| Type | Meaning |
| --- | --- |
| `Geometry` | Blender geometry socket. |
| `Float` | Numeric field or value. |
| `Int` | Integer field or value. |
| `Bool` | Boolean field or value. |
| `Vector` | 3-component vector field or value. |
| `String` | Compile-time string literal. |

## Inputs

### `input_geometry(name)`

Creates a Geometry input socket.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `name` | `String` | Input socket name. |

Returns: `Geometry`.

### `input_float(name, default=0.0)`

Creates a Float input socket.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `name` | `String` | Input socket name. |
| `default` | compile-time `Float` | Socket default value. |

Returns: `Float`.

### `input_int(name, default=0)`

Creates an Int input socket.

Returns: `Int`.

### `input_bool(name, default=False)`

Creates a Bool input socket.

Returns: `Bool`.

### `input_vector(name, default=vector(0, 0, 0))`

Creates a Vector input socket.

Returns: `Vector`.

## Field inputs

### `position()`

Returns the current element position field.

Returns: `Vector`.

### `normal()`

Returns the current element normal field.

Returns: `Vector`.

### `index()`

Returns the current element index field.

Returns: `Int`.

### `id()`

Returns the current element ID field.

Returns: `Int`.

## Geometry

### `points(count)`

Creates point geometry with `count` points.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `count` | `Int` | Number of points. |

Returns: `Geometry`.

### `grid(width, height)`

Creates a planar mesh grid on the XY plane.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `width` | `Int` | Number of vertices in X. |
| `height` | `Int` | Number of vertices in Y. |

Returns: `Geometry`.

### `grid_uv()`

Returns the UV coordinates produced by the most recent `grid(width, height)` call in the current script scope.

Returns: `Vector` with `.x` and `.y` in the `0..1` range.

### `set_position(geometry, position, selection=True)`

Sets positions on a geometry.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `geometry` | `Geometry` | Input geometry. |
| `position` | `Vector` | New position field. |
| `selection` | `Bool` | Optional field mask. |

Returns: `Geometry`.


### Point layouts

Reusable point-layout helpers are function-library entries. Import them from `functions` before use. `from functions import *` is also available for exploratory scripts, but explicit imports are preferred in examples and reusable code. These helpers operate on point-domain geometry; `grid(width, height)` remains the planar mesh grid primitive and is not changed by `grid_points(...)`.

Angles are radians. Use `radians(...)` when authoring degree values.

```python
from functions import layout_grid, grid_points, layout_circle

pts = points(6)
pts = layout_grid(pts, count=vector(3, 2, 1), spacing=vector(1.0, 2.0, 3.0))
grid = grid_points(count=vector(5, 4, 1), spacing=vector(1.0, 1.0, 0.0))
pts = layout_circle(pts, count=6, radius=2.0)
output("Geometry", join(pts, grid))
```

#### `layout_grid(geometry, count=vector(1, 1, 1), spacing=vector(1, 1, 1), centered=False)`

Places existing points in a 3D lattice. `count` is a `Vector` interpreted as `(count_x, count_y, count_z)`. `spacing` is a `Vector`, so scalar spacing should be written explicitly as `vector(s, s, s)` or another vector appropriate for the layout. When `centered=True`, positions are shifted by half of the layout extent.

Returns: `Geometry`.

#### `grid_points(count=vector(1, 1, 1), spacing=vector(1, 1, 1), centered=False)`

Creates points and places them with `layout_grid(...)`. Zero count components produce zero points. Negative count components are clamped to zero for the generated point count.

Returns: `Geometry`.

#### `layout_circle(geometry, count=16, radius=1.0, start_angle=0.0, end_angle=tau, include_endpoint=False)`

Places existing points on an XY circle or arc. The imported function uses the explicit `count` input; it does not derive count from the input geometry. Full circles default to `include_endpoint=False` so the first and last point are not duplicated at the same location.

Returns: `Geometry`.

#### `layout_spiral(geometry, count=16, radius=1.0, turns=1.0, height=0.0, start_radius=0.0, start_angle=0.0)`

Places existing points on a simple radial spiral in the XY plane with optional Z height. The imported function uses the explicit `count` input; it does not derive count from the input geometry. For geometries with more than one point, the last point reaches the final radius and height.

Returns: `Geometry`.

#### `spiral_points(count=16, radius=1.0, turns=1.0, height=0.0, start_radius=0.0, start_angle=0.0)`

Creates points and places them with `layout_spiral(...)`.

Returns: `Geometry`.

#### `layout_random(geometry, min=vector(-1, -1, -1), max=vector(1, 1, 1), seed=0)`

Places existing points using `random_value(...)` with the current point `index()` as the random ID, so points receive stable per-index values for a given seed. Bounds are `Vector` values. This is uniform random placement, not Poisson or blue-noise sampling.

Returns: `Geometry`.

#### `random_points(count=16, min=vector(-1, -1, -1), max=vector(1, 1, 1), seed=0)`

Creates points and places them with `layout_random(...)`.

Returns: `Geometry`.

### `store_named_attribute(geometry, name, value, selection=True, domain="POINT", type=None)`

Stores a named attribute on geometry.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `geometry` | `Geometry` | Input geometry. |
| `name` | `String` | Attribute name. |
| `value` | `Float`, `Int`, `Bool`, or `Vector` | Attribute value field. |
| `selection` | `Bool` | Optional field mask. |
| `domain` | `String` | Attribute domain, such as `"POINT"`, `"EDGE"`, `"FACE"`, `"CORNER"`, or `"INSTANCE"`. |
| `type` | `String` | Optional data type override, such as `"FLOAT"`, `"INT"`, `"BOOLEAN"`, `"VECTOR"`, or `"COLOR"`. |

Returns: `Geometry`.

### `set_material(geometry, material_name)`

Assigns a material by name. The material is created when it does not exist.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `geometry` | `Geometry` | Input geometry. |
| `material_name` | `String` | Compile-time Blender material name. |

Returns: `Geometry`.

### `cube(size=1.0)`

Creates a cube mesh.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `size` | `Float` | Cube side length. |

Returns: `Geometry`.

### `join(*geometry)`

Joins multiple geometries.

Forms:

```python
join(geo_a, geo_b, geo_c)
join([geo_a, geo_b, geo_c])
```

Returns: `Geometry`.

### `transform(geometry, translation=None, scale=None, rotation=None)`

Transforms geometry.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `geometry` | `Geometry` | Input geometry. |
| `translation` | `Vector` | Translation. |
| `scale` | `Vector` or `Float` | Scale. |
| `rotation` | `Vector` | Euler rotation. |

Returns: `Geometry`.

### `polyline(points)`

Creates a polyline from a compile-time list of vector points.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `points` | compile-time list of `Vector` constants | Polyline vertices. |

Returns: `Geometry`.

## Instancing

### `instance_on_points(instance, points, scale=None, rotation=None, realize=True)`

Instances geometry on points.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `instance` | `Geometry` | Geometry to instance. |
| `points` | `Geometry` | Point geometry. |
| `scale` | `Vector` or `Float` | Instance scale. |
| `rotation` | `Vector` | Instance rotation. |
| `realize` | compile-time `Bool` | Realize instances before returning. |

Returns: `Geometry`.

### `realize_instances(geometry)`

Realizes instances in geometry.

Returns: `Geometry`.

## Runtime loops

### `range(count)`

Compile-time loop form for static Python-style loops.

Use inside:

```python
for i in range(steps):
    ...
```

`steps` must evaluate at compile time.

### `runtime_range(count)`

Runtime loop form that compiles to a Geometry Nodes Repeat Zone.

Use inside:

```python
for i in runtime_range(max_iter):
    ...
```

`count` can be a runtime `Int` socket.

## Scalar math

Unary functions:

```text
sin cos tan asin acos atan sqrt abs floor ceil round fract radians degrees exp ln
```

Signature:

```python
fn(value)
```

Returns: `Float`.

Binary functions:

```text
min max pow log atan2 mod
```

Signature:

```python
fn(a, b)
```

Returns: `Float`.

### `clamp(value, min, max)`

Clamps a numeric value.

Returns: `Float`.

### `mix(a, b, factor)`

Interpolates between `a` and `b`.

Returns: type compatible with inputs.

### `select(cond, false, true)`

Selects between two values using a boolean condition.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `cond` | `Bool` | Selection condition. |
| `false` | any compatible socket type | Value when `cond` is false. |
| `true` | any compatible socket type | Value when `cond` is true. |

Returns: selected value type.

### `map_range(value, from_min, from_max, to_min, to_max)`

Maps a value from one numeric range to another.

Returns: `Float`.

### `noise(vector=position(), scale=..., detail=..., roughness=..., lacunarity=..., distortion=..., normalize=...)`

Creates a 3D noise texture field and returns its factor output.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `vector` | `Vector` | Sampling coordinate. Defaults to `position()`. |
| `scale` | `Float` | Noise scale. |
| `detail` | `Float` | Noise detail. |
| `roughness` | `Float` | Noise roughness. |
| `lacunarity` | `Float` | Noise lacunarity. |
| `distortion` | `Float` | Noise distortion. |
| `normalize` | compile-time `Bool` | Sets the Blender Noise Texture normalize option. |

Returns: `Float`.

### `random_value()` / `random_value(min, max, seed=..., id=...)`

Creates a random value field.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `min` | `Float` or `Vector` | Minimum value. |
| `max` | `Float` or `Vector` | Maximum value. |
| `seed` | `Int` | Seed. |
| `id` | `Int` | ID field. |

Returns: `Float` or `Vector`.

## Vector

### `vector(x, y, z)`

Combines three numeric components into a vector.

Returns: `Vector`.

Vector functions that return `Float`:

```text
length(v)
distance(a, b)
dot(a, b)
```

Vector functions that return `Vector`:

```text
normalize(v)
cross(a, b)
reflect(v, normal)
project(v, normal)
```

Derived scalar and vector helpers such as `smoothstep(...)`, `sign(...)`, `rotate2d(...)`, and `angle_between(...)` are function-library entries. Import them from `functions` before use.

## Embedded L-systems

L-system constructors are global DSL calls resolved by the embedded systems registry, not ordinary built-ins in `builtins/registry.py`. They use the reserved `ls_` prefix and return normal `Geometry` through `ls_system(...)`. The L-system constructor set is `ls_system`, `ls_axiom`, `ls_rule`, `ls_iterations`, `ls_angle`, `ls_step`, `ls_param`, `ls_marker`, and `ls_points`.

See [L-systems](LSYSTEMS.md) for constructor reference, symbol rules, backend selection, generated-resource ownership, limits, and examples.

## Raw Blender node layer

Use `node(...)` when NodeForge does not yet have a normal wrapper for a Blender Geometry Node.

`node(...)` creates a Blender node by its `bl_idname`, sets node properties, connects inputs, and returns normal typed NodeForge values. You still work with NodeForge types such as `Float`, `Vector`, `Bool`, and `Geometry`; raw Blender objects are not exposed to the script.

Use Blender's Python node identifier as the first argument:

```python
value = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "GREATER_THAN"},
    inputs={"A": position().z, "B": 0.5},
    output="Result",
    typ=Bool,
)
```

Use the returned value like any other NodeForge value:

```python
geo = delete_geometry(input_geometry("Geometry"), value)
output("Geometry", geo)
```

The identifier is the Blender node type name, for example:

```text
FunctionNodeCompare
ShaderNodeSeparateXYZ
GeometryNodeJoinGeometry
```

### Single-output nodes

Use `output=` and `typ=` when you need one output socket from the Blender node.

```python
is_high = node(
    "FunctionNodeCompare",
    props={
        "data_type": "FLOAT",
        "operation": "GREATER_THAN",
    },
    inputs={
        "A": position().z,
        "B": 0.5,
    },
    output="Result",
    typ=Bool,
)

output("is_high", is_high)
```

This creates a `FunctionNodeCompare`, sets its Blender properties, links `position().z` into socket `A`, assigns `0.5` as the default value of socket `B`, and returns the `Result` socket as a NodeForge `Bool`.

### Multi-output nodes

Use `outputs={...}` when one Blender node has several outputs that you want to use later.

```python
parts = node(
    "ShaderNodeSeparateXYZ",
    inputs={"Vector": position()},
    outputs={
        "X": Float,
        "Y": Float,
        "Z": Float,
    },
)

x = parts.X
z = parts["Z"]

output("x_plus_z", x + z)
```

A multi-output `node(...)` returns a `NodeResult`. The `NodeResult` itself is not a geometry, float, vector, or boolean value. Select one declared socket first:

```python
parts.X
parts["Socket Name"]
```

Use bracket access when the socket name contains spaces or punctuation:

```python
sphere = node(
    "GeometryNodeMeshUVSphere",
    inputs={"Segments": 16, "Rings": 8, "Radius": 1.0},
    outputs={
        "Mesh": Geometry,
        "UV Map": Vector,
    },
)

uv = sphere["UV Map"]
```

### Connecting raw nodes to normal DSL calls

Raw node outputs are normal NodeForge values after you select a socket. They can be passed into higher-level DSL calls.

```python
parts = node(
    "ShaderNodeSeparateXYZ",
    inputs={"Vector": position()},
    outputs={
        "X": Float,
        "Y": Float,
        "Z": Float,
    },
)

height_offset = node(
    "ShaderNodeMapRange",
    props={"data_type": "FLOAT", "clamp": True},
    inputs={
        "Value": parts.X,
        "From Min": -1.0,
        "From Max": 1.0,
        "To Min": -0.4,
        "To Max": 0.8,
    },
    output="Result",
    typ=Float,
)

geo = node(
    "GeometryNodeSetPosition",
    inputs={
        "Geometry": grid(20, 20),
        "Selection": True,
        "Offset": vector(0.0, 0.0, height_offset),
    },
    output="Geometry",
    typ=Geometry,
)

output("Geometry", geo)
```

The important flow is:

```text
position()
  -> ShaderNodeSeparateXYZ.Vector
  -> parts.X
  -> ShaderNodeMapRange.Value
  -> height_offset
  -> vector(0, 0, height_offset)
  -> GeometryNodeSetPosition.Offset
```

### Multi-input sockets

Use a literal list only for Blender multi-input sockets, such as `GeometryNodeJoinGeometry.Geometry`.

```python
a = transform(cube(0.4), translation=vector(-1.0, 0.0, 0.0))
b = transform(cube(0.4), translation=vector(0.0, 0.0, 0.0))
c = transform(cube(0.4), translation=vector(1.0, 0.0, 0.0))

geo = node(
    "GeometryNodeJoinGeometry",
    inputs={
        "Geometry": [a, b, c],
    },
    output="Geometry",
    typ=Geometry,
)

output("Geometry", geo)
```

A list in `inputs={...}` means “create several links into the same multi-input socket”. It is not vector syntax.

Use `vector(...)` or a numeric 3-tuple for vector values:

```python
vector(1.0, 2.0, 3.0)
(1.0, 2.0, 3.0)
```

### Field values must be used in field-aware sockets

A raw node can create a field value, but the field only has a visible effect when it is connected to a node socket that evaluates fields.

This works because `GeometryNodeSetPosition.Offset` is field-aware:

```python
rand_z = node(
    "FunctionNodeRandomValue",
    props={"data_type": "FLOAT"},
    inputs={
        "Min": 0.0,
        "Max": 1.5,
        "ID": index(),
        "Seed": 19,
    },
    output="Value",
    typ=Float,
)

geo = node(
    "GeometryNodeSetPosition",
    inputs={
        "Geometry": grid(20, 20),
        "Selection": True,
        "Offset": vector(0.0, 0.0, rand_z),
    },
    output="Geometry",
    typ=Geometry,
)

output("Geometry", geo)
```

Use geometry-level sockets for whole-geometry operations, and field-aware sockets for per-point, per-face, or per-instance variation.

For example, `GeometryNodeSwitch` with `input_type="GEOMETRY"` selects one complete geometry branch. It does not switch individual points inside one mesh. For per-point switching, switch a `Float`, `Vector`, or `Bool`, then feed that result into a field-aware socket:

```python
parts = node(
    "ShaderNodeSeparateXYZ",
    inputs={"Vector": position()},
    outputs={
        "X": Float,
        "Y": Float,
        "Z": Float,
    },
)

right_side = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "GREATER_THAN"},
    inputs={"A": parts.X, "B": 0.0},
    output="Result",
    typ=Bool,
)

offset = node(
    "GeometryNodeSwitch",
    props={"input_type": "VECTOR"},
    inputs={
        "Switch": right_side,
        "False": vector(0.0, 0.0, -0.4),
        "True": vector(0.0, 0.0, 0.8),
    },
    output="Output",
    typ=Vector,
)

geo = node(
    "GeometryNodeSetPosition",
    inputs={
        "Geometry": grid(20, 20),
        "Selection": True,
        "Offset": offset,
    },
    output="Geometry",
    typ=Geometry,
)

output("Geometry", geo)
```

### Arguments

| Argument    | Required           | Description                                                                                                                                                                                |
| ----------- | ------------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `bl_idname` | yes                | Blender node identifier, such as `"GeometryNodeSetPosition"` or `"ShaderNodeSeparateXYZ"`. Must be a non-empty string literal.                                                             |
| `props`     | no                 | Literal dictionary of Blender node properties to assign before sockets are resolved. Use this for settings that change available sockets, such as compare type, switch type, or node mode. |
| `inputs`    | no                 | Literal dictionary mapping exact input socket names to NodeForge values, literal defaults, or multi-input fanout lists.                                                                    |
| `output`    | single-output mode | Exact output socket name to return.                                                                                                                                                        |
| `typ`       | single-output mode | NodeForge type token for the selected output socket.                                                                                                                                       |
| `outputs`   | multi-output mode  | Literal dictionary mapping output socket names to NodeForge type tokens.                                                                                                                   |

Use either:

```python
output="Socket Name", typ=Float
```

or:

```python
outputs={"A": Float, "B": Vector}
```

Do not combine the two forms.

### Supported socket types

Raw node declarations support these NodeForge type tokens:

```text
Float
Int
Bool
Vector
Geometry
```

The declared type must match the Blender socket family. For example, a Blender geometry socket must be declared as `Geometry`, and a Blender boolean socket must be declared as `Bool`.

Integer values may be linked into Blender float sockets. Other mismatches are rejected.

These names are reserved for `node(...)` type declarations. Do not use them as variable names.

### Socket names are exact

`node(...)` resolves sockets by exact enabled Blender socket name.

This means spelling, spaces, and property-dependent socket layouts matter:

```python
# Correct when the Blender output socket is named "UV Map":
uv = sphere["UV Map"]

# Correct when the Blender input socket is named "Profile Curve":
inputs={"Profile Curve": profile}
```

If a socket is missing, disabled, unsupported, or ambiguous, compilation fails with `CompileError`.

### Properties are assigned before sockets are resolved

Many Blender nodes expose different sockets depending on node properties. Put those settings in `props`.

```python
offset = node(
    "GeometryNodeSwitch",
    props={"input_type": "VECTOR"},
    inputs={
        "Switch": condition,
        "False": vector(0.0, 0.0, -0.5),
        "True": vector(0.0, 0.0, 0.5),
    },
    output="Output",
    typ=Vector,
)
```

Here `input_type="VECTOR"` must be assigned before NodeForge looks for the `False`, `True`, and `Output` sockets.

### Literal defaults

Input values can be:

```python
inputs={
    "A": position().z,              # linked runtime value
    "B": 0.5,                       # literal default
    "Selection": True,              # literal default
    "Offset": vector(0.0, 0.0, 1.0) # vector value
}
```

Supported literal defaults are booleans, integers, floats, strings, and numeric 3-tuples. A string literal is accepted only for sockets that Blender exposes as supported default-value sockets.

### Typical workflow

1. Find the node in Blender's manual or Python API.
2. Copy its `bl_idname`.
3. Check the exact input and output socket names in Blender.
4. Set property-dependent options in `props`.
5. Declare every output socket you want to use with a NodeForge type token.
6. Connect the returned value to normal NodeForge DSL calls.
7. If the graph compiles but the result is visually unchanged, check whether a field value was connected to a field-aware socket.

### Comparisons

Use normal DSL comparison operators for common comparisons:

```python
high = position().z > 0.5
left = position().x < 0.0
mask = high and left
output("mask", mask)
```

Use `node("FunctionNodeCompare", ...)` when you need direct Blender compare-node control.
