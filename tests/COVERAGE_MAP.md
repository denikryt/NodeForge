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
| **S02 — definitions/entries** | `tests/unit/test_source_modules.py`, `tests/unit/test_source_call_graph.py` declaration-before-use, aliases, duplicate names and recursive cycles; `tests/blender/test_unified_definition_only_entries.py` zero-incoming entries and mixed-source exclusion. |
| **S03 — signatures/labels** | `tests/unit/test_function_signatures.py`, `tests/unit/test_unified_call_binding.py` explicit parameter types, rejected unannotated parameters, labels and logical/physical mapping; `tests/blender/test_unified_definition_only_entries.py` required entry sockets. |
| **S04 — defaults/requiredness** | `tests/unit/test_function_signatures.py`, `tests/unit/test_unified_call_binding.py` absent default versus Blender fallback, compile-time defaults, invalid values and missing required parameters. |
| **S05 — source argument binding** | `tests/unit/test_unified_call_binding.py` exact keywords, positional arguments, label-keyword rejection, invalid/duplicate arguments and panels. |
| **S06 — returns** | `tests/unit/test_function_bodies.py` terminal return, annotations, tuple result names and invalid shapes; `tests/blender/test_unified_module_materialization.py` physical group outputs. |
| **S07 — bodies/panels** | `tests/unit/test_function_bodies.py`, `tests/unit/test_function_signatures.py`, `tests/blender/test_unified_final_regressions.py` typed AST bodies, prefix panels and obsolete interface declarations. |
| **S08 — Local module paths** | `tests/unit/test_unified_local_modules.py` qualified/nested imports, direct member aliases and exact definition origin; `tests/blender/test_unified_module_materialization.py` resolved imported group. |
| **S09 — Local collisions** | `tests/unit/test_unified_local_modules.py` module/function-name collisions, unknown members, missing modules and file/directory ambiguity. |
| **S10 — directory imports** | `tests/unit/test_unified_local_modules.py` nested paths, aliases and explicit rejection of legacy file calls and star imports. |
| **S11 — package exports** | `tests/unit/test_unified_package_exports.py` each definition as a package member, export collisions and frozen snapshots; `tests/blender/test_unified_catalog_add.py` selected package member Add. |
| **S12 — laziness/dependencies** | `tests/unit/test_unified_package_exports.py`, `tests/unit/test_unified_local_modules.py`, `tests/unit/test_source_call_graph.py`, `tests/blender/test_unified_module_materialization.py` import-only resolution without runtime composition. |
| **S13 — initial base composition** | `tests/blender/test_unified_direct_composition.py`, `tests/blender/test_unified_definition_only_entries.py`, `tests/blender/test_unified_ui_integration.py` existing level-0 group, correct nodes, missing targets and retained user links. |
| **S14 — nested groups/shared-unique** | `tests/unit/test_unified_module_materialization.py`, `tests/blender/test_unified_module_materialization.py`, `tests/blender/test_unified_group_authority.py` physical groups, owner identity and unique instances. |
| **S15 — canonical identity** | `tests/unit/test_source_modules.py`, `tests/unit/test_compiler_identities.py`, `tests/blender/test_unified_group_authority.py` module/definition scope and duplicated authoritative owner detection. |
| **S16 — one revision** | `tests/unit/test_source_modules.py`, `tests/unit/test_unified_package_exports.py`, `tests/blender/test_unified_catalog_add.py` parsed snapshot reuse, package revision freezing and Add without importing top-level statements. |
| **S17 — in-place group updates** | `tests/blender/test_unified_selected_unique_reload.py`, `tests/blender/test_unified_source_detachment.py`, `tests/blender/test_unified_group_authority.py`, `tests/blender/test_update_group.py` authority, stable datablock pointers, unique selected reload, detachment and interface preservation. |
| **S18 — breaking syntax** | `tests/unit/test_unified_local_modules.py`, `tests/blender/test_unified_direct_composition.py`, `tests/blender/test_unified_final_regressions.py` negative tests for removed source forms; new frontend has no legacy-selection branch. |
| **S19 — interface migration** | `tests/unit/test_function_signatures.py`, `tests/blender/test_unified_selected_unique_reload.py`, `tests/blender/test_update_group.py` stable parameter origins, physical sockets and compatible values/links. |
| **S20 — runtime role** | `tests/unit/test_unified_runtime_capabilities.py`, `tests/blender/test_unified_runtime_capability_preflight.py` ROOT/NESTED restrictions, unused imports and nested extensions. |
| **S21 — operators/editor** | `tests/unit/test_unified_editor_signatures.py`, `tests/unit/test_editor_intelligence.py`, `tests/blender/test_unified_ui_integration.py`, `tests/blender/test_unified_catalog_add.py` symbol names, base tree target, Add and detachment. |
| **S22 — diagnostics and atomicity** | `tests/blender/test_unified_initial_composition_rollback.py`, `tests/blender/test_unified_runtime_capability_preflight.py`, `tests/blender/test_unified_final_regressions.py`, `tests/blender/test_unified_group_authority.py` attempted-node cleanup, nested/resource rollback, destructive extension preflight, bad source and duplicate owners. |

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
does not install a modifier runtime during direct level-0 composition. Legacy UI
integration tests for the former modifier route remain in the suite as strict
expected failures (`xfail`) pending a separate Mesh Compute follow-up. This does
not affect the existing runtime backend/resource tests, which remain active.
The old erosion example remains a legacy fixture and is not documented as a
working unified `.nf` example.

The existing Blender add-on double-unregister failure is tracked separately as a
pre-#48 baseline failure; it is not converted to an expected failure.
