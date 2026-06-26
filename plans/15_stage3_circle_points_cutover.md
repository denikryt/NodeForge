# 15 — Stage 3 Circle Points Cutover Plan

## Problem / Goal

Stage 3 is the first helper cutover in the core/functions/examples separation umbrella. The current repository still exposes `circle_points` as a global callable built-in through `builtins/layout.py` and `builtins/registry.py`. The Stage 2 foundation already supports flat function-library files, explicit imports, aliases, multi-imports, and `from functions import *` as source-local callable bindings.

The goal of this stage is to move only `circle_points` from the global layout built-in layer into a reusable flat DSL function at `functions/circle_points.nf`, prove the import-based call path, and remove the global callable binding. No other layout helper moves in this stage.

The implementation must leave the repository in a working state where:

```python
from functions import circle_points
pts = circle_points(16, radius=2.0)
output("Geometry", pts)
```

compiles, while the old unimported form:

```python
pts = circle_points(16)
output("Geometry", pts)
```

fails with a controlled `CompileError` because `circle_points` is no longer core.

## Current Repository Facts

- `builtins/layout.py` owns `circle_points` inside `layout.NAMES` and dispatches it through `compile_call(...)` to `_circle_points_geometry(...)`.
- `builtins/registry.py` registers every name in `layout.NAMES` into `CALLABLE_BUILTIN_NAMES`, so `circle_points` is currently globally callable.
- `geometry.py` contains the actual Python node-construction helpers `_layout_normalized_index(...)`, `_layout_circle_geometry(...)`, and `_circle_points_geometry(...)`. `_circle_points_geometry(...)` creates `points(max(count, 0))` and then applies the same circular position formula as `layout_circle`.
- `expression_compiler.py` resolves calls in this order: callable built-ins, system constructors, local functions, package-local backend helpers, then source-local imported library functions. Therefore `circle_points` must be removed from the built-in registry before the imported `functions/circle_points.nf` version becomes authoritative.
- `library.py` discovers flat `functions/name.nf` files before legacy `functions/name/source.nf` packages for the same public name and returns public names through `library_function_names()`.
- `library_calls.py` compiles imported DSL functions by materializing their node group with `get_or_create_library_group(...)`, mapping positional arguments to group input socket order, mapping keyword arguments through normalized socket names, and requiring exactly one expression-call output.
- `select` has the project-local order `select(cond, false, true)`, and the underlying switch helper rejects mismatched branch value types. Math operations such as `count - 1` produce float values, so `circle_points.nf` must use same-type select branches.
- `compiler.py` validates import bindings against local bindings, local function names, core built-ins, systems, constants, type tokens, and package-local backend helpers. Star import expands all discovered public function-library names and fails on conflicts.
- Current docs still document `circle_points` as a point-layout built-in in `docs/BUILTINS.md`. Stage 1/2 docs already describe the target rule that migrated helpers are imported from `functions` and are not kept as global compatibility aliases.
- Current tests still assert that all layout helper names, including `circle_points`, are registered global built-ins and compile without imports. Those tests must change in this stage for `circle_points` only.

## Risk Dimensions

This stage is small, but it crosses important trust and ownership boundaries:

