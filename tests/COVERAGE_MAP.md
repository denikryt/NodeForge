# Test coverage map

The unified `.nf` frontend is the only supported source language as of Issue #48.
Regression tests verify its public behavior and the reusable Blender datablock
backend independently. Checks which historically assumed implicit root I/O,
`input_*`/`output()` declarations, bare file-as-callable imports, or automatic
root-group creation are **not** evidence of the unified language contract.

## Issue #48 — source language and group composition

| Contract | Positive and negative regression targets |
| --- | --- |
| **S01 — unified module** | `tests/unit/test_source_modules.py` mixed declarations, exact AST snapshot and source-location errors; `tests/blender/test_unified_direct_composition.py` ordinary top-level composition and rejected root I/O. |
| **S02 — definitions/entries** | `tests/unit/test_source_modules.py`, `tests/unit/test_source_call_graph.py` declarations, aliases, duplicate names, source-ordered compile-time pruning, static-loop cross-iteration state, recursion and entries; `tests/blender/test_unified_definition_only_entries.py`, `tests/blender/test_runtime_loop_index_shadowing.py` nested branches, automatic placement and evaluated static-loop dependencies. |
| **S03 — signatures/labels** | `tests/unit/test_function_signatures.py`, `tests/unit/test_unified_call_binding.py` explicitly declared types, missing-annotation errors, labels and logical/physical socket mapping; `tests/blender/test_unified_definition_only_entries.py` required sockets and typed runtime loop counts. |
| **S04 — defaults/requiredness** | `tests/unit/test_function_signatures.py`, `tests/unit/test_unified_call_binding.py` absent default versus Blender fallback, compile-time defaults, invalid values and missing required parameters. |
| **S05 — source argument binding** | `tests/unit/test_unified_call_binding.py` exact keywords, positional arguments, label-keyword rejection, invalid/duplicate arguments and panels. |
| **S06 — returns** | `tests/unit/test_function_bodies.py` terminal return, annotations, tuple result names and invalid shapes; `tests/blender/test_unified_module_materialization.py` physical group outputs. |
| **S07 — bodies/panels** | `tests/unit/test_function_bodies.py`, `tests/unit/test_function_signatures.py`, `tests/blender/test_unified_final_regressions.py` typed AST bodies, prefix panels and obsolete interface declarations. |
| **S08 — Local module paths** | `tests/unit/test_unified_local_modules.py` qualified/nested imports, direct member aliases and exact definition origin; `tests/blender/test_unified_module_materialization.py` resolved imported group. |
| **S09 — Local collisions** | `tests/unit/test_unified_local_modules.py` module/function-name collisions, unknown members, missing modules and file/directory ambiguity. |
| **S10 — directory imports** | `tests/unit/test_unified_local_modules.py`, `tests/unit/test_examples_directory_index.py`, `tests/blender/test_examples_directory_imports.py` nested Local/Examples module-member paths, aliases, ambiguity and rejection of legacy file calls. |
| **S11 — package exports** | `tests/unit/test_unified_package_exports.py` each definition as a package member, export collisions and frozen snapshots; `tests/blender/test_unified_catalog_add.py`, `tests/blender/test_library_catalogs.py` selected package/Local member Add and missing-source failures. |
| **S12 — laziness/dependencies** | `tests/unit/test_unified_package_exports.py`, `tests/unit/test_unified_local_modules.py`, `tests/unit/test_source_call_graph.py`, `tests/blender/test_unified_module_materialization.py` import-only resolution without runtime composition. |
| **S13 — initial base composition** | `tests/blender/test_unified_direct_composition.py`, `tests/blender/test_unified_definition_only_entries.py`, `tests/blender/test_unified_ui_integration.py` existing level-0 group, correct nodes, missing targets and retained user links. |
| **S14 — nested groups/shared-unique** | `tests/unit/test_unified_module_materialization.py`, `tests/blender/test_unified_module_materialization.py`, `tests/blender/test_unified_group_authority.py` physical groups, owner identity and unique instances. |
| **S15 — canonical identity** | `tests/unit/test_source_modules.py`, `tests/unit/test_compiler_identities.py`, `tests/blender/test_unified_group_authority.py` module/definition scope and duplicated authoritative owner detection. |
| **S16 — one revision** | `tests/unit/test_source_modules.py`, `tests/unit/test_unified_package_exports.py`, `tests/blender/test_unified_catalog_add.py`, `tests/blender/test_module_compile_time_fingerprints.py` parsed snapshot reuse, used-constant fingerprint changes, package revision freezing and Add without importing top-level statements. |
| **S17 — in-place group updates** | `tests/blender/test_unified_module_selected_reload.py`, `tests/blender/test_unified_selected_unique_reload.py`, `tests/blender/test_unified_source_detachment.py`, `tests/blender/test_unified_group_authority.py`, `tests/blender/test_module_compile_time_fingerprints.py`, `tests/blender/test_update_group.py` authority, stable datablock pointers, unique selected reload, detachment and interface preservation. |
| **S18 — breaking syntax** | `tests/unit/test_unified_local_modules.py`, `tests/blender/test_unified_direct_composition.py`, `tests/blender/test_unified_final_regressions.py` negative tests for removed source forms; new frontend has no legacy-selection branch. |
| **S19 — interface migration** | `tests/unit/test_function_signatures.py`, `tests/blender/test_unified_selected_unique_reload.py`, `tests/blender/test_update_group.py` stable parameter origins, physical sockets and compatible values/links. |
| **S20 — runtime role** | `tests/unit/test_unified_runtime_capabilities.py`, `tests/blender/test_unified_runtime_capability_preflight.py` ROOT/NESTED restrictions, unused imports and nested extensions. |
| **S21 — operators/editor** | `tests/unit/test_unified_editor_signatures.py`, `tests/unit/test_editor_intelligence.py`, `tests/blender/test_unified_ui_integration.py`, `tests/blender/test_unified_catalog_add.py` symbol names, base tree target, Add and detachment. |
| **S22 — diagnostics and atomicity** | `tests/blender/test_unified_initial_composition_rollback.py`, `tests/blender/test_unified_runtime_capability_preflight.py`, `tests/blender/test_unified_final_regressions.py`, `tests/blender/test_unified_group_authority.py` attempted-node cleanup, nested/resource rollback, destructive extension preflight, bad source and duplicate owners. |

