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
- `tests/blender/test_library_catalogs.py`: unused malformed Local state remains deferred, failed snapshot lookup performs no rediscovery, root/parent/leaf compilation identity sharing, nested materialization after live lookups are disabled, and a new root session observing changed Local state.
- `tests/blender/test_unique_function_groups.py`, `tests/blender/test_update_group.py`, and the existing catalog regressions preserve shared/unique counts, ownership and package metadata, selected-root identity, topology, rollback, and temporary-group cleanup across the session-resolution refactor.
- UI catalog and package coverage continues to call the permanent live discovery APIs, proving that refresh operations observe current state independently of compiler snapshots.

## Object sockets

- `tests/unit/test_object_type.py`: Object token, builtin registration, local-function source, raw-node type token.
- `tests/blender/test_object_inputs.py`: Object interface sockets, lazy Object Info configuration/cache, keyword isolation, and configuration lock.

## Local function multiple returns

- `tests/unit/test_local_function_return_shapes.py`: fixed return-shape analysis, output naming, annotation resolution, generated helper source, and permanent structural return-shape indexing.
- `tests/blender/test_local_function_multi_return.py`: helper interfaces, one-node call materialization, unpacking, indexing, diagnostics, annotations, and scalar/library regressions.

## Local function helper identity and titles

- `tests/blender/test_local_function_multi_return.py`: metadata-based helper identity for long signatures, no false truncation collision, complete long-signature interfaces, readable helper datablock names and call-node titles, and legacy-name migration without datablock replacement.
- `tests/blender/test_local_functions.py`, `tests/blender/test_structural_local_functions.py`, and `tests/blender/test_update_group.py`: legacy short-name compatibility, real collision handling, rollback, and stable helper identity across updates.

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

- `tests/unit/test_semantic_ir.py`: Blender-independent IR records, structural `IRArray` results, detached semantic constants/const-eval snapshots, unary-plus structural identity, Vector subscript normalization, Object-property ownership, canonical `BindingId` runtime references, direct semantic diagnostics for pending callable categories, materialization invariants, and exact migration-marker contracts.
- `tests/unit/test_semantic_analysis.py`: semantic result/type validation plus cycle-safe detached constant snapshots that preserve list/tuple cycles, aliasing, and distinct opaque identities for const-eval while replacing opaque compiler/backend leaves without retaining original object identity.
- `tests/unit/test_semantic_backend_contract.py`: analyzer-derived operation/type contracts executed through the explicit lowering context and real `nodes.py` helpers, including recursive array-result reconstruction, Vector literal Combine XYZ realization, exact backend `ObjectValue` Object Info cache reuse, and impossible unary-plus IR rejection.
- `tests/unit/test_runtime_bindings.py`: pure frontend runtime-binding identity, eager `BindingId` reservation, immutable snapshots, restore-without-ID-rewind, compiler runtime/structural exclusivity, pre-commit validation, generic package-owned `CompileTimeObject` structural compatibility including resolved-system assignment, shallow binding checkpoint identity, Blender-independence, and private-store ownership audits.
- `tests/blender/test_semantic_ir_compile.py`: production routing for structural arrays, Vector literals/subscripts, Object properties, and explicit call-parent fallback; real Blender RNA coverage for arrays/unary identity, named Vector constants, Object property reuse/configuration, comparison-chain topology, cycle-safe unused constants, and fresh-build cleanup after semantic/backend failure.
- `tests/blender/test_runtime_range_state.py` and statement/runtime regressions cover runtime-if/Repeat binding-map checkpoint restoration, nested loop-index shadowing, ordinary state rebinding, and builder state while preserving existing Geometry Nodes topology.

## Raw node socket addressing / metadata schema v2

