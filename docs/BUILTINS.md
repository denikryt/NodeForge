# Core DSL Built-ins Reference

Core built-ins are the primitive operations available in every NodeForge source file. They are registered by `builtins/registry.py` and do not require imports.

Use this file for the compiler-level DSL vocabulary: inputs, outputs, math, vectors, fields, geometry primitives, instancing, raw Blender nodes, and runtime loops. Reusable helpers under `functions/` are documented in [Function Library Reference](FUNCTIONS.md).

## Type names

| Type | Meaning |
| --- | --- |
| `Geometry` | Blender geometry socket. |
| `Float` | Numeric value or field. |
| `Int` | Integer value or field. |
| `Bool` | Boolean value or field. |
| `Vector` | 3-component vector value or field. |
| `String` | Compile-time string expression. |
| `Float`, `Int`, `Bool`, `Vector`, `Geometry` | Type tokens used by `node(..., typ=...)` and `node(..., outputs=...)`. |

NodeForge values are typed socket wrappers. A value can be a constant lowered to a node, a linked runtime field, or geometry. Most built-ins accept either literal values or runtime values of the declared type.

## Compile-time constants

NodeForge exposes three lowercase mathematical constants in every source file. They are compile-time numeric constants, so they can be used anywhere a numeric literal can be used.

| Constant | Value | Typical use |
| --- | --- | --- |
| `pi` | π, approximately `3.141592653589793` | Half-turn angles, circle arcs, radians-based trigonometry. |
| `tau` | 2π, approximately `6.283185307179586` | Full-turn angles and normalized circle formulas. |
| `e` | Euler's number, approximately `2.718281828459045` | Exponential and natural-log formulas. |

```python
radius = input_float('Radius', default=2.0)
angle = input_float('Angle', default=pi / 4.0)
cos_angle = cos(angle)
x = cos_angle * radius
sin_angle = sin(angle)
y = sin_angle * radius
pos = vector(x, y, 0)
output('Position', pos)
```

String literals, f-strings, imports, assignments, local functions, loops, conditionals, arrays, and automatic final outputs are documented in [DSL Syntax and Semantics](SYNTAX.md).


## Compile-time helper calls

These helpers are evaluated during compilation. They do not create Geometry Nodes sockets and cannot operate on runtime values.

### `range(stop)`
### `range(start, stop)`
### `range(start, stop, step)`

Creates a compile-time list of integers. Use it mainly for unrolled `for` loops or compile-time indexing.

| Parameter | Type | Description |
| --- | --- | --- |
| `start` | compile-time `Int` | First integer. Defaults to `0`. |
| `stop` | compile-time `Int` | Exclusive upper bound. |
| `step` | compile-time `Int` | Step size. Defaults to `1`. |

Returns: compile-time `List[Int]`.

```python
BASE_COUNT = 4
EXTRA_COUNT = 2
COUNT = BASE_COUNT + EXTRA_COUNT

items = []
for i in range(COUNT):
    offset = vector(i * 1.25, 0, 0)
    geo = cube(size=1.0)
    moved = transform(geo, translation=offset)
    items.append(moved)
result = join(items)
output('Geometry', result)
```

### `len(value)`

Returns the length of a compile-time list, tuple, or string.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time `List`, `Tuple`, or `String` | Sequence whose length is known during compilation. |

Returns: compile-time `Int`.

```python
sizes = [0.5, 1.0, 1.5]
count = len(sizes)
step = 1.0 / count
value = input_float('Value', default=step)
output('Value', value)
```

### `sum(value)`

Returns the numeric sum of a compile-time list or tuple.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time numeric `List` or `Tuple` | Sequence whose elements can be added during compilation. |

Returns: compile-time number.

```python
sizes = [0.5, 1.0, 1.5]
total = sum(sizes)
count = len(sizes)
average = total / count
geo = cube(size=average)
output('Geometry', geo)
```

### Compile-time math calls

The following math calls can be evaluated at compile time when all arguments are compile-time numbers: `sin`, `cos`, `tan`, `asin`, `acos`, `atan`, `sqrt`, `floor`, `ceil`, `round`, `abs`, `radians`, `degrees`, `exp`, and `ln`.

