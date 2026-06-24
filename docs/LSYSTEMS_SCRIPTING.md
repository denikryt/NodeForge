# Writing L-system scripts

This guide shows how to write L-system scripts in NodeForge.

Use `LSYSTEMS.md` for constructor reference and implementation notes.

## Minimal example

```python
angle = input_float("Angle", default=25.0)
step = input_float("Step", default=0.1)

geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(2),
    ls_angle(angle),
    ls_step(step),
)

output("Geometry", geo)
```

What happens:

1. Start with the axiom string: `F`.
2. Apply rewrite rules `ls_iterations(...)` times.
3. Read the final string as turtle commands.
4. Generate geometry from drawn `F` segments.

## Basic parts

```python
ls_axiom("F")
```

The starting string. There is exactly one axiom per `ls_system(...)`.

If the axiom contains `F`, the system starts with drawable geometry. For example, `ls_axiom("F")` with zero iterations already draws one segment.

If the axiom contains only grammar symbols such as `X`, `A`, or `B`, it does not draw anything by itself. Those symbols must be rewritten into `F` or other turtle commands before geometry appears.

```python
ls_rule("F", "F[+F]F[-F]F")
```

A rewrite rule. Every `F` becomes `F[+F]F[-F]F` during each iteration.

```python
ls_iterations(2)
```

How many rewrite passes to run. This is compile-time.

```python
ls_angle(angle)
```

Turn angle in degrees. `+` and `-` use this value.

```python
ls_step(step)
```

Forward distance for `F` and `f`.

## Symbols

| Symbol | Does |
| --- | --- |
| `F` | Move forward and draw a segment. |
| `f` | Move forward without drawing. |
| `+` | Turn left by `ls_angle(...)`. |
| `-` | Turn right by `ls_angle(...)`. |
| `[` | Save current position and direction. Start a branch. |
| `]` | Restore saved position and direction. End a branch. |

Other ASCII letters, digits, and `_` can be grammar symbols. They help control rewriting but do not draw anything if they remain in the final string.

Example:

```python
geo = ls_system(
    ls_axiom("X"),
    ls_rule("X", "F+X"),
    ls_iterations(3),
    ls_angle(60),
    ls_step(0.1),
)
```

`X` keeps the rewrite going. The drawn geometry comes from `F`.

## How branches work

This rule creates two side branches:

```python
ls_rule("F", "F[+F]F[-F]F")
```

Read it as:

```text
F     draw forward
[+F]  save state, turn left, draw branch, restore state
F     continue from the original trunk
[-F]  save state, turn right, draw branch, restore state
F     continue from the original trunk
```

`[` and `]` are important because they stop a side branch from moving the main trunk position.

## Runtime controls

These values can be runtime inputs:

```python
angle = input_float("Angle", default=24.0)
step = input_float("Step", default=0.06)
```

Use them in the system:

```python
ls_angle(angle)
ls_step(step)
```

Changing `angle` changes branch direction. Changing `step` changes segment length.

The following are not runtime controls in the current implementation:

```text
axiom
rules
iteration count
branch topology
```

Changing those means the L-system must be rebuilt.

## Transforming and joining L-systems

An L-system result is normal geometry.

```python
plant_a = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(2),
    ls_angle(25),
    ls_step(0.1),
)

plant_b = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(4),
    ls_angle(25),
    ls_step(0.07),
)

plant_b = transform(plant_b, translation=vector(1, 0, 0))
geo = join(plant_a, plant_b)

output("Geometry", geo)
```

Use `translation`, not `position`, in `transform(...)`.

## Common patterns

### Koch-style curve

```python
geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F+F--F+F"),
    ls_iterations(3),
    ls_angle(60),
    ls_step(0.1),
)
output("Geometry", geo)
```

### Branching plant

```python
angle = input_float("Angle", default=25.0)
step = input_float("Step", default=0.12)

geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(3),
    ls_angle(angle),
    ls_step(step),
)
output("Geometry", geo)
```


### Classic fractal plant

This is a common L-system plant pattern. `X` controls growth and does not draw; `F` draws the visible segments.

```python
angle = input_float("Angle", default=25.0)
step = input_float("Step", default=0.04)

geo = ls_system(
    ls_axiom("X"),
    ls_rule("X", "F+[[X]-X]-F[-FX]+X"),
    ls_rule("F", "FF"),
    ls_iterations(5),
    ls_angle(angle),
    ls_step(step),
)

geo = transform(geo, rotation=vector(0, 0, radians(90)))
output("Geometry", geo)
```

### Grammar symbol for growth

```python
geo = ls_system(
    ls_axiom("X"),
    ls_rule("X", "F[+X][-X]FX"),
    ls_rule("F", "FF"),
    ls_iterations(4),
    ls_angle(24),
    ls_step(0.06),
)
output("Geometry", geo)
```

`X` does not draw. It controls where future growth happens.

## Errors to avoid

Do not use unsupported characters in rules:

```python
ls_rule("F", "F → F+F")  # bad: Unicode arrow
```

Do not leave branches unmatched:

```python
ls_rule("F", "F[+F")  # bad: missing ]
```

Do not expect `f` to draw:

```python
ls_rule("F", "FfF")  # middle move creates a gap
```

Do not use `position=` in `transform(...)`:

```python
geo = transform(geo, position=vector(1, 0, 0))  # bad
geo = transform(geo, translation=vector(1, 0, 0))  # good
```
