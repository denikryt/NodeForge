# 17 — Stage 5 Derived Math / Vector / Extra Global Helper Audit and Migration

## Problem / Goal

Stage 4 left the layout layer in the target imported-function shape: `builtins/layout.py` exposes no callable names, and the migrated layout helpers live as flat `functions/*.nf` entries. The remaining violation of the umbrella core/functions split is now concentrated in the global callable registry for derived scalar helpers, derived vector helpers, aliases, and compare wrappers.

The Stage 5 goal is to make the global callable surface match the accepted core primitive policy for the math/vector/extra helper area. Every current callable in `registry.CALLABLE_BUILTIN_NAMES` must be classified against the final core list, and every non-core name must either become an imported flat function or be removed from the public DSL surface with tests proving the old global call fails.

Current extra globals found in the Stage 4 repository are:

```text
angle_between
equal
frac
greater_equal
greater_than
inverse_lerp
lerp
less_equal
less_than
not_equal
pingpong
polar
remap
rotate2d
rotate_around_axis
saturate
sign
smootherstep
smoothstep
step
wrap
```

The accepted core names that must remain global are the existing scalar/vector primitives from the umbrella list, including `fract`, `mix`, `map_range`, normal trig/math primitives, `vector`, `length`, `distance`, `dot`, `normalize`, `cross`, `reflect`, `project`, `noise`, and `random_value`. `node(...)` must remain unchanged.

## Expected Behavior

After this stage:

- The following useful derived helpers are available only through `from functions import ...` or `from functions import *`:

  ```text
  inverse_lerp
  remap
  saturate
  step
  smoothstep
  smootherstep
  pingpong
  wrap
  sign
  rotate2d
  polar
  angle_between
  rotate_around_axis
  ```

- These migrated helpers are flat pure DSL files under `functions/` and do not use package directories or package-local Python helpers.
- Calling any migrated helper without an import raises a controlled `CompileError` through normal unsupported-function resolution.
- Imported calls support positional and socket-normalized keyword arguments through the existing library-call path.
- Imported aliases compile, for example:

  ```python
  from functions import smoothstep as smooth
  h = smooth(0, 1, grid_uv().x)
  output("h", h)
  ```

- `from functions import *` binds these helpers only in the current source file and does not mutate `registry.CALLABLE_BUILTIN_NAMES`.
- The following obsolete aliases/wrappers are removed rather than migrated:

  ```text
  lerp      -> use core mix(a, b, factor)
  frac      -> use core fract(value)
  greater_than / greater_equal / less_than / less_equal / equal / not_equal
             -> use DSL comparison operators, or raw node(...) for direct FunctionNodeCompare control
  ```

- Existing scripts, fixtures, and docs owned by this repository are updated to import migrated helpers or use core alternatives.
- Existing behavior outside this surface remains unchanged: layout functions stay imported, existing function packages such as `mandelbrot` keep package-local helpers, systems remain untouched, and raw `node(...)` behavior remains unchanged.

## Architecture

### Classification and selected migration path

Use the existing Stage 2/3/4 function-library path instead of adding new compiler mechanisms. The migrated helpers are pure expressions over existing core primitives, so each belongs in `functions/name.nf` with the existing `input_*`/`output(...)` function-source model.

Classification:

| Name | Stage 5 action | Rationale |
| --- | --- | --- |
| `inverse_lerp` | migrate to `functions/inverse_lerp.nf` | useful derived scalar helper; expressible as `(x - a) / (b - a)` |
| `remap` | migrate | useful derived scalar helper; expressible through `inverse_lerp` and arithmetic |
| `saturate` | migrate | useful derived scalar helper; expressible as `clamp(x, 0, 1)` |
| `step` | migrate | useful derived scalar helper; expressible through comparison and `select` |
| `smoothstep` | migrate | useful derived scalar helper used by fixtures; expressible through imported helpers and arithmetic |
| `smootherstep` | migrate | same as `smoothstep`, with quintic polynomial |
| `pingpong` | migrate | useful derived scalar helper; expressible through `mod`, `abs`, and arithmetic |
| `wrap` | migrate | useful derived scalar helper; expressible through `mod` and arithmetic |
| `sign` | migrate | useful derived scalar helper; no longer core, but cheaply expressible through comparisons and `select` |
| `rotate2d` | migrate | useful vector helper; expressible through `sin`, `cos`, vector component access, and `vector(...)` |
| `polar` | migrate | useful vector helper; expressible through trig and `vector(...)` |
| `angle_between` | migrate | useful vector helper; expressible through `normalize`, `dot`, `clamp`, and `acos` |
| `rotate_around_axis` | migrate | useful vector helper; expressible through Rodrigues' formula using core vector primitives |
| `lerp` | remove | compatibility alias for core `mix`; keeping it as imported API would preserve an alias outside the accepted product contract |
| `frac` | remove | compatibility alias for core `fract` |
| compare wrappers | remove | duplicate DSL comparison operators and raw `node(...)`; not part of accepted core or reusable helper contract |

