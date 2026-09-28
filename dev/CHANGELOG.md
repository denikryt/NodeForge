# Development changelog

## 0.49.51

- Added the `Material` DSL type, `input_material()`, runtime material support in `set_material()`, Material sockets for local/library functions and raw nodes, plus regression tests.

## 0.49.52

- Added the `Object` DSL type, `input_object()`, lazy `obj.info()` configuration, and `.geometry`, `.location`, `.rotation`, and `.scale` Object Info properties across raw, local, and library sockets.

## 0.49.53

- Expanded Object DSL coverage across compile-time validation, lazy state, local/library/raw boundaries, runtime Object Info evaluation, and shared regression suites.

## 0.49.54

- Added multi-value returns for local functions, flat tuple/list unpacking, compile-time tuple indexing, explicit positional parameter type annotations, and Blender regression coverage for the new compiler paths.

## 0.49.55

- Added the modular Minecraft biome example and fixed tuple unpacking inside `repeat_range`, unpack-target implicit input discovery, and type-token capture analysis for local raw-node calls.

## 0.49.56

- Prevented false local-function helper collisions caused by Blender name truncation and added readable function-name labels to local-function call nodes.


## 0.49.57

- Made local-function helper node groups use short readable function titles while keeping helper reuse and updates keyed by ownership metadata rather than datablock names.

## 0.49.58

- Added experimental `capture_attribute()` support for Float, Int, Bool, and Vector fields; Blender also supports Color capture, but NodeForge does not yet implement a Color DSL type.
- Fixed local-function call result binding to use declared output names, preventing stale input sockets from being treated as outputs during Repeat Zone compilation.

## 0.49.59

- Fixed raw-node and local-function socket resolution to reject stale sockets with the wrong direction or declared type during helper updates.
- Tightened `capture_attribute()` implementation around Blender's Capture Attribute API: NodeForge now validates domains before node creation, names the internal mapping as Blender socket types, keeps `Vector` capture on the required `VECTOR` socket type, and covers the new builtin in the public callable-surface contract.
- `capture_attribute()` intentionally supports only the current NodeForge runtime field types: Float, Int, Bool, and Vector. Blender Capture Attribute also has socket/data families such as Color/RGBA, Rotation, Matrix, String, object-like sockets, bundles, closures, and newer domains such as Grease Pencil Layer, but these are not implemented until NodeForge has matching DSL value types and authoring rules.

## 0.49.60

- Added multi-output unpacking for imported local `.nf` groups, allowing assignments such as `a, b = local_group(...)`.
- Added `.nf` file import to the Local section UI, including multi-file import, folder targeting, replacement of existing scripts, and immediate refresh.

## 0.49.61

- Added deletion of saved Local scripts from the Local section UI, with confirmation, catalog-path validation, and immediate refresh.

## 0.49.62

Fixed transactional node-group updates for capture_attribute() by preserving Blender Capture Attribute dynamic capture_items and their sockets during compiler cutover.

## 0.49.64

- Added the `panel()` DSL declaration for grouping node-group inputs into native Blender interface panels, including collapsed panels, root-only validation, identity-safe membership checks, and hierarchy-preserving transactional updates.

## 0.49.65

- Made Local `.nf` dependencies content-addressed snapshots so compiling newer Local sources no longer mutates backing groups used by existing generated nodes; transitive Local source changes produce new snapshots while explicit selected-group updates remain intentional.

## 0.49.66

- Added persistent external Local source folders that are read directly from disk without copying, plus visible Local folder rows and an explicit managed destination for copy/save operations.

## 0.49.67

- Replaced hash-named Local dependency snapshots with fresh Blender-managed datablocks using native `.001`, `.002`, and later suffixes for each new compile, while preserving the existing explicit node-group update behavior.

## 0.49.68

- Reworked the Local UI into a minimal folder browser with navigable directories, current-folder Save/New Folder behavior, and file-or-folder imports that reference external `.nf` sources directly instead of copying them.

## 0.49.69

- Added nested `repeat_range()` support with nested Repeat Zone state propagation, runtime-frame scoping, GeometryBuilder state inheritance, and nested implicit Int count inference.

## 0.49.70

- Fixed nested `repeat_range()` lexical index restoration when an inner Repeat assigns the enclosing loop index, including runtime-`if` branch scopes.

