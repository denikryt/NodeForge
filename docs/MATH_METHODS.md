# Math Methods

These callables are provided by the included `nodeforge.math` package.

The Math library adds scalar and procedural callables to the DSL. Import its package namespace and use qualified calls.

```python
from packages import math

def example(angle: Float = 0.0):
    value = math.sin(angle)
    return value
```

## Scalar Math Callables

These names are members of the imported `math` package namespace. They create the corresponding Geometry Nodes operations, including when called with numeric literals. The scalar callables accept positional arguments or the parameter names shown below as keywords.

### Unary Callables

| Function | Description |
| --- | --- |
| `sin(value)` | Sine. |
| `cos(value)` | Cosine. |
| `tan(value)` | Tangent. |
| `asin(value)` | Arcsine. |
| `acos(value)` | Arccosine. |
| `atan(value)` | Arctangent. |
| `sqrt(value)` | Square root. |
| `abs(value)` | Absolute value. |
| `floor(value)` | Floor. |
| `ceil(value)` | Ceiling. |
| `round(value)` | Rounded value. |
| `fract(value)` | Fractional component. |
| `radians(value)` | Converts degrees to radians. |
| `degrees(value)` | Converts radians to degrees. |
| `exp(value)` | Exponential. |
| `ln(value)` | Natural logarithm. |

### Binary Callables

| Function | Description |
| --- | --- |
| `min(a, b)` | Minimum value. |
| `max(a, b)` | Maximum value. |
| `pow(a, b)` | Raises `a` to power `b`. |
| `log(a, b)` | Logarithm of `a` with base `b`. |
| `atan2(a, b)` | Two-argument arctangent. |
| `mod(a, b)` | Modulo. |

### Range and Selection Callables

| Function | Description |
| --- | --- |
| `clamp(value, min, max)` | Clamps a value to a range. |
| `mix(a, b, factor)` | Interpolates between two values. |
| `select(cond, true, false)` | Selects one value from a runtime boolean condition. |
| `map_range(value, from_min, from_max, to_min, to_max)` | Maps a value between ranges. |

```python
from packages import math

def example(value: Float = 0.25):
    angle = math.radians(value * 360.0)
    wave = math.sin(angle)
    result = math.clamp(wave, -0.5, 0.5)
    return result
```

## Procedural Value Callables

### `noise()`
### `noise(vector, *, scale=..., detail=..., roughness=..., lacunarity=..., distortion=..., normalize=...)`

Creates a 3D Noise Texture field and returns its Factor output. Pass `vector` as the optional first positional argument. When it is omitted, `position()` is used. The remaining options are keyword arguments; omitted options keep the corresponding Blender Noise Texture defaults.

| Parameter | Type | Description |
| --- | --- | --- |
| `vector` | `Vector` | Optional coordinate field passed positionally. Defaults to `position()`. |
| `scale` | numeric | Noise scale. |
| `detail` | numeric | Noise detail. |
| `roughness` | numeric | Noise roughness. |
| `lacunarity` | numeric | Noise lacunarity. |
| `distortion` | numeric | Noise distortion. |
| `normalize` | compile-time `Bool` | Sets the node normalization option. |

Returns: `Float`.

### `random_value()`
### `random_value(min, max, seed=..., id=...)`

Creates a Random Value field. With no bounds it returns a `Float` in the default range. Numeric bounds return `Float`; vector bounds return `Vector`.

| Parameter | Type | Description |
| --- | --- | --- |
| `min` | `Float` or `Vector` | Lower bound. |
| `max` | same as `min` | Upper bound. |
| `seed` | `Int` | Random seed. |
| `id` | `Int` | Per-element identifier. |

```python
from packages import math

def example():
    pts = points(100)
    pos = math.random_value(vector(-2, -2, 0), vector(2, 2, 1), seed=12, id=index())
    pts = set_position(pts, pos)
    return pts
```
