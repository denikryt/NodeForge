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

## Compilation-session resolved environment

- `tests/unit/test_resolved_environment.py`: immutable catalog/system mappings, defensive copies, record placement invariants, deferred `CompileError` and complete `OSError` diagnostic replay, one active-state read, stable old/new session observations, exact import record identity, snapshot system dispatch, record-bound library calls, backend binding validation, parse-before-resolution ordering, and search-based rejection of compilation-path live discovery.
- `tests/unit/test_package_inventory.py`: manifest-derived library/system records match permanent live wrappers without state reads, explicit system-owner resolution, lazy record-bound handler loading, invalid active-record exclusion, exact collision diagnostics, and unchanged package state schema behavior.
- `tests/blender/test_library_catalogs.py`: unused malformed Local state remains deferred, failed snapshot lookup performs no rediscovery, root/parent/leaf compiler identity sharing, nested materialization after live lookups are disabled, direct `Compiler` one-resolution compatibility, and a new root session observing changed Local state.
- `tests/blender/test_unique_function_groups.py`, `tests/blender/test_update_group.py`, and the existing catalog regressions preserve shared/unique counts, ownership and package metadata, selected-root identity, topology, rollback, and temporary-group cleanup across the session-resolution refactor.
- UI catalog and package coverage continues to call the permanent live discovery APIs, proving that refresh operations observe current state independently of compiler snapshots.

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
- `tests/unit/test_function_instances.py`: compiler-reserved `__unique__` extraction, strict compile-time Bool validation, Semantic IR-to-instance-key compatibility, one-path reusable owner-scope serialization, canonical `IRFunctionMaterialization` dependency identity, literal pre-change fingerprint compatibility, missing-fingerprint fallback, root owner ID validation, and cycle detection.
- `tests/unit/test_function_materializer.py`: canonical local/imported dependency observation after successful access, exact materialization object reuse, local and imported cache-hit multiplicity, per-parent imported cache-hit recording, imported fingerprint propagation, missing-fingerprint fallback, direct catalog owner separation, publication-failure ordering, and Local catalog freshness fallback.
- `tests/unit/test_package_inventory.py`: direct catalog-definition materialization without compiler call state, explicit reusable-call/direct-definition authority discrimination, shared ownership metadata, and package replacement isolation.
- `tests/blender/test_unique_function_groups.py`: focused Blender regressions for local and editable imported shared/unique ownership, shallow unique instances, metadata lookup independent of Blender names, root rename durability, local dependency-change rebuilds, two-parent imported cache-hit dependency invalidation through `preserve_if_equivalent`, topology preservation, unsupported-call diagnostics, and physical transaction identity.
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

- `tests/unit/test_semantic_ir.py`: Blender-independent IR records, structural `IRArray` results, detached semantic constants/const-eval snapshots, unary-plus structural identity, Vector subscript normalization, Object-property ownership, canonical `BindingId` runtime references, explicit call/legacy-binding fallbacks, materialization invariants, and exact migration-marker contracts.
- `tests/unit/test_semantic_analysis.py`: semantic result/type validation plus cycle-safe detached constant snapshots that preserve list/tuple cycles, aliasing, and distinct opaque identities for const-eval while replacing opaque compiler/backend leaves without retaining original object identity.
- `tests/unit/test_semantic_backend_contract.py`: analyzer-derived operation/type contracts executed through the explicit lowering context and real `nodes.py` helpers, including recursive array-result reconstruction, Vector literal Combine XYZ realization, exact `ObjectValue` Object Info reuse, impossible unary-plus IR rejection, and no legacy retry after accepted semantic lowering.
- `tests/unit/test_runtime_bindings.py`: pure frontend runtime-binding identity, eager `BindingId` reservation, immutable snapshots, restore-without-ID-rewind, compiler runtime/structural exclusivity, pre-commit validation, generic package-owned `CompileTimeObject` structural compatibility including resolved-system assignment, shallow binding checkpoint identity, Blender-independence, and private-store ownership audits.
- `tests/blender/test_semantic_ir_compile.py`: production routing for structural arrays, Vector literals/subscripts, Object properties, and explicit call-parent fallback; real Blender RNA coverage for arrays/unary identity, named Vector constants, Object property reuse/configuration, comparison-chain topology, cycle-safe unused constants, and fresh-build cleanup after semantic/backend failure.
- `tests/blender/test_runtime_range_state.py` and statement/runtime regressions cover runtime-if/Repeat binding-map checkpoint restoration, nested loop-index shadowing, ordinary state rebinding, and builder state while preserving existing Geometry Nodes topology.

## Semantic callable resolution and Call IR