## 0.49.71

- Added nested Repeat Zone transactional-update and save/reopen persistence regression coverage.

## 0.49.72

- Added **Reload from Source** for selected library-backed node groups, preserving root identity and selected-node state while rebuilding from the current catalog source.

## 0.49.73

- Simplified Local external sources to read-only imported folders with explicit **Remove from Local**, added managed file and empty-folder deletion, path-based managed mutations, imported-root overlap checks, and folder-only **Add Folder...** selection.

## 0.49.74

- Restored Local folder row selection by moving folder navigation to a separate arrow action, so managed folders and imported roots can be selected before Delete/Remove.
## 0.49.75

- Removed catalog/filesystem resolution from the NodeForge N-panel redraw path so selected-node UI state changes no longer rescan Local roots or package manifests.

## 0.49.76

- Preserved generated Blender resources when NodeForge is disabled or uninstalled so compiled Geometry Nodes setups continue evaluating without the add-on enabled.

## 0.49.77

- Added runtime `String` values with `input_string()`, String sockets across raw/local/library calls, and runtime String attribute names for `store_named_attribute()` and `store()`.

## 0.49.78

- Added first-class runtime `Bundle` values with `input_bundle()`, `bundle()`, `bundle_get()`, `bundle_set()`, Bundle sockets across raw/local/library calls, native Switch/Repeat state, and transactional dynamic-item preservation.

## 0.49.79

- Preserved compatible input overrides and external links for every shared `GeometryNodeGroup` instance during in-place updates, including cross-instance links and strict rollback restoration.

## 0.50.0

- Added compiler-reserved `__unique__=True` for supported reusable function-group calls, with shallow per-occurrence local/imported group instances, persistent root owner IDs, metadata-based imported ownership, trace-derived editable-graph fingerprints, and generalized function-group transaction rollback.

## 0.50.1

- Fixed `__unique__` validation before compile-time folding, exact function-group savepoint/cache rollback across repeated mutations, Curve Mapping manual-state restoration, and readable legacy local-helper naming during transactional updates.

## 0.50.2

- Added the first typed Semantic IR expression path, separating pure AST semantic lowering from Blender node materialization while preserving existing runtime behavior and fallback coverage.

## 0.50.3

- Made the NodeForge core test suite self-contained by removing package-owned math/L-System/example/layout coverage, replacing package-manager dependencies with synthetic fixtures, and isolating Blender tests from user-installed package inventories.

## 0.50.4

- Converted the initial Semantic IR expression path to program-local typed values and ordered operations, with Blender lowering owning explicit IR-value materialization while preserving existing graph topology and placement.

## 0.50.5

- Split the migrated expression frontend into explicit semantic resolution/type checking followed by Semantic IR emission, with immutable compiler-state snapshots and exhaustive NodeForge/Blender backend realization contracts while preserving existing DSL behavior and generated graph topology.
- Fixed Bool comparisons on Blender 5.2 by realizing the existing Bool comparison semantics through `FunctionNodeCompare` Int inputs instead of the removed `BOOLEAN` compare data type.

## 0.50.6

- Made Semantic IR Blender lowering use an explicit immutable backend context instead of the general compiler object, preserving existing expression behavior and Geometry Nodes realization.

## 0.50.7

- Added canonical compiler-owned `BindingId`, `FunctionId`, and `CallSiteId` identities across Semantic IR runtime bindings and reusable-function materialization while preserving existing graph topology, metadata strings, fingerprints, and unique instance keys.

## 0.50.8

- Moved reusable-function shared/unique materialization policy into immutable Semantic IR while preserving existing `CallSiteId` instance keys, ownership/freshness metadata, direct catalog-definition materialization, and Geometry Nodes behavior.

## 0.50.9

- Separated editable reusable-function physical materialization into a dedicated `FunctionMaterializer`, preserving existing local/imported cache, freshness, reload, metadata, and Blender transaction behavior while making the materializer result the single source of physical instance identity for caller-node metadata.
## 0.50.10

- Extracted physical GeometryNodeTree create/update publication into `BlenderGroupBackend`, with one nested rollback/commit transaction, atomic editable catalog metadata finalization, and authority-safe handling of leaked temporary/provisional groups and generated-resource manifests.

