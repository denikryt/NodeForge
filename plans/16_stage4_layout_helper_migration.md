# 16 — Stage 4 Layout Helper Migration Plan

## Problem / Goal

Stage 4 moves the remaining reusable point-layout helpers out of the global built-in layer and into explicit `functions` imports. The repository after Stage 3 already has the required import foundation and has completed the first helper cutover: `circle_points` exists as `functions/circle_points.nf`, is discovered as a flat library function, and is no longer present in `builtins.layout.NAMES` or `registry.CALLABLE_BUILTIN_NAMES`.

The Stage 4 target helpers are:

```text
layout_circle
spiral_points
layout_spiral
grid_points
layout_grid
random_points
layout_random
copy_by_offsets
```

The goal is to leave all of those names callable only through source-local imports from `functions`, while preserving the existing compiler/import/materialization model and keeping `circle_points`, math/vector helper globals, examples, `mandelbrot`, and `systems/` outside this stage.

## Current Repository Facts

- `builtins/layout.py` currently owns seven global layout names: `layout_grid`, `grid_points`, `layout_circle`, `layout_spiral`, `spiral_points`, `layout_random`, and `random_points`. `circle_points` has already been removed by Stage 3.
- `tests/unit/test_layout_grid_validation.py` currently imports `NodeForge.builtins.layout` directly and asserts private helper APIs plus old omitted-count behavior for `layout_circle` / `layout_spiral`; those assertions are stale under the Stage 4 cutover and must be deleted or rewritten in this stage.
- `builtins/registry.py` registers every name in `layout.NAMES` into `_HANDLERS` and `CALLABLE_BUILTIN_NAMES`; removing a name from `layout.NAMES` removes its global call dispatch without changing registry mechanics.
- `geometry.py` still contains Python node-construction helpers for all layout formulas. The Stage 4 implementation can use them as reference behavior, but migrated helpers must be authored as flat DSL files, not native package backends.
- `functions/copy_by_offsets/source.nf` is a pure DSL package-style function. It has no `function.py`, no package-local backend helpers, and should become `functions/copy_by_offsets.nf`.
- `library.py` discovers `functions/name.nf` before `functions/name/source.nf` and reports public names with `library_function_names()`. Leaving both a flat file and a stale package directory for the same migrated helper would create ambiguous repository ownership even though discovery returns a single name.
- `library_calls.py` maps positional arguments to the materialized group input socket order and maps keyword names by normalized socket names. A migrated `.nf` file therefore owns its public signature by the order and names of its `input_*` declarations.
- `expression_compiler.py` resolves built-ins before imported library calls. Any migrated helper left in `registry.CALLABLE_BUILTIN_NAMES` would continue to compile through the old Python built-in path even when imported.
- Stage 2 import validation already rejects missing function imports, duplicate imports, conflicts with local bindings/functions, core built-ins, system constructors, constants, type tokens, and package-local backend helpers. Stage 4 should add coverage for migrated layout names, not redesign import validation.
- `random_value(min, max, seed=..., id=...)` is a core primitive and can express `layout_random` / `random_points` in pure DSL by using `index()` as the `id`.
- The accepted final core primitive list does not include a point-count/domain-size primitive. The old Python `layout_circle` and `layout_spiral` derive omitted `count` from geometry using `GeometryNodeAttributeDomainSize`; pure DSL functions cannot express that behavior without expanding core.

## Risk Dimensions

