# Optional circular layout count — Stage Plan

## Problem / Goal

`layout_circle(...)` and `layout_spiral(...)` currently require users to repeat the same total point count that usually created the input point geometry:

```python
pts = points(count)
pts = layout_circle(pts, count=count, radius=radius)
```

The stage goal is to make `count` optional for those two existing layout helpers, while preserving explicit `count=...` as an override:

```python
pts = points(count)
pts = layout_circle(pts, radius=radius)
pts = layout_spiral(pts, radius=radius, turns=turns)
```

The implementation must update user documentation for the new public API. It must not change `layout_grid(...)`, `grid_points(...)`, `circle_points(...)`, `spiral_points(...)`, `layout_random(...)`, or `random_points(...)` semantics.

## Expected Behavior

`layout_circle(geometry, count=None, radius=1.0, start_angle=0.0, end_angle=tau, include_endpoint=False)` derives its count from the Point-domain size of `geometry` when no `count` argument is supplied.

`layout_spiral(geometry, count=None, radius=1.0, turns=1.0, height=0.0, start_radius=0.0, start_angle=0.0)` derives its count from the Point-domain size of `geometry` when no `count` argument is supplied.

Existing explicit-count forms remain valid and keep their current interpretation:

```python
pts = points(16)
geo = layout_circle(pts, count=16, radius=2.0)
geo = layout_circle(pts, 16, radius=2.0)
geo = layout_spiral(pts, count=input_int("Count", default=16), radius=2.0)
```

The second positional argument remains `count` for backward compatibility. `layout_circle(pts, 2.0)` must continue to mean `count=2.0`, not `radius=2.0`. Users who omit `count` and set non-default radius must use keyword arguments:

```python
pts = layout_circle(pts, radius=2.0)
```

Derived count is the runtime Point-domain count reported by Blender for the supplied geometry. For the main intended input, `points(n)`, this equals `n`. For other geometry values, the derived value is still the geometry Point-domain size. NodeForge currently has only a generic `Geometry` type and no separate `PointGeometry` provenance type, so this stage must not invent stricter semantic typing.

Zero-point geometry remains valid. The existing denominator guard in `_layout_normalized_index(...)` keeps division safe by using at least `1`. Compile-time negative explicit counts remain rejected by `_compile_scalar_count(..., reject_negative=True)`. Runtime explicit counts keep current behavior; this stage does not add runtime validation or clamping beyond the existing formula.

`layout_spiral(...)` endpoint semantics must not change. The current implementation calls `_layout_normalized_index(group, count, True, ...)`, so the denominator branch uses `count - 1` guarded by `MAXIMUM(..., 1)`. With `count=n`, the last point reaches `t=1.0` for `n > 1`. This must remain true for both explicit-count and derived-count paths.

`layout_grid(...)` remains unchanged because its `count` is a dimension vector `(count_x, count_y, count_z)`, not just the total point count. `layout_random(...)` remains unchanged because it does not need a count to compute positions.

## Architecture

### Current code path

The public layout built-ins are dispatched by `builtins/registry.py` into `builtins/layout.py`. `builtins/layout.py` owns AST argument binding, compile-time validation, and conversion from AST expressions to `Value` or constants. Low-level Geometry Nodes graph construction lives in root `geometry.py`.

Current circular layout flow:

```text
builtins/layout.py
  compile_call(... name == "layout_circle")
    _bind_args(... params include required "count")
    _compile_value(... geometry)
    _compile_scalar_count(... count)
    _compile_numeric(... radius/start/end)
    _compile_bool_const(... include_endpoint)
    _layout_circle_geometry(...)

geometry.py
  _layout_circle_geometry(... count ...)
    _layout_normalized_index(group, count, include_endpoint, ...)
    _set_position_geometry(...)
```

Current spiral layout flow:

```text
builtins/layout.py
  compile_call(... name == "layout_spiral")
    _bind_args(... params include required "count")
    _compile_value(... geometry)
    _compile_scalar_count(... count)
    _compile_numeric(... radius/turns/height/start_radius/start_angle)
    _layout_spiral_geometry(...)

geometry.py
  _layout_spiral_geometry(... count ...)
    _layout_normalized_index(group, count, True, ...)
    _set_position_geometry(...)
```