- `tests/unit/test_builtin_call_semantics.py`: Blender-independent `node(...)` selector normalization for unique names, exact non-negative addressable positions, contextual `ID(...)`, malformed/runtime-dependent selector diagnostics, duplicate normalized selectors, and named-output alias/selector separation.
- `tests/unit/test_raw_nodes.py`: generic direct-call-callee input-discovery semantics, centralized addressability (`NodeSocketVirtual`/unavailable exclusion), name/position/identifier resolver behavior, Blender-proxy physical equality, preflight-before-mutation, required non-empty durable identifiers, adversarial rejection of structural fallback identity, schema-v1/v2 parsing, exact v2 cutover identity, v1 name compatibility, and undeclared-link generic cutover behavior.
- `tests/unit/test_semantic_ir.py`: detached raw selector invariants (`str`, exact `int`, tagged identifier tuple), unique selector contracts, and named-output alias/type shape without Blender objects or persisted physical refs in Semantic IR.
- `tests/blender/test_raw_nodes.py`: real Blender 5.2 Math, Vector Math SCALE, Integer Math and Boolean Math duplicate-socket addressing; contextual identifier selection; positional/named output selectors; schema-v2 writes; v1-to-v2 update and rollback contracts; and existing raw literal/link/multi-input/update behavior.

## Semantic callable resolution and Call IR

- `tests/unit/test_call_resolution.py`: immutable callable-environment snapshots, exact builtin/system/local/helper/library precedence, canonical imported `FunctionId`, detached `__unique__` parsing, unresolved diagnostic precedence, and pure-resolution dependency boundaries.
- `tests/unit/test_builtin_call_semantics.py`: exhaustive stateless-vs-stateful builtin classification, Blender-independent arity/type/keyword analysis, const-vs-runtime normalization, tuple results, raw single/named output modes, and explicit `geometry_builder()` expression diagnostics.
- `tests/unit/test_semantic_analysis.py` and `tests/unit/test_semantic_ir.py`: mixed expressions with core calls remain on the Semantic IR path; local/imported/Python-extension callables stop at explicit migration diagnostics instead of legacy routing; `IRCall`/`IRTuple`/`IRNamedOutputs` invariants, raw attribute/string-subscript selection, canonical `NFType` call operands/results, and exact Semantic Call IR migration-marker contracts remain covered.
- `tests/blender/test_semantic_ir_compile.py`: core Call IR realization, depth-based node placement, raw-node dependency-first insertion-order exception, one-entry named-output structure, tuple selection, mixed-invalid raw-node error ordering, contextual `grid`/`grid_uv` realization, and the corrected one-call/one-socket explicit-input contract.

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

- `tests/unit/test_semantic_body.py`: pure body analysis, deterministic body-local `BindingId` allocation/rebinding, augmented-assignment normalization, compile-time constant snapshots, explicit/auto outputs, stable per-target `InputDeclarationId` allocation, duplicate display labels, direct migration/permanent diagnostics, the fail-closed internal `BODY_UNSUPPORTED` tripwire, body-entry allocator audit, and static proof that root compilation never enters `compile_statement()` after Semantic Body rejection.
- `tests/unit/test_input_declaration_identity.py`: versioned durable input metadata, mandatory metadata-write failure, semantic/physical identity separation, malformed/duplicate metadata rejection, value-equal stand-in separation, declaration-ID replacement mapping, type-compatibility gating, and fail-closed legacy ambiguity.
- `tests/unit/test_nf_types.py`: occurrence-aware physical input/default locators, duplicate-label independent defaults, and pre-declaration-metadata normalization.
- `tests/blender/test_semantic_body_ir.py`: one-session IRBody routing, assignment/augassign/output topology and placement, duplicate-label physical sockets/defaults, separation of display labels from runtime source bindings, and declaration-only `input_*` placement with no failure-time resource leak.
- `tests/blender/test_update_group.py`: durable explicit-input link/override restoration across duplicate-label insertion/reorder/display rename, unambiguous legacy migration, and pre-cutover rejection of ambiguous legacy live state.
- `tests/blender/test_semantic_ir_compile.py`: contextual `grid`/`grid_uv` stays Semantic-IR owned with shared compiler context while preserving the one-call/one-socket explicit-input contract.


## Fixed structural / Object semantic body ownership