1. **Public namespace cutover.** `circle_points` must not be callable through both owners after stage exit. A stale entry in `layout.NAMES`, `registry.CALLABLE_BUILTIN_NAMES`, or docs/tests would mask an incomplete migration.
2. **Dispatch order.** Because built-ins are resolved before imported library bindings, leaving `circle_points` in core would make `from functions import circle_points` compile the old Python helper, not the new flat DSL function.
3. **Function-library discovery.** `functions/circle_points.nf` must be public through the existing flat-file discovery path and must not require a package directory, native `function.py`, or new import syntax.
4. **Argument and socket contract.** The function call adapter maps positional arguments to the input socket order produced by the `.nf` source. The `.nf` file must define inputs in the intended public signature order: `Count`, `Radius`, `Start Angle`, `End Angle`, `Include Endpoint`.
5. **Behavioral parity boundary.** The old Python built-in performed AST-level argument validation and compile-time negative-count rejection. A pure DSL library function cannot exactly reproduce all of that custom validation without adding new function metadata or native code. Stage 3 must not add such infrastructure. The supported Stage 3 contract is the geometry-producing behavior for valid counts and arc parameters, not byte-for-byte preservation of old Python error messages.
6. **Materialized Blender groups.** Imported `circle_points` materializes a reusable `GeometryNodeTree` and stores existing library metadata. A failed compile must keep the existing compiler cleanup path intact and must not introduce generated Blender datablocks outside the function group.
7. **Nested compilation and imports.** Library function sources compile as normal NodeForge source. `circle_points.nf` must use only core primitives, so it does not need nested imports and must not rely on future migrated helpers.
8. **Performance and fanout.** The pure DSL implementation should produce a compact node graph equivalent in shape to the old formula: one point source, index-based normalized parameter, trig nodes, vector combine, and set-position. It must not use compile-time loops or build per-point static geometry.
9. **Scope containment.** Other layout helpers remain global until Stage 4. Tests and docs must not prematurely migrate or remove `layout_circle`, `layout_spiral`, `grid_points`, `layout_grid`, `spiral_points`, `random_points`, or `layout_random`.
10. **Secrets and deployment.** This scope has no secrets, credentials, network access, database state, or deployment configuration. Do not add secret-management or deployment mechanics.

## Expected Behavior

### Supported import forms

All of these must compile:

```python
from functions import circle_points
pts = circle_points(16, radius=2.0)
output("Geometry", pts)
```

```python
from functions import circle_points as circle
pts = circle(8, start_angle=0, end_angle=pi, include_endpoint=True)
output("Geometry", pts)
```

```python
from functions import *
pts = circle_points(12)
output("Geometry", pts)
```

### Removed global behavior

This must fail with controlled `CompileError`:

```python
pts = circle_points(16)
output("Geometry", pts)
```

`circle_points` must be absent from:

```python
NodeForge.builtins.layout.NAMES
NodeForge.builtins.registry.CALLABLE_BUILTIN_NAMES
```

### Function signature

The final imported function signature is:

```text
circle_points(count, radius=1.0, start_angle=0.0, end_angle=tau, include_endpoint=False) -> Geometry
```

The `.nf` source must define input sockets in this order:

```python
count = input_int("Count", default=16)
radius = input_float("Radius", default=1.0)
start_angle = input_float("Start Angle", default=0.0)
end_angle = input_float("End Angle", default=tau)
include_endpoint = input_bool("Include Endpoint", default=False)
```

This preserves positional call behavior for valid calls because `library_calls.py` maps positional arguments by group input order and normalizes keyword names so `start_angle`, `end_angle`, and `include_endpoint` bind to the display names.

### Geometry formula

For point index `i`, count `count`, radius `radius`, start angle `start_angle`, end angle `end_angle`, and bool `include_endpoint`:

```python
count_value = count * 1.0
pts = points(max(count_value, 0.0))
raw_denominator = select(include_endpoint, count_value, count_value - 1.0)
denominator = max(raw_denominator, 1.0)
t = index() / denominator
angle = start_angle + (end_angle - start_angle) * t
position = vector(cos(angle) * radius, sin(angle) * radius, 0)
pts = set_position(pts, position)
output("Geometry", pts)
```

This formula relies on the actual NodeForge `select(cond, false, true)` contract. The two branch values must have the same NodeForge value type, so `count` is first converted into a float-valued expression with `count * 1.0`. The required branch behavior is:

```text
include_endpoint=False -> raw_denominator = count
include_endpoint=True  -> raw_denominator = count - 1
```