The `True` in `_layout_spiral_geometry(...)` is a compatibility contract for this stage: spiral uses endpoint normalization and must continue to use the `count - 1` denominator branch.

### Chosen design

Make `count` optional at the AST binding layer by giving `layout_circle(...)` and `layout_spiral(...)` a `count=None` default. Keep parameter order unchanged:

```python
("geometry", "count", "radius", "start_angle", "end_angle", "include_endpoint")
("geometry", "count", "radius", "turns", "height", "start_radius", "start_angle")
```

This preserves the existing positional contract. The only new call shape is omission of `count`, normally with layout parameters supplied by keyword.

Add a low-level helper in root `geometry.py` that derives a point count from a `Geometry` value using Blender's Domain Size node. The helper must convert node and socket compatibility failures into `CompileError`, because `_new_node(...)` in `nodes.py` currently delegates directly to Blender's `group.nodes.new(...)` and does not wrap Blender RNA failures.

Use a small socket lookup helper local to `geometry.py`:

```python
def _socket_by_name_or_compile_error(sockets, name, label):
    try:
        return sockets[name]
    except Exception as exc:
        raise CompileError(f"{label}: Blender node missing {name!r} socket") from exc
```

Then use it inside point-count derivation:

```python
def _geometry_point_count(group, geo, label, x=0, y=0):
    if geo.typ != TYPE_GEOMETRY:
        raise CompileError(f"{label}: count derivation expects Geometry")
    try:
        node = _new_node(group, "GeometryNodeAttributeDomainSize", x, y)
    except Exception as exc:
        raise CompileError(f"{label}: Blender Domain Size node is unavailable") from exc
    geometry_input = _socket_by_name_or_compile_error(node.inputs, "Geometry", label)
    point_count = _socket_by_name_or_compile_error(node.outputs, "Point Count", label)
    group.links.new(geo.socket, geometry_input)
    return Value(point_count, TYPE_INT)
```

Use output lookup by socket name rather than numeric index. This mirrors the public node contract and reduces breakage if Blender changes output ordering while preserving the `Point Count` socket. The accepted architecture recommendation is to make the socket failure controlled here instead of relying on raw `KeyError`, `RuntimeError`, or Blender RNA exceptions.

The Blender manual describes the Domain Size node as outputting attribute-domain sizes, including point-domain size / point count for geometry. Treat this as Blender-specific behavior, not a generic protocol guarantee. The implementation must still verify the concrete Python `bl_idname` and socket names in the supported Blender runtime during Blender tests, because Python RNA identifiers and socket labels are version-specific Blender API details.

Add a resolver helper near `_layout_normalized_index(...)`:

```python
def _resolve_layout_count(group, geo, count, label, x=0, y=0):
    if count is None:
        return _geometry_point_count(group, geo, label, x, y)
    return count
```

Then update `_layout_circle_geometry(...)` and `_layout_spiral_geometry(...)` to call the resolver before `_layout_normalized_index(...)`. Count derivation belongs in `geometry.py`, not `builtins/layout.py`, because it is a Geometry Nodes graph-construction concern. `builtins/layout.py` should only decide whether the user supplied a count expression and compile that expression when present.

The required spiral implementation shape is:

```python
count = _resolve_layout_count(group, geo, count, "layout_spiral() count", x + 20, y - 40)
t = _layout_normalized_index(group, count, True, x + 20, y - 80)
```

The required circle implementation shape is:

```python
count = _resolve_layout_count(group, geo, count, "layout_circle() count", x + 20, y - 40)
t = _layout_normalized_index(group, count, include_endpoint, x + 20, y - 80)
```

### Alternatives considered

Alternative: infer the original argument passed to `points(count)` by tracking provenance on `Value` objects. This was rejected because `Value` currently carries only `socket` and semantic `typ`; adding layout-specific provenance would create a new hidden type contract and would fail through transformations, joins, loaded library functions, and arbitrary geometry expressions.

Alternative: change the positional signature so `layout_circle(pts, 2.0)` means `radius=2.0` once count is optional. This was rejected because it breaks existing scripts that pass count positionally.

Alternative: add a generic `point_count(geometry)` public built-in first and use it internally. This was rejected for the current stage because the requested API does not require a new public built-in. A future public count/query built-in should be designed separately with complete documentation for domains and geometry types.