- `tests/unit/test_semantic_structures.py`: detached tuple/named-output leaf descriptors, strict per-leaf Object provenance, Bundle leaf genericity, `IRBindLeaves` invariants, discarded-expression IR, forbidden aggregate `NFType` variants, and Blender-independent semantic-layer contracts.
- `tests/unit/test_semantic_body.py`: IRBody tuple assignment/projection/unpack, stored raw named outputs, standalone `Object.info()` discard semantics, Object alias/pass-through identity and configuration lock, scalar rebinding with stale body-local Object state, and direct controlled diagnostics for structural-result misuse.
- `tests/unit/test_semantic_analysis.py` and `tests/unit/test_semantic_ir.py`: frontend-owned Object semantic identity/state, exact Object Info configuration in IR, no duplicate Object-info backend semantic state, and Structural/Object/Bundle IR invariants.
- `tests/unit/test_semantic_backend_contract.py`: explicit Object Info configuration reaches the centralized Blender helper while backend `ObjectValue` remains a minimal physical cache carrier.
- `tests/unit/test_builtin_call_semantics.py` and `tests/unit/test_bundle_type.py`: Bundle remains `NFType.BUNDLE`, runtime String bundle paths remain typed, and no structural schema/type layer is introduced.
- `tests/blender/test_semantic_body_ir.py`, `tests/blender/test_raw_nodes.py`, `tests/blender/test_object_inputs.py`, and `tests/blender/test_bundle_runtime.py`: real-Blender topology/behavior regression targets for tuple/named-output body IR, Object Info, and Bundle routing. Source-backed local/imported calls are temporarily blocked at the semantic boundary pending callable-contract migration.

## Structured runtime control-flow Semantic IR

- `tests/unit/test_semantic_control_flow.py`: pure runtime-if construction, literal- and dynamic-condition runtime `IRIf` routing, top-level versus Repeat-local policy, both-branch semantic validation, conservative post-branch compile-time joins, deterministic Repeat mutation/state order, assignment-time exact carried-state typing across direct/augmented/unpack paths, including rejection of runtime-to-array/tuple/named-output rebinding, lexical iteration ownership, non-publishing nested carried state, durable branch input ordinals, and atomic fallback classification.
- `tests/unit/test_semantic_ir.py`: immutable `IRBranchMerge` / `IRIf` / `IRRepeatState` / `IRRepeat` structural invariants, including one exact `NFType` per Repeat state and branch merges without coercion metadata.
- `tests/unit/test_consteval.py`: ordinary `ast.If` is retained during preprocessing even for literal conditions, while the retained write-set barrier invalidates names either branch may write before later source transformations.
- `tests/unit/test_input_declaration_identity.py`: deterministic non-rewinding `InputDeclarationId` stable keys across opposite runtime branches, including literal-condition branches.
- `tests/blender/test_semantic_body_ir.py`: runtime-if IR routing, literal-condition Switch topology with both branch subgraphs, both-branch implicit-input discovery, and distinct branch declaration metadata.
- `tests/blender/test_runtime_range_state.py`: ordinary/nested Repeat IR routing, state item order/socket types, lexical iteration restoration, Repeat-local runtime-if behavior including constant-Bool conditions, exact Int/Float physical-and-semantic state parity, post-Repeat Int arithmetic, Bundle state, and evaluated nested results.
- `tests/blender/test_update_group.py`: transactional preservation of branch-declared input identities, user overrides, and external links across recompilation.
- `tests/unit/test_semantic_geometry_builder.py`: pure frontend builder state, pending/current topology rules, branch-local runtime-if construction (including legacy-compatible identity-assignment merge eligibility), inherited-builder rejection, Repeat hidden Geometry state, compile-time loop ownership, and backend-independence without a builder-specific static-if path.
- `tests/blender/test_geometry_builder.py`: real-Blender behavior/topology regression for `GeometryBuilder`, including Semantic Body production routing for the branch-local `value = value` runtime-if compatibility case; root compilation no longer falls back to the legacy statement engine.

## Contextual group Semantic IR

