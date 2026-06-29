# Architecture

NodeForge uses one DSL compiler with separate ownership layers for core primitives, reusable function-library entries, examples, and embedded systems. See [Architecture Layers](ARCHITECTURE_LAYERS.md) for the public layer contract and migration rules.

## Compiler core

The compiler core turns Python-like source code into a Blender `GeometryNodeTree`.

Core modules own parsing, statement routing, type tracking, interface sockets, node creation, runtime state, and function-call orchestration.

Key files:

| File | Responsibility |
| --- | --- |
| `compiler.py` | High-level compile flow, expression dispatch, local variables, function calls. |
| `parsing.py` | Python AST parsing and source inspection. |
| `consteval.py` | Compile-time constants and static expression evaluation. |
| `statements.py` | Assignments, outputs, stores, and statement-level helpers. |
| `runtime.py` | Runtime loops and Repeat Zone state wiring. |
| `nodes.py` | Low-level node creation and socket wiring. |
| `geometry.py` | Reusable helpers that build Geometry Nodes structures. |
| `values.py` | Typed socket values passed through the compiler. |
| `compile_time.py` | Base protocol and guards for compile-time-only compiler objects. |
| `systems/registry.py` | Embedded subsystem constructor dispatch and reserved-name policy. |
| `systems/lsystem/` | L-system constructors, validation, expansion, analysis, turtle interpretation, static baked generated-data backend, branch-free vectorized runtime backend, and branch-aware vectorized runtime backend. |
| `interface.py` | Node group input/output sockets and defaults. |
| `library.py` | Function discovery, function-group materialization, and group-node calls. |

The compiler core provides infrastructure. Domain-specific algorithms belong in `functions/`.

## DSL built-ins

Built-ins are the primitive vocabulary of the DSL. They live under `builtins/` and are registered through `builtins/registry.py`.

Built-ins should be general-purpose operations that compose across many procedural systems:

- input sockets
- numeric math
- vector math
- field inputs
- geometry primitives and transforms
- attributes and materials
- instancing
- runtime loop forms

A new built-in belongs in `builtins/` when it represents a reusable DSL primitive rather than one algorithm's private implementation detail.

## Function library

Functions live under `functions/`. They are reusable authored operations built on top of the DSL.

A function package can contain:

```text
functions/name/
├── __init__.py
├── source.nf
└── function.py
```

`source.nf` contains the readable function definition. `function.py` is optional and supplies native helpers for work that the DSL does not express directly.

Examples:

| Function | Implementation style |
| --- | --- |
| `fibonacci` | Pure DSL source. |
| `fibonacci` | Pure reusable DSL source. |
| `sierpinski_carpet` | Reusable DSL source using another library function. |
| `dragon_curve` | Example catalog source-only L-system demo. |
| `koch_curve` | Example catalog source-only L-system demo. |
| `fibonacci_spiral` | Example catalog source-only showcase. |
| `fractal_plant` | Example catalog source-only L-system showcase. |
| `mandelbrot` | Example catalog DSL algorithm with `backend.py` material helper. |

## Package-local backend helpers

A reusable function package can expose helpers from `function.py`. An example package uses `backend.py` for the same package-local boundary:

```python
BACKEND_BUILTINS = {
    "apply_example_material": compile_apply_example_material,
}
```

These helpers are available while compiling that package's `source.nf`. They are scoped to the package and do not enter the global DSL registry.

Use package-local helpers for function-specific Blender API operations, for example:

- creating or configuring a shader material for a generated attribute
- generating cached mesh data for one specialized function
- building a node pattern that is tightly coupled to one library function

Keep reusable operations in `builtins/`. Keep algorithm scripts in `source.nf` when the DSL can express them clearly.

## Call resolution

When compiling a call expression, NodeForge resolves it in this order:

1. global callable built-ins from `builtins/registry.py`
2. embedded system constructors from `systems/registry.py`
3. local DSL functions in the current source
4. package-local backend helpers from the current function package
5. explicit imported library bindings from `functions/`

Global built-ins define the shared DSL vocabulary. Package-local helpers extend one function package without changing global semantics. Library bindings are source-local `from functions import ...` declarations that map an exposed name or alias to a canonical function-library entry.


## Embedded systems

Embedded systems add subsystem-specific constructor syntax without creating a second compiler or UI mode. The L-system subsystem lives under `systems/lsystem/` and exposes these reserved constructor names:

```text
ls_system
ls_axiom
ls_rule
ls_iterations
ls_angle
ls_step
ls_param
ls_marker
ls_points
```

The constructor names are excluded from implicit input discovery and cannot be reused by local functions, package-local backend helpers, or function-library entries. Existing ordinary built-ins keep priority over system constructors.

System constructors can return compile-time-only objects. These objects may be assigned and later consumed by their subsystem, and shared runtime consumers reject them before node socket/type handling. The generic `CompileTimeObject` base fails closed on accidental `.typ` or `.socket` access so leaked compile-time objects raise `CompileError` instead of Python attribute errors.

L-system compilation flows through constructor parsing, deterministic expansion, stream analysis, internal backend selection, backend materialization, and a normal `Geometry` return value. Static L-systems produce generated Curve/Object data and read it through Object Info. Runtime-parameter L-systems produce generated command Mesh/Object data and read command attributes through vectorized Geometry Nodes field graphs. Branch-aware runtime graphs use path/depth/parent-attach attributes and a depth-unrolled branch-origin chain bounded by `MAX_LSYSTEM_BRANCH_DEPTH`.

Generated Curve/Mesh/Object IDs are owned by `systems/lsystem/resources.py`. Deletion requires positive NodeForge metadata on the generated ID; deterministic names are diagnostics, not ownership proof. Live group metadata is the authoritative index for successful recompiles, while ID metadata supports restart/orphan cleanup when no live group references the ID. Existing-group updates compile into a replacement group and cut over only after replacement graph/resources are ready. Failed replacement compile or cutover restores the previous graph, source/default metadata, generated-resource manifest, and generated IDs. Successful zero-resource replacements after a generated-resource group commit an explicit empty manifest before old verified resources are cleaned.

See [L-systems](LSYSTEMS.md) for syntax, backend-selection rules, limits, and examples.
