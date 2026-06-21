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
