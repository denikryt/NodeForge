# Get Started with NodeForge

NodeForge turns `.nf` scripts into Blender Geometry Nodes. Executable statements add nodes to an open base tree; typed functions create reusable groups or, with [inline compilation](SYNTAX.md#inline-compilation), native caller-tree nodes. See [Core Concepts](CORE_CONCEPTS.md) and [`.nf` Modules](MODULES.md) for the language model.

## Install NodeForge

NodeForge requires Blender 5.2 or newer. Install it through its Blender Extensions repository to receive updates in Blender:

1. Open **Edit → Preferences → Get Extensions**.
2. Under **Repositories**, choose **Add Remote Repository** and enter `https://denikryt.github.io/NodeForge/index.json`.
3. Find **NodeForge**, click **Install**, and enable it.

To install from a file, download the latest Extension ZIP from [GitHub Releases](https://github.com/denikryt/NodeForge/releases/latest). In **Get Extensions**, choose **Install from Disk...**, select the ZIP, and enable **NodeForge**. This also works offline after downloading the ZIP.

When switching from the earlier ZIP add-on, disable the old NodeForge add-on before enabling the Extension. Your Local files and installed packages remain in Blender's user-data directories.

The add-on adds a **NodeForge** tab to the Geometry Node Editor sidebar. Enabling the extension installs or updates its bundled libraries. See the guides for [Nodes](NODES_GUIDE.md), [Math](MATH_METHODS.md), and [L-System](LSYSTEMS_SCRIPTING.md).

## Update NodeForge

For a repository installation, open **Get Extensions**, use **Refresh Remote**, then update **NodeForge** when available. You can enable **Check for Updates on Startup** for the repository.

For a ZIP installation, download a newer Extension ZIP and install it from disk again.

## Compile your first script

1. Select a mesh object and open the **Geometry Node Editor**.
2. Click **New** to create a Geometry Nodes modifier and base node tree.
3. Press `N` and open the **NodeForge** tab.
4. In a **Text Editor**, create a Text datablock with this script:

```python
def make_cube(size: Float["Size"] = 2.0) -> Geometry:
    return cube(size)
```

5. Select that Text in **Text Script** and click **Compile Script**.

**Compile Script** adds a Group Node for `make_cube` to the open tree. Connect its output to the base tree's **Group Output** Geometry input to display the cube. Change **Size** on the Group Node to see Blender reevaluate it.

A script can also build ordinary nodes directly:

```python
geometry = cube(2.0)
```

**Compile Script** replaces all ordinary nodes in the open base tree with this script's nodes, including the Group Node from the previous example. Connect the Cube output to **Group Output** to display it.

## Update a script

Edit the source in a Text datablock, select it under **Text Script**, open the base Geometry Nodes tree, and click **Compile Script** again. With top-level node-building code, compilation replaces every ordinary node, including manually created nodes and Group Nodes. It keeps the base tree, its interface sockets and defaults, its Group Input/Output nodes, and references from modifiers or other groups.

Compatible unambiguous links between the base tree's Group Input/Output nodes and rebuilt nodes can be restored; manual connections between replaced nodes are replaced by the script's graph. Inspect interface links after compiling, especially if node types or counts changed. A warning reports links that could not be restored.

**Compile Script** places independent function groups alongside any nodes built by the top-level code. When the top-level code builds no nodes, existing nodes stay in place. See [Compose the base tree](MODULES.md#compose-the-base-tree) for the placement rules. Empty or invalid source is rejected.

Root replacement rejects animation or drivers on ordinary base-tree nodes. **Compile Script** requires an editable base tree in the Geometry Nodes editor for both modes. Linked read-only trees and NodeForge-managed function or Mesh Compute trees are unsupported targets. Return from any nested group before compiling.

## Main actions

| Button | What it does |
| --- | --- |
| **Compile Script** | Places independent function groups in the open tree. Top-level node-building code also replaces the ordinary nodes in that tree. |
| **Reload Function from Source** | Rebuilds a selected source-backed Function Group from its current source. |
| **Load Script From Selected NodeGroup** | Copies the selected function's stored module source into the selected Text, replacing its contents. |
| **Install Mesh Compute** / **Reload Compute** | Install or reload an experimental [Mesh Compute](MESH_COMPUTE.md) modifier on a Mesh object. |

For function source selection, shared groups, and Reload requirements, see [Refresh a compiled function](WRITING_FUNCTIONS.md#refresh-a-compiled-function).

## Use the Library panel

The **Library** panel contains reusable scripts and package tools. Install other packages through **Library → Packages**.

| Catalog | Purpose |
| --- | --- |
| **Local** | NodeForge-managed `.nf` files and read-only external source folders. |
| **Functions** | Reusable functions provided by installed packages. |
| **Examples** | Bundled and package-provided example scripts. |
| **Packages** | Install and remove NodeForge packages. |

Select a Local file or Example, then click **Add Node Group** to place its entry function in the open base tree. This action requires a single entry function in the selected file; for a file with several entries, import and call the function you need from another script. For a [Mesh Compute](MESH_COMPUTE.md) entry, **Add Node Group** installs a modifier on the selected Mesh.

### Save a module to Local

1. Write or load a `.nf` module in a Text datablock.
2. Open **Library → Local** and browse to a NodeForge-managed folder.
3. Click **Save**, enter a file name, and confirm. Enable **Overwrite** to replace an existing file.

Use **Add Folder...** to register an existing directory of `.nf` files without copying it. External Local folders are read-only in NodeForge: edit their files with your normal editor. **Remove from Local** removes the registration but keeps the external files. In managed Local storage, **Delete File** removes a selected file and **Delete Folder** removes an empty folder.

To call functions saved in Local from another script, see [Import functions](MODULES.md#import-functions).

## Text Editor completion (prototype)

Text Editor completion suggests Core DSL functions, Local and package imports, imported members, and functions declared in the current Text. Call hints show parameter names, types, and defaults, plus available output names and types in return order.

## What to read next

Start with [`.nf` Modules](MODULES.md), then use [DSL Syntax and Semantics](SYNTAX.md) and [Core DSL Built-ins](BUILTINS.md) as references. [Writing Functions](WRITING_FUNCTIONS.md) covers reusable declarations; [Package Guide](PACKAGES.md) covers distribution.