## 0.50.11

- Fixed rollback-backup construction so failed snapshot copies clean up their private datablock, restored logical local-helper namespaces during parent updates, and kept dedicated failure injection on destructive authoritative cutover.

## 0.50.12

- Moved reusable freshness dependency observation to canonical `IRFunctionMaterialization` identity, unified physical and fingerprint owner serialization, and fixed imported cache hits so every successful parent access records the child fingerprint.

## 0.50.13

- Resolved active package catalogs and system constructors once per root compilation into an immutable environment shared by all nested builds, with record-bound imports, deferred Local discovery errors, and preserved live UI inventory behavior.

## 0.50.14

- Added Blender expression behavior characterization baselines with `.nf` source fixtures, canonical JSON node-tree snapshots, explicit graph/error outcome contracts, semantic literal and Object Info payload capture, isolated per-case cleanup, explicit baseline recording, manifest navigation, readable mismatch diffs, and targeted expression-boundary coverage.

## 0.50.15

- Completed Semantic IR ownership of the non-call expression surface with structural arrays, cycle/alias-preserving detached compile-time const-eval snapshots with distinct opaque placeholders, Vector indexing, Object properties, production-routing coverage, and backend-preserving materialization while retaining explicit call and legacy-binding fallbacks.

## 0.50.16

- Replaced internal runtime semantic type strings with canonical `NFType` identities while preserving public DSL syntax, Blender topology, durable metadata tokens, and reusable-function identity.

## 0.50.17

- Added compiler-owned semantic callable resolution and typed Call IR for stateless core expression calls, with canonical imported identity validated before native module execution and explicit whole-expression/stateful/dynamic compatibility fallbacks preserving existing public DSL behavior.

## 0.50.18

- Moved ordinary runtime source bindings to compiler-owned `BindingId`/`NFType` metadata with separate Blender `Value` materializations, preserving existing DSL behavior while removing the heterogeneous `Compiler.vars` binding store.
- Preserved package-system compile-time state assignment by accepting the generic `CompileTimeObject` extension protocol in the temporary structural binding store, with resolved-system regression coverage.
- Fixed `repeat_range()` local multi-return assignment so temporary `TupleValue` bindings remain structural compiler state instead of being routed through ordinary runtime `Value` bindings.

## 0.51.0

- Added compiler-owned straight-line function/group body IR for ordinary assignments, direct explicit inputs, augmented assignments, outputs, and final expressions, with body-local `BindingId -> Value` Blender materialization and whole-body fallback for remaining structural/control-flow/stateful categories.
- Corrected explicit `input_*` identity so the string argument is display text only: every call creates its own interface socket, duplicate labels retain independent defaults, and reusable calls reject ambiguous duplicate-label keywords while preserving positional binding.
- Added durable `InputDeclarationId` metadata for direct explicit inputs so transactional updates preserve links and overrides by compiler-owned declaration identity rather than duplicate-label occurrence, with fail-closed migration for ambiguous legacy live state.
- Made explicit-input declaration metadata mandatory for publication and restricted `input_*` to a complete simple-assignment RHS, rejecting nested/general expression use before Blender effects.
- Preserved user overrides for groups that mix implicit runtime inputs with explicit `input_*` declarations by merging both persisted script-default metadata stores during update capture.
## 0.51.1

- Moved fixed tuple/raw named-output body bindings and migrated Object Info configuration/alias locking into compiler-owned semantic state, while preserving legacy whole-body fallback behavior and keeping Bundle as the existing opaque runtime type.

## 0.51.2

- Moved ordinary runtime `if` and `repeat_range()` into structured Semantic IR with compiler-owned branch/repeat state, lexical iteration identities, deterministic carried-state ordering, and recursive Blender lowering while preserving legacy topology, Int/Float Repeat behavior, durable input-declaration identity, and whole-body fallback for builder/array/stateful/dynamic categories.

## 0.51.3

- Separated const-evaluable compile-time bindings from runtime semantic and backend state with explicit snapshot/fork ownership, preserving whole-body fallback atomicity, control-flow constant threading, legacy structural fallback, and generated Geometry Nodes behavior.

## 0.51.4

