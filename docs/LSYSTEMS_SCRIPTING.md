# L-System Guide

Use this page to learn the `nodeforge.lsystem` library by building scripts. Exact constructor contracts, turtle commands, grammar restrictions, and limits are collected in the [L-System Reference](LSYSTEMS.md).

NodeForge includes the L-System library. Import it with an alias:

```python
from packages import lsystem as ls
```

Package qualification is useful here because Core DSL also has a `points(...)` callable.

## Build a first L-system

An L-system has two stages: rewrite an initial string for some number of generations, then interpret the final modules with a turtle.

```python
from packages import lsystem as ls

def example(angle: Float = 25.0, step: Float = 0.1):
    geo = ls.system(
        ls.axiom("F"),
        ls.rule("F", "F[+F]F[-F]F"),
        ls.iterations(2),
        ls.angle(angle),
        ls.step(step),
    )
    return geo
```

Starting from `F`, the rule replaces every `F` with `F[+F]F[-F]F`. Rewriting is parallel: a replacement produced in one generation is not rewritten again until the next generation. After two generations, the turtle interprets the resulting `F`, `+`, `-`, `[` and `]` modules as geometry.

`ls.system(...)` returns ordinary `Geometry`, so the result can be transformed, joined, assigned materials, or passed to other NodeForge functions.

## Separate grammar symbols from drawing commands

Letters such as `A`, `B`, or `X` are useful as growth symbols. They control rewriting without drawing unless they eventually produce turtle commands or declared markers.

```python
def example():
    geo = ls.system(
        ls.axiom("X"),
        ls.rule("X", "F+X"),
        ls.iterations(5),
        ls.angle(60),
        ls.step(0.15),
    )
    return geo
```

Symbols without a matching rule survive into the next generation. If an ordinary grammar symbol reaches the final stream, the turtle ignores it.

A rule can delete its predecessor by using an empty replacement:

```python
def example():
    geo = ls.system(
        ls.axiom("FAF"),
        ls.rule("A", ""),
        ls.iterations(1),
        ls.angle(90),
        ls.step(0.5),
    )
    return geo
```

