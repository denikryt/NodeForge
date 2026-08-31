# Test Refactor Coverage Map

| Previous monolithic entry point | Pytest target modules |
| --- | --- |
| `run_import_and_registry_checks()` | `tests/blender/test_import_registry.py` |
| `run_startup_shutdown_checks()` | `tests/blender/test_addon_lifecycle.py` |
| `run_compile_fixtures()` | `tests/blender/test_compile_fixtures.py` |
| Compile-time helper surface (`sign` consteval removal) | `tests/unit/test_consteval.py` |
| `run_library_checks()` | `tests/blender/test_library_functions.py` |
| `run_update_checks()` | `tests/blender/test_update_group.py` |

| Geometry builder DSL accumulation and Repeat Zone state | `tests/blender/test_geometry_builder.py` |

## Core test package isolation

- `tests/blender/conftest.py`: every core Blender test starts with an isolated empty NodeForge package inventory, so the core suite cannot silently depend on user-installed packages.
- Package-manager and library-boundary tests create temporary synthetic packages inside the test itself when package behavior is the NodeForge behavior under test. Package-owned function/example/system behavior belongs to the package repositories, not the NodeForge core suite.

## Object sockets

- `tests/unit/test_object_type.py`: Object token, builtin registration, local-function source, raw-node type token.
- `tests/blender/test_object_inputs.py`: Object interface sockets, lazy Object Info configuration/cache, keyword isolation, and configuration lock.

## Local function multiple returns

- `tests/unit/test_local_function_return_shapes.py`: fixed return-shape analysis, output naming, annotation resolution, generated helper source, and `TupleValue` indexing.
- `tests/blender/test_local_function_multi_return.py`: helper interfaces, one-node call materialization, unpacking, indexing, diagnostics, annotations, and scalar/library regressions.

## Local function helper identity and titles

- `tests/blender/test_local_function_multi_return.py`: metadata-based helper identity for long signatures, no false truncation collision, complete long-signature interfaces, readable helper datablock names and call-node titles, and legacy-name migration without datablock replacement.
- `tests/blender/test_local_functions.py`, `tests/blender/test_stage18_local_functions.py`, and `tests/blender/test_update_group.py`: legacy short-name compatibility, real collision handling, rollback, and stable helper identity across updates.

## Unique function-group instances

- `tests/unit/test_compiler_identities.py`: immutable `BindingId`/`FunctionId`/`CallSiteId` records, exact legacy stable serialization, compiler-owned shared/unique materialization resolution, unique-only occurrence sequencing, core-package normalization, and hard-coded unique instance-key digest compatibility.
- `tests/unit/test_function_instances.py`: compiler-reserved `__unique__` extraction, strict compile-time Bool validation, Semantic IR-to-instance-key compatibility, canonical owner scopes, root owner ID validation, and trace-derived fingerprint payload behavior.
- `tests/unit/test_package_inventory.py`: direct catalog-definition materialization without compiler call state, explicit reusable-call/direct-definition authority discrimination, shared ownership metadata, and package replacement isolation.
- `tests/blender/test_unique_function_groups.py`: focused Blender regressions for local and editable imported shared/unique ownership, shallow unique instances, metadata lookup independent of Blender names, root rename durability, dependency-change rebuilds, unsupported-call diagnostics, and physical transaction identity.
- `tests/blender/test_function_root_identity.py`: isolated root rename/save/reopen persistence for root owner ID, local definition owner, unique instance key, helper identity, and manual Float Curve state across an unchanged post-reopen update.

## Native interface panels

- `tests/blender/test_interface_panels.py`: root-only `panel()` syntax, native Blender panel hierarchy, implicit-input membership, resolved-socket alias identity, foreign-socket ownership rejection, real `GeometryNodeGroup` exposure, and generic nested-panel interface copying.
- `tests/blender/test_update_group.py`: transactional panel cutover, socket reorder/default propagation, visible value and external-link restoration, temporary-group cleanup, and rollback after destructive reset.

## Local dependency isolation

