# 50 — Named Function Results

**Baseline:** NodeForge 0.100.0, `feature/nodes-bundled-source-install` at `b69e1acf1ad398d6b1442c12d66efdb2d17fd099`, Nodes 1.5.0, Blender 5.2.0. Source: supplied `NodeForge-v0.100.0_dev.zip`, including `.git`, generator and pinned Inspector catalogue.

**Architecture review:** AR-01 option B approved by coordinator on 2026-11-25; AR-02/03 incorporated. See `50_named_function_results_architecture_review.md`.

**Scope:** named `.nf` returns, static and dynamic executable Extension API results, existing raw-node named results, bundled Nodes migration, editor projection, tests. No production changes in this planning step.

## Problem / Goal

Ordinary source-backed calls and Python extension calls discard the identity of individual results and expose positional tuples. Nodes therefore requires `[0]` or complete unpacking even when only one socket is needed. Raw `node(outputs=...)` already supports structural named results. Reuse that mechanism for explicit named returns and extension declarations, including invocation-dependent output names and types.

The current generator has 297 wrappers and 2,671 supported configurations. `result_signature()` records only types. Domain Size has configurations returning `(Int,)` with different output names (Point Count, Instance Count, Layer Count); Voronoi Texture has `(Float,)` for Radius or Distance. Names must participate in the result contract.

## Expected Behavior

```python
def make_branch(geometry: Geometry):
    # existing construction
    return {"axes": axes, "segments": segments, "buds": buds}

branch = make_branch(geometry)
axes = branch.axes
segments = branch["segments"]

mesh = nodes.cylinder(radius=1.0).mesh
sample = nodes.sample_curve(curves=curve, data_type="FLOAT_VECTOR")
direction = sample.tangent
value = sample["value"]
```

A call produces one set of typed graph values. Selecting fields neither invokes the function again nor creates an extra node. Ordinary scalar and explicit positional-tuple returns remain distinct supported constructs. Nodes returns named results consistently, including single-output nodes.

## Specification