- `tests/unit/test_call_resolution.py`: immutable callable-environment snapshots, exact builtin/system/local/helper/library precedence, canonical imported `FunctionId`, detached `__unique__` parsing, unresolved diagnostic precedence, and pure-resolution dependency boundaries.
- `tests/unit/test_builtin_call_semantics.py`: exhaustive stateless-vs-stateful builtin classification, Blender-independent arity/type/keyword analysis, const-vs-runtime normalization, tuple results, raw single/named output modes, and explicit `geometry_builder()` expression diagnostics.
- `tests/unit/test_semantic_analysis.py` and `tests/unit/test_semantic_ir.py`: mixed expressions with core calls remain on the Semantic IR path, dynamic extension/stateful builtin fallbacks stay explicit, `IRCall`/`IRTuple`/`IRNamedOutputs` invariants, raw attribute/string-subscript selection, canonical `NFType` call operands/results, and exact stage-15 migration-marker contracts.
- `tests/blender/test_semantic_ir_compile.py`: core Call IR realization, depth-based node placement, raw-node dependency-first insertion-order exception, one-entry named-output structure, tuple selection, mixed-invalid raw-node error ordering, and stateful `grid`/`grid_uv` fallback behavior plus the corrected one-call/one-socket explicit-input contract.

## Blender group transaction backend

- `tests/unit/test_blender_group_backend.py`: nested savepoints, cache snapshots, commit/PONR ordering, multi-resource preparation, non-raising retirement diagnostics, custom-property marker preservation, and backend request contracts.
- `tests/unit/test_blender_group_authority.py`: persistent transaction-private authority filtering, reload-stable process-local provisional state, publication/forget behavior, and stale-entry cleanup.
- `tests/blender/test_update_group.py`, `tests/blender/test_library_catalogs.py`, and `tests/blender/test_unique_function_groups.py`: physical identity, rollback/cutover, mandatory input-declaration metadata publication failure, invalid-input-placement pre-cutover rejection, editable metadata finalization, leaked temporary/provisional authority exclusion, generated-resource liveness, and reusable-function integration.

## Expression behavior characterization baseline

`tests/blender/expression_characterization/` records and verifies black-box expression behavior through the production compiler and a real Blender `GeometryNodeTree`.

- `.nf` source cases map mechanically to committed canonical JSON baselines through the flat manifest case ID.
- Graph baselines cover ordered interface sockets, node types, semantic node properties/payloads, and exact links while excluding inactive Blender defaults.
- The manifest independently declares `graph` versus `compile_error` outcome, so the recorder cannot bless an unexpected success/failure transition.
- Cases cover structural list/tuple/index behavior, Vector indexing, named compile-time Vectors, Object properties/Object Info reuse and configuration, unary identity, successful mixed call-boundary expressions, compile-time container semantics, and representative nested expressions.
- Every case removes all node groups it created; controlled-failure cases additionally fail if compilation leaked any fresh group.
- Harness contract tests verify literal payload sensitivity, socket-reference uniqueness, semantic-default filtering, outcome enforcement, child-group cleanup, and pre-record missing-baseline behavior.
- Baselines are updated only by the explicit Blender recorder; ordinary pytest runs are read-only comparisons.


## Basic statement / function-body Semantic IR

- `tests/unit/test_semantic_body.py`: pure straight-line body analysis, deterministic body-local `BindingId` allocation/rebinding, augmented-assignment normalization, compile-time constant snapshots, explicit/auto outputs, stable per-target `InputDeclarationId` allocation, duplicate display labels, whole-body fallback, body-entry allocator audit, and static proof that the semantic/accepted route does not publish backend Values through `Compiler`.
- `tests/unit/test_input_declaration_identity.py`: versioned durable input metadata, mandatory metadata-write failure, semantic/physical identity separation, malformed/duplicate metadata rejection, value-equal stand-in separation, declaration-ID replacement mapping, type-compatibility gating, and fail-closed legacy ambiguity.
- `tests/unit/test_nf_types.py`: occurrence-aware physical input/default locators, duplicate-label independent defaults, and pre-declaration-metadata normalization.
- `tests/blender/test_semantic_body_ir.py`: one-session IRBody routing, assignment/augassign/output topology and placement, duplicate-label physical sockets/defaults, separation of display labels from runtime source bindings, and declaration-only `input_*` placement with no failure-time resource leak.
- `tests/blender/test_update_group.py`: durable explicit-input link/override restoration across duplicate-label insertion/reorder/display rename, unambiguous legacy migration, and pre-cutover rejection of ambiguous legacy live state.
- `tests/blender/test_semantic_ir_compile.py`: legacy stateful fallback keeps `grid`/`grid_uv` shared state while adopting the 0.51 one-call/one-socket explicit-input contract.