`denominator = max(raw_denominator, 1.0)` prevents division by zero for `count <= 1` when endpoint mode is enabled.

The old Python helper used separate compile-time and runtime validation paths. The pure DSL function should document and test the supported final behavior: valid non-negative counts produce the intended circle or arc, `include_endpoint=True` uses `count - 1` with a minimum denominator of `1.0`, and count values below zero produce no points through `points(max(count_value, 0.0))`. Do not add new compiler validation infrastructure just to preserve the old global helper's compile-time negative-count error.

## Architecture

### Chosen option

Use a flat pure DSL function file:

```text
functions/circle_points.nf
```

The file owns only the reusable `circle_points` algorithm. It uses existing core primitives: `input_int`, `input_float`, `input_bool`, `points`, `index`, `select`, `max`, `cos`, `sin`, `vector`, `set_position`, and `output`.

`builtins/layout.py` remains the owner for all other layout helpers during Stage 3. It must no longer list or dispatch `circle_points`. The low-level Python helper `_circle_points_geometry(...)` in `geometry.py` may remain temporarily as dead internal code because Stage 4 still owns broader layout cleanup; removing it is optional only if it has no other references and does not force unrelated edits. The public registry membership is the authoritative exit condition.

### Rejected alternatives

1. **Keep a compatibility global alias.** Rejected because the umbrella explicitly forbids global compatibility aliases for migrated helpers. It would also hide import regressions due to dispatch order.
2. **Implement `circle_points` as a package with `function.py`.** Rejected because Stage 3's target is a flat pure DSL reusable helper. A native package would preserve Python validation but violate the migration direction and broaden the package-local backend model unnecessarily.
3. **Add generic function signature metadata or validators.** Rejected for this stage because it is infrastructure beyond the first-helper cutover. The existing function group socket contract is sufficient for valid calls.
4. **Move `layout_circle` at the same time.** Rejected because Stage 3 exists specifically to validate one small helper migration before the Stage 4 layout batch. `layout_circle` also has omitted-count behavior that requires a separate Stage 4 signature decision.
5. **Add a new core point-count primitive.** Rejected because `circle_points` does not need to derive count from existing geometry. The point-count issue belongs to Stage 4 for `layout_circle` and `layout_spiral`.

### Boundaries and ownership

- `functions/circle_points.nf` owns the public imported function source and user-facing helper behavior.
- `library.py`, `library_calls.py`, `compiler.py`, and `parsing.py` are not expected to need production changes; Stage 2 already owns the import foundation. Tests may exercise them.
- `builtins/layout.py` owns removal of the global callable name for this stage.
- `builtins/registry.py` should update automatically through `layout.NAMES`. Direct edits are only needed if a static list or test fixture is found during implementation.
- `docs/BUILTINS.md`, `docs/WRITING_FUNCTIONS.md`, and possibly `README.md` own user-facing examples affected by the moved name.
- Blender group materialization remains owned by the existing library-function path. Stage 3 must not introduce new metadata keys, new group naming rules, examples metadata, or generated resource ownership.

### Transaction, cleanup, restart, duplicate, and concurrency behavior

This stage does not introduce a new transaction owner. Imported `circle_points` uses the existing `get_or_create_library_group(...)` path:

- source text is read from `functions/circle_points.nf`;
- a `GeometryNodeTree` named through the existing function display-name mechanism is created or reused;
- `nodeforge_library_name`, `nodeforge_library_source`, `nodeforge_function_kind`, and `nodeforge_backend_signature` metadata are stored;
- if compilation fails, `_build_group(...)` removes the partially created group;
- existing-group transactional replacement behavior in `compiler.py` remains unchanged.

Duplicate operations should be idempotent at the existing function-group layer: compiling the same imported call repeatedly must reuse the materialized library group when source and backend signature metadata match, and must not create duplicate function groups. Stage 3 tests should assert this for `circle_points` if an existing reusable function reuse test does not cover the new flat function.