```python
angle = radians(45)
x = cos(angle)
y = sin(angle)
pos = vector(x, y, 0)
output('Position', pos)
```

## Outputs

### `output(value)`
### `output(name, value)`
### `output(name="Name", value=value)`

Creates a group output socket and connects a runtime value to it.

| Parameter | Type | Description |
| --- | --- | --- |
| `name` | `String` | Optional output socket name. When omitted, the output socket is named `out`. Duplicate output names receive suffixes such as `_2`. |
| `value` | `Float`, `Int`, `Bool`, `Vector`, or `Geometry` | Runtime value to expose on the node group. Arrays cannot be output directly; use `join(array)` or index the array first. |

Returns: statement only.

```python
geo = cube(size=2.0)
height = input_float('Height', default=1.0)
vec_1 = vector(1, 1, height)
geo_2 = transform(geo, scale=vec_1)
output('Geometry', geo_2)
output(name='Height', value=height)
```

Automatic final outputs are part of the source language semantics. See [DSL Syntax and Semantics](SYNTAX.md#automatic-final-outputs).

## Active Geometry stream statements

These statement-only forms operate on an implicit Geometry input/output stream. When a script contains `store(...)` or statement-form `set_position(...)`, NodeForge creates a Geometry group input named `Geometry` and a Geometry group output named `Geometry`.

Use these forms when the node group is meant to modify geometry that is passed into it. Use expression-form `store_named_attribute(geometry, ...)` and `set_position(geometry, ...)` when you want to operate on an explicit Geometry value inside the script.

### `store(name, value, selection=True, domain="POINT", type=None)`

Stores a named attribute on the active Geometry stream.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `String` | required | Attribute name. |
| `value` | `Float`, `Int`, `Bool`, or `Vector` | required | Attribute value field. Arrays are rejected. |
| `selection` | `Bool` | `True` | Optional field mask. |
| `domain` | `String` | `"POINT"` | Attribute domain, for example `"POINT"`, `"EDGE"`, `"FACE"`, `"CORNER"`, or `"INSTANCE"`. |
| `type` | `String` or `None` | `None` | Optional Blender data type override, for example `"FLOAT"`, `"INT"`, `"BOOLEAN"`, `"VECTOR"`, or `"COLOR"`. |

Returns: statement only.

```python
height = position().z
store('height', height, domain='POINT', type='FLOAT')
```

### `set_position(position, selection=True)`

Sets point positions on the active Geometry stream.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `position` | `Vector` | required | New position field. |
| `selection` | `Bool` | `True` | Optional field mask. |

Returns: statement only.

```python
pos = position()
height = sin(pos.x * tau)
offset = vector(0, 0, height)
new_pos = pos + offset
set_position(new_pos)
```

## Inputs

Input built-ins create group input sockets. The `name` argument must be a compile-time string. Defaults must be compile-time values.

### `input_geometry(name)`

Creates a Geometry input socket.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `String` | required | Input socket name. |

Returns: `Geometry`.

```python
geo = input_geometry('Geometry')
vec_1 = vector(1.5, 1.5, 1.5)
geo_2 = transform(geo, scale=vec_1)
output('Geometry', geo_2)
```

### `input_float(name, default=0.0)`

Creates a Float input socket.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `String` | required | Input socket name. |
| `default` | compile-time `Float` | `0.0` | Socket default value. |

Returns: `Float`.

```python
radius = input_float('Radius', default=2.0)
geo_1 = cube(size=radius)
output('Geometry', geo_1)
```

### `input_int(name, default=0)`

Creates an Int input socket.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `String` | required | Input socket name. |
| `default` | compile-time `Int` | `0` | Socket default value. Numeric constants are converted to integer defaults. |

Returns: `Int`.

```python
count = input_int('Count', default=32)
geo = points(count)
output('Geometry', geo)
```

### `input_bool(name, default=False)`

Creates a Bool input socket.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `String` | required | Input socket name. |
| `default` | compile-time `Bool` | `False` | Socket default value. |

Returns: `Bool`.

```python
center = input_bool('Center', default=True)
vec_1 = vector(1, 0, 0)
vec_2 = vector(0, 0, 0)
pos = select(center, vec_1, vec_2)
output('Position', pos)
```

### `input_vector(name, default=vector(0, 0, 0))`

Creates a Vector input socket.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `name` | `String` | required | Input socket name. |
| `default` | compile-time `Vector` or 3-number tuple/list | `vector(0, 0, 0)` | Socket default value. |

Returns: `Vector`.

```python
vec_1 = vector(0, 0, 2)
offset = input_vector('Offset', default=vec_1)
geo_2 = cube()
geo_3 = transform(geo_2, translation=offset)
output('Geometry', geo_3)
```

## Field inputs

Field built-ins read Blender Geometry Nodes context fields. They take no arguments and do not accept keywords.

| Function | Returns | Description |
| --- | --- | --- |
| `position()` | `Vector` | Current element position field. |
| `normal()` | `Vector` | Current element normal field. |
| `index()` | `Int` | Current element index field. |
| `id()` | `Int` | Current element ID field. |

### `position()`

Returns the current element position field. Use it when a formula should be relative to the existing point or vertex position.

```python
pts = points(32)
base_position = position()
offset = vector(0, 0, 1)
new_position = base_position + offset
pts = set_position(pts, new_position)
output('Geometry', pts)
```

### `normal()`

Returns the current element normal field. Use it for displacement or orientation formulas that depend on surface direction.

```python
geo = cube(size=2.0)
surface_normal = normal()
displacement = surface_normal * 0.25
base_position = position()
new_position = base_position + displacement
geo = set_position(geo, new_position)
output('Geometry', geo)
```

### `index()`

Returns the current element index field. Use it for per-element spacing, alternating patterns, and procedural ordering.

```python
pts = points(32)
i = index()
x = i * 0.1
z = sin(i * 0.25)
pos = vector(x, 0, z)
pts = set_position(pts, pos)
output('Geometry', pts)
```

### `id()`

Returns the current element ID field. Use it as a stable per-element random seed when available.

```python
pts = points(64)
element_id = id()
offset = random_value(-0.5, 0.5, seed=12, id=element_id)
base_position = position()
delta = vector(0, 0, offset)
new_position = base_position + delta
pts = set_position(pts, new_position)
output('Geometry', pts)
```

## Scalar math

Scalar math built-ins compile to Blender Math, Clamp, Mix, Switch, Map Range, Noise Texture, or Random Value nodes.

### Unary functions

| Function | Signature | Returns | Description |
| --- | --- | --- | --- |
| `sin` | `sin(value)` | `Float` | Sine. |
| `cos` | `cos(value)` | `Float` | Cosine. |
| `tan` | `tan(value)` | `Float` | Tangent. |
| `asin` | `asin(value)` | `Float` | Arcsine. |
| `acos` | `acos(value)` | `Float` | Arccosine. |
| `atan` | `atan(value)` | `Float` | Arctangent. |
| `sqrt` | `sqrt(value)` | `Float` | Square root. |
| `abs` | `abs(value)` | `Float` | Absolute value. |
| `floor` | `floor(value)` | `Float` | Floor. |
| `ceil` | `ceil(value)` | `Float` | Ceiling. |
| `round` | `round(value)` | `Float` | Rounded value. |
| `fract` | `fract(value)` | `Float` | Fractional component. |
| `radians` | `radians(value)` | `Float` | Degrees to radians. |
| `degrees` | `degrees(value)` | `Float` | Radians to degrees. |
| `exp` | `exp(value)` | `Float` | Exponential. |
| `ln` | `ln(value)` | `Float` | Natural logarithm. |

```python
count = input_int('Count', default=64)
pts = points(count)
value_1 = index()
value_2 = sin(value_1 * 0.25)
wave = value_2 * 0.5
value_3 = index()
vec_4 = vector(value_3 * 0.1, 0, wave)
pts = set_position(pts, vec_4)
output('Geometry', pts)
```

### Binary functions

| Function | Signature | Returns | Description |
| --- | --- | --- | --- |
| `min` | `min(a, b)` | `Float` | Minimum. |
| `max` | `max(a, b)` | `Float` | Maximum. |
| `pow` | `pow(a, b)` | `Float` | Power. |
| `log` | `log(a, b)` | `Float` | Logarithm with explicit base. |
| `atan2` | `atan2(a, b)` | `Float` | Two-argument arctangent. |
| `mod` | `mod(a, b)` | `Float` | Modulo. |

These functions also accept keyword arguments using the listed parameter names.

```python
size = input_float('Size', default=2.0)
clamped = max(size, 0.1)
value_1 = pow(clamped, 2.0)
geo_2 = cube(size=value_1)
output('Geometry', geo_2)
```

### `clamp(value, min, max)`

Constrains a value between lower and upper bounds.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | `Float` | Input value. |
| `min` | `Float` | Lower bound. |
| `max` | `Float` | Upper bound. |

Returns: `Float`.

```python
height = input_float('Height', default=3.0)
value_1 = clamp(height, 0.0, 2.0)
output('Height', value_1)
```

### `mix(a, b, factor)`

Interpolates between two compatible values. `factor=0` selects `a`; `factor=1` selects `b`.

| Parameter | Type | Description |
| --- | --- | --- |
| `a` | `Float` or `Vector` | Start value. |
| `b` | same as `a` | End value. |
| `factor` | `Float` | Interpolation factor. |

Returns: same type as `a`.

```python
t = input_float('Factor', default=0.5)
vec_1 = vector(1, 1, 1)
vec_2 = vector(2, 2, 0.5)
scale = mix(vec_1, vec_2, t)
geo_3 = cube()
geo_4 = transform(geo_3, scale=scale)
output('Geometry', geo_4)
```

### `select(cond, true, false)`

Chooses between two values with a boolean condition. The second argument is the value used when `cond` is true; the third argument is the value used when `cond` is false.

| Parameter | Type | Description |
| --- | --- | --- |
| `cond` | `Bool` | Selection condition. |
| `true` | `Float`, `Int`, `Bool`, `Vector`, or `Geometry` | Value for true condition. |
| `false` | same as `true` | Value for false condition. |

Returns: same type as `true` and `false`.

```python
large = input_bool('Large', default=False)
size = select(large, 3.0, 1.0)
geo_1 = cube(size=size)
output('Geometry', geo_1)
```

### `map_range(value, from_min, from_max, to_min, to_max)`

Maps a value from one numeric range into another.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | `Float` | Input value. |
| `from_min` | `Float` | Source range lower bound. |
| `from_max` | `Float` | Source range upper bound. |
| `to_min` | `Float` | Target range lower bound. |
| `to_max` | `Float` | Target range upper bound. |

Returns: `Float`.

```python
count = input_int('Count', default=32)
pts = points(count)
value_1 = index()
z = map_range(value_1, 0.0, count - 1.0, 0.0, 3.0)
value_2 = index()
vec_3 = vector(value_2 * 0.1, 0, z)
pts = set_position(pts, vec_3)
output('Geometry', pts)
```

### `noise(vector=position(), scale=..., detail=..., roughness=..., lacunarity=..., distortion=..., normalize=...)`

Creates a 3D Noise Texture node and returns its Factor output.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `vector` | `Vector` | `position()` | Noise coordinate. At most one positional argument is accepted. |
| `scale` | numeric | Blender default | Noise scale input. |
| `detail` | numeric | Blender default | Noise detail input. |
| `roughness` | numeric | Blender default | Noise roughness input. |
| `lacunarity` | numeric | Blender default | Noise lacunarity input. |
| `distortion` | numeric | Blender default | Noise distortion input. |
| `normalize` | compile-time `Bool` | Blender default | Sets the Blender node `normalize` property when provided. |

Returns: `Float`.

```python
pts = points(128)
value_1 = index()
coord = vector(value_1 * 0.08, 0, 0)
z = noise(coord, scale=6.0, detail=8.0, roughness=0.55)
value_2 = index()
vec_3 = vector(value_2 * 0.05, 0, z)
pts = set_position(pts, vec_3)
output('Geometry', pts)
```

### `random_value()`
### `random_value(min, max, seed=..., id=...)`

Creates a Random Value node. With no positional arguments it returns a float in the default `0..1` range. With `min` and `max`, both bounds must be numeric or both must be vectors.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `min` | `Float` or `Vector` | `0.0` | Lower bound. Required when `max` is provided. |
| `max` | same as `min` | `1.0` | Upper bound. Required when `min` is provided. |
| `seed` | `Int` or compile-time numeric constant | Blender default | Random seed. |
| `id` | `Int` | Blender default | Per-element random ID. |

Returns: `Float` for numeric bounds, `Vector` for vector bounds.

```python
pts = points(100)
vec_1 = vector(-2, -2, 0)
vec_2 = vector(2, 2, 1)
value_3 = index()
pos = random_value(vec_1, vec_2, seed=12, id=value_3)
pts = set_position(pts, pos)
output('Geometry', pts)
```

## Vector construction and vector math

### `vector(x, y, z)`

Creates a `Vector` from three numeric components. Positional arguments and `x=`, `y=`, `z=` keywords are supported.

| Parameter | Type | Description |
| --- | --- | --- |
| `x` | `Float` | X component. |
| `y` | `Float` | Y component. |
| `z` | `Float` | Z component. |

Returns: `Vector`.

```python
height = input_float('Height', default=2.0)
offset = vector(x=0.0, y=0.0, z=height)
geo_1 = cube()
geo_2 = transform(geo_1, translation=offset)
output('Geometry', geo_2)
```

Vector components can be read with `.x`, `.y`, and `.z`.

```python
vec_1 = vector(1, 2, 3)
v = input_vector('Vector', default=vec_1)
vec_2 = vector(v.x, v.y, 0)
value_3 = length(vec_2)
output('Length XY', value_3)
```

### Vector functions

| Function | Signature | Returns | Description |
| --- | --- | --- | --- |
| `length` | `length(v)` | `Float` | Vector length. |
| `distance` | `distance(a, b)` | `Float` | Distance between two vectors. |
| `dot` | `dot(a, b)` | `Float` | Dot product. |
| `normalize` | `normalize(v)` | `Vector` | Normalized vector. |
| `cross` | `cross(a, b)` | `Vector` | Cross product. |
| `reflect` | `reflect(a, b)` | `Vector` | Reflection vector. |
| `project` | `project(a, b)` | `Vector` | Projection vector. |

```python
vec_1 = vector(1, 1, 0)
n = normalize(vec_1)
vec_2 = vector(0, 0, 1)
side = cross(n, vec_2)
pos = n + side * 0.5
output('Position', pos)
```

## Geometry

### `points(count)`

Creates point geometry with `count` points.

| Parameter | Type | Description |
| --- | --- | --- |
| `count` | `Int` | Number of points. Compile-time constants and runtime Int values are supported. |

Returns: `Geometry`.

```python
count = input_int('Count', default=32)
pts = points(count)
value_1 = index()
vec_2 = vector(value_1 * 0.1, 0, 0)
pts = set_position(pts, vec_2)
output('Geometry', pts)
```

### `grid(width, height)`

Creates a planar mesh grid on the XY plane and stores the generated UV field for `grid_uv()` in the current script scope.

| Parameter | Type | Description |
| --- | --- | --- |
| `width` | `Int` | Number of vertices in X. |
| `height` | `Int` | Number of vertices in Y. |

Returns: `Geometry`.

```python
geo = grid(16, 16)
uv = grid_uv()
vec_1 = vector(uv.x, uv.y, 0)
value_2 = noise(vec_1)
vec_3 = vector(uv.x * 2.0, uv.y * 2.0, value_2)
geo = set_position(geo, vec_3)
output('Geometry', geo)
```

### `grid_uv()`

Returns the UV coordinates produced by the most recent `grid(width, height)` call in the current script scope. `grid_uv()` requires a preceding `grid(...)` call.

Returns: `Vector` with `.x` and `.y` in the `0..1` range.

```python
geo = grid(8, 8)
uv = grid_uv()
value_1 = sin(uv.x * tau)
value_2 = cos(uv.y * tau)
height = value_1 * value_2
value_3 = position()
vec_4 = vector(0, 0, height)
geo = set_position(geo, value_3 + vec_4)
output('Geometry', geo)
```

### `set_position(geometry, position, selection=True)`

Sets point positions on geometry.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `geometry` | `Geometry` | required | Input geometry. |
| `position` | `Vector` | required | New position field. |
| `selection` | `Bool` | `True` | Optional field mask. |

Returns: `Geometry`.

```python
pts = points(32)
value_1 = index()
value_2 = mod(value_1, 2)
mask = value_2 == 0
value_3 = index()
value_4 = index()
value_5 = sin(value_4 * 0.4)
pos = vector(value_3 * 0.1, 0, value_5)
pts = set_position(pts, pos, selection=mask)
output('Geometry', pts)
```

### `store_named_attribute(geometry, name, value, selection=True, domain="POINT", type=None)`

Stores a named attribute on geometry.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `geometry` | `Geometry` | required | Input geometry. |
| `name` | `String` | required | Attribute name. |
| `value` | `Float`, `Int`, `Bool`, or `Vector` | required | Attribute value field. Arrays are rejected. |
| `selection` | `Bool` | `True` | Optional field mask. |
| `domain` | `String` | `"POINT"` | Attribute domain, for example `"POINT"`, `"EDGE"`, `"FACE"`, `"CORNER"`, or `"INSTANCE"`. |
| `type` | `String` or `None` | `None` | Optional Blender data type override, for example `"FLOAT"`, `"INT"`, `"BOOLEAN"`, `"VECTOR"`, or `"COLOR"`. |

Returns: `Geometry`.

```python
pts = points(64)
value_1 = index()
vec_2 = vector(value_1 * 0.1, 0, 0)
height = noise(vec_2, scale=4.0)
value_3 = index()
vec_4 = vector(value_3 * 0.05, 0, height)
pts = set_position(pts, vec_4)
pts = store_named_attribute(pts, 'height', height, domain='POINT', type='FLOAT')
output('Geometry', pts)
```

### `set_material(geometry, material_name)`

Assigns a material by name. The material is created when it does not exist.

| Parameter | Type | Description |
| --- | --- | --- |
| `geometry` | `Geometry` | Input geometry. |
| `material_name` | `String` | Compile-time Blender material name. |

Returns: `Geometry`.

```python
geo = cube(size=2.0)
geo = set_material(geo, 'NodeForge Material')
output('Geometry', geo)
```


### `empty_geometry()`

Creates a valid Geometry value with zero elements. Use it as the neutral starting value for generated geometry collections, optional branches, and repeat-loop accumulators.

Returns: `Geometry`.

```python
geo = empty_geometry()
count = input_int('Count', default=3)
for i in repeat_range(count):
    part = transform(cube(0.25), translation=vector(i, 0, 0))
    geo = join(geo, part)
output('Geometry', geo)
```

### `point(position)`

Creates one point and sets its position. `position` can be a literal vector expression or a runtime `Vector` value.

| Parameter | Type | Description |
| --- | --- | --- |
| `position` | `Vector` | Point position. |

Returns: `Geometry`.

```python
pos = input_vector('Position', default=(0, 0, 1))
geo = point(pos)
output('Geometry', geo)
```

### `line(start, end)`

Creates a curve line segment between two endpoints. `start` and `end` can be literal vector expressions or runtime `Vector` values.

| Parameter | Type | Description |
| --- | --- | --- |
| `start` | `Vector` | Start endpoint. |
| `end` | `Vector` | End endpoint. |

Returns: `Geometry`.

```python
start = input_vector('Start', default=(0, 0, 0))
end = input_vector('End', default=(1, 0, 0))
geo = line(start, end)
output('Geometry', geo)
```

### `cube(size=1.0)`

Creates a cube mesh.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `size` | `Float`, `Int`, or `Vector` | `1.0` | Cube side length or per-axis size. Positional and `size=` forms are supported. |

Returns: `Geometry`.

```python
size = input_float('Size', default=1.0)
geo_1 = cube(size=size)
output('Geometry', geo_1)
```

### `join(geometry, ...)`
### `join([geometry_a, geometry_b, ...])`

Joins multiple geometry values. Arguments can be separate geometry values, arrays of geometry values, or one literal list/tuple of geometry values. A supplied empty array such as `join([])` returns `empty_geometry()`. A call with no arguments is still invalid.

| Parameter | Type | Description |
| --- | --- | --- |
| `geometry` | `Geometry` values or an array of `Geometry` values | Geometry values to join. A supplied array may be empty. |

Returns: `Geometry`.

```python
geo_1 = cube(size=0.4)
vec_2 = vector(-1, 0, 0)
geo_3 = transform(geo_1, translation=vec_2)
geo_4 = cube(size=0.4)
vec_5 = vector(1, 0, 0)
geo_6 = transform(geo_4, translation=vec_5)
geo_7 = points(8)
parts = [geo_3, geo_6, geo_7]
geo_8 = join(parts)
output('Geometry', geo_8)

empty_parts = []
empty = join(empty_parts)
```

### `transform(geometry, translation=None, scale=None, rotation=None)`

Transforms geometry. `translation`, `scale`, and `rotation` can be supplied by keyword. The first two transform options can also be supplied as positional arguments after `geometry`.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `geometry` | `Geometry` | required | Input geometry. |
| `translation` | `Vector` | no transform | Translation vector. |
| `scale` | `Float`, `Int`, or `Vector` | no transform | Scale value. Use `vector(x, y, z)` for non-uniform scale. |
| `rotation` | `Vector` | no transform | Euler rotation in radians. |

Returns: `Geometry`.

```python
geo = cube(size=1.0)
vec_1 = vector(0, 0, 1)
vec_2 = vector(2, 1, 0.5)
geo = transform(geo, translation=vec_1, scale=vec_2)
output('Geometry', geo)
```

### `polyline(points)`

Creates curve geometry from a compile-time list of vector points.

| Parameter | Type | Description |
| --- | --- | --- |
| `points` | compile-time list of vectors | Ordered points for the polyline. |

Returns: `Geometry`.

```python
vec_1 = vector(-1, 0, 0)
vec_2 = vector(0, 1, 0)
vec_3 = vector(1, 0, 0)
shape = polyline([vec_1, vec_2, vec_3])
output('Geometry', shape)
```

## Instancing

### `instance_on_points(instance, points, scale=None, rotation=None, realize=True)`

Instances one geometry value on point geometry.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `instance` | `Geometry` | required | Geometry to instance. |
| `points` | `Geometry` | required | Point geometry receiving the instances. |
| `scale` | value accepted by the underlying instance helper | Blender default | Optional instance scale. |
| `rotation` | value accepted by the underlying instance helper | Blender default | Optional instance rotation. |
| `realize` | compile-time `Bool` | `True` | When true, realizes instances before returning. |

Returns: `Geometry`.

```python
pts = points(12)
value_1 = index()
vec_2 = vector(value_1 * 0.3, 0, 0)
pts = set_position(pts, vec_2)
geo_3 = cube(size=0.15)
vec_4 = vector(1, 1, 1)
geo = instance_on_points(geo_3, pts, scale=vec_4, realize=True)
output('Geometry', geo)
```

### `realize_instances(geometry)`

Realizes instances on geometry.

| Parameter | Type | Description |
| --- | --- | --- |
| `geometry` | `Geometry` | Geometry containing instances. |

Returns: `Geometry`.

```python
pts = points(6)
geo_1 = cube(size=0.2)
instanced = instance_on_points(geo_1, pts, realize=False)
geo_2 = realize_instances(instanced)
output('Geometry', geo_2)
```

## Raw Blender node construction

### `node(bl_idname, props={}, inputs={}, output=..., typ=...)`
### `node(bl_idname, props={}, inputs={}, outputs={...})`

Creates a Blender node directly. Use this for Blender node types that are not exposed through a dedicated NodeForge built-in.

`bl_idname`, `props` keys and values, input socket names, output socket names, and type tokens are compile-time declarations. Runtime values are allowed inside `inputs={...}` and are linked to the corresponding Blender input socket.

Single-output mode requires both `output=` and `typ=`. Multi-output mode uses `outputs={"Socket": TypeToken, ...}` and returns a result object whose outputs can be accessed with attribute syntax or item syntax.

Supported type tokens: `Float`, `Int`, `Bool`, `Vector`, `Geometry`.

| Parameter | Type | Default | Description |
| --- | --- | --- | --- |
| `bl_idname` | `String` | required | Blender node type identifier, for example `"ShaderNodeMath"`. |
| `props` | literal dict | `{}` | Blender node properties to assign before linking inputs. Custom properties are not supported. |
| `inputs` | literal dict | `{}` | Maps enabled input socket names to literal defaults, runtime values, or a literal list for multi-input fanout. |
| `output` | `String` | required in single-output mode | Enabled output socket name to return. |
| `typ` | type token | required in single-output mode | NodeForge type of `output`. |
| `outputs` | literal dict of `String: type token` | required in multi-output mode | Enabled output sockets to expose. |

Returns: a `Value` in single-output mode; a multi-output result in `outputs=` mode.

```python
value = input_float('Value', default=0.25)
rounded = node('ShaderNodeMath', props={'operation': 'ROUND'}, inputs={'Value': value}, output='Value', typ=Float)
output('Rounded', rounded)
```

```python
value_1 = position()
separate = node('ShaderNodeSeparateXYZ', inputs={'Vector': value_1}, outputs={'X': Float, 'Y': Float, 'Z': Float})
height = separate.Z
output('Height', height)
```

## Runtime loops

Runtime loops compile to Blender Repeat Zones. Use `repeat_range(...)` when the iteration count or loop state must be represented in the generated Geometry Nodes graph. Use compile-time `range(...)` for fixed authoring loops that should be unrolled during compilation.

### `for i in repeat_range(steps): ...`

Updates existing Geometry, Vector, Float, Int, and Bool variables inside one Repeat Zone. The body supports assignments and nested `if` blocks with assignments. It must update at least one existing variable. Assigned names that did not exist before the loop are iteration-local temporaries rather than Repeat Zone state items.

State item order follows first assignment in the loop body, including nested branches. Conditional branches preserve omitted state values and merge changed state through Switch nodes. The loop index name cannot also be state, and state names cannot collide with Repeat Zone system socket names such as `Iterations` or `Iteration`.

| Part | Type | Description |
| --- | --- | --- |
| `steps` | `Int` | Repeat count. May be an integer literal, compile-time integer, or runtime `Int` value. |
| state variables | `Geometry`, `Vector`, `Float`, `Int`, or `Bool` | Existing variables assigned inside the loop. |

```python
n = input_int('N', default=8)
geo = cube(0.1)
pos = vector(0, 0, 0)
angle = input_float('Angle', default=45)
for i in repeat_range(n):
    next_pos = pos + vector(1, 0, 0)
    part = transform(cube(0.05), translation=next_pos)
    geo = join(geo, part)
    pos = next_pos
    angle = -angle
output('Geometry', geo)
output('Position', pos)
output('Angle', angle)
```

`range(...)` does not create Repeat Zones. A non-constant `range(...)` count raises `CompileError`; use `repeat_range(...)` for runtime repetition.

## Arrays and unrolled loops

Script arrays are authoring-time collections of compiled values. They are useful for building a fixed list of geometry parts and then passing the list to `join(...)`.

```python
parts = []
vec_1 = vector(-1, 0, 0)
vec_2 = vector(0, 0, 0)
vec_3 = vector(1, 0, 0)
for offset in [vec_1, vec_2, vec_3]:
    geo_4 = cube(size=0.25)
    geo_5 = transform(geo_4, translation=offset)
    parts.append(geo_5)
geo_6 = join(parts)
output('Geometry', geo_6)
```

Arrays cannot be output directly and cannot be used as runtime values.