- `tests/unit/test_semantic_ir.py` and `tests/unit/test_builtin_call_semantics.py`: typed group-context slot/read/write invariants, projected `grid()` `(Geometry, Vector)` result contracts, const/runtime dimension slots, Float/Int acceptance, Bool rejection, and `grid_uv()` context-read diagnostics.
- `tests/unit/test_semantic_body.py` and `tests/unit/test_semantic_control_flow.py`: statement `store()` / `set_position()` context read-call-write lowering, exact contextual diagnostics including tuple and raw named-output/`NodeResult` operands, compiler-traversal availability through runtime-if/Repeat, attempt-local panel provenance, exact input aliasing, compile-time loop shadow/restore provenance, duplicate/membership rules, Switch/Repeat provenance clearing, one-root-compiler routing, direct extension migration diagnostics, and compiler-instance isolation.
- `tests/blender/test_semantic_ir_compile.py`: real Blender topology for contextual store/set-position chains and one-grid/one-UV realization, including const grid defaults without standalone dimension Value nodes and no legacy statement routing for accepted core bodies.
- `tests/blender/test_interface_panels.py`: native interface panel realization, implicit/explicit membership, exact Group Input alias acceptance, duplicate physical-input detection, and Switch/Repeat output rejection.
- `tests/blender/test_string_inputs.py` and `tests/blender/test_compile_time_fstrings.py`: runtime String attribute names and compile-time `domain`/`type` configuration remain unchanged through contextual statement migration.

## CTFE / residualization / runtime-fold boundaries

- `tests/unit/test_consteval.py`: distinct `ConstEvalUnavailable` versus hard `CompileError`, call ownership before argument CTFE, exact-Bool compile-time operators, closed-world runtime-fold denial, root-only compile-time-owned residualization, retained known/non-foldable assignments, source-ordered compile-time effect handoff, retained-runtime-`if` write invalidation without seed-source preservation, alias-preserving `CompileTimeForEffect` replay for erased compile-time loops, and recursive binding-write collection without alias/use analysis.
- `tests/unit/test_compile_time_state.py` and `tests/unit/test_semantic_control_flow.py`: one `ConstVector` owner, independent runtime-`if` branch snapshots, conservative post-branch compile-time joins, exact scalar type equality, Float/`ConstVector` discard, and inherited alias-sensitive identity preservation.
- `tests/unit/test_semantic_body.py`: source-ordered replay of preprocessing CT effects into one active `CompileTimeState`, sound post-`if` CT fact publication, seed-free runtime constant materialization from the state valid at each source position, late consumption of direct `range`/`len`/`sum`/list/tuple/f-string compile-time-owned assignments after recovered control-flow knowledge, alias-preserving erased-loop replay, and preservation of empty structural-array and Repeat residualization boundaries. `tests/blender/test_semantic_body_ir.py` and `tests/blender/test_compile_time_range_constants.py` cover the corresponding Blender topology, including retained numeric runtime operations, seed-free runtime-`if` materialization, and late compile-time-owned completion without legacy statement lowering.
- `tests/unit/test_semantic_analysis.py`, `tests/unit/test_builtin_call_semantics.py`, `tests/unit/test_function_instances.py`, and `tests/unit/test_consteval.py`: permanent static probes distinguish CTFE unavailability from hard errors; const-or-runtime builtin arguments fall back only on genuine unavailability; static options remain effect-free; parsing/f-string discovery and `__unique__` use the same outcome protocol.

## Declarative consumer evaluation modes