### Data flow and call resolution

The current call resolution order is:

```text
core callable builtins -> systems constructors -> local functions -> package backend helpers -> imported library functions
```

Because imported names are rejected when they conflict with built-ins or reserved names, Stage 5 must perform each helper cutover atomically at repository exit:

1. Add the flat function source.
2. Remove the same name from the global built-in registry source (`math.NAMES`, `vector.NAMES`, or `node_wrappers.NAMES`/registry membership).
3. Update tests/docs/fixtures so no repository script relies on the old global call.

A temporary implementation state where both the global and function-library versions exist will make `from functions import *` or explicit imports fail conflict validation. That state is acceptable only inside the developer's local edit sequence, not at stage exit.

### Function file contracts

All migrated helpers must expose one output suitable for expression calls. Use stable input socket names that preserve the old public parameter names where they are not misleading:

```text
inverse_lerp:        A, B, X
remap:               X, In Min, In Max, Out Min, Out Max
saturate:            X
step:                Edge, X
smoothstep:          Edge0, Edge1, X
smootherstep:        Edge0, Edge1, X
pingpong:            X, Length
wrap:                X, Min, Max
sign:                X
rotate2d:            V, Angle
polar:               Radius, Angle
angle_between:       A, B
rotate_around_axis:  V, Axis, Angle
```

The existing library-call keyword normalizer means calls such as `smoothstep(edge0=0, edge1=1, x=0.5)`, `rotate2d(v=vector(...), angle=...)`, and `remap(in_min=..., out_max=...)` map to these sockets. Preserve this behavior through tests.

DSL source examples for the core formulas:

```python
# functions/inverse_lerp.nf
a = input_float("A", default=0.0)
b = input_float("B", default=1.0)
x = input_float("X", default=0.0)
output("Value", (x - a) / (b - a))
```

```python
# functions/smoothstep.nf
from functions import inverse_lerp, saturate
edge0 = input_float("Edge0", default=0.0)
edge1 = input_float("Edge1", default=1.0)
x = input_float("X", default=0.0)
t = saturate(inverse_lerp(edge0, edge1, x))
output("Value", t * t * (3.0 - 2.0 * t))
```

```python
# functions/rotate_around_axis.nf
v = input_vector("V", default=vector(1, 0, 0))
axis = input_vector("Axis", default=vector(0, 0, 1))
angle = input_float("Angle", default=0.0)
axis_n = normalize(axis)
cos_a = cos(angle)
sin_a = sin(angle)
term_a = v * cos_a
term_b = cross(axis_n, v) * sin_a
term_c = axis_n * (dot(axis_n, v) * (1.0 - cos_a))
output("Vector", term_a + term_b + term_c)
```

### Registry and module boundaries

`builtins/math.py` should keep only core math names plus `noise` and `random_value`. Remove derived-helper specs and the alias entries `lerp`, `frac`, and `sign`. `fract` remains in `_FLOAT_FUNCS_1`; `mix` remains in `_SPECS`.

`builtins/vector.py` should keep only `vector`, `length`, `distance`, `dot`, `normalize`, `cross`, `reflect`, and `project`. Remove the migrated helper names from `NAMES` and remove their direct compile branches. Private helper implementations may be deleted after tests are updated; they must not remain reachable through `registry.CALLABLE_BUILTIN_NAMES`.

`builtins/node_wrappers.py` should stop contributing public callable names. The lowest-risk implementation is to set `NAMES = set()` and remove the module from `_MODULES` in `builtins/registry.py`, or leave the module importable with empty `NAMES` while deleting/defanging public compile dispatch. The public DSL condition is that none of the compare wrapper names appear in `registry.CALLABLE_BUILTIN_NAMES` and all old global wrapper calls fail.

