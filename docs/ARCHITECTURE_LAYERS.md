# Architecture Layers

NodeForge has one DSL compiler and several authoring layers. The layers define who owns a callable name, where source files live, and how users make reusable code available to a script.

## Layer model

| Layer | Location | User access | Owns |
| --- | --- | --- | --- |
| Core | `builtins/`, compiler modules | global callable names | language primitives and direct Geometry Nodes lowering |
| Functions | `functions/` | `from functions import ...` | reusable DSL functions and intentionally scoped package helpers |
| Examples | `examples/` | `from examples import ...` | finished demo/showcase scripts |
| Local | `local/` | `from local import ...` | user-owned DSL-only scripts saved from Blender UI |
| Systems | `systems/` | subsystem constructors | embedded subsystems such as L-systems |

The layer split is a public authoring contract. A callable belongs to exactly one public layer at a time. Reusable helpers that move to `functions/` are not kept as compatibility globals.

## Core

Core is the global primitive vocabulary. It is registered through `builtins/registry.py` and lowered by compiler-owned code where needed.

Core owns primitives for:

- group inputs and outputs
- field access
- numeric and vector operations
- geometry primitives and transforms
- attributes and materials
- instancing
- runtime loop forms
- raw node access through `node(...)`

Core does not own finished patterns, reusable layout algorithms, showcase geometry, or convenience helpers that can be authored as DSL functions.

The intended final core callable surface is:

```text
input_geometry
input_float
input_int
input_bool
input_vector

output

position
normal
index
id

vector

sin
cos
tan
asin
acos
atan
atan2
sqrt
abs
floor
ceil
round
fract
radians
degrees
exp
ln
log
min
max
pow
mod
clamp
mix
select
map_range

length
distance
dot
normalize
cross
reflect
project

noise
random_value

empty_geometry
geometry_builder
points
grid
grid_uv
set_position
store_named_attribute
set_material
cube
polyline
join
transform

instance_on_points
realize_instances

range
repeat_range

node
```

Names currently exposed as globals outside this list are migration candidates, not permanent core by default. A migration stage must classify each such name before removing it from core or moving it to `functions/`.

## Library catalogs

The compiler accepts only fixed top-level catalog imports: `functions`, `examples`, and `local`. Catalog folder names are part of the public import contract, but `local` subfolders are only storage/UI metadata. `local/math/noise.nf` exposes `noise` through `from local import noise`; nested imports such as `from local.math import noise` are not part of this stage.

Discovery rejects duplicate physical records that expose the same public name inside one catalog instead of choosing by path order. Generated `examples` and `local` node groups include catalog namespace/name metadata, and materialization refuses to update a same-named datablock unless that metadata matches.

## Functions

`functions/` contains reusable callable operations. Function names are source-local callable bindings imported by a script or by another function source file.

Reusable pure DSL functions use the flat layout:

```text
functions/name.nf
```

Package layout remains supported for existing function packages and for entries that need package-local Python helpers:

```text
functions/name/
├── __init__.py
├── source.nf
└── function.py
```

A function source uses the existing input/output model:

```python
radius = input_float("Radius", default=1.0)
pts = points(16)
output("Geometry", pts)
```

Function calls are not available globally. A script imports them explicitly:

```python
from functions import circle_points
from functions import circle_points as circle
from functions import circle_points, layout_grid
from functions import *
```

`from functions import *` imports all public discoverable function-library entries into the current source file's imported callable namespace. It is an authoring shortcut only. It does not mutate `builtins/registry.py`, does not add core globals, and does not make those names available to other scripts without an import.

Public function discovery includes flat `.nf` files and supported package entries whose names do not start with `_`. It does not include package-local backend helper names, underscore-private entries, hidden files, systems, examples, or implementation files.

## Package-local backend helpers

A package can expose helpers through `functions/name/function.py`:

```python
BACKEND_BUILTINS = {
    "apply_material": compile_apply_material,
}
```

Those helpers are available only while compiling that package's `source.nf`. They do not enter the global built-in registry and do not become normal imported function-library names.

Use package-local helpers for behavior tightly owned by one package, such as function-specific material setup or specialized Blender API work. Do not generalize package-local helpers into a second public native layer.

## Examples

`examples/` is reserved for finished shapes, demos, and showcase scripts after examples import support is added. Examples are not core globals and are not reusable function-library helpers.

The examples layer is separate from `functions/` so users can distinguish reusable building blocks from completed demonstrations. The examples stage owns examples discovery, import validation, materialization metadata, and movement of in-scope showcase entries.

Until examples support exists, do not add examples import behavior opportunistically in function-import stages.

## Systems

`systems/` owns embedded subsystems such as L-systems. Systems expose their own constructor names through `systems/registry.py` and keep their own validation, runtime, resource ownership, and cleanup rules.

This layer split does not reorganize `systems/`. Function and example import changes must preserve system constructor reservation and must not treat systems as importable function-library entries.

## Namespace and conflict policy

Imports create source-local callable bindings. They are not first-class runtime values.

The effective callable namespace must reject ambiguous ownership. A function import, alias, or star-imported binding must not silently shadow or override:

- core built-ins
- system constructors
- local DSL functions
- local assignments or input names
- constants and type tokens
- package-local backend helper names
- other explicit or star-imported bindings

Conflicts raise controlled `CompileError`s. Star import must fail on conflicts instead of skipping, renaming, or shadowing a function. Removed global helpers must fail without an import; no fallback lookup from globals to `functions/` is allowed.

## Migration and cutover rules

Each helper migration is a cutover from one owner to another:

1. add the reusable function source under `functions/`
2. compile it through explicit import and `from functions import *`
3. remove the old core built-in registration
4. update docs and tests in the same stage
5. verify unimported calls fail with a controlled error

A stage must not leave a migrated helper callable both globally and by import after stage exit. A stage must not depend on a later stage to repair stale registry entries, stale docs, or tests that still compile removed global names.

## Durable state and generated resources


Stage 5 also moves derived math/vector helpers (`smoothstep`, `sign`, `rotate2d`, and related helpers) to `functions/*.nf` and removes obsolete aliases/wrappers such as `lerp`, `frac`, and `greater_than`. These names are not core globals; scripts import the migrated helpers explicitly from `functions` or use core alternatives such as `mix`, `fract`, and comparison operators.

Function materialization creates Blender node groups and stores source/signature metadata on generated groups. Import/discovery changes must preserve existing group update and reuse behavior.

A stage that adds examples materialization must distinguish example groups from function groups, or reject collisions with controlled errors. Function and example generated groups must not become indistinguishable durable state.

## Out-of-scope architecture

This layer contract does not introduce:

- `stdlib`
- a public `native` import layer
- category imports such as `from functions.layouts import ...`
- export statements
- mandatory `def`-based reusable modules
- multi-function module files
- compatibility aliases for removed globals
- arbitrary star imports from sources other than `functions`

`node(...)` remains the raw global escape hatch.