For the accepted grammar alphabet and validation rules, see [Rewrite and grammar rules](LSYSTEMS.md#rewrite-and-grammar-rules).

## Create branches

`[` saves the turtle state and `]` restores it. This lets a side branch change position and orientation without changing the continuation of its parent.

```python
def example():
    geo = ls.system(
        ls.axiom("F[+F]F[-F]F"),
        ls.iterations(0),
        ls.angle(35),
        ls.step(0.4),
    )
    return geo
```

Read the stream as:

```text
F     draw the trunk forward
[+F]  save state, turn, draw a branch, restore state
F     continue the trunk
[-F]  create a branch on the other side
F     continue the trunk
```

The saved state includes the full 3D frame, not only the position.

## Work in 3D

The turtle starts facing `+X`. Yaw uses `+` and `-`, pitch uses `^` and `&`, and roll uses `/` and `\`. Because these rotations update a local coordinate frame, their order changes the result.

```python
def example():
    first = ls.system(
        ls.axiom("+(90)^(90)F"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(1),
    )

    second = ls.system(
        ls.axiom("^(90)+(90)F"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(1),
    )
    second = transform(second, translation=vector(2, 0, 0))
    return join(first, second)
```

The two curves use the same rotations in a different order and therefore end in different orientations. The complete coordinate-frame definition and command table are in [Turtle coordinate frame](LSYSTEMS.md#turtle-coordinate-frame) and [Turtle commands](LSYSTEMS.md#turtle-commands).

## Parameterize individual modules

`ls.angle(...)` and `ls.step(...)` provide defaults, but a command can carry its own numeric argument. Bind runtime values into the grammar with `ls.param(...)`.

```python
def example(
    long_step: Float["Long Step"] = 1.5,
    short_step: Float["Short Step"] = 0.5,
    turn: Float = 70.0,
):
    geo = ls.system(
        ls.axiom("F(long_step)+(turn)F(short_step)-(turn)F(long_step)"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(1),
        ls.param("long_step", long_step),
        ls.param("short_step", short_step),
        ls.param("turn", turn),
    )
    return geo
```

Arguments inside the L-system string are references, not NodeForge expressions. Compute a more complex value in the surrounding script first, then bind it:

```python
def example(base: Float["Base Length"] = 0.4):
    segment_length = base * 2.0

    geo = ls.system(
        ls.axiom("F(length)+(90)F(length)"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(1),
        ls.param("length", segment_length),
    )
    return geo
```

## Make growth runtime-adjustable

A runtime `Int` passed to `ls.iterations(...)` lets the node-group interface control how many rewrite generations are evaluated.

```python
def example(iterations: Int = 3):
    geo = ls.system(
        ls.axiom("A"),
        ls.rule("A", "F[+A][-A]"),
        ls.iterations(iterations),
        ls.angle(30),
        ls.step(0.2),
    )
    return geo
```

Changing **Iterations** changes topology without recompiling the NodeForge script. Keep branches self-contained when using runtime rewriting; the exact structural restrictions are listed under [`ls.iterations(...)`](LSYSTEMS.md#constructors).

Runtime values are also useful for angle, step, and named parameters. See [Runtime values](LSYSTEMS.md#runtime-values) for the full matrix.

## Emit marker points

Markers let the grammar place points for leaves, buds, joints, or other downstream geometry without drawing a segment.

Declare the marker, put its name in the grammar, then extract its points with `ls.points(...)`:

```python
def example():
    plant = ls.system(
        ls.axiom("F[+FLeaf][-FLeaf]FLeaf"),
        ls.iterations(0),
        ls.angle(35),
        ls.step(0.5),
        ls.marker("Leaf"),
    )

    leaf_points = ls.points(plant, marker="Leaf")
    leaves = instance_on_points(cube(0.12), leaf_points)
    return join(plant, leaves)
```

A marker may carry named numeric attributes:

```python
def example(size: Float["Leaf Size"] = 0.25):
    plant = ls.system(
        ls.axiom("FLeaf(size)+(45)FLeaf(size)"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(0.7),
        ls.param("size", size),
        ls.marker("Leaf", "size"),
    )

    leaf_points = ls.points(plant, marker="Leaf")
    return leaf_points
```

Marker points also carry the turtle tangent and up vectors, which can be used to orient instanced geometry. Attribute names and marker contracts are documented under [`ls.marker(...)`](LSYSTEMS.md#constructors).

## Assemble compile-time grammar fragments

Use f-strings when the grammar itself should be composed from compile-time string pieces:

```python
def example():
    left = "[-F]"
    right = "[+F]"
    rule = f"F{left}F{right}F"

    return ls.system(
        ls.axiom("F"),
        ls.rule("F", rule),
        ls.iterations(3),
        ls.angle(25),
        ls.step(0.1),
    )
```

F-string interpolation changes the grammar at compile time. Runtime numeric controls should stay in `ls.param(...)` instead.

## Complete plant example

This combines runtime growth, pitch, roll, step length, and leaf markers:

```python
def example(
    growth_iterations: Int["Growth Iterations"] = 5,
    branch_angle: Float["Branch Angle"] = 35.0,
    node_rotation: Float["Node Rotation"] = 137.507764,
    step: Float = 0.2,
):
    plant = ls.system(
        ls.axiom("A"),
        ls.rule("A", "F[&(branch_angle)B]/(node_rotation)A"),
        ls.rule("B", "FLeaf"),
        ls.iterations(growth_iterations),
        ls.angle(25),
        ls.step(step),
        ls.param("branch_angle", branch_angle),
        ls.param("node_rotation", node_rotation),
        ls.marker("Leaf"),
    )

    leaf_points = ls.points(plant, marker="Leaf")
    leaves = instance_on_points(cube(0.08), leaf_points)
    return join(plant, leaves)
```

The roll distributes successive branches around the trunk while pitch moves each branch away from the current heading. Because those controls are runtime values, the shape can be adjusted from the node-group interface.

## More patterns

### Koch curve

```python
def example():
    geo = ls.system(
        ls.axiom("F"),
        ls.rule("F", "F+F--F+F"),
        ls.iterations(4),
        ls.angle(60),
        ls.step(0.08),
    )
    return geo
```

### Classic fractal plant

```python
def example(angle: Float = 25.0, step: Float = 0.04):
    geo = ls.system(
        ls.axiom("X"),
        ls.rule("X", "F+[[X]-X]-F[-FX]+X"),
        ls.rule("F", "FF"),
        ls.iterations(5),
        ls.angle(angle),
        ls.step(step),
    )
    geo = transform(geo, rotation=vector(0, 0, pi / 2))
    return geo
```

### 3D branch whorl

```python
def example(pitch: Float = 35.0, roll: Float = 120.0):
    geo = ls.system(
        ls.axiom("F[&(pitch)F]/(roll)[&(pitch)F]/(roll)[&(pitch)F]"),
        ls.iterations(0),
        ls.angle(25),
        ls.step(0.8),
        ls.param("pitch", pitch),
        ls.param("roll", roll),
    )
    return geo
```

## Keep iteration counts practical

Rewrite systems can grow exponentially. Prefer bounded user inputs and test the largest intended iteration count before publishing a script. The hard compile-time guards and the distinction between compile-time and runtime expansion are listed under [Limits](LSYSTEMS.md#limits).