`constants.py` owns shared operator maps and math primitive maps. Remove only alias/core-surface drift entries from `_FLOAT_FUNCS_1`: `frac` and `sign`. Do not change `_COMPARE_OPS`, because normal DSL comparison operators still use it.

`consteval.py` is a separate compile-time evaluation boundary and must be audited in the same cutover. In the Stage 4 codebase, `_ALLOWED_MATH_FUNCS` still admits `sign(...)` for constant-evaluable expressions even though `sign` is non-core. Stage 5 must remove `sign` from `_ALLOWED_MATH_FUNCS` unless a future umbrella explicitly defines a separate compile-time helper surface. This stage does not define that exception; therefore unimported `sign(...)` must fail both in ordinary runtime expressions and in preprocessed constant contexts such as list literals or compile-time branch/loop expressions.

### Durable state, restart, and cleanup

No database, migrations, user secrets, or new startup/shutdown hooks are involved. New flat `.nf` files are discovered by `library.library_function_names()` at compile/materialization time. Generated Blender node groups use the existing function materialization contract:

```text
group name: display_name_for_function(name)
metadata: nodeforge_library_name, nodeforge_library_source, nodeforge_function_kind, nodeforge_backend_signature
reuse: source/signature equality check in get_or_create_library_group(...)
```

This stage must preserve that mechanism. Do not add new metadata, leases, retry state, or cleanup machinery. Partial compilation failures should continue to be handled by the existing `_compile_fresh_with_cleanup(...)` and `_update_existing_group_transactional(...)` paths, including rollback of generated resources. The only stale-resource risk introduced by Stage 5 is an old generated function group becoming stale when a `.nf` source changes; existing `nodeforge_library_source` comparison already covers that.

### Concurrency, duplicates, and idempotency

Function discovery is file-system based and already tolerates repeated scans. Re-running materialization for a migrated helper must reuse the same group when source metadata is unchanged and must not create duplicate groups. Adding flat files with names that previously existed only as built-ins does not create package-directory duplicates because these names are not currently present under `functions/`.

No new cross-process coordination is required. Blender add-on execution is still in-process; Stage 5 should not invent locks, leases, retry markers, or persistent migration state.

### Safety and trust boundaries

The relevant trust boundary is user-authored DSL source and repository-owned function-library source files. Stage 5 must not broaden the Python execution boundary:

- migrated helpers must be `.nf`, not `function.py`;
- no package-local backend helpers are added;
- `from functions import *` must continue to import public function-library names only, not backend helper names, hidden files, systems, or examples;
- compare-wrapper removal must not weaken raw `node(...)` validation or type-token reservation;
- unsupported old globals must fail as controlled `CompileError`, not as Python exceptions.

There are no secrets or credential-bearing configuration in this scope. Do not add secret-management code.

### Alternatives considered

1. Keep all derived helpers as globals and classify them as core.
   Rejected because the umbrella accepted core list does not include these names, and leaving them global would keep the mixed core/helper namespace that Stage 5 exists to remove.

2. Migrate every non-core name, including aliases and compare wrappers, to `functions/*.nf`.
   Rejected for `lerp` and `frac` because they are compatibility aliases for accepted core names (`mix` and `fract`). Rejected for compare wrappers because DSL comparison operators and raw `node(...)` already cover the intended behavior without preserving another public wrapper surface.

3. Introduce a generic helper-module abstraction or category imports such as `from functions.math import smoothstep`.
   Rejected because Stage 2 established flat `functions/name.nf` and the umbrella forbids category imports, `stdlib`, and a new native layer.

4. Leave Python helper implementations in `builtins/math.py`/`builtins/vector.py` and route imported functions to those private implementations.
   Rejected because migrated helpers are expressible in DSL, and routing through Python built-ins would keep two lowering paths and blur core/function ownership.

## Touched Files

