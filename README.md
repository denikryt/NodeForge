# NodeForge v0.27

Refactored Blender Geometry Nodes compiler add-on.

Module layout:

- `__init__.py` — Blender add-on entrypoint.
- `ui.py` — N-panel, properties, and operators.
- `compiler.py` — public compiler facade and high-level group assembly.
- `constants.py` — shared type constants and function maps.
- `errors.py` — compiler exception type.
- `values.py` — typed socket value wrapper.
- `nodes.py` — low-level node creation and wiring helpers.
- `parsing.py` — AST parsing and script inspection.
- `statements.py` — top-level statement helpers.
- `consteval.py` — compile-time evaluator and preprocessing.
- `geometry.py` — geometry macro implementations.
- `runtime.py` — Repeat Zone / runtime loop helpers.
- `interface.py` — input defaults and interface socket helpers.
- `update.py` — link/default preservation during selected node updates.
- `storage.py` — NodeTree source storage and scratch Text helpers.


## v0.22

- Added `koch_curve(steps=..., length=..., max_steps=...)` macro.
- Runtime `steps=input_int(...)` is supported by switching between baked levels 0..max_steps.


## v0.22

- Centralized geometry macro keyword handling so `koch_curve(steps=..., max_steps=..., length=...)` is recognized consistently.


## v0.23

- N-panel uses only a Blender Text datablock as the script source.
- Node group tooltip description now starts source on a new line and preserves line breaks.


## v0.24

- Preserved both `koch_curve` modes:
  - `steps=3` bakes exactly one deterministic level.
  - `steps=input_int(...)` bakes levels `0..max_steps` and switches at runtime.
- Added an extra keyword-argument compatibility guard for `koch_curve(steps=..., max_steps=..., length=...)`.


## v0.25

- Fixed compile-time `len(...)` on Python builds where `__builtins__` is a dict.
- Fixed compile-time list indexing such as `pts[j]` and `pts[-1]`.
- Fixed compile-time vector arithmetic: add/sub/mul/div, unary minus, and `.x/.y/.z`.
- Verified Koch, Dragon, Levy C, and quadratic Koch scripts.


## v0.26 - Library function groups

NodeForge now supports user-defined library functions stored as scripts in the `functions/` folder.
Each function script is compiled into its own reusable Geometry Nodes node group and calls from other scripts are inserted as nested `GeometryNodeGroup` nodes.

Example function file: `functions/scale_to_unit.nf`

```python
geo = input_geometry("Geometry")
scale = input_vector("Scale", default=vector(1, 1, 1))
geo = transform(geo, scale=scale)
output("Geometry", geo)
```

Example usage from a normal script:

```python
geo = input_geometry("Geometry")
geo = scale_to_unit(geo, scale=vector(0.5, 0.5, 1))
output("Geometry", geo)
```

Keyword names are normalized, so `scale=...` can match an input socket named `Scale`.


## v0.27 - Built-in function library

- Moved `copy_by_offsets()` and `koch_curve()` out of the main compiler dispatch into `builtin_functions.py`.
- Built-in functions now compile to reusable nested node groups named `NodeForge.fn.*`.
- `copy_by_offsets()` creates a specialized function group for its offset pattern and scale socket type.
- `koch_curve()` creates a specialized static or runtime-switch function group.
- Main graphs now call these functions through `GeometryNodeGroup` nodes instead of inlining their internals.

## v0.28 - File-based function modules

- `copy_by_offsets()` and `koch_curve()` now live in `functions/copy_by_offsets.py` and `functions/koch_curve.py`.
- Python function modules in `functions/*.py` are discovered by the same function library scanner as `.nf` scripts.
- `builtin_functions.py` was removed; built-ins now behave like modular library functions.


## Function Library UI

The N-panel includes a Function Library list populated from `NodeForge/functions/`. Use Refresh to reload files, then Add Node Group to create the selected function node group if needed and insert it into the active Geometry Nodes editor.


## v0.30

- Removed Koch-specific helpers from `geometry.py`; Koch curve generation now lives entirely in `functions/koch_curve.py`.
- Moved copy-by-offset implementation details into `functions/copy_by_offsets.py`; core geometry now only contains generic helpers.
- `koch_curve()` and `copy_by_offsets()` continue to behave as file-based function modules discovered from `functions/`.


## v0.31

- Simplified the Function Library UI to one action: `Add Node Group`.
- Removed the separate `Create Group` button because materializing without inserting was confusing.
- The action now requires an active Geometry Nodes editor, creates the selected function group if needed, and immediately inserts it as a `GeometryNodeGroup` node.

### v0.43
- Added `functions/sierpinski_carpet/source.nf` as a pure DSL library function.
- `sierpinski_carpet()` composes the existing DSL `copy_by_offsets()` function in a runtime `range(steps)` loop.


### v0.43.1
- Added `builtins/` package with a central registry for DSL built-ins.
- Moved builtin dispatch into category modules: `math.py`, `vector.py`, `fields.py`, `geometry.py`, `instancing.py`, `runtime.py`, and `io.py`.
- Library functions remain under `functions/` and are not treated as DSL built-ins.

### v0.43.6
- Added `noise(...)` builtin using Blender Noise Texture node.
- Added `random_value(...)` builtin using Blender Random Value node.
- Updated `DSL_IMPLEMENTATION_TODO.md` to remove items already implemented in v0.43.x.
