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


### Script library catalogs

NodeForge has three explicit script-library catalogs. Reusable helpers live in `functions/`, bundled demonstrations live in `examples/`, and user-owned scripts saved from Blender live in `local/`. Catalog entries are callable only after a source-local import.

```python
from functions import sierpinski_carpet
from examples import mandelbrot as mb
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
├── systems/                 # Registry for systems supplied by installed packages
├── functions/               # Reusable NodeForge functions
├── examples/                # Bundled demo/showcase scripts
├── local/                   # User-owned local scripts; .nf files are not packaged
└── docs/                    # Architecture and authoring documentation
```

## Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — responsibility boundaries between DSL core, built-ins, function library, and Python backends.
- [`docs/WRITING_FUNCTIONS.md`](docs/WRITING_FUNCTIONS.md) — guide for adding functions under `functions/`.
- [`docs/BUILTINS.md`](docs/BUILTINS.md) — reference for DSL built-ins.
- [`docs/TESTING.md`](docs/TESTING.md) — commands for running unit and Blender tests.


## NodeForge packages

NodeForge supports installable package/library directories and zip archives. Packages contain `nodeforge_package.json` plus declared `functions`, `examples`, and/or `systems` roots. Installed packages extend the existing `functions` and `examples` catalogs and unqualified system constructor namespace. See `docs/PACKAGES.md` for the package format and safety rules.

## Material inputs

Use `input_material()` to expose a Material socket on the generated node group. The material can be selected in the Geometry Nodes modifier or connected from another node group.

```python
grass_material = input_material("Grass Material")
geo = set_material(cube(1.0), grass_material)
output("Geometry", geo)
```

`set_material()` accepts either a runtime `Material` value or a compile-time material name:

```python
geo = set_material(geo, "Grass")
```

The `Material` type token is supported by local functions, library function group sockets, and compatible raw node inputs and outputs.


### Object inputs

Create an Object socket with `input_object(name)`. Configure the lazy Object Info reader before accessing object data:

```python
source = input_object("Source")
source.info(transform_space="RELATIVE", as_instance=False)
output("Geometry", source.geometry)
```

`source.geometry`, `source.location`, `source.rotation`, and `source.scale` share one lazily created Object Info node. `source.info()` accepts `transform_space="ORIGINAL"|"RELATIVE"` and `as_instance=True|False`; the defaults are `ORIGINAL` and `True`. Configure it before the first property access. Object values remain Object sockets when passed to raw nodes, local functions, and installed library functions.

## Local function node titles

Local-function call nodes and their backing helper node groups use the function identifier as a short readable title. For example, `mix_biomes()` is shown as **Mix Biomes** and `generate_chunk()` as **Generate Chunk**. Helper identity and reuse are determined by ownership metadata for the namespace, function name, parameter signature, source, and return shape, not by the Blender datablock name.

## Local function return values

A script-local function may return one runtime value or a fixed flat tuple of runtime values. A tuple return creates one output socket per element on the reusable helper group.

```python
def split_values(value: Float):
    doubled = value * 2
    tripled = value * 3
    return doubled, tripled

first, second = split_values(input_float("Value"))
result = split_values(input_float("Other Value"))
last = result[-1]
```

Tuple results are compiler-side containers. Store them in one variable, unpack them into a flat tuple or list target, or select an element with a compile-time integer index. Arithmetic, `output()`, runtime indexing, nested tuples, starred unpacking, tuple parameters, and list returns require selecting or unpacking an individual value first.

Local-function positional parameters accept simple NodeForge type annotations: `Float`, `Int`, `Bool`, `Vector`, `Geometry`, `Material`, and `Object`. An annotation constrains the helper input socket type; unannotated parameters retain call-site type inference.
