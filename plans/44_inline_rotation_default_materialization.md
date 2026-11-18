# 44 — Materialize Rotation defaults in inline compilation

**Baseline:** NodeForge `0.93.2`, Blender `5.2`
**Scope:** Fix the existing inline constant materializer; keep the current constant-node behavior for all supported types.

## Problem / Goal

A source function can declare a `Rotation` parameter with an XYZ Euler default, but an inline call that omits that parameter fails with `Internal error: unsupported inline default of type ROTATION`. The same call works as a grouped function. `blender/ir_lowering.py::_inline_parameter_bindings()` correctly binds the declared default, but `_inline_constant_value()` lacks a `TYPE_ROTATION` branch. This affects explicit static Rotation arguments to inline calls as well, because both use the same materialization path.

Add the missing Rotation constant-node path, matching the current behavior of Float, Int, Bool, String, Vector and Color. Do not introduce direct writes to consumer socket defaults, usage-count analysis or other inline optimizations in this fix.

## Expected Behavior

```python

def apply_rotation(
    geometry: Geometry,
    rotation: Rotation = (0.0, 0.0, 1.5707963),
    __inline__=True,
) -> Geometry:
    return node(
        "GeometryNodeTransform",
        inputs={ID("Geometry"): geometry, ID("Rotation"): rotation},
        output=ID("Geometry"), typ=Geometry,
    )

result = apply_rotation(cube(1.0))
```

The inline call compiles into the **caller's** node tree. In addition to the geometry and transform nodes, the backend creates one `FunctionNodeInputRotation`, assigns its canonical XYZ Euler angles to `rotation_euler` and links its `NodeSocketRotation` output to the transform's `Rotation` input. No `GeometryNodeGroup` is created for the inline call.

A single inline invocation creates one constant node per bound Rotation default, even if the parameter has several consumers; they share the same output socket. Distinct inline invocations receive distinct constant nodes. A function returning only its Rotation parameter also produces a valid Rotation output. Explicit runtime Rotation values reuse the existing runtime binding and **do not** create an input-rotation constant. Grouped calls retain their present behavior.

## Architecture

The semantic type/default contract is already complete: `semantic/callable_contracts.py::canonicalize_group_input_default(TYPE_ROTATION, ...)` returns an immutable three-component float32 tuple. Keep this as the sole value validator; no new IR representation or default inference is necessary.

1. Add a small backend constant helper alongside `_color_value()` and `_vector_value()` in `blender/nodes.py`:

   ```python
   def _rotation_value(group, angles, x=0, y=0):
       """Create a native Rotation constant from canonical XYZ Euler angles."""
       node = _new_node(group, "FunctionNodeInputRotation", x, y)
       node.rotation_euler = angles
       return Value(node.outputs["Rotation"], TYPE_ROTATION)
   ```

   Export it in `nodes.py::__all__`. The helper operates on the already canonical tuple passed by the inline lowering path; it does not introduce another semantic validator. Blender 5.2 was checked to accept direct tuple assignment and expose `Rotation` as `NodeSocketRotation`.

2. In `blender/ir_lowering.py`, import `TYPE_ROTATION` and `_rotation_value`, then extend `_inline_constant_value()` immediately after the existing typed constant branches:

   ```python
   if typ is TYPE_ROTATION:
       return _rotation_value(context.group, value, x, y)
   ```

   Preserve its existing `canonicalize_group_input_default(typ, value)` call, location arguments, `Value` return contract and fallback error for unsupported types. `_inline_parameter_bindings()` needs no change: omitted defaults and explicit static operands both reach this branch; runtime operands continue via `_inline_runtime_value()`.

3. Do not change source-call argument binding, IR contracts, `blender/interface.py`, the grouped-function interface, Extension API type inference, or `Vector`/`Rotation` compatibility rules. This is a **generic NodeForge inline constant-materialization case for the Rotation type**, not a special case for `GeometryNodeTransform`.

## Touched Files

```text
blender/nodes.py
blender/ir_lowering.py
tests/unit/test_inline_lowering.py
tests/blender/test_inline_source_calls.py
__init__.py
blender_manifest.toml
dev/CHANGELOG.md
```