- Moved mutable structural arrays and ordinary compile-time/array `for` unrolling into Blender-independent semantic body state with explicit array identity, alias-preserving immutable snapshots, recursive persistent leaves, and atomic speculative compile-time loop rollback while preserving legacy compatibility boundaries and Geometry Nodes behavior.

## 0.51.5

- Moved accepted core `GeometryBuilder` construction, accumulation, snapshots, compile-time iteration, runtime-if branch-local state, and Repeat carried state into Blender-independent semantic body ownership using existing typed Geometry Call/control-flow IR, while preserving historical behavior/topology (including legacy-accepted branch-local runtime-if cases with identity assignments such as `value = value`) and isolating the legacy backend builder path to separately selected compatibility bodies.

## 0.51.6

- Moved core contextual group semantics for statement `store()` / `set_position()`, `grid()` / `grid_uv()`, and `panel()` into compiler-owned Semantic Body/IR state, preserving existing Geometry Nodes topology, interface behavior, traversal-order context semantics, and the isolated whole-body compatibility route for dynamic Python extensions, including a scoped legacy `grid/grid_uv` expression bridge for mixed fallback bodies.
- Fixed contextual Semantic Body compatibility by preserving legacy statement-form `store()` / `set_position()` diagnostics and per-argument `grid()` type diagnostics, and by restoring p
nel input provenance when compile-time loop targets temporarily shadow runtime structural-array leaves.
- Restored contextual-group diagnostic compatibility for expression keyword/arity precedence and fixed tuple operands in statement-form `store()` / `set_position()`, with exact regressio
 coverage against the retained legacy path.
- Restored legacy `NodeResult` diagnostics when raw named-output results are passed directly to statement-form `store()` or `set_position()`, with byte-for-byte migrated-vs-legacy regres
ion coverage.

## 0.52.0

- Moved named scalar-math callable-to-Blender-operation ownership into `nodeforge.math`, removed the legacy core math-call CTFE allowlist and historical `count_zero()` evaluator, and kept source-language arithmetic plus structural/core compile-time helpers owned by NodeForge.

## 0.53.0

- Disabled whole-body legacy compiler fallback for production root compilation. Semantic Body is now the sole root-body route; known unsupported source forms fail with controlled diagnostics and unexpected residual `BODY_UNSUPPORTED` reaches a fail-closed internal tripwire.
- Temporarily blocked local/imported source calls and v1 Python extension callables at the semantic boundary until their typed callable and declarative extension contracts are migrated, without adding a replacement compatibility compiler.
- Made compile-time `if` diagnostics authoritative to the selected branch so errors are not swallowed and reinterpreted as runtime control flow.

## 0.54.0

- Separated compile-time evaluation unavailability from hard compiler-owned errors and introduced an explicit closed-world runtime-fold permission boundary, so known runtime-capable expressions are conservatively retained unless graph removal is proven safe.
- Made runtime-`if` compile-time facts sound by forking both branches from the same incoming snapshot, conservatively joining their exits, invalidating preprocessing facts for names that retained runtime branches may write, and preserving residual source seeds needed to reconstruct incoming runtime branch state.
- Added late Semantic Body consumption for narrowly compile-time-owned assignment roots when sound control-flow joining recovers the required facts after preprocessing; ordinary `if` branch-selection semantics remain intentionally unchanged pending the dedicated runtime-only migration.

## 0.54.1

- Fixed source-order correctness across compile-time preprocessing and Semantic Body by replaying erased compile-time effects at their original residual positions instead of seeding body analysis from the final preprocessing state; removed the whole-body runtime-if seed prescan and made ordinary assignment RHS analysis observe the pre-assignment compile-time state.
- Simplified runtime-if semantic results to expose only the authoritative merged compile-time exit and consolidated static-only builtin CTFE diagnostics through the shared `_const()` boundary.
- Preserved alias identity when replaying erased compile-time `for` loops by replacing copied iteration bindings/discards with one scoped `CompileTimeForEffect` that re-evaluates the iterable in replay state, binds exact yielded objects, restores the exact pre-loop target, and rejects iteration-count drift.

## 0.55.0

- Made ordinary statement `if` always use runtime Geometry Nodes control flow. Literal and compile-time-known conditions no longer prune source branches; both branches are semantically validated and follow the normal runtime-if merge rules.
- Kept ordinary `if` residual through compile-time preprocessing while preserving the conservative branch-write barrier that prevents later preprocessing from observing stale compile-time facts.

