# Get Started with NodeForge

NodeForge turns `.nf` scripts into Blender Geometry Nodes. Executable statements add nodes to an open base tree; typed functions create reusable nested groups. See [Core Concepts](CORE_CONCEPTS.md) and [`.nf` Modules](MODULES.md) for the language model.

## Install NodeForge

NodeForge requires Blender 5.2 or newer. Install it through its Blender Extensions repository to receive updates in Blender:

1. Open **Edit → Preferences → Get Extensions**.
2. Under **Repositories**, choose **Add Remote Repository** and enter `https://denikryt.github.io/NodeForge/index.json`.
3. Find **NodeForge**, click **Install**, and enable it.

To install from a file, download the latest Extension ZIP from [GitHub Releases](https://github.com/denikryt/NodeForge/releases/latest). In **Get Extensions**, choose **Install from Disk...**, select the ZIP, and enable **NodeForge**. This also works offline after downloading the ZIP.

When switching from the earlier ZIP add-on, disable the old NodeForge add-on before enabling the Extension. Your Local files and installed packages remain in Blender's user-data directories.

The add-on adds a **NodeForge** tab to the Geometry Node Editor sidebar. It comes with the [Math](MATH_METHODS.md) and [L-System](LSYSTEMS_SCRIPTING.md) libraries, which are installed or updated when the Extension is enabled.

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

The function becomes a Group Node in the open base tree. Connect its output to the base tree's **Group Output** Geometry input to display the cube. Change **Size** on the Group Node to see Blender reevaluate it.

A script can also build ordinary nodes directly:

```python
geometry = cube(2.0)
```

**Compile Script** adds these nodes to the open base tree. It keeps existing nodes and does not connect the base tree's Group Input/Output automatically.

## Update a script

Edit the source in a Text datablock, select it under **Text Script**, open the base Geometry Nodes tree, and click **Update Script**. Each update uses the currently selected Text. The action recompiles its root graph and called functions, including nested function groups. It replaces the tree's ordinary nodes, including Group Nodes, while keeping the physical tree, its interface, and its Group Input/Output nodes.

Compatible unambiguous links between the root interface nodes and rebuilt nodes can be restored; other manual connections between ordinary nodes are replaced by the script's graph. Inspect boundary links after updating, especially if node types or counts changed. Repeated **Update Script** calls replace rather than accumulate nodes. **Compile Script** adds to the existing graph. A module containing only compile-time constants clears the ordinary nodes when used with **Update Script**; a module containing only declarations places its entry functions. An empty or invalid source is rejected.

Update does not migrate animation or drivers on ordinary root nodes; a tree containing them cannot be updated with this action.

Both script actions require an editable base tree in the Geometry Nodes editor. Linked read-only trees and NodeForge-managed function or Mesh Compute trees are unsupported targets. Return from any nested group before using them.

## Main actions

| Button | What it does |
| --- | --- |
| **Compile Script** | Adds the selected Text's nodes or entry functions to the open base tree. |
| **Update Script** | Replaces ordinary nodes in the open base tree from the selected Text. |
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

NodeForge has built-in text completion for scripts in Blender's Text Editor. This prototype suggests Core DSL functions, Local and package imports, imported members, and functions declared in the current script. It also shows function parameters and return types when available. Enable it with **NodeForge** in the Text Editor header.

## What to read next

Start with [`.nf` Modules](MODULES.md), then use [DSL Syntax and Semantics](SYNTAX.md) and [Core DSL Built-ins](BUILTINS.md) as references. [Writing Functions](WRITING_FUNCTIONS.md) covers reusable declarations; [Package Guide](PACKAGES.md) covers distribution.