- `tests/blender/test_library_catalogs.py`: fresh Blender-suffixed Local backing groups per outer compilation, per-build reuse for repeated calls, preservation of existing generated node dependencies, and unchanged selected-group update semantics.

## Library-backed group reload

- `tests/blender/test_library_catalogs.py`: current Local source resolution, dependency refresh with unchanged root source, root identity, missing-source failure, and compile rollback cleanup.
- `tests/blender/test_update_group.py`: selected node-instance overrides, new defaults, incoming/outgoing links, and root identity across direct library reload.
- `tests/blender/test_library_catalogs.py`: same-package version upgrades, rejection of cross-package takeover for the same catalog identity, and native-only reload exclusion.
- `tests/unit/test_ui_library_panels_contract.py`: **Reload from Source** is exposed in the main selected-group panel and does not alter catalog panel actions.

## Local ownership and imported folders

- `tests/blender/test_local_linked_sources.py`: imported-folder add/remove and missing-root cleanup, root-overlap rejection, legacy linked-file cleanup, path-addressed managed save/overwrite/delete under duplicate public names, empty-folder deletion, and symlink-escape preflight.
- `tests/unit/test_local_linked_sources_contract.py`: folder-only Local import UI, explicit destructive action labels, path-addressed managed mutations, root overlap checks, and atomic registry publication contract.

## Runtime String values

- `tests/unit/test_string_type.py`: `String` type token, `input_string` registration, local-function source lowering, constant argument inference, and raw-node type-token parsing.
- `tests/blender/test_string_inputs.py`: String interface/defaults, literal lowering, raw String sockets, runtime attribute names for `store_named_attribute()` and `store()`, String Switch lowering, local/library function calls, update default preservation, and compile-time configuration/type diagnostics.

## Runtime Bundle values

- `tests/unit/test_bundle_type.py`: `Bundle` token, `input_bundle`, raw-node registration, local-function lowering, and runtime-only constant rules.
- `tests/blender/test_bundle_runtime.py`: heterogeneous/nested Bundle construction, runtime paths, get/set evaluation, group panels, local/local-catalog/library boundaries, raw nodes, Bundle Switch and Repeat state, transactional dynamic-socket updates/rollback, and controlled diagnostics.

## Semantic IR expression boundary

- `tests/unit/test_semantic_ir.py`: Blender-independent IR records, canonical `BindingId` runtime binding references, immutable `IRFunctionMaterialization` shared/unique invariants, literal/binding typing, name precedence, operator contracts, ordered mixed-tree ownership, explicit immutable `BlenderIRLoweringContext`, binding/materialization invariants, Object attribute fallback, conditional ownership, downstream materialization-authority contracts, and migration-marker invariants.
- `tests/unit/test_semantic_backend_contract.py`: analyzer-derived finite operation/type matrix executed through the explicit lowering context and the real `nodes.py` realization helpers, plus commitment semantics proving backend failure after successful analysis propagates without legacy AST retry.
- `tests/blender/test_semantic_ir_compile.py`: production IR routing, direct context lowering over real Blender RNA for analyzer-reachable backend signatures, representative graph materialization, unary topology/socket identity, comparison-chain pairwise materialization/layout, and fresh-build cleanup on semantic or partially materialized backend failure without legacy retry.

## Blender group transaction backend

- `tests/unit/test_blender_group_backend.py`: nested savepoints, cache snapshots, commit/PONR ordering, multi-resource preparation, non-raising retirement diagnostics, custom-property marker preservation, and backend request contracts.
- `tests/unit/test_blender_group_authority.py`: persistent transaction-private authority filtering, reload-stable process-local provisional state, publication/forget behavior, and stale-entry cleanup.
- `tests/blender/test_update_group.py`, `tests/blender/test_library_catalogs.py`, and `tests/blender/test_unique_function_groups.py`: physical identity, rollback/cutover, editable metadata finalization, leaked temporary/provisional authority exclusion, generated-resource liveness, and reusable-function integration.