- `tests/unit/test_evaluation_modes.py`: complete pure selector matrix for `COMPILE_TIME_ONLY`, `RUNTIME_ONLY`, and compile-time-first `COMPILE_TIME_OR_RUNTIME`, including hard-error propagation, no CTFE probe for runtime-only requirements, and no runtime-analysis/fold-policy coupling.
- `tests/unit/test_consteval.py`: shared frontend/backend static Number and Vector-like shape predicates, including Bool rejection and exact three-component vector carriers.
- `tests/unit/test_builtin_call_semantics.py`: input compile-time-only acquisition, `instance_on_points()` and `transform()` mixed representation selection with frontend-owned static admissibility, exact runtime operand ordering/options, hard-error versus CTFE-unavailable behavior, and the intentionally richer non-migrated `set_material()` fallback boundary.
- `tests/unit/test_semantic_body.py`: input declarations keep runtime binding authority while defaults consume source-ordered compile-time knowledge without publishing the actual input value as a compile-time fact.
- `tests/blender/test_semantic_body_ir.py` and `tests/blender/test_semantic_ir_compile.py`: compile-time input metadata coexists with retained runtime topology, valid static/runtime mixed options preserve Blender topology and wiring, and invalid static mixed options fail without publishing a generated group.

## Type-directed numeric semantics

- `tests/unit/test_numeric_semantics.py`: direct `Int`/`Float` literal typing, signed-32 Int validation, exact floor quotient/remainder helpers including zero-divisor and `INT_MIN/-1` handling, canonical binary32 Float leaves/results, explicit Float overflow behavior, target-equivalent CTFE for the characterized core arithmetic surface, left-to-right `sum()` and Int `len()`, runtime-fold separation, input-default conversions, Integer Math/Math/Compare backend selection, Vector reciprocal-plus-SCALE division, compile-time range Int materialization, ordinary runtime-if Int merging, and removal of the historical Repeat Int-to-Float migration boundary.
- Existing consteval, semantic-analysis, Semantic IR, backend-contract, and control-flow regressions are updated to assert the canonical type-directed numeric contract alongside the focused numeric semantics suite.
- Blender numeric characterization/regression uses Blender 5.2 for Integer Math floor division/floored modulo, Float Math binary32 operation boundaries, Compare typing, Vector reciprocal-plus-SCALE behavior, and precision-sensitive Float results.

## Source-backed callable contracts

- `tests/unit/test_source_callables.py`: Blender-independent group preparation, exact lowered-source fingerprint payload, canonical panel/interface provenance including aliases, final panelized callable tuple order without duplicated input indices, Unicode-aware optional imported keyword aliases plus positional binding/ambiguity, source argument type compatibility, source snapshot versus owner-specific preparation caching, FunctionId-keyed recursion detection, prepared-only materialization specs, Local build-scoped physical cache/fingerprint observation, selected-root prepared reload forwarding, and static absence of the removed source-call legacy entry points.
- `tests/blender/test_source_callable_contracts.py`: prepared local-helper metadata, nested helper namespace propagation, punctuation-only imported labels through positional binding, typed Object result reconstruction, retained Local array diagnostics, selected Local reload ownership, package-facing materialization orchestration, source-backed production routing through prepared semantic compilation, trace-frame cleanup when physical lowering raises, and local hidden runtime/static argument topology derived from final input tuple positions.
- Existing semantic/materializer/Blender suites remain regression targets for source-backed callable behavior and physical topology. Dedicated source-call suites own the permanent callable-contract coverage, while Blender integration remains the required physical verification environment.

## Declarative Python extension API v2