Alternative: derive count only when the input expression is syntactically `points(count)`. This was rejected because it would fail for common compositions such as `layout_circle(transform(points(n), ...), radius=...)` even though Blender can compute the Point-domain size at runtime.

Alternative: skip the socket helper and use `node.inputs["Geometry"]` / `node.outputs["Point Count"]` directly. This was rejected after review because raw Blender socket lookup failures would bypass the controlled compatibility failure promised by the plan.

## Boundaries and Ownership

`builtins/layout.py` owns the public callable signatures, keyword validation, and explicit-count validation. It should treat omitted `count` as `None` only for `layout_circle(...)` and `layout_spiral(...)`.

Root `geometry.py` owns all new node graph construction for derived point count. `_socket_by_name_or_compile_error(...)`, `_geometry_point_count(...)`, and `_resolve_layout_count(...)` should live there because they operate on compiled `Geometry` values and Blender sockets.

`docs/BUILTINS.md` owns the user-facing API reference and examples.

Tests under `tests/blender/` own Blender-node integration checks. Unit tests under `tests/unit/` should cover pure Python argument binding and explicit-count validation where possible, but should not import or require `bpy`.

No migration, persistent data model, generated resource ownership, startup hook, shutdown hook, file storage, authorization, public endpoint, pagination, fanout, retention, background retry, or deployment secret handling is involved in this stage.

## Failure Behavior

Omitted `count` with non-Geometry input still fails through the existing geometry validation path before or inside `_layout_circle_geometry(...)` / `_layout_spiral_geometry(...)` with a controlled `CompileError`.

Explicit invalid count keeps current behavior:

```python
layout_circle(points(4), count=-1)   # CompileError
layout_spiral(points(4), count=-1)   # CompileError
layout_circle(points(4), count=vector(1, 2, 3))  # CompileError
```

Unsupported keywords keep current `_check_no_extra_keywords(...)` behavior through `_bind_args(...)`.

If the target Blender runtime cannot create `GeometryNodeAttributeDomainSize`, or if the node lacks `Geometry` / `Point Count` sockets, compilation should raise a controlled `CompileError` that names the affected count derivation label. This is a version/runtime compatibility failure, not a silent fallback to a wrong count.

Concurrent or duplicate compile operations are unchanged. NodeForge compilation is synchronous in Blender's Python context and this stage adds only ordinary nodes inside the current `GeometryNodeTree`.

## Persistence, Migration, Compatibility, Security, Deployment

There is no database, migration, durable resource manifest, generated Blender ID ownership, or compatibility schema to update.

The public API change is backward-compatible for existing valid scripts because explicit `count` remains accepted by keyword and position. The only behavioral expansion is that two previously invalid calls now compile:

```python
layout_circle(points(8), radius=1.0)
layout_spiral(points(8), radius=1.0, turns=2.0)
```

The main compatibility risk is accidentally reinterpreting the second positional argument as `radius`. The plan explicitly preserves parameter order to avoid that.

A second compatibility risk is changing spiral endpoint behavior. The implementation must keep `_layout_spiral_geometry(...)` on `_layout_normalized_index(..., True, ...)` for both explicit and derived counts.

There are no secrets. The feature does not add file I/O, external process execution, network access, public endpoints, authorization checks, or user-controlled paths. User input is ordinary DSL source that already flows through AST parsing and controlled built-in dispatch.

Deployment consequences are limited to packaging the updated add-on after implementation, following the existing project patch-delivery rule. This planning stage does not package anything.

## Touched Files

builtins/layout.py
geometry.py
docs/BUILTINS.md
tests/blender/test_layout_points.py
tests/unit/test_layout_grid_validation.py

## New Files

plans/13_optional_circular_layout_count.md

## Implementation Steps

1. Update `builtins/layout.py` for `layout_circle(...)`.
   - Keep the existing parameter tuple order.
   - Add `"count": None` to the defaults dictionary.
   - Compile `args["count"]` only when it is not `None`:

```python
count_expr = args["count"]
count = None if count_expr is None else _compile_scalar_count(
    comp,
    count_expr,
    "layout_circle() count",
    reject_negative=True,
)
```