Concurrency is limited by Blender's single-process Python execution model used by the add-on and test runner. This stage must not add shared mutable global state. `from functions import *` must remain source-local and must not mutate `registry.CALLABLE_BUILTIN_NAMES`.

Restart behavior is file-based: after Blender or the add-on restarts, `library_function_names()` rediscoveres `functions/circle_points.nf` from disk, and generated function groups are recreated or refreshed from stored source metadata as existing library groups are today. No migration file or database schema is involved.

## Touched Files

```text
builtins/layout.py
builtins/registry.py
docs/BUILTINS.md
docs/WRITING_FUNCTIONS.md
README.md
tests/blender/test_layout_points.py
tests/blender/test_library_functions.py
tests/blender_refactor_regression.py
tests/unit/test_layout_builtin_names.py
tests/COVERAGE_MAP.md
```

`builtins/registry.py`, `README.md`, `docs/WRITING_FUNCTIONS.md`, `tests/blender/test_library_functions.py`, `tests/blender_refactor_regression.py`, and `tests/COVERAGE_MAP.md` are inspection/conditional edit targets. Edit them only if the implementation or stale documentation/tests require it.

## New Files

```text
functions/circle_points.nf
plans/15_stage3_circle_points_cutover.md
```

No package directory, `function.py`, migration file, generated artifact, or review bundle belongs to this planning stage.

## Implementation Steps

1. Confirm the Stage 2 entry state before production edits:
   - `docs/ARCHITECTURE_LAYERS.md` exists.
   - `from functions import *` tests exist and pass in the Stage 2 suite.
   - `circle_points` is still present in `builtins/layout.py` and `registry.CALLABLE_BUILTIN_NAMES` before the cutover.

2. Add `functions/circle_points.nf` as a flat pure DSL function. Use the exact socket order defined in **Function signature**. Implement the formula defined in **Geometry formula**. Do not import from `functions` inside this file.

3. Add or update the imported-behavior tests before production edits if following test-first workflow, but expect those positive import tests to fail until the built-in name is removed. In the actual compiler, import validation rejects function imports whose exposed name conflicts with a built-in, and expression dispatch checks built-ins before imported library calls. Do not weaken import validation or dispatch ordering to make temporary pre-removal imports pass.

4. Remove `circle_points` from `builtins/layout.py` in the same cutover slice that adds the flat function:
   - remove it from `NAMES`;
   - remove the `if name == "circle_points"` dispatch branch;
   - leave shared helper functions used by other layout built-ins intact.

5. After the built-in removal, run the imported behavior tests and add the final cutover tests:
   - explicit import compiles;
   - alias import compiles;
   - star import compiles;
   - the compiled node graph contains a `GeometryNodeGroup` call to the materialized `circle_points` library group when called through import, not direct inline built-in layout nodes;
   - unimported `circle_points(...)` fails with `CompileError`;
   - `circle_points` is absent from `layout.NAMES`;
   - `circle_points` is absent from `registry.CALLABLE_BUILTIN_NAMES`;
   - star import still does not mutate `registry.CALLABLE_BUILTIN_NAMES`;
   - `layout_circle`, `layout_spiral`, `grid_points`, `layout_grid`, `spiral_points`, `random_points`, and `layout_random` still compile globally.

6. Update layout behavior tests:
   - split `test_circle_spiral_and_random_points_compile` or equivalent so `circle_points` uses `from functions import circle_points`, while `spiral_points` and `random_points` remain global in Stage 3;
   - retain graph/geometry assertions that prove circle and arc behavior;
   - update controlled-error fixtures so old `circle_points(...)` global-call errors are separate from old argument-validation expectations.

