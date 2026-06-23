# NodeForge

NodeForge is a Blender add-on that compiles a small Python-like DSL into Geometry Nodes node groups.

The DSL is written as text, compiled into a `GeometryNodeTree`, and inserted or updated inside Blender. It is designed for procedural geometry scripts that benefit from readable source code while still producing normal Blender node groups.

## Main concepts

### DSL source

A NodeForge script describes a node group. The script declares inputs, builds geometry and field expressions, and writes one or more outputs.

```python
resolution = input_int("Resolution", default=120)
max_iter = input_int("Max Iter", default=32)

geo = mandelbrot(resolution=resolution, max_iter=max_iter)
output("Geometry", geo)
```

### Built-ins

Built-ins are the primitive operations of the DSL. They live in `builtins/` and are registered through `builtins/registry.py`.

Built-ins cover input sockets, scalar math, vector math, field inputs, geometry primitives, attributes, materials, instancing, and runtime loops. They form the compiler-level vocabulary used by scripts and library functions.

Embedded systems such as L-systems live under `systems/` and use reserved constructor names such as `ls_system(...)`. See [`docs/LSYSTEMS.md`](docs/LSYSTEMS.md) for L-system syntax, backend selection, limits, generated-resource behavior, and examples.

### Function library

Reusable functions live in `functions/`. A function can be a pure DSL script or a package with a DSL source and Python backend helpers.

Common layout:

```text
functions/<function_name>/
├── __init__.py
├── source.nf
└── function.py      # optional
```

A function can be called from another NodeForge script like a regular function. The compiler materializes it as a reusable nested Geometry Nodes group.

### Python backend helpers

`function.py` can expose package-local backend helpers through `BACKEND_BUILTINS`. These helpers are visible while compiling that package's `source.nf` and stay scoped to that package.

Use local backend helpers for function-specific Blender API work such as constructing a custom shader material or generating specialized data. Keep generic node operations in DSL built-ins.

## Project layout

```text
NodeForge/
├── __init__.py              # Blender add-on entry point
├── ui.py                    # NodeForge sidebar, operators, library UI
├── compiler.py              # DSL compiler orchestration
├── parsing.py               # Python AST parsing and source inspection
├── consteval.py             # Compile-time expression evaluation
├── statements.py            # Top-level statement compilation helpers
├── runtime.py               # runtime_range / Repeat Zone state handling
├── geometry.py              # Low-level Geometry Nodes construction helpers
├── nodes.py                 # Node creation and link utilities
├── values.py                # Typed socket wrappers
├── compile_time.py          # Compile-time-only object guards
├── interface.py             # Node group interface sockets and defaults
├── library.py               # Function discovery, compilation, calls
├── builtins/                # DSL primitive registry and category modules
├── systems/                 # Embedded subsystems such as L-systems
├── functions/               # Reusable NodeForge functions
└── docs/                    # Architecture and authoring documentation
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — responsibility boundaries between DSL core, built-ins, function library, and Python backends.
- [`docs/WRITING_FUNCTIONS.md`](docs/WRITING_FUNCTIONS.md) — guide for adding functions under `functions/`.
- [`docs/BUILTINS.md`](docs/BUILTINS.md) — reference for DSL built-ins.
- [`docs/LSYSTEMS.md`](docs/LSYSTEMS.md) — reference for embedded L-system constructors, limits, and examples.