1. **Namespace cutover.** Every Stage 4 helper must have exactly one public owner after exit: `functions/name.nf`. Stale global registry entries, stale docs, or stale tests would hide an incomplete cutover. The known stale unit-test risk is `tests/unit/test_layout_grid_validation.py`, which currently validates removed `builtins.layout` internals and must not be preserved as proof of behavior.
2. **Dispatch order.** Because built-ins win over imported library bindings, registry removal is part of the behavioral migration, not cleanup.
3. **Function signatures and socket typing.** The `.nf` input declarations determine positional binding, keyword binding, socket types, defaults, UI labels, and accepted dynamic values. Pure DSL functions cannot exactly preserve Python helper overloading such as scalar-or-vector spacing.
4. **Omitted count behavior.** `layout_circle(geometry, radius=...)` and `layout_spiral(geometry, radius=...)` currently derive count from input geometry. The chosen Stage 4 contract must be explicit and tested so users do not get silent geometry drift.
5. **Generated/project-owned artifacts.** Imported functions materialize Blender `GeometryNodeTree` groups with `nodeforge_library_*` metadata. A failed materialization must continue to use the existing compiler rollback path and must not leave partially built groups as authoritative state.
6. **Stale resource cleanup.** Moving `copy_by_offsets` from `functions/copy_by_offsets/source.nf` to `functions/copy_by_offsets.nf` must remove the obsolete pure-helper package directory before exit, otherwise future discovery/audit stages inherit two physical owners for one public function name.
7. **Composition fanout.** Shortcut functions such as `grid_points`, `spiral_points`, and `random_points` may call their corresponding layout functions. This creates nested materialized function groups; tests must prove reuse is stable and does not create duplicate groups on repeated calls.
8. **User input and validation.** The old Python built-ins performed AST-level checks for negative compile-time counts, whole grid components, bool-only `centered`, and scalar/vector unions. Pure DSL functions should rely on typed sockets and runtime node behavior for valid authoring forms rather than adding native validation infrastructure.
9. **Performance.** Migrated helpers should stay field-based and compact: one point source where needed, index-based math, random node where needed, and `set_position`. They must not generate static per-point geometry or compile-time loops.
10. **Scope containment.** Stage 4 must not migrate derived math/vector helpers, examples, `mandelbrot`, or `systems/`, and must not introduce `stdlib`, category imports, compatibility aliases, or new DSL syntax.
11. **Secrets and deployment.** This scope has no secrets, credentials, network access, database state, HTTP surface, or deployment configuration. Do not add secret-management, authorization, endpoint, or database mechanics.

## Expected Behavior

### Import-only layout helpers

These forms must compile:

```python
from functions import layout_grid, grid_points

pts = points(6)
pts = layout_grid(pts, count=vector(3, 2, 1), spacing=vector(1.0, 2.0, 3.0), centered=True)
grid = grid_points(count=vector(2, 2, 1), spacing=vector(1.25, 1.25, 1.25))
output("Geometry", join(pts, grid))
```

```python
from functions import layout_circle as lc, spiral_points

pts = points(16)
pts = lc(pts, count=16, radius=2.0)
spiral = spiral_points(32, radius=2.0, turns=3.0, height=2.0)
output("Geometry", join(pts, spiral))
```

```python
from functions import *

pts = random_points(10, min=vector(-1, -1, -1), max=vector(1, 1, 1), seed=3)
output("Geometry", pts)
```

Unimported calls must fail with controlled `CompileError`:

```python
pts = grid_points(count=vector(3, 2, 1))
output("Geometry", pts)
```

All Stage 4 helper names must be absent from:

```python
NodeForge.builtins.layout.NAMES
NodeForge.builtins.registry.CALLABLE_BUILTIN_NAMES
```

### Function signatures

The migrated functions must use these signatures and socket orders:

```text
layout_grid(geometry, count=vector(1, 1, 1), spacing=vector(1, 1, 1), centered=False) -> Geometry
grid_points(count=vector(1, 1, 1), spacing=vector(1, 1, 1), centered=False) -> Geometry
layout_circle(geometry, count=16, radius=1.0, start_angle=0.0, end_angle=tau, include_endpoint=False) -> Geometry
layout_spiral(geometry, count=16, radius=1.0, turns=1.0, height=0.0, start_radius=0.0, start_angle=0.0) -> Geometry
spiral_points(count=16, radius=1.0, turns=1.0, height=0.0, start_radius=0.0, start_angle=0.0) -> Geometry
layout_random(geometry, min=vector(-1, -1, -1), max=vector(1, 1, 1), seed=0) -> Geometry
random_points(count=16, min=vector(-1, -1, -1), max=vector(1, 1, 1), seed=0) -> Geometry
copy_by_offsets(geometry, scale=vector(1/3, 1/3, 1)) -> Geometry
```

`layout_circle` and `layout_spiral` require an explicit `count` socket. They may provide a default for interactive node insertion and omitted call arguments, but they must not derive count from the input geometry. Existing examples/docs/tests that relied on omitted-count derivation must be rewritten to pass `count`.