## 0.55.1

- Added frontend-only declarative evaluation modes for compile-time-only, runtime-only, and compile-time-or-runtime consumer requirements while keeping runtime folding, semantic typing, and IR representation separate.
- Migrated input compile-time acquisition plus `instance_on_points()` and `transform()` mixed options to the shared representation selector, moving invalid static option rejection into semantic analysis before Blender realization while preserving existing diagnostics and runtime operand order.
- Centralized the existing compile-time Number/Vector-like shape predicates for shared frontend/backend validation and retained `set_material()` as an explicit richer consumer-specific fallback boundary.

## 0.56.0

- Added type-directed numeric semantics: integer literals and integer-preserving arithmetic now remain signed-32 `Int`, mixed/Float arithmetic uses canonical Blender-compatible binary32 values, `//` and floored `%` have explicit Integer Math/Math lowering, and scalar comparisons choose typed Compare nodes.
- Unified core numeric CTFE with the runtime language contract for characterized arithmetic, `len()` and left-to-right `sum()`, while keeping runtime numeric folding fail-closed and leaving named `nodeforge.math` callables package-owned.
- Extended existing Vector scalar arithmetic to accept Int scalars without a cast IR, preserved reciprocal-plus-SCALE vector division semantics, and left the historical Repeat Int-to-Float publication boundary explicitly staged for the next migration.

## 0.57.0

- Made `repeat_range()` carried state exact-typed across entry, loop body, runtime branch merges, Repeat sockets, and post-Repeat publication. `Int` state now remains `Int` after Repeat, while type-changing assignments, runtime-to-structural rebinding, and branch merges fail with controlled compiler diagnostics instead of changing carried binding semantics or being logically retagged.

## 0.58.0

- Restored script-local and pure source-backed imported function calls on frontend-owned semantic callable contracts, with typed source Call IR and owner-specific prepared semantic groups materialized only after analysis.
- Made source function interfaces, output types, panelized callable order, defaults, source snapshots, and durable compilation identity semantic facts; Blender interfaces are now downstream realizations checked for parity instead of signature probes.
- Kept native/hybrid Python extension callables migration-disabled pending the declarative extension API, while preserving Local build-scoped reuse, reusable shared/unique ownership, catalog reload, and function freshness tracking through the prepared-only backend boundary.

## 0.58.1

- Made imported source-call keyword aliases Unicode-aware and optional, so arbitrary Blender input labels remain positionally callable while normalized Unicode labels support keyword addressing and only non-empty aliases participate in ambiguity checks.

## 0.58.2

- Simplified prepared group publication to carry one typed physical build request directly through materialization and Blender lowering, removing the legacy dict round-trip, duplicated identity options, and preparation-only backend state.
- Removed unused source-call/group semantic record fields whose information was either derivable from canonical identities or had no production consumer, keeping one authority for interface origin, cache keys, and body compile-time state.

## 0.58.3

- Simplified prepared group materialization so `GroupInterfaceContract.inputs` is the single authority for implicit entry `BindingId` provenance and physical input creation, removing the duplicate `SemanticGroupCompilation.entry_binding_ids` state and reverse lookup.

## 0.58.4

- Simplified physical compilation tracing to use the standard context-manager lifecycle with `nullcontext()` when no trace frame is active, removing manual `__enter__()` / `__exit__()` exception plumbing while preserving trace cleanup and cycle behavior.
- Removed the redundant `GroupInputContract.index`; final input position now comes exclusively from `GroupInterfaceContract.inputs` tuple order, while `GroupOutputContract.index` remains explicit for contextual-output offsets.

## 0.59.0

