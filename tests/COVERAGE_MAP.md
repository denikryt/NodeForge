# Test Refactor Coverage Map

| Previous monolithic entry point | Pytest target modules |
| --- | --- |
| `run_import_and_registry_checks()` | `tests/blender/test_import_registry.py` |
| `run_startup_shutdown_checks()` | `tests/blender/test_addon_lifecycle.py` |
| `run_compile_fixtures()` | `tests/blender/test_compile_fixtures.py` |
| `run_math_table_dispatch_checks()` | `tests/unit/test_math_specs.py`, `tests/blender/test_math_compile.py` |
| Compile-time helper surface (`sign` consteval removal) | `tests/unit/test_consteval.py` |
| `run_library_checks()` | `tests/blender/test_library_functions.py` |
| `run_update_checks()` | `tests/blender/test_update_group.py` |
| `run_mandelbrot_eval_check()` | `tests/blender/test_mandelbrot_eval.py` |

| Geometry builder DSL accumulation and Repeat Zone state | `tests/blender/test_geometry_builder.py`, `tests/unit/test_math_specs.py` |

## Object sockets

- `tests/unit/test_object_type.py`: Object token, builtin registration, local-function source, raw-node type token.
- `tests/blender/test_object_inputs.py`: Object interface sockets, lazy Object Info configuration/cache, keyword isolation, and configuration lock.

## Local function multiple returns

- `tests/unit/test_local_function_return_shapes.py`: fixed return-shape analysis, output naming, annotation resolution, generated helper source, and `TupleValue` indexing.
- `tests/blender/test_local_function_multi_return.py`: helper interfaces, one-node call materialization, unpacking, indexing, diagnostics, annotations, and scalar/library regressions.

## Local function helper identity and titles

- `tests/blender/test_local_function_multi_return.py`: metadata-based helper identity for long signatures, no false truncation collision, complete long-signature interfaces, readable helper datablock names and call-node titles, and legacy-name migration without datablock replacement.
- `tests/blender/test_local_functions.py`, `tests/blender/test_stage18_local_functions.py`, and `tests/blender/test_update_group.py`: legacy short-name compatibility, real collision handling, rollback, and stable helper identity across updates.