For grid helpers, Stage 4 should use vector spacing as the imported function contract. This preserves anisotropic spacing, avoids unsafe scalar-to-vector socket coercion in `library_calls.make_library_call_node(...)`, and keeps the target implementation pure DSL. Existing scalar-spacing examples should be rewritten to `vector(s, s, s)` or `vector(s, s, 0)` as appropriate.

### Layout formulas

`layout_grid.nf` must follow the current runtime formula, with vector count components clamped for positioning and optional centering:

```python
geometry = input_geometry("Geometry")
count = input_vector("Count", default=vector(1, 1, 1))
spacing = input_vector("Spacing", default=vector(1, 1, 1))
centered = input_bool("Centered", default=False)

cx = max(count.x, 1.0)
cy = max(count.y, 1.0)
cz = max(count.z, 1.0)

x_index = mod(index(), cx)
y_index = mod(floor(index() / cx), cy)
z_index = floor(index() / (cx * cy))
pos = vector(x_index, y_index, z_index) * spacing

extent = vector(cx - 1.0, cy - 1.0, cz - 1.0) * spacing * 0.5
pos = select(centered, pos, pos - extent)

geometry = set_position(geometry, pos)
output("Geometry", geometry)
```

`grid_points.nf` should compose with `layout_grid` to avoid formula duplication:

```python
from functions import layout_grid

count = input_vector("Count", default=vector(1, 1, 1))
spacing = input_vector("Spacing", default=vector(1, 1, 1))
centered = input_bool("Centered", default=False)

total = max(count.x, 0.0) * max(count.y, 0.0) * max(count.z, 0.0)
geometry = points(total)
geometry = layout_grid(geometry, count=count, spacing=spacing, centered=centered)
output("Geometry", geometry)
```

`layout_circle.nf` must use the Stage 3 `circle_points` denominator convention, but apply it to existing geometry:

```python
geometry = input_geometry("Geometry")
count = input_int("Count", default=16)
radius = input_float("Radius", default=1.0)
start_angle = input_float("Start Angle", default=0.0)
end_angle = input_float("End Angle", default=tau)
include_endpoint = input_bool("Include Endpoint", default=False)

count_value = count * 1.0
raw_denominator = select(include_endpoint, count_value, count_value - 1.0)
denominator = max(raw_denominator, 1.0)

t = index() / denominator
angle = start_angle + (end_angle - start_angle) * t
pos = vector(cos(angle) * radius, sin(angle) * radius, 0.0)

geometry = set_position(geometry, pos)
output("Geometry", geometry)
```

`layout_spiral.nf` must preserve the endpoint denominator behavior from the Python helper:

```python
geometry = input_geometry("Geometry")
count = input_int("Count", default=16)
radius = input_float("Radius", default=1.0)
turns = input_float("Turns", default=1.0)
height = input_float("Height", default=0.0)
start_radius = input_float("Start Radius", default=0.0)
start_angle = input_float("Start Angle", default=0.0)

count_value = count * 1.0
denominator = max(count_value - 1.0, 1.0)
t = index() / denominator

r = start_radius + (radius - start_radius) * t
angle = start_angle + tau * turns * t
pos = vector(cos(angle) * r, sin(angle) * r, height * t)

geometry = set_position(geometry, pos)
output("Geometry", geometry)
```

`spiral_points.nf` should compose with `layout_spiral`:

```python
from functions import layout_spiral

count = input_int("Count", default=16)
radius = input_float("Radius", default=1.0)
turns = input_float("Turns", default=1.0)
height = input_float("Height", default=0.0)
start_radius = input_float("Start Radius", default=0.0)
start_angle = input_float("Start Angle", default=0.0)

count_value = count * 1.0
geometry = points(max(count_value, 0.0))
geometry = layout_spiral(
    geometry,
    count=count,
    radius=radius,
    turns=turns,
    height=height,
    start_radius=start_radius,
    start_angle=start_angle,
)
output("Geometry", geometry)
```

`layout_random.nf` and `random_points.nf` must use the existing core `random_value` primitive and `index()` as the ID:

```python
geometry = input_geometry("Geometry")
min_value = input_vector("Min", default=vector(-1, -1, -1))
max_value = input_vector("Max", default=vector(1, 1, 1))
seed = input_int("Seed", default=0)

pos = random_value(min_value, max_value, seed=seed, id=index())
geometry = set_position(geometry, pos)
output("Geometry", geometry)
```

```python
from functions import layout_random

count = input_int("Count", default=16)
min_value = input_vector("Min", default=vector(-1, -1, -1))
max_value = input_vector("Max", default=vector(1, 1, 1))
seed = input_int("Seed", default=0)

count_value = count * 1.0
geometry = points(max(count_value, 0.0))
geometry = layout_random(geometry, min=min_value, max=max_value, seed=seed)
output("Geometry", geometry)
```

`copy_by_offsets.nf` must preserve the current `functions/copy_by_offsets/source.nf` DSL behavior exactly except for its physical path.

## Architecture

### Chosen approach

Use one flat `.nf` file per migrated helper under `functions/` and remove the migrated names from `builtins/layout.py`. Shortcut helpers may compose with their corresponding layout helper through `from functions import name` inside the function source.

This approach reuses the Stage 2/3 import and library materialization path:

```text
source import -> compiler import validation -> expression_compiler imported-library call -> library_calls -> library.get_or_create_library_group -> normal NodeForge source compile
```

The migration should not add a registry/spec abstraction, native function backend, new parser rule, new core primitive, or compatibility fallback. The existing `library.py` metadata contract remains authoritative for materialized function groups:

```text
nodeforge_library_name
nodeforge_library_source
nodeforge_function_kind
nodeforge_backend_signature
nodeforge_function_display_name
```

No durable file-format migration is required beyond moving project-owned `.nf` source files. Existing Blender groups materialized before the source move may remain in a user's `.blend`; when the imported function is materialized again, `get_or_create_library_group(...)` compares stored source/signature and recompiles through the existing transactional update path.

### Explicit count decision

Use the preferred umbrella outcome: `layout_circle` and `layout_spiral` require an explicit `count` input in the imported function contract.

Reasons:

- The current omitted-count behavior depends on compiler-owned Python construction of `GeometryNodeAttributeDomainSize` in `geometry.py`.
- The accepted core primitive list has no public point-count/domain-size primitive.
- Adding such a primitive would expand core language surface for compatibility with removed global helpers, which is contrary to the umbrella direction unless a broader product need is proven.
- The umbrella explicitly does not require compatibility aliases or exact preservation of removed global helper signatures.

Tests and docs must make this visible by rewriting omitted-count examples to pass `count=...` and by adding a negative or structural test that imported `layout_circle`/`layout_spiral` no longer insert a Domain Size node when called with explicit count.

### Spacing decision

Use vector `spacing` for imported `layout_grid` and `grid_points`.

Reasons:

- The pure function-library call path has concrete socket types; it does not provide a safe union type for `Float | Vector` inputs.
- A vector socket preserves anisotropic layout behavior, while scalar spacing can be written explicitly as `vector(s, s, s)`.
- Accepting scalar constants for a vector socket would depend on Blender socket-default coercion and `interface._set_socket_default(...)` failure behavior, which is not a controlled public contract.

### Responsibility boundaries

- `functions/*.nf` owns reusable layout formulas and public function signatures.
- `builtins/layout.py` should become empty or be removed from the registry module list if it has no `NAMES` left after Stage 4. The safer minimal edit is to keep the module with `NAMES = set()` and a defensive `compile_call(...)` that raises `CompileError`, unless lint/import tests show the module can be removed from `_MODULES` without churn.
- `builtins/registry.py` should keep its existing registration mechanism. It should not gain special cases for migrated names.
- `library.py`, `library_calls.py`, `parsing.py`, and `compiler.py` should remain unchanged unless tests expose a Stage 4-specific bug in existing flat-file discovery or nested function import behavior.
- Tests own proof of import forms, global removal, behavior, and stale package cleanup.
- Docs own the user-facing authoring contract and changed signatures.

### Alternatives considered