```text
builtins/registry.py
builtins/math.py
builtins/vector.py
builtins/node_wrappers.py
constants.py
consteval.py
docs/ARCHITECTURE_LAYERS.md
docs/BUILTINS.md
docs/WRITING_FUNCTIONS.md
tests/unit/test_math_specs.py
tests/unit/test_raw_nodes.py
tests/unit/test_layout_builtin_names.py
tests/unit/test_consteval.py
tests/blender/test_math_compile.py
tests/blender/test_raw_nodes.py
tests/blender/test_compile_fixtures.py
tests/blender/test_import_registry.py
tests/blender/test_library_functions.py
tests/blender_refactor_regression.py
tests/COVERAGE_MAP.md
```

`tests/unit/test_layout_builtin_names.py` is touched only if it is expanded into a broader callable-surface assertion or renamed in place. It must continue proving Stage 4 layout helpers remain absent from globals.

## New Files

```text
functions/inverse_lerp.nf
functions/remap.nf
functions/saturate.nf
functions/step.nf
functions/smoothstep.nf
functions/smootherstep.nf
functions/pingpong.nf
functions/wrap.nf
functions/sign.nf
functions/rotate2d.nf
functions/polar.nf
functions/angle_between.nf
functions/rotate_around_axis.nf
plans/17_stage5_derived_helper_migration.md
```

No new Python package directories should be created for these helpers.

## Implementation Steps

1. Add the new flat `.nf` helper files using only accepted core primitives and already-imported functions where needed.
   - `remap.nf`, `smoothstep.nf`, and `smootherstep.nf` may import `inverse_lerp` and/or `saturate`.
   - Keep one output per file.
   - Keep input socket names aligned with the parameter contract above.

2. Cut over scalar built-ins and compile-time helper exposure.
   - In `constants.py`, remove `frac` and `sign` from `_FLOAT_FUNCS_1`; keep `fract`.
   - In `consteval.py`, remove `sign` from `_ALLOWED_MATH_FUNCS` so it cannot survive as an unimported compile-time-only helper.
   - In `builtins/math.py`, remove specs and compile helper functions for `lerp`, `inverse_lerp`, `remap`, `saturate`, `step`, `smoothstep`, `smootherstep`, `pingpong`, and `wrap`.
   - Keep `ln`, `clamp`, `mix`, `select`, `map_range`, `noise`, and `random_value` unchanged.

3. Cut over vector built-ins.
   - In `builtins/vector.py`, remove `_FIELD_VECTOR_NAMES` from `NAMES` and remove the call branches for `rotate2d`, `polar`, `angle_between`, and `rotate_around_axis`.
   - Delete the now-unreachable helper-lowering functions unless a test needs a private formula reference. Do not leave them reachable through `compile_call`.

4. Remove compare wrappers from the DSL callable surface.
   - Ensure `greater_than`, `greater_equal`, `less_than`, `less_equal`, `equal`, and `not_equal` are not in `registry.CALLABLE_BUILTIN_NAMES`.
   - Update or remove `node_wrappers.py` dispatch as described in the architecture section.
   - Preserve raw-node and normal comparison-operator code paths.

5. Add or update a registry inventory test.
   - Assert the final callable built-in set has no extras beyond the accepted core callable names. Account for `output` and runtime-only `range`/`runtime_range` according to the existing registry split.
   - Assert every Stage 5 migrated or removed name is absent from `registry.CALLABLE_BUILTIN_NAMES`.

6. Update docs.
   - In `docs/BUILTINS.md`, remove migrated helpers, aliases, and compare wrappers from the global built-in reference. Keep core `fract` and `mix` documented.
   - Add a section in `docs/WRITING_FUNCTIONS.md` listing the newly migrated helpers as imported functions, next to the existing layout-helper migration section.
   - Update `docs/ARCHITECTURE_LAYERS.md` so `frac`, `sign`, aliases, and compare wrappers are no longer described as current globals; mention them only as examples of names removed or moved by Stage 5 if needed.

7. Update repository scripts and fixtures.
   - Any fixture using migrated helpers must add explicit imports, preferably narrow imports unless the fixture specifically tests star import.
   - Replace `lerp(...)` with `mix(...)` and `frac(...)` with `fract(...)` if found.
   - Replace compare wrappers in tests/docs with normal comparison operators, except tests that intentionally assert old wrapper calls now fail.