- `tests/unit/test_extension_contracts.py` and `tests/unit/test_extension_interface.py`: canonical extension identities, tagged system/library owner keys, exact annotation-marker parsing, EvaluationMode requirements, signature/default normalization, finite overload discovery, exact result contracts, symbolic implementation-reference validation, and reload-safe interface rules.
- `tests/unit/test_extension_registry.py` and `tests/unit/test_extension_phase_lifecycle.py`: conservative owner-code snapshots/fingerprints including `semantic.py`, canonical declaration-only `interface.py`, phase-local helper generations, stale-session isolation, mounted semantic/physical invocation, lazy relative imports, and SEMANTIC/IMPLEMENTATION import isolation.
- `tests/unit/test_extension_values.py` and `tests/unit/test_extension_semantic_records.py`: owner-scoped nominal record identities, recursive TypeSpec field grammar, same-owner inheritance, RuntimeRef validation, deep-detached record/container packing, cycle rejection, insertion-order dictionaries, and exact current-session record reconstruction.
- `tests/unit/test_extension_semantic_records.py`, `tests/unit/test_extension_values.py`, `tests/unit/test_extension_execution_forms.py`, `tests/unit/test_extension_semantic_values.py`, and `tests/unit/test_extension_semantic_body.py`: unambiguous owner/name record identity, package-side dataclass construction hooks with field-only compiler reconstruction, deterministic backend-only/semantic-only/semantic-then-backend classification, nested expression-local semantic composition, canonical empty/nested-empty semantic LIST TypeSpec, TypeSpec-directed frontend semantic-state validation, runtime dependency detachment into ordinary Call IR operands, TypeSpec-directed `IRCallOperandRef` validation, semantic-only freshness, and canonical detached semantic payload construction before body persistence.
- `tests/unit/test_extension_semantics.py`: backend-only EXTENSION name/argument analysis, exact-before-compatible overload selection, selected-overload transport into ordinary Call IR, reserved call syntax, and mixed static/runtime argument reconstruction contracts.
- `tests/unit/test_extension_bootstrap.py`, `tests/unit/test_extension_packages.py`, and `tests/unit/test_extension_freshness.py`: structural package activation, package-atomic declaration admission, deferred package-library failures, v2 system reservation, install-time shared normalization, native-library collision inventory, semantic-use dependency collection, and existing FunctionCompilationFrame freshness integration.
- `tests/blender/test_extension_api_v2.py`: real Geometry Nodes physical dispatch through `ExtensionBackendContext`, mounted lazy implementation imports, exact semantic-state dataclass/subtype reconstruction, nested private state plus `ExtensionBackendValue` runtime leaves, semantic-only suppression of physical dispatch, semantic-then-backend generated-resource rollback, checked runtime socket results, and `nodeforge.math` v2 integration.
- `tests/blender/test_generated_resources.py`: context-owned generated Mesh/Curve/Object creation, metadata-before-commit failure handling, outer-transaction-only rollback initiation, exact-identity rollback, and best-effort multi-ID cleanup.

Package-defined semantic values may persist in source-variable/body state through compiler-owned hidden runtime snapshots; expression-local semantic composition remains the transient form before persistence. Mutation of existing persistent Blender IDs remains outside the generated-resource contract.

## Persistent extension semantic values

- `tests/unit/test_extension_semantic_values.py`: dependency-compact canonical payloads, first-use dependency ordering, discarded-runtime demand suppression, semantic LIST caller-side `*` normalization, source-list common nominal-base inference for sibling semantic records, fail-closed mixed/cross-owner/ambiguous hierarchy diagnostics, and malformed dependency-slot rejection.
- `tests/unit/test_persistent_extension_semantic_values.py`: body-owned persistent extension bindings, fresh hidden runtime snapshots, already-persistent dependency reuse, exact `IRBindLeaves` carriers, compile-time rebind/shadow semantics, runtime-if joins, Repeat category guards, semantic LIST persistence including inferred source-list literals, transient callback single-execution, and no implicit structural-array mutation semantics.
- `tests/unit/test_extension_semantic_body.py`, `tests/unit/test_local_function_analysis.py`, and `tests/unit/test_source_callables.py`: persistent semantic assignment/freshness plus explicit source-callable output/input/capture boundaries.
- `tests/blender/test_extension_api_v2.py`: real Blender reconstruction of persisted runtime leaves, suppression of discarded nested runtime-extension backends/generated resources, and Extension API v2 generated-resource rollback after persistent semantic reconstruction.

## Extension API v2 production cutover / L-System hard case

