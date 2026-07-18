# NodeForge

NodeForge is a Blender add-on that compiles a small Python-like DSL into Geometry Nodes node groups.

The DSL is written as text, compiled into a `GeometryNodeTree`, and inserted or updated inside Blender. It is designed for procedural geometry scripts that benefit from readable source code while still producing normal Blender node groups.

## Main concepts

### DSL source

A NodeForge script describes a node group. The script declares inputs, builds geometry and field expressions, and writes one or more outputs.

```python
resolution = input_int("Resolution", default=120)
max_iter = input_int("Max Iter", default=32)

from examples import mandelbrot

geo = mandelbrot(resolution=resolution, max_iter=max_iter)
output("Geometry", geo)
```

### Built-ins

Built-ins are the primitive operations of the DSL. They live in `builtins/` and are registered through `builtins/registry.py`.

Built-ins cover input sockets, scalar math, vector math, field inputs, geometry primitives, attributes, materials, instancing, and runtime loops. They form the compiler-level vocabulary used by scripts and library functions.

Embedded systems such as L-systems live under `systems/` and use reserved constructor names such as `ls_system(...)`. See [`docs/LSYSTEMS.md`](docs/LSYSTEMS.md) for L-system syntax, backend selection, limits, generated-resource behavior, and examples.

### Script library catalogs

NodeForge has three explicit script-library catalogs. Reusable helpers live in `functions/`, bundled demonstrations live in `examples/`, and user-owned scripts saved from Blender live in `local/`. Catalog entries are callable only after a source-local import.

```python
from functions import sierpinski_carpet
from examples import koch_curve as kc
from local import my_custom_script
```

`from functions import *`, `from examples import *`, and `from local import *` expand only the selected catalog for the current source file. They do not mutate the global DSL built-in namespace.

`local/` may contain folders for organization, for example `local/math/noise.nf`, but import names remain flat: use `from local import noise`, not `from local.math import noise`.

### Python backend helpers

Reusable function packages may use `functions/<name>/function.py`. Example packages may use `examples/<name>/backend.py` only as a private implementation detail behind `source.nf`. User-owned `local/` scripts are DSL-only and do not load `function.py` or `backend.py`.

Package-local backend helpers can expose `BACKEND_BUILTINS`. These helpers are visible while compiling that package's `source.nf` and stay scoped to that package.

Use backend helpers for package-specific Blender API work such as constructing a custom shader material. Keep generic node operations in DSL built-ins.

## Project layout

```text
NodeForge/
├── __init__.py              # Blender add-on entry point
├── ui.py                    # NodeForge sidebar, operators, library UI
├── compiler.py              # DSL compiler orchestration
├── parsing.py               # Python AST parsing and source inspection
├── consteval.py             # Compile-time expression evaluation
├── statements.py            # Top-level statement compilation helpers
├── runtime.py               # repeat_range / Repeat Zone state handling
├── geometry.py              # Low-level Geometry Nodes construction helpers
├── nodes.py                 # Node creation and link utilities
├── values.py                # Typed socket wrappers
├── compile_time.py          # Compile-time-only object guards
├── interface.py             # Node group interface sockets and defaults
├── library.py               # Catalog discovery, local saves, materialization
├── builtins/                # DSL primitive registry and category modules
├── systems/                 # Embedded subsystems such as L-systems
├── functions/               # Reusable NodeForge functions
├── examples/                # Bundled demo/showcase scripts
├── local/                   # User-owned local scripts; .nf files are not packaged
└── docs/                    # Architecture and authoring documentation
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — responsibility boundaries between DSL core, built-ins, function library, and Python backends.
- [`docs/WRITING_FUNCTIONS.md`](docs/WRITING_FUNCTIONS.md) — guide for adding functions under `functions/`.
- [`docs/BUILTINS.md`](docs/BUILTINS.md) — reference for DSL built-ins.
- [`docs/LSYSTEMS.md`](docs/LSYSTEMS.md) — reference for embedded L-system constructors, limits, and examples.
- [`docs/TESTING.md`](docs/TESTING.md) — commands for running unit tests, Blender tests, and optional L-system benchmarks.


## NodeForge packages

NodeForge supports installable package/library directories and zip archives. Packages contain `nodeforge_package.json` plus declared `functions`, `examples`, and/or `systems` roots. Installed packages extend the existing `functions` and `examples` catalogs and unqualified system constructor namespace. See `docs/PACKAGES.md` for the package format and safety rules.
