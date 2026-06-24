# L-systems reference

NodeForge L-systems generate geometry from rewrite rules. An `ls_system(...)` result is normal `Geometry`: you can `transform(...)`, `join(...)`, assign materials, and connect it to `output(...)`.

For a practical walkthrough, see `LSYSTEMS_SCRIPTING.md`.

## Constructors

### `ls_system(part, ...)`

Builds the L-system and returns `Geometry`.

A system requires:

| Part | Required | Notes |
| --- | --- | --- |
| `ls_axiom(...)` | yes | Initial symbol stream. |
| `ls_iterations(...)` | yes | Compile-time rewrite count. |
| `ls_angle(...)` | yes | Turn angle in degrees. Can be runtime input. |
| `ls_step(...)` | yes | Forward distance. Can be runtime input. |
| `ls_rule(...)` | optional, many | Rewrite rules. |

Duplicate singleton parts and duplicate rule predecessors raise `CompileError`.

```python
geo = ls_system(
    ls_axiom("F"),
    ls_rule("F", "F[+F]F[-F]F"),
    ls_iterations(2),
    ls_angle(25),
    ls_step(0.1),
)
output("Geometry", geo)
```

### `ls_axiom(value)`

Defines the starting symbols before any rewrite iteration runs.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time `String` | Initial L-system symbols. |

### `ls_rule(symbol, replacement)`

Defines one rewrite rule.

| Parameter | Type | Description |
| --- | --- | --- |
| `symbol` | compile-time `String` | Exactly one allowed command or grammar symbol. |
| `replacement` | compile-time `String` | Symbols emitted when `symbol` is rewritten. |

Rules rewrite one symbol at a time. Symbols without a rule pass through unchanged.

```python
ls_rule("X", "F+X")
```

### `ls_iterations(value)`

Defines how many rewrite passes run before geometry is generated.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time `Integer` | Non-negative expansion iteration count. |

Changing this value changes the generated command data and requires recompilation.

### `ls_angle(value)`

Defines the turtle turn angle in degrees.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time number or runtime numeric `Value` | Angle used by `+` and `-`. |

Runtime values commonly come from `input_float(...)`.

### `ls_step(value)`

Defines the turtle forward distance.

| Parameter | Type | Description |
| --- | --- | --- |
| `value` | compile-time number or runtime numeric `Value` | Distance used by `F` and `f`. |

Runtime values commonly come from `input_float(...)`.

## Turtle commands

| Symbol | Meaning |
| --- | --- |
| `F` | Move forward and draw a segment. |
| `f` | Move forward without drawing. |
| `+` | Turn left by `ls_angle(...)`. |
| `-` | Turn right by `ls_angle(...)`. |
| `[` | Save current turtle state and begin a branch. |
| `]` | Restore saved turtle state and end a branch. |

ASCII letters, digits, and `_` can be grammar symbols. They participate in rewriting. If they remain in the final expanded stream and are not turtle commands, they are ignored by drawing.

Unsupported punctuation, whitespace, Unicode symbols, unmatched `]`, and unclosed `[` raise `CompileError`.

## Runtime values

`ls_angle(...)` and `ls_step(...)` may use runtime inputs:

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
```

Changing runtime angle or step updates the evaluated geometry. Changing the axiom, rules, iteration count, or branch structure changes generated data and requires recompilation.

## Backend selection

Backend selection is internal compiler behavior. The script always uses the same `ls_system(...)` API.

| Expanded system | Backend behavior |
| --- | --- |
| Compile-time angle and step | Static baked backend. Geometry is baked into generated Curve/Object data. |
| Runtime angle or step, no branches | Branch-free runtime backend. Runtime fields evaluate heading and position. |
| Runtime angle or step, with branches | Branch-aware runtime backend. Branch origins are propagated through a bounded depth chain. |

The returned value is always normal `Geometry`.

## Generated-resource ownership

NodeForge may create internal Curve, Mesh, and Object datablocks for L-systems. These resources are tagged as NodeForge-owned.

NodeForge deletes only resources it owns. On update, it builds the new group first. If the new build fails, the previous working group remains active. If the new build succeeds, NodeForge switches to it and then cleans up old generated resources.

## Limits and budgets

L-systems can grow quickly because the symbol stream is expanded before geometry is created.

| Limit or budget | Enforcement | Consequence |
| --- | --- | --- |
| `MAX_LSYSTEM_SYMBOLS = 200000` | Expansion hard limit in `systems/lsystem/expander.py`. | Expansion stops before backend analysis when the stream is too large. |
| `MAX_LSYSTEM_BRANCH_DEPTH = 32` | Branch-aware runtime hard limit in `systems/lsystem/backends.py`. | Deeper branched runtime systems raise `CompileError`. |
| At least one drawn `F` segment | Backend precondition. | Streams with no drawn segments raise `CompileError`. |

Optional benchmark tests are available for maintainers:

```bash
NODEFORGE_LSYSTEM_BENCHMARK=1 \
blender --background --factory-startup \
  --python tests/run_pytest_in_blender.py -- tests/blender/lsystem/test_benchmarks.py
```

The benchmark prints `LSYSTEM_BENCHMARK_ENV` and `LSYSTEM_BENCHMARK_ROW` JSON lines. Treat timings as trend data, not fixed thresholds.
