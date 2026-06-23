# L-systems

NodeForge supports L-systems as an embedded system inside the normal DSL source path. An L-system expression returns a normal `Geometry` value, so the result can be transformed, joined, assigned materials, passed to other geometry operations, and connected to `output(...)`.

L-system constructors use the `ls_` prefix and are resolved by `systems/registry.py`. They are reserved global DSL names and are kept separate from ordinary built-ins in `builtins/registry.py`.

## Minimal example

```python
angle_value = input_float("Angle", default=60.0)
step_value = input_float("Step", default=0.1)

geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F+F--F+F"),
    ls_iterations(4),
    ls_angle(angle_value),
    ls_step(step_value),
)

geo = transform(geo, translation=vector(0, 0, 1))
output("Geometry", geo)
```

`Angle` and `Step` are runtime inputs in this example. Changing them updates the evaluated geometry. Changing the axiom, rules, iteration count, or branch topology changes the generated command data and requires recompilation.

## Constructors

### `ls_system(part, ...)`

Builds the L-system and returns `Geometry`.

A system requires exactly one axiom, one iteration count, one angle, and one step. It can include any number of rewrite rules. Duplicate singleton parts and duplicate rule predecessors raise `CompileError`.

`ls_system(...)` accepts only L-system constructor parts. Constructor parts are compile-time-only values. They can be assigned to variables and reused inside `ls_system(...)`, and ordinary geometry/value consumers reject them.

```python
a = ls_axiom("F")
r = ls_rule("F", "F+X")
geo = ls_system(a, r, ls_iterations(2), ls_angle(60), ls_step(0.1))
output("Geometry", geo)
```

### `ls_axiom(value)`

Defines the initial symbol stream.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time `String` | Initial L-system symbols. |

### `ls_rule(symbol, replacement)`

Defines one symbol rewrite rule.

| Parameter | Type | Description |
| --- | --- | --- |
| `symbol` | compile-time `String` | Exactly one allowed command or grammar symbol. |
| `replacement` | compile-time `String` | Symbols emitted when `symbol` is encountered during expansion. |

Rules rewrite one input symbol at a time. Symbols without a matching rule pass through unchanged.

### `ls_iterations(value)`

Defines the number of rewrite passes.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time `Integer` | Non-negative expansion iteration count. |

### `ls_angle(value)`

Defines the turtle turn angle in degrees.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time number or runtime numeric `Value` | Turn angle in degrees. Runtime values commonly come from `input_float(...)`. |

### `ls_step(value)`

Defines the turtle forward distance.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time number or runtime numeric `Value` | Distance used by `F` and `f`. Runtime values commonly come from `input_float(...)`. |

## Symbols

Turtle commands:

| Symbol | Meaning |
| --- | --- |
| `F` | Draw forward. |
| `f` | Move forward without drawing. |
| `+` | Turn left by `ls_angle(...)`. |
| `-` | Turn right by `ls_angle(...)`. |
| `[` | Push turtle state and begin a branch. |
| `]` | Pop turtle state and end a branch. |

ASCII letters, digits, and `_` are grammar symbols. They are preserved during expansion and ignored by turtle emission unless a rule rewrites them.

Whitespace, Unicode symbols, and unsupported punctuation raise `CompileError`. Unmatched `]` and unclosed `[` raise `CompileError` after expansion analysis.

## Backend selection

Backend selection is internal compiler behavior. Source code supplies the same `ls_system(...)` expression for all categories.

| Expanded system | Backend behavior | Generated data |
| --- | --- | --- |
| `ls_angle(...)` and `ls_step(...)` are compile-time numbers | Static baked backend. Python expands and interprets the turtle stream, writes a generated Curve datablock, creates a hidden generated Object, and reads it through Object Info. | Curve + Object |
| Runtime angle or runtime step, with no `[` or `]` after expansion | Branch-free runtime backend. The compiler writes a command Mesh and Object; the node graph accumulates heading and position with vectorized fields. | Mesh + Object |
| Runtime angle or runtime step, with branches after expansion | Branch-aware runtime backend. The compiler writes a path-decomposed command Mesh and Object; the node graph computes path-local positions and propagates branch origins through a bounded depth-unrolled chain. | Mesh + Object |

The returned value is always normal `Geometry`.

## Generated-resource ownership

NodeForge may create internal Curve, Mesh, and Object datablocks for L-systems. These objects are marked with special metadata so NodeForge can tell that it created them, not the user.