1. **Add a core `point_count` / domain-size primitive.** Rejected for Stage 4 because it expands core to preserve old helper convenience and would need new primitive docs, tests, Blender-version compatibility checks, and conflict analysis. The umbrella recommends explicit count unless project facts justify the primitive.
2. **Keep layout helpers as native `compile_call` package functions.** Rejected because the stage target is flat pure DSL files for reusable helpers, and native backends would preserve the old high-level behavior in a broader Python extension surface.
3. **Leave temporary global aliases.** Rejected because the umbrella forbids compatibility globals after helper cutover, and dispatch order would make imported calls compile through the old owner.
4. **Use one large shared layout module.** Rejected because the project does not support multi-function reusable modules or export syntax. One public `.nf` file per function matches existing discovery.
5. **Duplicate every formula instead of composing shortcut helpers.** Rejected for shortcut helpers where composition is straightforward. Composition keeps formula ownership local to `layout_grid`, `layout_spiral`, and `layout_random`; tests can still verify nested materialization and reuse.

## Touched Files

```text
builtins/layout.py
builtins/registry.py
docs/BUILTINS.md
docs/WRITING_FUNCTIONS.md
docs/ARCHITECTURE_LAYERS.md
README.md
tests/blender/test_layout_points.py
tests/blender/test_library_functions.py
tests/blender/test_import_registry.py
tests/blender/test_compile_fixtures.py
tests/blender_refactor_regression.py
tests/unit/test_layout_builtin_names.py
tests/unit/test_layout_grid_validation.py
tests/COVERAGE_MAP.md
```

Only edit a listed file where inspection shows Stage 4 references or assertions actually exist. `builtins/registry.py` may require no textual change if an empty `layout.NAMES` is sufficient.

## New Files

```text
functions/layout_grid.nf
functions/grid_points.nf
functions/layout_circle.nf
functions/layout_spiral.nf
functions/spiral_points.nf
functions/layout_random.nf
functions/random_points.nf
functions/copy_by_offsets.nf
plans/16_stage4_layout_helper_migration.md
```

## Removed Files / Directories

```text
functions/copy_by_offsets/source.nf
functions/copy_by_offsets/__init__.py
functions/copy_by_offsets/
```

Remove the directory after `functions/copy_by_offsets.nf` is verified. Do not remove or move `functions/dragon_curve`, `functions/koch_curve`, `functions/mandelbrot`, other existing function packages, or `systems/`.

## Implementation Steps

1. Verify the Stage 4 entry condition in the working tree before production edits:
   - `functions/circle_points.nf` exists.
   - `circle_points` is absent from `builtins.layout.NAMES` and `registry.CALLABLE_BUILTIN_NAMES`.
   - Stage 3 layout tests for imported `circle_points` pass or are at least runnable in the environment.

2. Add regression tests first:
   - Update `tests/unit/test_layout_builtin_names.py` and the equivalent Blender test expectation so `layout.NAMES == set()` after Stage 4.
   - Delete or rewrite `tests/unit/test_layout_grid_validation.py` so it no longer imports `NodeForge.builtins.layout` to validate private `_compile_grid_count`, `_compile_scalar_count`, `_bind_args`, or `compile_call(...)` behavior that Stage 4 removes. Replacement coverage belongs to imported-function tests for explicit-count `layout_circle` / `layout_spiral`, vector `spacing`, registry absence, and controlled unimported-call failures.
   - Add tests proving every Stage 4 helper imports explicitly, imports by alias for at least representative helpers, and works through `from functions import *` for at least one migrated layout helper not covered by Stage 3.
   - Add unimported-call failure tests for all migrated helper names.
   - Add registry absence assertions for all migrated helper names.
   - Add library discovery assertions that all new flat files are public functions and that `copy_by_offsets` is not package-local.

3. Add flat `.nf` functions using the formulas in `Expected Behavior`:
   - Keep input declarations in the specified order.
   - Use only current core primitives and imports from `functions`.
   - Do not add `function.py` for any migrated helper.
   - Do not update `circle_points.nf` unless implementation discovers a direct Stage 4 regression in its interaction with `layout_circle`; if changed, document and test the reason.

4. Move `copy_by_offsets`:
   - Copy the exact current source content into `functions/copy_by_offsets.nf`.
   - Run discovery/materialization tests for `copy_by_offsets`.
   - Remove the obsolete `functions/copy_by_offsets/` package directory.

