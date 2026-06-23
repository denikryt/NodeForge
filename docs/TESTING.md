# Testing

NodeForge uses two test runtimes.

- Ordinary CPython runs pure unit tests under `tests/unit/`.
- Blender Python runs integration, Geometry Nodes, resource lifecycle, and depsgraph tests under `tests/blender/`.

## Prerequisites

Install `pytest` in both Python runtimes used for testing:

- the ordinary Python environment used for `python -m pytest`;
- Blender's bundled Python environment used by the `blender` executable.

Use the real Blender executable for test runs. Do not use `blender-launcher`.

## Unit tests

Run the pure Python tests from the repository root:

```bash
python -m pytest -q tests/unit
```

This command must not require Blender or import `bpy`.

## Blender tests

Run the Blender test suite from the repository root:

```bash
blender --background --factory-startup \
  --python tests/run_pytest_in_blender.py -- tests/blender
```

This command runs the Blender integration and eval tests. Benchmark tests are skipped unless explicitly enabled.

## Benchmarks

Run L-system benchmarks only when benchmark output is needed:

```bash
NODEFORGE_LSYSTEM_BENCHMARK=1 \
blender --background --factory-startup \
  --python tests/run_pytest_in_blender.py -- tests/blender/lsystem/test_benchmarks.py
```

Benchmark tests print machine-readable `LSYSTEM_BENCHMARK_ENV` and `LSYSTEM_BENCHMARK_ROW` lines. Timing values are informational and are not fixed pass/fail thresholds.

## Full local check

Use these commands for the normal full local test pass:

```bash
python -m pytest -q tests/unit

blender --background --factory-startup \
  --python tests/run_pytest_in_blender.py -- tests/blender
```

`python -m pytest` from the repository root is not the full test suite. Outside Blender, the Blender subtree is skipped by its runtime guard, so the command only executes the ordinary unit layer.