## Issue #48 Semantic Fixes Rev2 (F01–F12)

| Contract | Regression targets |
| --- | --- |
| **F01 — isolated mutable CT function state** | `tests/unit/test_rev2_semantic_contracts.py`, `tests/unit/test_compile_time_state.py` independent scan, aliased/nested lists and unsupported resource copying; `tests/blender/test_unified_semantic_scope.py` physical sibling isolation. |
| **F02 — unknown path/loop soundness** | `tests/unit/test_rev2_semantic_contracts.py`, `tests/unit/test_source_call_graph.py` branch ordering, no cross-arm leakage, conservative joins, zero/many loop iterations and static iteration writes; `tests/blender/test_unified_semantic_scope.py` false recursion and `test_runtime_loop_index_shadowing.py` physical execution. |
| **F03 — defining-module final CT defaults** | `tests/unit/test_rev2_semantic_contracts.py`, `tests/unit/test_function_signatures.py` default context and caller isolation; `tests/blender/test_unified_semantic_scope.py`, `tests/blender/test_rev2_module_snapshot_reload.py` unlinked socket defaults and in-place Reload. |
| **F04–F05 — eager pure declarations, lazy physical groups** | `tests/unit/test_rev2_semantic_contracts.py` used/unused invalid contracts; `tests/blender/test_unified_semantic_scope.py`, `tests/blender/test_rev2_module_snapshot_reload.py` negative preflight and no graph changes. |
| **F06–F09 — lexical ownership and definite assignment** | `tests/unit/test_rev2_semantic_contracts.py` AST inventory, `tests/unit/test_rev2_lexical_runtime.py`, `tests/unit/test_source_call_graph.py` parameter/local shadowing, verified alias chains, copied numeric locals, non-callable calls without false graph edges and read-before-assignment; `tests/blender/test_unified_semantic_scope.py` evaluated shadowing and rollback-safe failure; `tests/blender/test_runtime_loop_index_shadowing.py` dynamic/static loop index restoration. |
| **F10 — source attempt and in-place update isolation** | `tests/blender/test_rev2_module_snapshot_reload.py`, `tests/blender/test_unified_selected_unique_reload.py`, `tests/blender/test_module_compile_time_fingerprints.py` current Local/package snapshots, owner pointers and shared/unique preservation. |
| **F11 — process-local UI lifecycle** | `tests/blender/test_addon_lifecycle.py` repeated register/unregister, partial class/editor registration cleanup, generated resource preservation, editor catalog refresh and restart. |
| **F12 — retained scope boundaries** | Existing `test_unified_runtime_capability_preflight.py`, `test_unified_initial_composition_rollback.py` and source-syntax negative tests retain runtime capability, failure-only cleanup and no selective level-0 reconciliation. Mesh Compute migration remains separate. |