8. Update Blender tests for imported behavior.
   - Add explicit import, alias import, and star import coverage for representative migrated scalar and vector helpers.
   - Add materialization/reuse checks for at least one scalar helper and one vector helper; ideally verify all new helper names appear in `library.library_function_names()` and have no native module.
   - Add negative unimported-call tests for every migrated helper and removed alias/wrapper.
   - Keep existing layout and mandelbrot/package-helper scope tests unchanged except for shared registry expectations.

9. Update raw-node tests.
   - Preserve tests for `node(...)`, type tokens, raw compare nodes, rollback, and raw-node validation.
   - Convert the old positive compare-wrapper test into either a normal comparison-operator test or a negative test proving `greater_than(...)`/`less_equal(...)` no longer compile globally.
   - Keep a negative `equal(True, False)` case or equivalent for removed wrappers.

10. Run focused and regression validation. Fix only issues inside Stage 5 scope.

## Tests

### Unit tests

Run:

```bash
python -m pytest tests/unit/test_math_specs.py tests/unit/test_raw_nodes.py tests/unit/test_layout_builtin_names.py tests/unit/test_consteval.py
```

Required assertions:

- `math.NAMES` equals the final core math set:

  ```text
  sin cos tan asin acos atan atan2 sqrt abs floor ceil round fract radians degrees exp ln log min max pow mod clamp mix select map_range noise random_value
  ```

- `_FLOAT_FUNCS_1` excludes `frac` and `sign` while still including `fract`.
- `consteval._ALLOWED_MATH_FUNCS` excludes `sign`, while still preserving accepted compile-time math primitives that remain core.
- `vector.NAMES` equals:

  ```text
  vector length distance dot normalize cross reflect project
  ```

- `registry.CALLABLE_BUILTIN_NAMES` has no Stage 5 extra names.
- Raw-node type-token and malformed-call tests still pass.

### Blender compile/materialization tests

Run at least:

```bash
python -m pytest tests/blender/test_math_compile.py \
                 tests/blender/test_library_functions.py \
                 tests/blender/test_raw_nodes.py \
                 tests/blender/test_compile_fixtures.py \
                 tests/blender/test_import_registry.py
```

Required positive fixtures:

```python
from functions import inverse_lerp, remap, saturate, step, smoothstep, smootherstep, pingpong, wrap
x = inverse_lerp(0, 10, 5)
y = remap(x, 0, 1, -1, 1)
z = smoothstep(edge0=0, edge1=1, x=saturate(y + 2))
output("Value", x + y + z + step(0.5, z) + smootherstep(0, 1, z) + pingpong(-0.25, 1) + wrap(-1, 0, 2))
```

```python
from functions import rotate2d, polar, angle_between, rotate_around_axis, sign
v = rotate2d(polar(2, 1.57079632679), 1.57079632679)
w = rotate_around_axis(vector(1, 0, 0), vector(0, 0, 1), 1.57079632679)
a = angle_between(vector(1, 0, 0), vector(0, 1, 0))
s = sign(input_float("X", default=-1.0))
output("Value", a + s)
output("Vector", v + w)
```

Required import-form fixtures:

- alias import for `smoothstep` or `rotate2d`;
- star import with at least one migrated scalar and one migrated vector helper;
- keyword call for `smoothstep(edge0=..., edge1=..., x=...)`;
- keyword call for `rotate_around_axis(v=..., axis=..., angle=...)`.

Required negative fixtures:

- unimported global calls fail for every migrated helper:

  ```text
  inverse_lerp remap saturate step smoothstep smootherstep pingpong wrap sign rotate2d polar angle_between rotate_around_axis
  ```

- removed alias/wrapper calls fail:

  ```text
  lerp frac greater_than greater_equal less_than less_equal equal not_equal
  ```

- unimported `sign(...)` also fails in a constant-evaluable/preprocessed context, not only when its argument is a runtime socket. Required fixture shape:

  ```python
  values = [sign(-1)]
  x = values[0]
  output("x", x)
  ```

  Expected result: controlled `CompileError`.

- `from functions import *` does not mutate `registry.CALLABLE_BUILTIN_NAMES`.
- imported helper names do not become group inputs.
- package-local backend helper scope for `mandelbrot` still fails from normal top-level scripts.

### Broader regression

Run the broader Blender regression set after the focused tests, because Stage 5 touches shared registry and compile fixtures:

```bash
python -m pytest tests/blender/test_layout_points.py \
                 tests/blender/test_mandelbrot_eval.py \
                 tests/blender/lsystem \
                 tests/blender_refactor_regression.py
```