2. Update `builtins/layout.py` for `layout_spiral(...)` using the same optional-count pattern.
   - Keep the existing parameter tuple order.
   - Do not modify `circle_points(...)` or `spiral_points(...)`; their `count` remains required because they create geometry.

3. Add `_socket_by_name_or_compile_error(...)` in root `geometry.py` near other private low-level helpers.
   - Keep it private.
   - Use it only for this new compatibility-sensitive Blender node/socket lookup unless another nearby helper already needs identical behavior.
   - Raise `CompileError` with the passed label when a socket is absent.

4. Add `_geometry_point_count(...)` in root `geometry.py`.
   - Validate `geo.typ == TYPE_GEOMETRY`.
   - Create `GeometryNodeAttributeDomainSize` inside a `try` block and convert failures to `CompileError`.
   - Resolve the `Geometry` input and `Point Count` output through `_socket_by_name_or_compile_error(...)`.
   - Link `geo.socket` to the node's `Geometry` input.
   - Return `Value(point_count, TYPE_INT)`.

5. Add `_resolve_layout_count(...)` in root `geometry.py` near `_layout_normalized_index(...)`.
   - If `count is None`, call `_geometry_point_count(group, geo, label, ...)`.
   - Otherwise return the existing explicit count object unchanged.

6. Update `_layout_circle_geometry(...)`.
   - Resolve the count before `_layout_normalized_index(...)`:

```python
count = _resolve_layout_count(group, geo, count, "layout_circle() count", x + 20, y - 40)
t = _layout_normalized_index(group, count, include_endpoint, x + 20, y - 80)
```

   - Adjust node coordinates enough to avoid fully overlapping the derived-count node and formula nodes. Exact coordinates are not public behavior.

7. Update `_layout_spiral_geometry(...)`.
   - Resolve count before normalized-index construction.
   - Preserve endpoint normalization exactly:

```python
count = _resolve_layout_count(group, geo, count, "layout_spiral() count", x + 20, y - 40)
t = _layout_normalized_index(group, count, True, x + 20, y - 80)
```

   - Do not replace `True` with `False`; that would change existing explicit-count behavior.

8. Update `docs/BUILTINS.md`.
   - Change the `layout_circle` heading to show optional `count` while preserving parameter order.
   - Replace the sentence that says count is explicit because this stage does not infer geometry domain size.
   - Add an example with omitted count:

```python
pts = points(16)
pts = layout_circle(pts, radius=2.0)
```

   - State that `count=...` remains available as an override.
   - Update `layout_spiral` in the same style.
   - State that spiral keeps endpoint behavior: the last point reaches the final radius/height when there is more than one point.
   - Leave `layout_grid` documentation unchanged except, if helpful, one sentence saying grid count remains dimensional.

9. Do not update production version or package in this planning stage. The later implementation patch must bump the add-on version and package according to the project delivery rule.

## Tests

### Unit tests

Add pure unit coverage in `tests/unit/test_layout_grid_validation.py` only if it remains `bpy`-free. A new unit test file such as `tests/unit/test_layout_arg_binding.py` would be cleaner, but this plan does not require that optional file-organization change.

Recommended unit tests:

```python
def test_layout_circle_bind_args_allows_omitted_count():
    expr = ast.parse("layout_circle(points(4), radius=2.0)", mode="eval").body
    args = layout._bind_args(
        expr,
        "layout_circle",
        ("geometry", "count", "radius", "start_angle", "end_angle", "include_endpoint"),
        {"count": None, "radius": 1.0, "start_angle": 0.0, "end_angle": 6.283185307179586, "include_endpoint": False},
    )
    assert args["count"] is None
    assert args["radius"] is not None
```

Add the equivalent test for `layout_spiral(...)` and a backward-compatibility unit assertion that `layout_circle(points(4), 4, radius=2.0)` binds the second positional argument to `count`.

### Blender integration tests

Update `tests/blender/test_layout_points.py` with focused compile tests that prove `Point Count` is used, not merely that a Domain Size node exists.

Add graph traversal helpers local to the test file if no existing helper already covers this:

```python
def _socket_reaches_node_output(source_socket, target_node, target_input_name="Position"):
    target_socket = target_node.inputs[target_input_name]
    seen_nodes = set()
    stack = [target_socket]
    while stack:
        socket = stack.pop()
        for link in getattr(socket, "links", []):
            if link.from_socket == source_socket:
                return True
            from_node = link.from_node
            if from_node in seen_nodes:
                continue
            seen_nodes.add(from_node)
            stack.extend(from_node.inputs)
    return False
```

