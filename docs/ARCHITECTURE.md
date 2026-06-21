# Architecture

NodeForge has three authoring layers: compiler core, DSL built-ins, and function library.

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
| `fibonacci_spiral` | Pure DSL source. |
| `sierpinski_carpet` | Pure DSL source using another library function. |
| `dragon_curve` | Native backend for specialized generated geometry. |
| `koch_curve` | Native backend for optimized/static-runtime variants. |
| `mandelbrot` | DSL algorithm with a package-local backend helper for material setup. |

## Package-local backend helpers

A package can expose helpers from `function.py`:

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
2. local DSL functions in the current source
3. package-local backend helpers from the current function package
4. library functions from `functions/`

Global built-ins define the shared DSL vocabulary. Package-local helpers extend one function package without changing global semantics.
