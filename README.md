# NodeForge v0.18

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