- Added declarative Python extension API v2 for backend-only systems and native libraries, including typed interface contracts, finite overloads, snapshot-backed lazy implementation disp
tch, extension Call IR, physical backend context/value validation, transaction-owned generated resources, and the `nodeforge.math` system migration.
- Simplified extension lifecycle ownership so retained package enumeration validates only v1 system declarations, while install/root v2 normalization uses the canonical library owner ide
tity throughout bootstrap state.
- Hardened snapshot-only extension dispatch so only modules executed by the captured-source loader persist in an owner session, and removed the unused registry-level duplicate implementa
ion-reference map.
- Centralized canonical extension invocation semantics so normalized callable specs derive their Python signature once and semantic/IR boundaries share one transport-position validator.
- Fixed nested owner-local relative imports by mounting/restoring the synthetic extension root package, simplified snapshot namespace-package derivation to one post-capture pass, unified
generated-resource ownership/publication through one transaction operation, and removed dead private package compatibility wrappers that could execute v2 interfaces outside the supported
lifecycle.
## 0.60.0

- Added package-defined semantic values to extension API v2: owner-scoped frozen record schemas, `RuntimeRef`, expression-local semantic composition, semantic-only and semantic-then-backend execution, phase-isolated `semantic.py`/implementation lifecycles, detached semantic state in ordinary Call IR, and typed backend reconstruction while deliberately deferring body persistence to persistent semantic-value integration.
- Made `interface.py` the declaration-only canonical cross-phase module, rejected every owner-local child-module import while it executes, kept ordinary helpers phase-local to semantic/implementation generations, and required semantic and physical package function bodies to execute inside their active owner-session mounts so lazy relative imports cannot escape the captured snapshot boundary.
- Hardened semantic record identity and state publication by rejecting ambiguous owner-local dataclass aliases, validating detached semantic state against exact dependency NFTypes before canonical `AnalyzedCall` creation, and allowing package-side dataclass construction hooks while compiler reconstruction remains field-only and side-effect-free.
- Extended the paired `nodeforge.math` package compatibility metadata for NodeForge 0.60.0 without changing its backend-only math implementation semantics.

## 0.61.0

- Added persistent package-defined semantic values to Extension API v2: semantic records and semantic lists can survive source assignment with hidden runtime-dependency snapshots, canonical dependency compaction, body/control-flow ownership, and later semantic/backend reconstruction without exposing compiler bindings to packages.
- Made retained packed semantic dependencies the runtime-demand authority, so semantic callbacks that discard a `RuntimeRef` do not lower the discarded runtime expression or create backend/generated-resource side effects solely for it.
- Added generic caller-side `*` expansion for semantic lists with element-local dependency compaction, while keeping semantic lists distinct from mutable structural arrays and keeping package semantic values outside source-callable public/capture contracts and Repeat carried state.

## 0.61.1

- Fixed runtime-if extension-state joining so an incoming persistent package semantic binding cannot be silently replaced by another body ownership category on both branch exits.
- Relaxed two Blender extension rollback regressions from an obsolete exact error wording while retaining physical type-mismatch, backend-invocation, and generated-resource rollback assertions.

## 0.61.2

- Simplified persistent extension semantic plumbing without changing behavior: semantic LIST star expansion now passes canonical payloads directly through signature binding, nested payload reconstruction reuses one dependency-import path, and semantic-only calls reuse their already-canonical callback payload.
- Made extension dependency lowering return its exact ordered carrier leaves to body persistence, removing body-layer decoding of single-value versus tuple carrier representation.
- Made `_BodySemanticState` keyword-only and converted its construction sites to named fields, reducing positional state-coupling risk while preserving runtime/body semantics.

## 0.61.3

- Simplified persistent extension semantic body state by storing canonical `ExtensionSemanticPayload` values directly in `extension_bindings`; the BindingId-only persistence invariant is now enforced at the semantic-environment/body ownership boundary instead of through a one-field lifecycle wrapper.
- Simplified `ExtensionDependencySource` identity to standard frozen-dataclass field semantics, relying on `ast.AST` object identity and `BindingId` value identity with focused regressions covering both cases.

## 0.62.0

- Completed the Python Extension API v2 cutover: package-defined semantic values persist through the permanent compiler, `nodeforge.lsystem` can migrate entirely through interface/semantic/backend v2 contracts, and legacy SYSTEM/BACKEND_HELPER/native `compile_call` execution routes are no longer part of supported production compilation.

## 0.62.1

- Fixed source list literals composed entirely of package semantic records: sibling record types now infer one unique most-specific same-owner nominal base, preserve existing semantic runtime dependencies, and fail with controlled diagnostics for mixed, unrelated, or ambiguous element types instead of reaching runtime lowering.