During cleanup, NodeForge deletes only objects that have this metadata. A similar-looking name is not enough. This protects user-created objects from accidental deletion.

For each group, NodeForge stores a list of its current internal objects. This list is used during normal group updates. Metadata on the objects themselves is used to clean up old or orphaned objects after Blender restarts.

When a group is updated, NodeForge first builds the new version separately. The old version remains active. If the new build fails, nothing from the old version is deleted. If the new build succeeds, NodeForge switches to it and only then deletes the old internal objects.

In short: NodeForge deletes only its own objects and does not break the old working version if an update fails.

## Limits and budgets

L-systems can grow very quickly because each iteration rewrites the command string before any geometry is created. This section describes the limits NodeForge applies to keep compilation predictable, prevent accidentally huge node graphs or generated data, and make backend performance easier to reason about.

The budgets below cover three things: how large the expanded L-system string may become, how much branch nesting is supported, and what size ranges were tested for each backend. They are safety limits, not modeling recommendations. Smaller systems are usually easier to edit, preview, and update interactively.

| Limit or budget | Enforcement | Consequence |
| --- | --- | --- |
| `MAX_LSYSTEM_SYMBOLS = 200000` | Expansion hard limit in `systems/lsystem/expander.py`. | Expansion stops before backend analysis and materialization when the expanded stream exceeds the cap. |
| `MAX_LSYSTEM_BRANCH_DEPTH = 32` | Branch-aware runtime hard limit in `systems/lsystem/backends.py`. | Branched runtime systems above the depth cap raise `CompileError`. |
| At least one drawn `F` segment | Backend precondition. | Streams that emit no drawn segments raise `CompileError`. |
| Static generated Curve size | Conservative structural budget. | Generated Curve splines grow with drawn `F` segments. The Geometry Nodes graph stays small because the shape is stored in generated Curve/Object data. |
| Runtime command Mesh size | Conservative structural budget. | Generated Mesh vertices/edges grow with the expanded command stream. Runtime angle and step changes reuse the same generated resources. |
| Branch-aware node graph depth | Hard branch-depth limit plus conservative structural budget. | The depth-unrolled branch-origin chain grows with maximum branch depth, not with total segment count. |

NodeForge also provides optional pytest L-system benchmarks for maintainers. They check representative static, runtime, and branched runtime systems and report compile time, update time, generated topology size, node count, runtime evaluation timing, and cleanup status.

Run them from the add-on repository root:

```bash
NODEFORGE_LSYSTEM_BENCHMARK=1 \
blender --background --factory-startup \
  --python tests/run_pytest_in_blender.py -- tests/blender/lsystem/test_benchmarks.py
```

The benchmark writes machine-readable `LSYSTEM_BENCHMARK_ENV` and `LSYSTEM_BENCHMARK_ROW` JSON lines to stdout. Timing values depend on hardware and Blender version, so use them as trend data rather than fixed thresholds.

## Examples

### Static Koch-style curve

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

The angle and step are compile-time numbers, so the generated shape is baked into a Curve/Object resource pair.

### Runtime branch-free curve

```python
angle_value = input_float("Angle", default=90.0)
step_value = input_float("Step", default=0.25)

geo = ls_system(
    ls_axiom("F+F+F+F"),
    ls_iterations(0),
    ls_angle(angle_value),
    ls_step(step_value),
)
output("Geometry", geo)
```

The expanded stream contains no branch brackets, so runtime angle or step values use the branch-free command Mesh backend.

### Runtime branched plant

```python
angle_value = input_float("Angle", default=25.0)
step_value = input_float("Step", default=0.12)

geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(2),
    ls_angle(angle_value),
    ls_step(step_value),
)
output("Geometry", geo)
```

The expanded stream contains branches, so runtime angle or step values use the branch-aware command Mesh backend.

### Grammar symbols

```python
geo = ls_system(
    ls_axiom("X"),
    ls_rule("X", "F+X"),
    ls_iterations(3),
    ls_angle(60),
    ls_step(0.1),
)
output("Geometry", geo)
```

`X` is a grammar symbol. It participates in rewriting and is ignored by turtle emission when it remains in the final stream.

### Composition with other geometry operations

```python
plant = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(1),
    ls_angle(25),
    ls_step(0.2),
)
plant = transform(plant, translation=vector(0, 0, 1))
base = grid(2, 2)
geo = join(base, plant)
output("Geometry", geo)
```

The L-system result composes as ordinary geometry after `ls_system(...)` returns.