5. Remove global layout ownership:
   - Remove all remaining names and dispatch branches from `builtins/layout.py`, or reduce the module to an empty `NAMES` and defensive `compile_call(...)`.
   - Confirm `builtins/registry.py` no longer registers any layout helper names.
   - Do not remove core geometry/math/vector primitives used by the new `.nf` files.

6. Update tests that currently call layout helpers globally or assert old `builtins.layout` internals:
   - Replace global calls with explicit imports where the test is still about layout behavior.
   - Replace scalar grid spacing in imported helper calls with vector spacing.
   - Replace omitted-count `layout_circle` / `layout_spiral` calls with explicit `count`.
   - Remove or rewrite `tests/unit/test_layout_grid_validation.py` assertions for `_compile_grid_count`, `_compile_scalar_count`, `_bind_args`, and omitted-count `compile_call(...)`; those private APIs are no longer an owned contract after layout helpers leave the built-in module.
   - Remove or rewrite tests that specifically asserted old Domain Size derivation for layout helpers; add replacement tests asserting imported explicit-count behavior and absence of Domain Size for the pure DSL path.

7. Update docs and examples:
   - In `docs/BUILTINS.md`, remove Stage 4 helpers from the global built-in point-layout section and document them as imported function-library helpers with explicit import examples.
   - In `docs/WRITING_FUNCTIONS.md`, extend the migrated helper section from `circle_points` to include the Stage 4 layout helpers and mention `from functions import *` as quick exploratory import.
   - In architecture docs, ensure the Stage 4 cutover matches the core/functions boundary without adding implementation history.
   - Update README or compile fixtures only where they still show old global layout helper usage.

8. Validate materialization and cleanup behavior:
   - Repeatedly materialize at least one direct helper and one composed shortcut helper, such as `layout_grid` and `grid_points`, and assert no duplicate function groups are created on reuse.
   - Compile a source using `from functions import *` and assert `registry.CALLABLE_BUILTIN_NAMES` is unchanged before/after compilation.
   - Compile failure cases and assert failed group builds do not leave the newly requested top-level group behind. Use existing helpers where available rather than adding a new cleanup mechanism.

9. Final scope check:
   - Confirm no derived math/vector helper names were migrated or removed.
   - Confirm `dragon_curve`, `koch_curve`, `mandelbrot`, and `systems/` are unchanged except incidental test fixture imports if required.
   - Confirm no `stdlib`, category import, `examples` import, export syntax, compatibility alias, or new core primitive was added.

## Tests

Focused unit tests:

```bash
python -m pytest tests/unit/test_layout_builtin_names.py
python -m pytest tests/unit/test_layout_grid_validation.py
python -m pytest tests/unit/test_function_import_parsing.py
```

Focused Blender tests:

```bash
python tests/run_pytest_in_blender.py tests/blender/test_layout_points.py
python tests/run_pytest_in_blender.py tests/blender/test_library_functions.py
python tests/run_pytest_in_blender.py tests/blender/test_import_registry.py
```

Broader regression tests because Stage 4 changes public function discovery and many compile fixtures:

```bash
python tests/run_pytest_in_blender.py tests/blender/test_compile_fixtures.py
python tests/run_pytest_in_blender.py tests/blender/test_math_compile.py
python tests/run_pytest_in_blender.py tests/blender/test_mandelbrot_eval.py
python tests/run_pytest_in_blender.py tests/blender_refactor_regression.py
python -m pytest tests/unit
```

Required assertions to add or preserve:

- `layout.NAMES == set()` after Stage 4.
- `tests/unit/test_layout_grid_validation.py` no longer validates removed `builtins.layout` private APIs or old omitted-count behavior; if retained, it must validate Stage 4 replacement contracts only.
- Each Stage 4 helper name is absent from `registry.CALLABLE_BUILTIN_NAMES`.
- `library.library_function_names()` contains every Stage 4 helper.
- `library.has_module_library_function(name)` is false for every newly migrated pure DSL helper, including `copy_by_offsets`.
- `from functions import layout_grid`, alias import, multi-import, and `from functions import *` compile for migrated layout helpers.
- Unimported calls for `layout_circle`, `layout_spiral`, `spiral_points`, `layout_grid`, `grid_points`, `layout_random`, `random_points`, and `copy_by_offsets` fail with controlled `CompileError`.
- Star import does not mutate global built-ins and still rejects conflicts with a migrated layout helper local binding.
- `layout_circle(points(5), count=5, radius=2.0)` evaluates like the old explicit-count circle layout for representative points.
- `layout_spiral(points(5), count=5, radius=2.0, turns=1.0, height=1.0)` reaches the final height on the last point.
- `grid_points(count=vector(0, 2, 1), spacing=vector(1, 1, 1))` produces zero points and still materializes through the imported library path.
- `layout_random` / `random_points` create `FunctionNodeRandomValue` nodes with vector output and use `index()` as ID.
- Repeated materialization of `copy_by_offsets` and one composed shortcut does not create duplicate groups.
- Existing `mandelbrot` package-local helper scoping tests still pass.
- Existing `systems/lsystem` tests or compile fixtures remain unchanged in behavior.

## Validation / Handoff Data

The implementation handoff for this stage must report:

- Public callable names removed from global built-ins:

```text
layout_circle
spiral_points
layout_spiral
grid_points
layout_grid
random_points
layout_random
```

- Public function-library names added or changed:

```text
layout_circle
spiral_points
layout_spiral
grid_points
layout_grid
random_points
layout_random
copy_by_offsets
```

- Physical layout change:

```text
functions/copy_by_offsets/source.nf -> functions/copy_by_offsets.nf
```

- Intentional signature differences from old globals:
  - `layout_circle` and `layout_spiral` require/use explicit `count`; they do not derive count from geometry.
  - `layout_grid` and `grid_points` use vector `spacing` in the imported function contract.
  - Pure DSL functions rely on function socket typing and runtime node behavior rather than old AST-level validation for all invalid values.

- Materialization metadata changes: none expected beyond normal source text and kind updates caused by flat `.nf` discovery.

## Regression and Blind-Spot Analysis

- A migrated name can remain in `layout.NAMES`, causing imported calls to hit the old built-in because of dispatch order. Registry absence tests must catch this.
- A stale `functions/copy_by_offsets/` package can remain after adding the flat file, causing ambiguous ownership and future cleanup risk. Discovery tests must assert `has_module_library_function("copy_by_offsets")` is false and repository inspection must confirm the directory is gone.
- `grid_points.nf`, `spiral_points.nf`, or `random_points.nf` can accidentally duplicate formulas instead of composing, leading to future drift. Prefer composition and test both direct and shortcut behavior.
- `select(cond, false, true)` can be used with Python-like branch order by mistake. Tests for centered grid and endpoint circle/spiral behavior must catch reversed branches.
- Scalar spacing examples can silently keep default vector spacing if passed to a vector socket as a constant. Tests/docs must avoid scalar spacing for imported grid helpers and should include a negative or review assertion if implementation attempts to support scalar spacing without a controlled mechanism.
- Removing `builtins/layout.py` entirely could break imports from `builtins/registry.py` or tests that import the module. Keeping an empty module is safer unless all import sites are updated deliberately.
- Nested library imports inside shortcut functions can conflict with inherited imports or star imports if validation regresses. Existing Stage 2 tests plus one composed-helper materialization test must cover this path.
- Old Domain Size and private `builtins.layout` unit tests for omitted count can be accidentally left passing through a stale global helper. `tests/unit/test_layout_grid_validation.py` is the concrete Stage 3 file with this risk; it must be deleted or rewritten to imported explicit-count behavior, vector spacing, controlled global-call failures, and registry absence.
- Function group reuse can compare stale source metadata and skip recompilation unexpectedly. Materialization tests should create, then request the same migrated function again and assert no duplicate groups and correct `nodeforge_library_source`.
- Docs can continue to list layout helpers under built-ins while code requires imports. Documentation updates are part of the stage exit condition.
- `mandelbrot`, `dragon_curve`, and `koch_curve` can be touched because they live under `functions/`; Stage 4 must leave them physically and behaviorally unchanged.

## Open Questions

None.
