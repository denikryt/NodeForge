# Test Refactor Coverage Map

| Previous monolithic entry point | Pytest target modules |
| --- | --- |
| `run_import_and_registry_checks()` | `tests/blender/test_import_registry.py` |
| `run_startup_shutdown_checks()` | `tests/blender/test_addon_lifecycle.py` |
| `run_compile_fixtures()` | `tests/blender/test_compile_fixtures.py` |
| `run_math_table_dispatch_checks()` | `tests/unit/test_math_specs.py`, `tests/blender/test_math_compile.py` |
| Compile-time helper surface (`sign` consteval removal) | `tests/unit/test_consteval.py` |
| `run_lsystem_syntax_and_guard_checks()` | `tests/unit/lsystem/test_validation.py`, `tests/unit/lsystem/test_modules.py`, `tests/blender/lsystem/test_syntax_contracts.py`, `tests/blender/lsystem/test_validation_errors.py`, `tests/blender/lsystem/test_parametric_modules.py` |
| `run_lsystem_static_ownership_checks()` | `tests/blender/lsystem/test_static_baked_backend.py`, `tests/blender/lsystem/test_resource_lifecycle.py`, `tests/blender/lsystem/test_backend_transitions.py` |
| `run_lsystem_branch_free_runtime_checks()` | `tests/unit/lsystem/test_runtime_tables_branch_free.py`, `tests/blender/lsystem/test_runtime_branch_free_backend.py`, `tests/blender/lsystem/test_runtime_branch_free_eval.py`, `tests/blender/lsystem/test_backend_transitions.py` |
| `run_lsystem_branch_aware_runtime_checks()` | `tests/unit/lsystem/test_runtime_tables_branch_aware.py`, `tests/blender/lsystem/test_runtime_branch_aware_backend.py`, `tests/blender/lsystem/test_runtime_branch_aware_eval.py`, `tests/blender/lsystem/test_backend_transitions.py` |
| `run_lsystem_documented_contract_checks()` | `tests/unit/lsystem/test_backend_selection.py`, `tests/blender/lsystem/test_backend_exports.py`, `tests/blender/lsystem/test_documented_examples.py`, `tests/blender/lsystem/test_marker_points.py` |
| `run_lsystem_benchmark_if_requested()` | `tests/blender/lsystem/test_benchmarks.py` |
| `run_library_checks()` | `tests/blender/test_library_functions.py` |
| `run_update_checks()` | `tests/blender/test_update_group.py` |
| `run_mandelbrot_eval_check()` | `tests/blender/test_mandelbrot_eval.py` |

| Geometry builder DSL accumulation and Repeat Zone state | `tests/blender/test_geometry_builder.py`, `tests/unit/test_math_specs.py` |