1. **NR-01 — Source declaration.** A `.nf` function may use a nonempty dictionary display directly in its single final top-level `return`. Keys are nonempty literal strings; values are existing expressions that resolve to one supported runtime NF value each. Preserve declaration order. Reject duplicate keys, nonliteral keys, `**` expansion, empty dictionaries, nested result structures, and non-runtime leaves. Existing restrictions on return placement remain. A scalar return annotation, including `Type["Label"]`, cannot annotate a named return, even with one field. No new `.nf` annotation syntax is introduced.
2. **NR-02 — Identity and names.** Keys are exact, case-sensitive strings; do not infer keys from returned expressions, normalize them, or add aliases. A named source function creates output sockets in declaration order, using the exact keys as labels. Named grouped extension outputs follow the same label rule. Source and executable extension result keys must be nonempty strings, encode as valid UTF-8, contain no NUL and occupy at most 63 UTF-8 bytes (NodeForge's existing group-compatible limit, also relevant to Blender collection labels). Reject invalid keys before physical materialization. Raw node aliases retain their existing contract. Dot syntax selects a key only when Python parses the attribute with its exact spelling; keywords, names with spaces and identifiers whose spelling changes under Python normalization remain accessible by exact bracket lookup. No aliases are added.
3. **NR-03 — Projection.** Both `f(...).field` / `f(...)["field"]` and `r = f(...); r.field` / `r["field"]` select the same typed leaf for the same call. String subscripts use the existing compile-time evaluator and must produce a nonempty string. Unknown names, numeric indices, runtime keys and tuple unpacking of a named result produce a controlled compile error. A one-entry mapping is a named result, not a scalar. Projection chains such as `r.position.x` use existing type-specific access after projection.
4. **NR-04 — Structural lifetime.** A named result can be bound and aliased as a local structural value using existing binding semantics. Multiple projections of that binding use one call occurrence. Distinct call occurrences remain distinct. Rebinding obeys current scope/invalidation rules. A whole result is not a physical socket, `NFType`, ordinary dict, iterable, Bundle, semantic record or a new user class. No mutation, arbitrary runtime dictionary assignment, dictionary methods, nested result values, passing a whole result into a runtime socket, implicit forwarding by `return r`, or runtime-zone state containing a whole result. Explicit forwarding with `return {"mesh": r.mesh}` is supported. Existing compile-time dict-like metadata, raw node options and Python semantic records retain their current contracts.
5. **NR-05 — Static extension declaration.** Executable Python functions can declare a named mapping in the return annotation:
   ```python
   def bounding_box(...) -> {
       "bounding_box": Geometry,
       "min": Vector,
       "max": Vector,
   }:
       ...
   ```
   The evaluated mapping must be a nonempty plain dict of exact string keys to one valid NodeForge runtime type marker per key. Reject unions of multiple types, nested mappings, records, invalid keys and empty declarations. The ordinary scalar and fixed tuple forms remain valid deliberate positional contracts. Python itself overwrites duplicate dictionary keys before the interface loader sees them; the loader validates the evaluated mapping, not a reconstructed source history. This is an Extension API declaration convention, not a standard Python typing annotation.
6. **NR-06 — Dynamic extension declaration.** `-> DynamicResults` remains the sole invocation-dependent result marker. Existing `_result_types_<name>(arguments)` semantic exports return a nonempty plain dict of exact names to exact `NFType` values, rather than a tuple/list of types. Order is mapping iteration order. Resolve once after argument binding/overload selection, in SEMANTIC, before lowering. Defaults, explicit None and variadic argument metadata follow current resolver argument contracts; do not evaluate runtime socket data to determine the schema. Names alone may vary. Reject old positional dynamic resolver returns, wrong types, invalid keys, empty maps and resolver exceptions with callable/owner diagnostics. No second dynamic marker or fallback protocol. Apply to backend-only and semantic-then-backend executable calls.
7. **NR-07 — Physical extension result.** Named executable backends return a dict of the resolved keys to `ExtensionBackendValue` leaves. Match key sets exactly; reorder by the resolved declaration, not backend insertion order. Validate each value's NF type, output direction, socket provenance and group ownership using current leaf validation. Missing/extra keys, non-dict returns and wrong leaves are errors. No semantic resolver rerun in BACKEND. A failed backend must not publish a partially generated group or resources. Existing scalar and fixed tuple backend contracts are unchanged.
8. **NR-08 — Grouping and compilation.** Named calls work in source-backed inline/grouped paths, local imports (including folder-qualified modules), package `.nf` exports, Python extensions inline/grouped, root materialization, normal expressions and the existing Local Add Node Group operator. Grouped named output labels and order equal the resolved keys. Group authority/call-site identity is independent of output names; effective fingerprints include the ordered result schema. Recompilation with changed names at unchanged types updates the authoritative group's interface under the existing transaction policy. **AR-01/B:** persist a named-result shape marker on generated source and extension groups (one Boolean property; absent means positional/legacy). When either the previous or replacement group is named, restore external output links only for exact matching keys and physical socket types; do not apply the singleton same-type rename fallback. Reordering retains matching links; deleted, renamed or type-changed outputs lose their links. Transitions between named and positional contracts follow the same rule when either side is named. When both are positional/legacy, preserve the existing output-link policy. A failed cutover restores the original group, marker (including absence), links and ownership without leaked groups. Native direct-composition root interfaces are not replaced or marked by this rule.
9. **NR-09 — Generated Nodes.** Every supported wrapper returns a named result, even with one output. Fixed native socket keys are derived from Inspector socket display names using the existing `sanitized()` convention (lowercase, snake_case, existing `option_` handling); generated declarations explicitly contain the resulting keys. No new `rna_`, `menu_` or per-node naming exceptions. Same-layout generated name collisions or invalid required output schemas must fail the entire generation/build with node/state/physical identifiers, not become skipped wrappers. Failed generation leaves previously generated files unchanged; none of these collisions occur in the audited catalogue. Genuine unsupported nodes/states retain the existing reporting policy. Dynamic Capture Attribute and Menu Switch element labels are explicit user keys, not automatically normalized; use exact labels (e.g. `"FLOAT:weight"` gives `.weight`, `"FLOAT:Weight"` gives `.Weight`). Apply NR-02 to collection keys; reject clashes with base output keys, duplicate labels and invalid keys before materialization. Keep the existing `elements` grammar, input types and ordering. Index Switch's output schema does not depend on item count. Base output names precede collection-created outputs in the existing physical order.
10. **NR-10 — Output parity.** Generator static/dynamic classification uses the entire ordered `(key, NFType)` signature. Physical selection retains actual Blender identifiers and names; generated backend independently checks the observed active output names/types against the resolved schema and returns the corresponding sockets. A same-typed socket swap must be detectable. No new Inspector coverage, zone support, input semantics or output type conversions. Unsupported states remain explicitly reported. Blender-normalized states remain a separately recorded outcome only when the independent reference demonstrates normalization and the resulting graph matches the reference.
11. **NR-11 — Editor.** Signature previews show explicitly declared output names and known types for named source returns and static extension results. Distinguish named one-output results from scalar results. Complete exact dot-accessible fields for supported straight-line bindings and direct call receivers when a unique schema is known; do not suggest a dot spelling that Python would reject or normalize to another key. For dynamic invocations, expose fields only when the existing compiler can resolve the selected call and arguments without physical execution; otherwise show an unresolved dynamic result instead of invented fields. No duplicate implementation of overload or resolver logic. Keep incomplete-source safety, existing package namespace and argument completions.
12. **NR-12 — Migration.** Regenerate bundled Nodes from its existing pinned specification; migrate every repository consumer of migrated wrappers, dynamic resolver test fixture and example to named projection/return. Do not add aliases, tuple-to-mapping adapters or old Nodes keyword/output access fallbacks. Retain scalar/positional static contracts because they are independent language features, not compatibility shims. Keep existing package API execution forms, installed package lifecycle, source packaging, and exclusion of `tools/` from release ZIP. Bump NodeForge once to 0.101.0 and bundled Nodes to 1.6.0 with minimum NodeForge 0.101.0, so the existing installer updates installed copies.

## Architecture

### One structural result pipeline

Current implementation already defines `NamedOutputsCallResult` (`semantic/call_resolution.py`), `NamedOutputsResultShape` and `StructuralBindingSymbol` (`semantic/values.py`), `IRNamedOutputs` (`semantic/ir.py`) and projections in `semantic/analysis.py` and `semantic/lowering.py`. Raw node output declarations remain their own physical selector syntax. Extend producers of the existing shape, rather than introducing another object/value domain.

```text
.nf return mapping       Extension static/dynamic declaration      node(outputs=...)
       |                              |                              |
SourceCallableContract        ExtensionCallableSpec/resolver           |
       +---------------------- NamedOutputsCallResult ----------------+
                                       |
                            NamedOutputsResultShape
                                       |
                  structural bindings / existing name projection
                                       |
                 IRCall ordered leaves + ordered result names
                                       |
                physical source / extension / raw materializer
```

The carrier is compiler-owned and immutable. Identity is the existing call occurrence and its ordered leaf identities; the field name is a projection key within that occurrence, not a new global identity. It lives through semantic analysis, lowering and physical call validation. Each member has exactly one NF type and one result leaf. No field is itself structural. Multiple fields may refer to the same returned source value. Do not merge different calls based on equal names/types.

**Precedent:** [MLIR Language Reference — Operations](https://mlir.llvm.org/docs/LangRef/#operations) models one operation defining several typed SSA results; use the same distinction between the operation and its projections, not MLIR's syntax or type system. The immediate implementation precedent is NodeForge's own raw-node named output path and plans 31 (source-backed callable contracts), 39 (raw socket addressing), 45/46 (grouped/inline and unified compilation).

### Declaration and source boundary

Extend `LocalReturnElement` with optional `result_name`; retain its existing internal `key` (`return:<index>`), `socket_name` and expression. The presence of names on all elements denotes a named shape; forbid mixed named/unnamed members and require each named member’s result_name to equal its display label. Add `result_name: str | None = None` to `GroupOutputContract`. Keep positional identity and physical socket label distinct from the programmatic key. `GroupInterfaceContract` / `SourceCallableContract` validate ordered indices and the all-or-none name invariant. Source preflight and demanded body compilation must use the same parsed return shape; no separate dictionary evaluator.

Use exact string keys as labels for explicitly named source outputs; retain current display naming for scalar/tuple outputs. This avoids extra normalization and label collisions. `function_bodies.py` joins `shape.elements` to `body.output_summary.explicit_outputs` by index and verifies count and labels before constructing the interface. The existing `body.py` terminal-return loop can emit each value normally. Verify no `ast.Dict` runtime expression support is inadvertently enabled elsewhere. Reject named scalar annotations at both preflight and demanded compilation.

**Shared key validation (AR-03).** Add `result_keys.py` at the package root (pure Python, no Blender/compiler dependencies), with `validate_named_result_key(key)` enforcing NR-02 and `is_exact_attribute_key(key)` testing `ast.parse("r." + key, mode="eval")` for one `ast.Attribute` whose receiver is exactly `Name("r")` and whose `attr` equals the original key. Reject syntax errors; do not normalize. The validator raises a descriptive `ValueError`; source/extension callers translate it into existing phase-specific diagnostics with declaration/call context. Use it for named source contracts, static/dynamic extension schemas and user-supplied collection result names in the generated semantic resolver (export `validate_named_result_key` through `extension_result_api.py` for generated semantic modules; no package-relative dependency on the generator). IR verifies names against the resolved contract, not a new alias policy. Raw-node aliases keep their current validation. The editor and verifier use the exact-attribute predicate or the same AST criterion where package import boundaries require it. The 63-byte constraint is a NodeForge policy, not a universal Blender interface maximum: review demonstrated collection truncation at 64 bytes and NUL truncation, plus surrogate encoding failure.

**Precedent:** [Python lexical analysis — Identifiers and keywords](https://docs.python.org/3/reference/lexical_analysis.html#identifiers) specifies NFKC normalization and reserved keywords; compare parsed spelling rather than relying on `isidentifier()`.

**Precedent:** [Python expression reference — Dictionary displays](https://docs.python.org/3/reference/expressions.html#dictionary-displays) provides an existing AST representation and ordered entries. Adapt it as a restricted declaration, rejecting duplicate keys before evaluation in `.nf` rather than adopting Python's last-value-wins rule. No parser change.

### Extension contract and phase boundary

Use one additional executable `TypeSpec` kind `NAMED_RESULTS`, with `named_items: tuple[tuple[str, NFType], ...]` and all other payload fields empty. Validate it in `TypeSpec.__post_init__` and `is_executable_result_type_spec`; `ExtensionCallableSpec` accepts it only for executable results. Never admit it as a record field, parameter, semantic-only result or packed semantic-state value. `DICT_STR` remains the existing unrelated semantic-state field type.

`extensions/interface.py::_normalize_result_annotation()` recognizes the concrete dict before generic semantic type normalization; normalize each marker to one exact `NFType`, freeze ordered pairs. The current annotation loader uses `typing.get_type_hints`; a direct Python experiment confirmed concrete dict return annotations are returned as dictionaries. Include tests with actual NodeForge markers and postponed annotations; do not replace Python's annotation machinery.

`extensions/registry.py::invoke_result_resolver()` validates the dictionary and freezes it to ordered `(name, NFType)` pairs while preserving owner/phase isolation. The public forwarding registry method uses the same contract. `_extension_result_spec()` turns both static named specs and resolved dynamic pairs into the existing `NamedOutputsCallResult`; scalar and positional tuple branches remain explicit. Preserve ordinary overload resolution and SEMANTIC_THEN_BACKEND dependency packing. Resolver names and the `DynamicResults` marker remain unchanged. Update their docstrings and type hints.

**Precedent:** NodeForge plan 47 and `ResultStaticArg` / `ResultRuntimeArg` already establish per-invocation resolution before physical execution. Extend the payload, not the phase model. Python dict syntax places each name next to its type; no parallel arrays or per-node nominal result classes.

### IR and physical boundary

Add optional `result_names: tuple[str, ...] | None` to `IRCall`, populated from `NamedOutputsCallResult` during lowering. When present, names are nonempty, distinct and have the same arity/order as `results`. Existing raw-node `options["outputs"]` is still the physical selector mapping; verify the keys agree with named IR metadata. For named source calls compare against the prepared contract; for extensions compare against the selected declaration or already-resolved dynamic schema. Do not add the new name field to scalar/tuple calls or use output count to infer carrier kind. Audit all IR construction/copy, verification, residualization and fingerprint paths for retention; unrelated calls retain their existing defaults.

`ExtensionBackendContext` gains read-only `result_names`, parallel to existing `result_types`, with `None` for positional contracts. The physical context receives detached metadata from IR and never invokes SEMANTIC. `_validate_result()` verifies the static named declaration when applicable, accepts a dictionary only for named executable calls, checks exact key set, and returns the usual ordered internal physical leaves via `_validate_result_leaf`. This keeps the physical lowerer's per-leaf mapping and transactions unchanged.

`extension_group_materializer._output_names()` uses the resolved names for named results; `_fingerprint()` includes them, and candidate/published group socket validation checks both name and type at each position. Keep `IRCall` result ordering, call-site identity, owner identity and existing generated-resource transaction unchanged. Source group creation uses the already-prepared interface; audit consumers of `GroupOutputContract` for accidental reconstruction or name loss.

### External output-link identity (AR-01/B)

Define `NAMED_RESULTS_PROP = "nodeforge_named_results_v1"` in `blender/interface.py` and shared read/write helpers. The owner is the published function group; the property is written as true for named interfaces and removed for positional ones. Absence on a legacy group means positional. It describes only the result shape, is not ownership authority, and must not duplicate keys already present in the interface. Set/remove it in `group_assembly._populate_group()` from the prepared source contract, before interface/fingerprint finalization, and in the `populate` closure of `extension_group_materializer.materialize_extension_call()` from `operation.result_names is not None`. Candidates must be stamped before `publish_candidate()` validates external links; do not defer this to `finalize_before_commit`. Inline calls and native direct-composition roots do not acquire their own marker.

In `group_backend.py`, ensure the `preserve_if_equivalent` shortcut requires equal old/new named shape even when physical sockets and fingerprints otherwise coincide. Keep the marker out of `BACKEND_PRIVATE_GROUP_PROPERTIES`: `_copy_group_contents()`/`_copy_custom_properties()` must copy it from candidate to authoritative group and backup to restored group, removing it when absent. Assert marker parity after copying in the existing strict cutover/backup paths. Source and extension effective fingerprints must distinguish named vs positional even with identical labels/types; do not change authority keys or introduce a metadata migration of old groups.

In `update.py::_validate_group_external_state_for_replacement()`, leave the input-reference pass unchanged. In the singleton output fallback, skip the override of `new_socket_key` when the replacement or `endpoint["node"].node_tree` is named. Existing exact output keys `(direction, name, socket_type, ordinal)` then perform matching in `_resolve_link_endpoint()`; named groups have unique names, so order changes cannot redirect links. Keep positional fallback for two positional/legacy groups. The existing capture/copy/restore transaction remains the sole owner of external link migration and rollback. Review reproduced nine live Blender scenarios with this guard, including rollback, without extra groups; the production change must verify the complete pipeline rather than reuse the prototype as proof.

**Precedent:** the current output structural-key restoration and input declaration identity mechanisms in `blender/update.py` already separate exact identity from legacy rename heuristics. NR-08 extends that principle to explicit named output identity without adding per-socket IDs or a new migration registry.

**Precedent:** existing NodeForge extension grouped materializer and source group contract already distinguish physical order from ownership. [Blender NodeSocket API](https://docs.blender.org/api/current/bpy.types.NodeSocket.html) distinguishes `identifier` (socket mapping identity) from `name` (display label). Apply that distinction in the generator; do not treat a friendly name as Blender's internal identifier.

### Generator boundary

`inspect_contract.prepare_node()` currently retains `socket` (identifier), `type` and `physical`, dropping the socket display name. Add `name` to converted output records from the same joint layout. Keep all existing configuration selection and input records. **AR-02:** define a dedicated `ResultSchemaError` in the generator and raise it for mandatory output-schema failures (including collisions). In `generate_clean.generate()`, re-raise it before the existing broad per-node exception handler; do not convert it to a `skipped` entry. Include node, configuration, physical identifiers and offending keys in diagnostics. `build.build()` already stages generation in a `TemporaryDirectory`; let the error propagate before copying any generated owner files or publishing an archive. Preserve ordinary unsupported-node outcomes. `result_signature()` forms ordered `(sanitized(display_name), type.upper())` pairs. For dynamic menu use the base output only when computing dependencies; for capture use base outputs; extend with collection names/types at invocation. Compare complete signatures in `result_rule()` and verify every catalogue configuration resolves to its recorded schema. The existing minimum-dependency search remains; no new search algorithm.

`render_interface()` emits dictionary annotations for static wrappers and `DynamicResults` for invocation-dependent ones. Adapt import collection to include the NF marker of each named static member. `render_semantic()` renders ordered dictionary signatures and uses the shared pure collection parser (SR-01). Capture entries map their type tokens to NFType; menu entries map to Bool and retain selected-item validation. One common semantic loop copies the base map, validates each exact key through the NR-02/AR-03 validator and rejects collisions before insertion. Index Switch retains its existing count validation and base result schema.

**SR-01 — Shared collection grammar.** Define `capture_entries(elements, supported)` and `menu_names(elements)` once as pure functions in the existing `tools/nodegen/generator/generate_clean.py`. Return immutable ordered tuples of `(type_token, name)` pairs or names respectively. Emit the same function bodies directly into `semantic.py` and `backend.py` during generation; neither phase imports the other. Preserve the baseline grammar: split comma-separated chunks, strip each chunk; capture splits at the first colon without further stripping the token or name, rejects missing tokens/names, unsupported tokens and duplicate exact names. Menu strips each name and rejects blank or duplicate entries. Empty input yields `()` in either parser; empty capture is valid, empty menu fails the existing semantic selection check. Use the existing phase-local error handling. The parsers own syntax only: semantic type mapping, exact-key validation/base collisions and selected-menu checks remain in SEMANTIC; physical construction and observation remain in IMPLEMENTATION. No normalization or escaping of collection names, Blender/compiler dependencies in the parsers, cross-phase imports, shared mutable state, cache, extra IR payload or resolver invocation.

`set_collection()` uses the same pure parser to construct Capture Attribute or Menu Switch collections and returns the ordered requested names from the parsed entries; Index Switch retains its existing count/physical handling and returns `()`. `materialize()` calls `set_collection()` once and reuses its returned tuple (or `()` for a non-collection node) to partition base and collection-created active output slots: capture/menu append one output per name; index appends none. Do not reparse `elements` or derive expected names from context or observed sockets. For fixed outputs use normalized actual socket display labels; for collection-created outputs compare actual labels with the requested tuple in order. Independently inspect actual types and validate the full ordered `(key, NFType)` schema against context before returning `context.value(socket, typ)` under each key. Preserve current input/property/default delivery and physical identifier diagnostics. Reject count, label, order or type mismatches rather than silently zip arbitrary names to sockets. The independent verifier remains separate.

Keep `sanitized()` in `inspect_contract.py` as the canonical native-name normalization function; `generate_clean.py` already imports it for generation-time use. In `generate_clean.py`, use standard-library `inspect.getsource()` on that function and on the two SR-01 parser functions to emit their definitions without rewriting their bodies. `render_backend()` emits `sanitized()`, `capture_entries()` and `menu_names()` into `backend.py`, with the required `re` and `keyword` imports in `BACKEND_HEADER`. `render_semantic()` emits the same two parser definitions into `semantic.py`; it does not need the native-name sanitizer because native keys are already generated into its signatures. Place definitions after the existing module headers and before the generated wrappers/resolvers. The functions must be self-contained apart from their explicit arguments and required standard-library imports. Source extraction occurs only during generation, not at runtime. Both phases execute their own local definitions; repeated generated text is intentional, while the maintained implementation of each function remains single-sourced. Do not introduce a helper module, a general-purpose code-emission abstraction or runtime imports from `tools/`. Retain exactly the existing generated files (`interface.py`, `backend.py`, `semantic.py`), the existing `generate()` output list and `build.py::FILES`; no new packaging/staging mechanism or per-state backend tables. `--check` verifies the same three generated files byte-for-byte; generation remains deterministic.

**Precedent:** current generator already separates Inspector data, semantic dependency tables and live Blender physical construction. Preserve that split; use the existing normalized naming convention for native labels only. Explicit user keys in `.nf`, raw nodes and extension declarations are never normalized by the compiler.

### Editor boundary

`editor/intelligence.py` owns detached signature projections. Extend `Output` with optional exact key and `Signature` with an explicit carrier indicator; named fields must not be inferred from display labels. `_source_return_values()` visits dict values with the current conservative inference and retains explicit keys even if types are unknown; `_definition_signature()` avoids the scalar rewrite for named returns. `_extension_signatures()` consumes named `TypeSpec` entries; dynamic signatures advertise unresolved named results.

For dot completion, reuse `SymbolIndex` package/Local resolution and the existing token cursor context. Track only straight-line result bindings and completed direct receiver calls that can be resolved uniquely. Use the actual compiler semantic call path for dynamic call resolution on complete, supported source fragments if available, with the same environment snapshot and no BACKEND; do not execute arbitrary incomplete source or invent overload binding. If the existing editor cannot safely acquire semantic facts for a dynamic receiver, report unresolved schema and omit field completions. This is the defined conservative editor contract, not a temporary fallback. Static field completions and correct signature previews are mandatory; use the exact-attribute criterion in AR-03, with bracket lookup available for other keys. Add no parallel resolver or Blender UI changes unless the existing editor data flow requires them.

**Roadmap:** `dev/08_FEATURE_external_ide_lsp_support.md` calls for reuse of the existing frontend; keep result schemas available through detached contracts rather than a separate editor registry. New socket types, general Bundle operations, zone support and cross-version Blender compatibility remain separate work.

## Touched Files

Core:
- semantic/source_callables.py
- semantic/source_definition_contract.py
- semantic/function_bodies.py
- semantic/callable_contracts.py
- semantic/call_resolution.py
- semantic/analysis.py
- semantic/values.py
- semantic/lowering.py
- semantic/ir.py
- extensions/contracts.py
- extensions/interface.py
- extensions/registry.py
- extension_result_api.py
- extension_api.py
- blender/extension_backend.py
- blender/extension_group_materializer.py
- blender/interface.py
- blender/group_assembly.py
- blender/group_backend.py
- blender/update.py
- editor/intelligence.py

Generator and generated package:
- tools/nodegen/generator/inspect_contract.py
- tools/nodegen/generator/generate_clean.py
- tools/nodegen/verify/validate_nodes.py
- tools/nodegen/verify/smoke_named_optional.py
- tools/nodegen/verify/smoke_output_constants.py
- tools/nodegen/verify/smoke_dynamic_runtime.py
- packages/nodes/systems/blender/interface.py
- packages/nodes/systems/blender/backend.py
- packages/nodes/systems/blender/semantic.py
- packages/nodes/nodeforge_package.json

Documentation/release:
- docs/WRITING_FUNCTIONS.md
- docs/SYNTAX.md
- docs/EXTENSION_API.md
- docs/BUILTINS.md
- docs/GEOMETRY_NODES_COVERAGE.md
- dev/CHANGELOG.md
- __init__.py
- blender_manifest.toml

Tests/fixtures:
- tests/unit/test_local_function_return_shapes.py
- tests/unit/test_source_callables.py
- tests/unit/test_semantic_structures.py
- tests/unit/test_extension_contracts.py
- tests/unit/test_extension_interface.py
- tests/unit/test_extension_dynamic_results.py
- tests/unit/test_extension_execution_forms.py
- tests/unit/test_extension_semantic_body.py
- tests/unit/test_editor_intelligence.py
- tests/unit/test_bundled_nodes_generator.py
- tests/unit/test_nodes_parameter_names.py
- tests/blender/test_local_function_multi_return.py
- tests/blender/test_update_group.py
- tests/blender/test_structural_local_functions.py
- tests/blender/test_extension_dynamic_results.py
- tests/blender/test_extension_grouped_materialization.py
- tests/blender/test_root_extension_materialization.py
- tests/blender/test_extension_boundary_matrix.py
- tests/blender/expression_characterization/cases/packages/extension_graphs/systems/extension_graphs/interface.py
- tests/blender/expression_characterization/cases/packages/extension_graphs/systems/extension_graphs/semantic.py
- tests/blender/expression_characterization/cases/packages/extension_graphs/systems/extension_graphs/backend.py

Audit before editing: `tools/nodegen/build.py` retains its three-file `FILES` inventory and existing staging/error propagation (AR-02); no change is planned there. Audit every call to `IRCall`, `GroupOutputContract`, `invoke_result_resolver`, `DynamicResults`, and every imported Nodes consumer, including test source strings, docs and examples. Update only sites that actually carry a named result or consume a migrated API. `semantic/body.py` terminal-return emission, `semantic/source_callable_session.py`, `semantic/residualization.py`, `blender/ir_lowering.py`, `extensions/values.py`, group publication and CLI should need no new result carrier; audit their boundaries and modify only if required to preserve the specified invariants. Record any additional necessary substantive change as a plan amendment before implementation.

## New Files

- result_keys.py
- tests/unit/test_named_function_results.py
- tests/blender/test_named_function_results.py
- plans/50_named_function_results.md

## Implementation Steps

1. **Baseline and audit.** Record git status and the 297/2,671 catalogue inventory; run existing source/extension/structural/editor and Blender characterization baselines. Enumerate all generated Nodes and dynamic resolver consumers (including fixture strings and installed test packages). Record exact migration paths before edits. Keep the supported-state set and existing input names unchanged. Read AGENTS.md and plans 47–49. No version changes during partial implementation.
2. **Source declarations.** Implement the shared pure key validator and exact-attribute predicate (AR-03); implement NR-01/02 in `analyze_local_return_shape`; share validation with `validate_source_definition_contract` and `function_bodies`. Add the named output metadata to existing group contracts. In `analysis.py`, replace the repeated scalar/tuple construction at the three source-backed call sites with one helper that recognizes the named contract and otherwise preserves existing scalar/tuple behavior. Update raw-only error text in shared named projections, structural bindings and IR docstrings; leave raw node physical selector semantics intact. Run source/structural tests before extension changes.
3. **Extension declarations and resolution.** Add named executable `TypeSpec` payload, normalization and guards described above. Replace the `DynamicResults` resolver payload protocol atomically, including both registry forwarding methods and both semantic call paths. Keep semantic-only record/list types distinct. Update fixtures and documentation together. Cover defaults, static/runtime argument markers, overload selection, phase isolation and malformed declarations before Blender execution.
4. **IR/physical integration.** Propagate ordered names through `IRCall` and `ExtensionBackendContext`; update `_validate_result`, grouped output names and fingerprint. Reuse ordinary physical leaf validation and ordered result mapping. Test named extensions in inline/grouped and semantic-then-backend forms, authoritative rebuild and transactional failure. Implement AR-01/B marker stamping before publication, equivalence checking, copy/rollback parity and the singleton fallback guard. Test direct and stored projections; no new node for projection.
5. **Generator migration.** Preserve Inspector output name + identifier, generate complete result rules and static mapping annotations, emit dynamic dictionary resolvers, keep `sanitized()` in `inspect_contract.py`, define the SR-01 parsers once in `generate_clean.py`, emit their definitions into both generated phase modules and the sanitizer into `backend.py` as specified in Generator boundary. Replace independent lexical parsing in both renderers, use a common semantic insertion loop, and make `set_collection()` return requested names for independently checked backend mappings. Add parser grammar/parity, emitted-definition and three-file inventory tests; verify both generated modules load in their respective owner phases without importing each other or generator sources. Add a targeted phase-loading test if existing tests do not exercise the actual generated package. Retain the existing build/check generated file inventory. Implement AR-02 schema error propagation and inject a collision in generator/build tests; assert failure and unchanged existing artifacts. Regenerate all sources in `packages/nodes/systems/blender`. Remove migrated Nodes positional calls from source examples and tests; no compatibility layer. Retain current optional inputs, constants, plain RNA/menu naming, Index/Menu public names and unsupported coverage.
6. **Editor and manuals.** Add named schema projection/receiver completions using existing source/environment machinery. Update docs at the relevant return, call, API, and coverage sections. Explain explicit key semantics and that named results are not general dictionaries or Bundle values. Keep raw-node examples and positional-return examples valid.
7. **Verification and release.** Execute the matrix below, including full generated catalogue and installed Local UI operator. Inspect actual output links, not only compilation counts. Make any required characterization expectation updates only for intentional named output changes, with reviewed diffs. On successful full change set, bump NodeForge and library versions as NR-12; update changelog and package minimum. Verify source install/upgrade, `tools/` exclusion, deterministic `generate --check`, and actual extension archive. Package and provide commit/patch commands.

**Temporary mechanisms:** none proposed. Old positional dynamic resolver protocol and old Nodes tuple projections are removed in the same completed change set. The existing positional scalar/fixed tuple language feature remains a separate permanent contract. Do not introduce temporary compatibility, hidden fallbacks or disabled tests; amend this plan before adding one.

## Tests

### Pure compiler / contracts

| Requirement | Positive and negative cases |
|---|---|
| NR-01/02 | Single/multiple fields, arbitrary expressions, repeated source values, exact keys/case/UTF-8 length (63 accepted, 64 rejected; multibyte boundary), NUL and lone surrogate rejection, declaration order; duplicate literal key, empty mapping, `**`, computed key, nested value, scalar annotation, invalid return placement rejected. |
| NR-03/04 | Same IR leaf for dot and key on same binding, direct call projection, key constant, alias/rebind, `.position.x`; unknown key, numeric index, tuple unpack, runtime key, whole-result arithmetic/socket/return/zone state, mutation rejected. No new general runtime dict domain. |
| NR-05/06 | Static mapping with actual type markers and postponed annotations; one-entry vs scalar; named overloads; dynamic names change with same types, typed default/omitted/variadic argument resolution; empty, invalid names/types, old tuple/list resolver, exception and phase errors. Named specs rejected in parameters, semantic-only record fields and packed state. Python duplicate-key overwrite documented, no false guarantee. |
| NR-07/08 | Ordered keys survive analyzed result, IR, context and grouped fingerprint; mismatched IR arity/keys fail. Both source-backed and extension paths use identical projection rules. |
| NR-09/10 | Independently generate full pinned catalogue; assert same node/configuration set; no normalized-name collisions; dynamic signatures include names; Domain Size and Voronoi Texture regression; one-output mapping; default and nondefault capture/menu/index elements; reserved base collision errors. SR-01: test shared parser ordering, exact case, whitespace before/after the first colon, additional colons, empty capture, empty-menu selection failure, unsupported tokens, missing names, empty chunks and duplicates. Compare projected result types/error categories with baseline grammar cases; validate exact key/base collisions separately. In `test_bundled_nodes_generator.py`, compare the emitted parser definitions in both generated modules with their canonical generator definitions (AST equality, ignoring source locations), and the emitted backend sanitizer with `inspect_contract.sanitized()`; exercise emitted functions on representative and invalid inputs to check required imports and behavioral parity. Verify generated semantic and implementation modules load and execute their local parser definitions in separate owner phases, with no cross-phase/generator imports or leaked modules after unmount (reuse existing coverage where sufficient). Assert that fresh generation, the build/check inventory and the installed phase directory contain exactly the three generated Python sources `interface.py`, `backend.py`, `semantic.py`. Injected same-layout collision must fail generator and build, report node/state/physical identifiers, leave existing generated files byte-identical and not publish an archive; genuine unsupported node still reported as skipped. Deterministic `generate --check` for all three generated files. |
| NR-11 | Signature preview and static receiver completion, direct result and assigned result, exact keys, keywords (`class`), spaces, NFKC-changing identifier (`K` vs `K`), exact bracket access and no misleading dot completion, unknown field type, named single output, invalidated/rebound binding, incomplete code and unresolved dynamic receiver, existing namespace/argument completion. |

### Real Blender 5.2

- Run `tests/blender/test_named_function_results.py` across local inline/grouped, Local folder imports, package `.nf`, backend-only and semantic-then-backend extension, grouped and inline/root execution forms. Assert one physical call occurrence, exact socket identities and group labels/order, no projection-only node, correct leaf types and links. For two same-typed outputs, deliberately return values in reverse dictionary insertion order and verify binding by key, not position.
- Backend negative matrix: missing/extra key, wrong type, tuple instead of dict, foreign group socket, input socket, failed resolver/backend; assert compile error and rollback with no published partial groups or leaked generated resources. Recompile same authoritative call site with different names and equal types; assert group fingerprint/interface updated and existing ownership semantics hold.
- AR-01/B: extend `tests/blender/test_update_group.py` and named source/extension integration tests using real generated groups and external Group Nodes. Check named singleton `point_count` → `instance_count` (link removed); same key/type (retained); same key/different physical type (removed); reorder two outputs (both retained by exact key); remove one of two (only matching link retained); positional `Value` → `Result` (retained); legacy/positional → named and reverse with changed name (removed), with same key/type (retained); same physical schema but changed carrier shape must not bypass marker update via equivalence shortcut. Check both source-backed and grouped extension producers. Test copy/backup, save/reopen marker persistence, destructive cutover failure and transaction rollback (old marker/absence, exact external links, group identity, no extra groups); verify inputs and their defaults/overrides remain unchanged. Use existing failure injection and transaction helpers.
- Run full `validate_nodes.py --all` for inline and grouped configurations with both current runtime/default-input scenarios. Each mode has 5,342 regular scenarios (2,671 × 2) plus current six dynamic extras per validator invocation. Log by node, Inspector state, mode, runtime flag and dynamic elements. Check complete case identities without duplicates. Extend `source_for()` to bind result once and project each expected key, using dot only for exact AST-preserved attribute spelling (AR-03) and bracket access for all others; derive expected keys from independent reference sockets and compare to generation metadata. Assert each root output link traces to the correct physical socket identifier/name/type through the extension group if grouped. Keep existing RNA, input default, input-link and socket layout comparisons. Add non-default capture/menu names with spaces/case and invalid base-key collision checks. Exercise name-only output variations and verify invalid normalization is not counted as success.
- Explicitly test both access forms on representative one-output and multi-output nodes, e.g. `sample_index`, `cylinder`, `sample_curve`, `named_attribute`, capture/menu/index. Repeated projection must share one native node. Retain named/optional and output-constant smoke suite coverage.
- Install the release extension in an isolated Blender profile, verify bundled Nodes 1.6.0 is copied from `packages/nodes` and upgrades a previously installed 1.5.0. Re-register without recopy. Register a real Local folder via `local_sources.link_local_source_folder`; use selected script in Geometry Nodes context and invoke `bpy.ops.nodeforge.create_function_group(namespace="local")`. Assert `FINISHED`, one inserted group, correct named output links. This tests the actual UI operator path, not just a direct compiler helper. Verify archive includes package and omits nested Nodes ZIP and root `tools/`.

### Observable regression invariants

| Invariant | Verification |
|---|---|
| Scalar and positional tuples remain separate from named results; tuple index/unpack and scalar `Type["Label"]` still work. | Existing return-shape and multi-return tests; compare socket labels/order/types. |
| Raw `node(outputs=...)` exact aliases, explicit selectors and one-output structural behavior unchanged. | Existing semantic structures, raw-node addressing and Blender tests; case-sensitive dot/key. |
| Existing semantic records, lists, `RuntimeRef`, Bundle operations and Repeat scalar state remain independent. | Existing extension values/semantic records, structural and control-flow suites. |
| Source demand/eager preflight, import identity, Local folder resolution and inline/grouped choices remain authoritative. | Existing source callable/namespace/module tests; real UI operator and qualified imports. |
| Existing optional/omitted parameters preserve Blender defaults; constants are delivered to their original physical consumers. | Plans 48/49 tests, named optional and constant smoke; compare default values and links. |
| Every previously supported Inspector configuration remains represented; unsupported configurations/zones not promoted. | Compare generated catalogue identities, not only counts; independent oracle links and state readback. |
| No duplicate extension execution when selecting several named fields; separate calls not merged. | Node counts, call identities and exact graph links. |
| Named output link identity is independent of arity/order; positional rename remains supported. | AR-01/B live update matrix, exact marker and external link checks after successful cutover and rollback. |
| Nodes keeps its three-file generated layout and independent phase execution; normalization and collection grammar each have one maintained source. | Generated-definition AST parity and behavioral tests; fresh generation/build/check/install file inventory and owner phase load/unmount checks. |
| Mandatory generator schema errors cannot publish a partial library. | AR-02 collision injection, nonzero failure and byte-identical generated files; unsupported-node reporting regression. |
| Group/extension resource ownership, reload, rollback and source-package upgrade remain intact. | Existing lifecycle, physical-owner, grouped/root, freshness and package-install tests plus failure/upgrade scenarios. |
| Release workflow still checks generated sources, packages Nodes directory and excludes tools. | Existing release-workflow tests and actual archive contents; no change to staging architecture. |

Run full unit suite, Blender regression and characterization suites under the project test harness. Review changed baselines rather than regenerate blindly. Record exact commands, Blender version, input spec hash, actual attempted/passed/normalized/failed/skipped counts, and all failure evidence. A compile-only run or signature comparison is insufficient for output provenance or the UI scenario. Do not claim full success for skipped or normalized states.

## Open Questions

- No blocking design question. AR-01/B is approved; AR-02 and AR-03 are incorporated as implementation requirements. The plan selects exact named group labels (no automatic title-casing), exact user-created collection keys, and removal of positional dynamic resolvers/Nodes tuple projections. These are explicit design decisions; scalar and static positional tuple contracts stay as distinct language features.
- Implementation verification: confirm collection output labels and the proposed fixed/dynamic partition with actual Blender 5.2 for nondefault item names and same-type outputs. The architecture review confirmed physical schema parity for all 2,671 baseline configurations and representative user-defined collection labels; the full named-result integration still needs verification. If live Blender violates the proposed schema mapping, amend the generator design before adding exceptions.
- Dynamic editor completion must be proven possible through the existing pure semantic path for each supported receiver shape; where that path is unavailable, NR-11 explicitly requires an unresolved display rather than a speculative inference engine.

**Simplification review (SR-01):** accepted the shared collection parser design, with the coordinator’s subsequent correction to emit functions into the existing phase modules instead of creating a shared module. The supplied review reports 26/26 baseline grammar/type comparisons, 9/9 named-key rejections, 13/13 live Blender collection schema comparisons, and 100 passed / 3 skipped focused unit tests. Its phase-loading prototype used a separate shared module; that result does not verify the revised emission design. These are prototype/baseline results, not end-to-end implementation verification. Public specification, AR-01/B, AR-02, AR-03, versions and release scope are unchanged.

**Planning evidence:** inspected supplied archive and Git reflog, executed catalogue preparation and naming comparison in Python. This revision additionally checked the actual source/extension candidate publication, output-link fallback, property copy, equivalence shortcut and generator/build boundaries against the supplied archive and incorporated the architecture review evidence. The subsequent SR-01 correction checked the archived `sanitized()`, generator renderers and existing three-file build inventory, and changed only the plan. No production code modified; no Blender tests rerun during this revision. Review prototypes and baseline tests are not implementation verification.