If Blender's socket traversal API does not expose `target_socket.links` for input sockets as expected, implement the equivalent traversal over `group.links`: start from `GeometryNodeSetPosition.inputs["Position"]`, follow inbound links to each upstream node input, and stop when `Domain Size.outputs["Point Count"]` is reached.

Required derived-count assertion for circle:

```python
def test_layout_circle_derives_count_from_input_points():
    source = """
pts = points(16)
pts = layout_circle(pts, radius=2.0)
output("Geometry", pts)
"""
    group = compile_group(source, "NFTest_layout_circle_derived_count")
    domains = _nodes(group, "GeometryNodeAttributeDomainSize")
    check(domains, "expected Domain Size for derived count")
    domain = domains[0]
    point_count = domain.outputs["Point Count"]
    check(point_count.links, "derived Point Count must feed layout formula")
    check(
        any(getattr(link.to_node, "bl_idname", "") == "ShaderNodeMath" for link in point_count.links),
        "Point Count should feed normalized-index math",
    )
    set_positions = _nodes(group, "GeometryNodeSetPosition")
    check(set_positions, "expected Set Position")
    check(
        _socket_reaches_node_output(point_count, set_positions[0], "Position"),
        "derived Point Count must be upstream of Set Position.Position",
    )
```

Required derived-count assertion for spiral:

```python
def test_layout_spiral_derives_count_from_input_points_with_endpoint_denominator():
    source = """
pts = points(16)
pts = layout_spiral(pts, radius=2.0, turns=3.0, height=1.0)
output("Geometry", pts)
"""
    group = compile_group(source, "NFTest_layout_spiral_derived_count")
    domains = _nodes(group, "GeometryNodeAttributeDomainSize")
    check(domains, "expected Domain Size for derived count")
    domain = domains[0]
    point_count = domain.outputs["Point Count"]
    check(point_count.links, "derived Point Count must feed spiral formula")
    check(
        _socket_reaches_node_output(point_count, _nodes(group, "GeometryNodeSetPosition")[0], "Position"),
        "derived Point Count must be upstream of Set Position.Position",
    )
    check(
        _has_math_operation_from_socket(point_count, "SUBTRACT"),
        "spiral derived count must use count - 1 endpoint denominator branch",
    )
```

The helper `_has_math_operation_from_socket(point_count, "SUBTRACT")` should inspect outgoing links from `point_count`, traverse downstream math nodes, and return true when a `ShaderNodeMath` with `operation == "SUBTRACT"` is on the path before the denominator `MAXIMUM` / `DIVIDE` chain. The assertion is intentionally graph-level because the stage must preserve the current `_layout_normalized_index(..., True, ...)` endpoint behavior without requiring rendered coordinate sampling.

Add one explicit override regression:

```python
def test_layout_circle_explicit_count_does_not_insert_domain_size():
    source = """
pts = points(16)
pts = layout_circle(pts, count=8, radius=2.0)
output("Geometry", pts)
"""
    group = compile_group(source, "NFTest_layout_circle_explicit_count_override")
    check(not _nodes(group, "GeometryNodeAttributeDomainSize"), "explicit count should not need Domain Size")
```

Add a spiral endpoint regression for the explicit-count path:

```python
def test_layout_spiral_explicit_count_keeps_endpoint_denominator():
    source = """
pts = points(5)
pts = layout_spiral(pts, count=5, radius=2.0, height=1.0)
output("Geometry", pts)
"""
    group = compile_group(source, "NFTest_layout_spiral_explicit_endpoint")
    count_value_socket = _find_constant_or_value_socket(group, 5)
    check(
        _has_math_operation_from_socket(count_value_socket, "SUBTRACT"),
        "spiral explicit count must keep count - 1 endpoint denominator branch",
    )
```

If `_find_constant_or_value_socket(...)` is brittle in the current test helpers, replace this with a narrower assertion on the normalized-index math chain shape: there must be a `ShaderNodeMath` with `operation == "SUBTRACT"` whose output feeds a `ShaderNodeMath` `MAXIMUM`, and that `MAXIMUM` output feeds a `ShaderNodeMath` `DIVIDE` used upstream of `Set Position.Position`. This protects endpoint semantics without depending on node labels.

