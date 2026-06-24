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
sin cos tan asin acos atan sqrt abs floor ceil round fract frac radians degrees exp ln sign
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

### `mix(a, b, factor)` / `lerp(a, b, factor)`

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


### `inverse_lerp(a, b, x)`

Returns the normalized position of `x` inside the range `a..b`.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `a` | `Float` | Range start. |
| `b` | `Float` | Range end. |
| `x` | `Float` | Value to normalize. |

Returns: `Float`.

### `remap(x, in_min, in_max, out_min, out_max)`

Maps `x` from one numeric range into another range without clamping the output.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `x` | `Float` | Input value. |
| `in_min` | `Float` | Input range start. |
| `in_max` | `Float` | Input range end. |
| `out_min` | `Float` | Output range start. |
| `out_max` | `Float` | Output range end. |

Returns: `Float`.

### `saturate(x)`

Clamps `x` to the `0..1` range.

Returns: `Float`.

### `step(edge, x)`

Returns `0` below `edge` and `1` at or above `edge`.

Returns: `Float`.

### `smoothstep(edge0, edge1, x)`

Returns a cubic smoothed transition from `0` to `1` across `edge0..edge1`.

Returns: `Float`.

### `smootherstep(edge0, edge1, x)`

Returns a quintic smoothed transition from `0` to `1` across `edge0..edge1`.

Returns: `Float`.

### `pingpong(x, length)`

Repeats `x` as a positive triangular wave in the `0..length` range.

Returns: `Float`.

### `wrap(x, min, max)`

Wraps `x` into the positive repeating range `min..max`, including negative inputs.

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

### `rotate2d(v, angle)`

Rotates `v` around the Z axis by `angle` radians and preserves the original Z component.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `v` | `Vector` | Input vector. |
| `angle` | `Float` | Rotation angle in radians. |

Returns: `Vector`.

### `polar(radius, angle)`

Creates an XY vector from polar coordinates.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `radius` | `Float` | Distance from origin. |
| `angle` | `Float` | Angle in radians. |

Returns: `Vector`.

### `angle_between(a, b)`

Returns the angle in radians between two vectors.

Returns: `Float`.

### `rotate_around_axis(v, axis, angle)`

Rotates `v` around `axis` by `angle` radians.

Parameters:

| Name | Type | Description |
| --- | --- | --- |
| `v` | `Vector` | Input vector. |
| `axis` | `Vector` | Rotation axis. |
| `angle` | `Float` | Rotation angle in radians. |

Returns: `Vector`.

## Embedded L-systems

L-system constructors are global DSL calls resolved by the embedded systems registry, not ordinary built-ins in `builtins/registry.py`. They use the reserved `ls_` prefix and return normal `Geometry` through `ls_system(...)`.

See [L-systems](LSYSTEMS.md) for constructor reference, symbol rules, backend selection, generated-resource ownership, limits, and examples.

## Raw Blender node layer

`node(...)` is a controlled escape hatch for creating ordinary Geometry Nodes by Blender `bl_idname` while keeping NodeForge's `Value(socket, typ)` boundary. It never exposes raw `bpy` objects, node trees, Blender nodes, or arbitrary sockets to DSL code.

Single-output form:

```python
mask = node(
    "FunctionNodeCompare",
    props={"data_type": "FLOAT", "operation": "GREATER_THAN"},
    inputs={"A": position().z, "B": 0.5},
    output="Result",
    typ=Bool,
)
```

Multi-output form:

```python
sep = node(
    "ShaderNodeSeparateXYZ",
    inputs={"Vector": (1.0, 2.0, 3.0)},
    outputs={"X": Float, "Y": Float, "Z": Float},
)
out = sep.X + sep["Y"]
```

`props`, `inputs`, and `outputs` must be literal dictionaries. Properties are assigned before sockets are resolved, because many Blender nodes expose property-dependent sockets. Sockets are resolved by exact enabled socket name only; missing, disabled, or ambiguous sockets raise `CompileError`.

`inputs` values may be runtime NodeForge expressions, supported literal socket defaults, or a non-empty list for multi-input fanout. A list means repeated links into a verified multi-input socket; it is not vector syntax. Use tuple syntax such as `(1.0, 2.0, 3.0)` for vector-like literal defaults.

Type declarations use reserved type-token names: `Float`, `Int`, `Bool`, `Vector`, and `Geometry`. These names are globally reserved and may only appear in `node(..., typ=...)` and `node(..., outputs={...})`. They cannot be used as variables, loop targets, function names, function parameters, normal expressions, or implicit inputs.

Multi-output `node(...)` returns a compile-time-only `NodeResult`. Selecting a declared output, for example `result.Geometry` or `result["Socket Name"]`, returns a normal runtime `Value`. Passing the `NodeResult` itself to arithmetic, geometry built-ins, wrappers, `output(...)`, or final auto-output raises `CompileError`.

Raw nodes store internal NodeForge metadata on the created Blender node. This metadata is not public API. It records the raw node's declared properties, input modes (`literal_default`, `single_link`, or `multi_link`), literal socket defaults, output sockets, and output mode so existing-group updates can copy and validate raw-node-owned surface fail-closed. Non-raw nodes continue to use the existing tolerant update copy path.

### Compare wrappers

The following wrappers are implemented through the same raw-node builder:

```python
greater_than(a, b)
greater_equal(a, b)
less_than(a, b)
less_equal(a, b)
equal(a, b)
not_equal(a, b)
```

They produce `Bool` values using `FunctionNodeCompare` and support numeric and Vector comparisons. Bool-to-Bool comparisons are not exposed through these wrappers in this stage because the supported Blender compare-node surface for NodeForge raw wrappers is limited to verified `FunctionNodeCompare` data types and socket contracts.