7. Add a Blender evaluation test for the imported function's geometry:
   - `circle_points(4, radius=1.0)` should produce four points approximately at `(1,0,0)`, `(0,1,0)`, `(-1,0,0)`, `(0,-1,0)` for the default full-circle non-endpoint case;
   - `circle_points(3, start_angle=0, end_angle=pi, include_endpoint=True)` should include both arc endpoints approximately `(1,0,0)` and `(-1,0,0)`;
   - count `0` should produce zero vertices;
   - negative runtime/default count behavior should match the documented Stage 3 contract if it is tested.

8. Update docs:
   - remove `circle_points` from the built-in point-layout section in `docs/BUILTINS.md`;
   - add or update reusable function documentation showing `from functions import circle_points` and the supported signature;
   - ensure quick authoring examples mention `from functions import *` only as a convenience, not a global namespace change;
   - remove or update any README example that implies `circle_points` is global.

9. Run targeted checks, then broad checks as described in **Tests**. Fix only Stage 3 regressions. Do not migrate or redesign other helpers.

10. Before handoff, inspect the final diff and verify:
    - production code changes are limited to the cutover and docs/tests;
    - no `examples/` source/import behavior was introduced;
    - no package-local backend or new native layer was introduced;
    - no compatibility alias or fallback lookup exists;
    - no review bundle or final commit is created unless a later instruction asks for one.

## Tests

### Unit/static tests

Update or add unit tests for namespace membership:

```python
assert "circle_points" not in layout.NAMES
assert "circle_points" not in registry.CALLABLE_BUILTIN_NAMES
assert {"layout_grid", "grid_points", "layout_circle", "layout_spiral", "spiral_points", "layout_random", "random_points"}.issubset(registry.CALLABLE_BUILTIN_NAMES)
```

Add or update library discovery assertions:

```python
assert library.has_library_function("circle_points")
assert "circle_points" in library.library_function_names()
assert not library.has_module_library_function("circle_points")
```

### Blender compile tests

Positive compile tests:

```python
compile_group('''
from functions import circle_points
pts = circle_points(16, radius=1.0)
output("Geometry", pts)
''', 'NFTest_circle_points_import')
```

```python
compile_group('''
from functions import circle_points as circle
pts = circle(8, start_angle=0, end_angle=pi, include_endpoint=True)
output("Geometry", pts)
''', 'NFTest_circle_points_alias_import')
```

```python
compile_group('''
from functions import *
pts = circle_points(12)
output("Geometry", pts)
''', 'NFTest_circle_points_star_import')
```

Negative compile tests:

```python
expect_compile_error('''
pts = circle_points(16)
output("Geometry", pts)
''', 'NFTest_circle_points_unimported_fails')
```

```python
expect_compile_error('''
from functions import *
circle_points = 1
output("x", circle_points)
''', 'NFTest_circle_points_star_assignment_conflict')
```

The second test is only needed if existing star-import conflict fixtures do not cover a newly added `circle_points` public library name.

### Geometry/evaluation tests

Add Blender runtime evaluation for imported `circle_points`:

- default full circle with `count=4`, `radius=1.0`;
- endpoint arc with `count=3`, `start_angle=0`, `end_angle=pi`, `include_endpoint=True`;
- zero-count case.

Use existing `_evaluated_vertices(...)` and `_same_positions(...)` helpers in `tests/blender/test_layout_points.py` or move reusable helpers only if necessary.

### Regression tests

Run focused tests:

```text
pytest tests/unit/test_layout_builtin_names.py
pytest tests/unit/test_function_import_parsing.py
```

Run Blender-focused tests for affected paths:

```text
tests/blender/test_layout_points.py
tests/blender/test_library_functions.py
tests/blender/test_import_registry.py
```

Run broader regression coverage used by the project for shared compiler/import/discovery behavior:

```text
tests/blender_refactor_regression.py
tests/blender/test_compile_fixtures.py
tests/blender/test_mandelbrot_eval.py
```

If the local environment cannot run Blender tests, record that explicitly in the implementation handoff; do not replace Blender runtime tests with only static checks.

## Validation Steps