Keep existing controlled-error cases for negative explicit count:

```python
layout_circle(points(4), count=-1)
layout_spiral(points(4), count=-1)
```

Add a controlled-error case proving `layout_grid(points(3), spacing=1.0)` still fails for missing `count`.

### Validation commands

Run from the repository root after implementation:

```bash
python -m pytest -q tests/unit/test_layout_grid_validation.py tests/unit/test_layout_builtin_names.py
```

Run focused Blender integration tests:

```bash
blender --background --factory-startup \
  --python tests/run_pytest_in_blender.py -- tests/blender/test_layout_points.py
```

If the implementation touches shared low-level `geometry.py` helpers beyond the count resolver, Domain Size node, and socket lookup helper, also run the full Blender test suite:

```bash
blender --background --factory-startup \
  --python tests/run_pytest_in_blender.py -- tests/blender
```

## Regression and Blind-Spot Analysis

The highest regression risk is public call binding. Preserving parameter order prevents existing positional count calls from changing meaning. Tests must explicitly cover both omitted count and positional explicit count.

The second risk is inserting Domain Size for explicit-count calls. That would change generated node graphs and could produce different spacing if the explicit count intentionally differs from geometry point count. Tests must assert no `GeometryNodeAttributeDomainSize` is inserted for at least one explicit override path.

The third risk is failing to wire `Point Count` into the normalized-index formula. Tests must not stop at node-existence checks; they must assert that `Domain Size.outputs["Point Count"]` feeds the math graph upstream of `GeometryNodeSetPosition.inputs["Position"]`.

The fourth risk is changing spiral endpoint normalization. The plan preserves `_layout_normalized_index(..., True, ...)`, and tests must verify the `count - 1` denominator branch for spiral derived count and explicit count.

The fifth risk is over-generalizing the change to `layout_grid(...)`. `layout_grid` count is dimensional and cannot be derived from total point count without losing shape information. Tests should keep a missing-count failure for `layout_grid(...)`.

The sixth risk is assuming all `Geometry` values are point clouds. The implementation should document derived count as Point-domain size, not as the count originally passed to `points(...)`. That is accurate for current NodeForge typing and Blender's domain model.

The seventh risk is Blender socket-name drift. The plan uses socket-name lookup because it is clearer than numeric indices and wraps missing sockets into `CompileError`, but validation must run in the supported Blender runtime. A controlled compatibility error is preferable to silently connecting the wrong socket.

A blind spot remains for semantic geometry types: NodeForge cannot distinguish point clouds from meshes or curves at compile time. This stage should not attempt to solve that because it would require a new type/provenance system and a wider compatibility plan.

## Review Corrections Applied

Blocking correction 1 is applied: the plan now states that `_layout_spiral_geometry(...)` currently uses `_layout_normalized_index(..., True, ...)`, and the implementation steps require preserving that endpoint branch for explicit and derived counts.

Blocking correction 2 is applied: the Blender test plan now requires graph assertions that `Domain Size.outputs["Point Count"]` feeds the normalized-index math upstream of `Set Position.Position`, plus spiral endpoint-denominator regression checks.

The socket-helper architecture recommendation is accepted because it keeps runtime Blender compatibility failures inside NodeForge's controlled `CompileError` boundary. This adds only a local private helper and does not expand the public API.

The optional unit-test file organization note is not made mandatory. The plan keeps `tests/unit/test_layout_grid_validation.py` as the touched file because the stage does not require a test-suite reorganization; a new `tests/unit/test_layout_arg_binding.py` remains an implementation-time cleanup option if the maintainer chooses it.

## External Contract Check

The only external contract used by this stage is Blender Geometry Nodes' Domain Size node. The Blender manual describes Domain Size as returning attribute-domain sizes and includes Point-domain count behavior. Treat this as Blender-specific behavior, not a generic protocol guarantee. During implementation, confirm the concrete Python node `bl_idname` and socket names against the supported Blender runtime by compiling the new Blender tests.

No HTTP API, file format, database, cloud service, authorization mechanism, routing contract, pagination contract, or retention guarantee is involved.

## Open Questions

None.