## Reusable infrastructure (not legacy source-language compatibility)

- `tests/unit/test_semantic_ir.py`, `tests/unit/test_semantic_analysis.py`,
  `tests/unit/test_semantic_backend_contract.py`: typed IR, expression semantics,
  stable identity, pure preparation and early diagnostics.
- `tests/unit/test_function_instances.py`, `tests/unit/test_function_materializer.py`,
  `tests/unit/test_compiler_identities.py`: materialization ownership and dependency
  tracking, reusable group scopes, fingerprints and instance keys.
- `tests/unit/test_package_inventory.py`, `tests/unit/test_package_resolution_regressions.py`:
  package inventory consistency, export availability and failure propagation.
- `tests/blender/test_update_group.py`, `tests/blender/test_generated_resources.py`:
  function datablock updates, reversible graph publication, interface default/link
  migration and post-commit resource retirement.
- `tests/blender/test_unified_source_detachment.py`,
  `tests/blender/test_unified_selected_unique_reload.py`: explicit copied-owner
  detachment and in-place selected group updates, including `.blend` reopen.
- `tests/blender/test_unified_initial_composition_rollback.py`,
  `tests/blender/test_unified_runtime_capability_preflight.py`: bounded failed-attempt
  recovery and restricted Extension API v2 execution in user-managed trees.

Run Python unit tests with `pytest tests/unit`. Run Blender 5.2+ integration
with `blender -b --factory-startup --python tests/run_pytest_in_blender.py -- tests/blender`.
Record both suites separately. The pre-Issue-#48 baseline is not validation of the
new language. Do not require selective reconciliation of previously inserted
level-0 nodes or repeated direct-composition semantics: both are excluded from #48.

## Deferred root-only Mesh Compute follow-up

The unified compiler rejects Mesh Compute root-only callbacks in nested functions and
does not install a modifier runtime during direct level-0 composition. Legacy UI tests for the former modifier route remain in the suite as
strict expected failures (`xfail`); additional historical modifier lifecycle
regressions still fail under the removed route pending a separate Mesh Compute
follow-up. Neither category constitutes passing new runtime tests. This does
not affect the existing runtime backend/resource tests, which remain active.
The old erosion example remains a legacy fixture and is not documented as a
working unified `.nf` example.

The previously observed Blender add-on double-unregister failure was fixed by
Rev2 F11. The full **non-Mesh Compute** Blender suite now includes this
lifecycle regression as a passing test rather than a baseline exception.
