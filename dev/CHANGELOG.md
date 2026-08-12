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