- `tests/unit/test_package_inventory.py`, `tests/unit/test_call_resolution.py`, `tests/unit/test_source_callables.py`, and related semantic-body fixtures: supported Python extension owners resolve only through Extension API v2; recognizable v1 `system.py`/native handler layouts are rejected before execution, while pure source callables retain their permanent path and `source.nf + interface.py` remains explicitly unsupported.
- `nodeforge.lsystem/tests/test_v2_contract.py` and `nodeforge.lsystem/tests/test_extension_v2_cutover_invariants.py`: L-System declaration/semantic/backend execution forms, package-record identity, static/runtime leaf normalization, generic persistence and Repeat rejection, backend reconstruction, marker invariants, backend category selection, resource-boundary source audits, and positive/negative invariant pairs for the Extension API v2 production cutover.
- `nodeforge.lsystem/tests/blender/test_functional_scenarios.py`: static/runtime L-System topology, persistent semantic constructor values including generic `parts = [...] ; ls_system(*parts)` semantic-list compilation, marker extraction, generated-resource rollback including late result-validation failure, and successful supported v2 compilation against a core where the legacy compiler API is physically absent.
## Legacy compiler physical removal

- `tests/unit/test_legacy_compiler_removal.py`: positive/negative invariants for the single semantic/IR production route, permanent backend helper ownership, declarative builtin namespace, minimal backend value carriers, total semantic/body boundaries, structured control-flow preservation, fail-closed v1/unsupported mixed-owner separation, Extension API v2 ownership, temporary-marker absence, and the permanent legacy-free core-version floor.
- `tests/unit/test_compile_time_state.py`, `tests/unit/test_semantic_structures.py`, `tests/unit/test_raw_nodes.py`, `tests/unit/test_object_type.py`, `tests/unit/test_bundle_type.py`, and related semantic suites now assert permanent authorities directly rather than importing deleted legacy executor/container APIs.
- `nodeforge.math/tests/test_math_v2_contract.py` proves the reference Math package contains only its supported Extension API v2 owner and no shipped v1 Mandelbrot backend artifact. `nodeforge.lsystem/tests/blender/test_functional_scenarios.py` compiles the supported hard-case v2 scenario normally against the legacy-free core.

## Sample Index and normalized default ownership

- `tests/unit/test_attribute_domains.py`, `tests/unit/test_builtin_call_semantics.py`, `tests/unit/test_semantic_ir.py`, and `tests/unit/test_semantic_backend_contract.py`: strict shared attribute-domain normalization, typed `sample_index()` static/runtime argument contracts, ordinary Call IR representation, required normalized-option access, pre-mutation backend validation, and generic call-result type enforcement.
- `tests/unit/test_legacy_compiler_removal.py`: audited physical helper signatures contain no duplicate source-language defaults, backend lowering does not reconstruct guaranteed normalized options, and legacy-free production-route invariants remain intact.
- `tests/blender/test_sample_index.py`: real Sample Index node type/domain/clamp/index-mode realization for Float/Int/Bool/Vector. Existing Capture, Store/Set Position, Instance on Points, panel, input, transform, and raw-node Blender suites lock the bounded source-default ownership cleanup.
## Runtime control-flow stabilization

- `tests/unit/test_semantic_control_flow.py` and `tests/unit/test_semantic_body.py`: Repeat-local runtime merge candidates, changed-only carried-state merges, deterministic `BindingId` ordering, general top-level/Repeat stale-runtime ownership invalidation, branch-local structural work, nested convergence composition, and compile-time-owned rebind ownership replacement while preserving exact NFType merge/Repeat contracts.
- `tests/unit/test_semantic_geometry_builder.py` and `tests/unit/test_persistent_extension_semantic_values.py`: ordinary runtime invalidation does not replace GeometryBuilder or package semantic-value ownership/merge authorities.
- `tests/blender/test_semantic_ir_compile.py`: terrain/erosion-style Repeat integration verifies one local `delta` Switch, no redundant carried `height` Switch, no promotion of iteration-local values to Repeat items, and evaluated results for both runtime branches.


## Repository hygiene and release closure

- `tests/unit/test_repository_hygiene.py`: Python filenames/content use semantic terminology instead of refactor chronology, and production Python contains no temporary compatibility/migration TODO markers.
- `tests/unit/test_legacy_compiler_removal.py`: release-coupled synthetic package ceilings derive from the current core version while legacy execution APIs remain physically absent.
- Final release validation additionally requires the complete unit/Blender regression matrix plus downstream package and extracted-artifact checks described by the release process.