Existing `tests/blender/test_rotation_runtime.py` can be extended instead of duplicating an existing fixture if that is more cohesive. No user-documentation rewrite is required: `docs/WRITING_FUNCTIONS.md` already defines inline defaults as typed runtime values; verify that description remains accurate.

## New Files

```text
plans/44_inline_rotation_default_materialization.md
```

## Implementation Steps

1. **Introduce the native Rotation constant helper** in `blender/nodes.py` using `FunctionNodeInputRotation` and its `rotation_euler` property. Return a typed `Value` from the `Rotation` output, not a Vector value or an Euler-to-Rotation conversion chain.
2. **Wire the existing inline dispatch** in `blender/ir_lowering.py` to the helper for `TYPE_ROTATION`. Keep input canonicalization at the current boundary; do not modify or special-case consumers.
3. **Add unit coverage** to `tests/unit/test_inline_lowering.py`: stub `_rotation_value()` as the other helpers are stubbed; confirm a missing Rotation default and an explicit static Rotation argument produce exactly one `Rotation` materialization with canonical float32 components. Confirm the resulting value has `NFType.ROTATION` and existing Float/Vector/Color paths remain unchanged.
4. **Add Blender 5.2 integration coverage** in `tests/blender/test_inline_source_calls.py`: compile the reproduction; assert one `FunctionNodeInputRotation`, its `rotation_euler`, its `NodeSocketRotation` output, valid link to `GeometryNodeTransform`, and no inline `GeometryNodeGroup`. Exercise a direct Rotation return, two uses inside one inline invocation, two separate invocations, nested inline calls and an imported/package function. Test explicit runtime Rotation from `FunctionNodeEulerToRotation` (no extra Input Rotation node).
5. **Verify regression boundaries**: forced `__inline__=False` retains grouped socket defaults, Vector cannot be passed as runtime Rotation, invalid declared defaults still fail during semantic analysis, and existing Color/Vector/Float inline behavior is unchanged. Compare evaluated geometry for the example with grouped compilation, using float32-appropriate tolerance. Exercise Compile Script / Update Script and at least one grouped caller with inline callee to confirm ownership and rollback behavior.
6. **Run validation**: focused unit tests; full `tests/unit` suite; focused and full Blender 5.2 suites; extension `validate` and `build`. Record unrelated baseline failures separately. Update changelog and bump both add-on and manifest patch version from `0.93.2` to `0.93.3` in the implementation branch (unless a newer version has already been merged).

## Tests

- **T01 — Baseline reproduction:** omitted Rotation default now compiles; exactly one native input-rotation node is created in caller tree with canonical radians and valid physical links.
- **T02 — Binding variants:** omitted default; explicitly supplied static triple where accepted by the source-call contract; explicitly supplied typed runtime Rotation; positional/keyword binding; two consumers reuse one node; distinct invocations do not alias sockets.
- **T03 — Composition:** direct return `Rotation`; nested inline calls; inline inside grouped function; imported/package inline functions; consistent dataflow and no unnecessary group node.
- **T04 — Negative and boundary:** wrong Vector runtime type, malformed/overflowing Rotation defaults, signed angles and angles greater than `2π`; no Vector-to-Rotation implicit coercion or alteration of float32 normalization.
- **T05 — Regressions/lifecycle:** unchanged Float, Int, Bool, String, Vector and Color constant paths; unchanged grouped-function defaults; matched evaluated geometry; Update Script / Reload / rollback where inline dependencies are involved.

## Compatibility / Blind Spots

- The helper must return the native `NodeSocketRotation` output, not a Vector socket. The three-angle tuple is already canonical; constructing a `mathutils.Euler` in semantic code is unnecessary.
- The source-call contract may carry an explicit static argument through the same path as an omitted default. Cover both; do not only test the missing-argument case.
- Multiple consumers should share the one `Value` bound by `_inline_parameter_bindings()`; do not materialize once per consumer.
- Inline materialization uses the immediate caller's tree, including when called from a grouped function. New nodes must not escape that transaction's lifetime.

## Open Questions

None within this fix. Allowing omitted required runtime parameters or optimizing inline defaults into consumer socket `default_value` is a separate design task.