1. Inspect `registry.CALLABLE_BUILTIN_NAMES` at runtime and confirm `circle_points` is absent while untouched Stage 4 layout helpers remain present.
2. Inspect `library.library_function_names()` and confirm `circle_points` is present as a public function-library name.
3. Compile explicit, alias, and star-import scripts and verify each produces a live `GeometryNodeTree`.
4. Compile the old unimported script and verify it raises `CompileError`, not `NameError`, `KeyError`, or an uncontrolled Python exception.
5. Compile an imported call twice and confirm the materialized function group is reused rather than duplicated when source metadata has not changed.
6. Evaluate simple circle/arc outputs and compare vertex positions within the tolerance already used by layout tests.
7. Search docs/tests for `circle_points(` and classify every remaining occurrence as either imported usage, negative unimported regression, or historical architecture text.

## Durable State, Format, Migration, and Compatibility

No database migration, file-format migration, or persistent schema migration is required.

The only new durable project-owned file is `functions/circle_points.nf`. Blender node groups generated from it use the existing function-library metadata keys and existing group naming behavior. Stage 3 must not create new metadata keys because examples materialization and function/example collision policy belong to Stage 6.

Compatibility is intentionally a cutover, not a deprecation period. Existing user scripts that call `circle_points(...)` without an import are expected to fail after this stage. The docs and tests must reflect that break.

## Runtime / Deployment Consequences

No add-on registration, UI panel, deployment configuration, environment variable, network path, or packaging behavior changes are expected.

Packaging must include `functions/circle_points.nf` with the add-on in the same way existing `functions/*.nf` or package sources are included. If the packaging mechanism enumerates function files explicitly, update it in this stage. If packaging already includes the full `functions/` tree, no packaging code edit is needed, but the implementation handoff should state that this was checked.

## Regression and Blind-Spot Analysis

- **False positive imports.** Imported `circle_points` may appear to work while actually dispatching to the old built-in if the registry removal is missed. Tests must inspect both namespace membership and graph/materialized function-group behavior.
- **Stale docs.** `docs/BUILTINS.md` currently presents `circle_points` beside global layout helpers. Leaving that section unchanged would contradict runtime behavior.
- **Stale test fixtures.** Existing layout tests compile `circle_points` without import. Updating only one test can leave other fixtures masking the old contract.
- **Signature order drift.** Reordering input declarations in `circle_points.nf` would break positional calls because `library_calls.py` maps them by socket order.
- **Keyword normalization mismatch.** The docs and tests should use `start_angle`, `end_angle`, and `include_endpoint` to verify normalization from Python-style keyword names to display socket names.
- **Validation mismatch.** The old built-in rejected compile-time negative counts. The pure DSL function should not grow native infrastructure to preserve that exact error. Docs/tests must define the new supported behavior instead of leaving a stale negative-count error fixture.
- **Premature Stage 4 work.** Touching `layout_circle` or other helpers now can accidentally import Stage 4's unresolved count-derivation decision into Stage 3.
- **Backend helper leakage.** `circle_points` must not add a `function.py`; package-local backend helper scope tests for `mandelbrot` must continue to pass.
- **Materialized group staleness.** Reusing an old generated group after editing `circle_points.nf` would indicate metadata comparison failure. Existing source metadata tests should cover this path; add a focused check if not.
- **Star-import conflict expansion.** Adding `circle_points` to `library_function_names()` means `from functions import *` now imports one more name. Existing conflict tests may start failing if a fixture locally binds `circle_points`; that should be resolved by making the fixture explicit about expected conflict behavior, not by skipping the function in star import.

## Open Questions

None blocking.

The only material behavior decision is settled in this plan: Stage 3 implements `circle_points` as a pure DSL function and does not preserve the old Python built-in's compile-time negative-count validation. This is acceptable because the umbrella requires no compatibility alias and forbids adding unnecessary infrastructure during the first helper cutover.