If the monolithic `tests/blender_refactor_regression.py` contains duplicated old expectations, update only the expectations that correspond to Stage 5 public-surface changes. Do not use the regression file to reintroduce old global helper behavior.

### Manual validation and deployment package

During implementation, direct Blender test execution must use the already established project command path and must not depend on `blender-launcher`.

After focused and regression tests pass, package the updated add-on using the established project packaging path. The archive name must be versioned and identify Stage 5 / derived helper migration. Verify the produced archive contains every new `functions/*.nf` file added by this stage and provide the required local patch commands with the archive.

No archive is produced during this planning update; this section binds the implementation stage after project changes are made.

## Risk Dimensions and Failure Modes

### Namespace cutover risk

Adding `functions/smoothstep.nf` while `smoothstep` remains global will conflict with import validation. The final diff must remove each migrated name from globals and update tests in the same stage.

### Silent compatibility risk

`lerp`, `frac`, and compare wrappers are intentionally removed, not imported replacements. Tests must prove they fail globally; docs must point users to `mix`, `fract`, comparison operators, and `node(...)` rather than implying compatibility aliases exist.

### Compile-time surface leak risk

`consteval.py` can accept helper calls before normal runtime call resolution. Leaving `sign` in `_ALLOWED_MATH_FUNCS` would create a split public surface where `sign(input_float(...))` fails but `sign(-1)` inside a preprocessed constant expression succeeds. Stage 5 must remove that entry and test a constant-evaluable failure path.

### Formula drift risk

The migrated `.nf` formulas must match the old helper semantics where the helper is migrated. This especially matters for:

- `step(edge, x)`: returns `1` at or above the edge;
- `smoothstep`/`smootherstep`: saturate normalized `t` before polynomial evaluation;
- `pingpong`/`wrap`: double-modulo behavior keeps negative inputs positive;
- `rotate2d`: preserves original Z;
- `rotate_around_axis`: normalizes `axis` before applying Rodrigues' formula.

Tests should assert graph structure or evaluated outputs for representative scalar/vector cases rather than only checking successful compilation.

### Type and keyword behavior risk

Built-in helpers previously performed direct argument validation. Migrated helpers now rely on library input sockets and normal expression compilation. Tests must cover invalid argument types and keyword names through the library-call path so failures remain controlled `CompileError`s.

### Raw-node and comparison risk

Removing compare wrappers must not break normal comparison operators or raw `node(...)` compare construction. Tests must separate these contracts: wrapper calls fail, `a > b` still compiles, and raw `FunctionNodeCompare` through `node(...)` still validates properties/sockets and rollback behavior.

### Star import fanout risk

Adding 13 new function-library files increases `from functions import *` fanout and conflict surface. Existing Stage 2 star-import validation should catch conflicts; Stage 5 should add at least one conflict test involving a newly migrated helper name.

### Materialization and stale group risk

New function files create new materialized node groups on first use. Existing metadata should update stale groups when source changes. Tests must verify reuse does not create duplicates for representative new helpers.

### Performance risk

Migrated helpers may materialize nested function groups (`smoothstep` -> `saturate` -> `inverse_lerp`; vector helpers use several core math/vector nodes). This is acceptable for Stage 5 because the project’s imported function model already materializes reusable helpers as groups. Do not inline or special-case these helpers in the compiler for performance unless a measured regression appears during implementation.

### Documentation drift risk

`docs/BUILTINS.md` currently documents several names as globals. Leaving those docs unchanged would contradict the runtime. Stage 5 must update docs in the same stage as registry changes.

## Compatibility and Future Stage Boundary

This stage does not introduce `examples/`, does not move `dragon_curve` or `koch_curve`, does not touch `mandelbrot`, and does not reorganize `systems/`. It also does not refactor the registry/spec model beyond local deletions required to remove non-core globals.

Stage 6 may rely on the following after Stage 5:

- `functions/` import behavior remains stable;
- layout helpers and derived helpers are imported functions, not globals;
- package-local helpers remain scoped to their package;
- no new `stdlib`, `native`, category imports, export syntax, or examples import source exists yet.

## Open Questions

None. The stage can be implemented from the current codebase and umbrella contract without coordinator input.
