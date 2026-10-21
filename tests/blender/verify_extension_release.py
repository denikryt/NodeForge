"""CI entrypoint: install the built Extension ZIP and run Blender release regressions.

Usage:
    blender --background --factory-startup --python-exit-code 1 \\
      --python tests/blender/verify_extension_release.py -- /absolute/path/nodeforge-x.y.z.zip
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path
import sys
import tempfile

import bpy


def main() -> None:
    """Use an isolated Blender repository and run tests against the installed ZIP."""
    if "--" not in sys.argv:
        raise RuntimeError("Pass the built Blender Extension ZIP after --")
    archive = Path(sys.argv[sys.argv.index("--") + 1]).resolve()
    if not archive.is_file():
        raise FileNotFoundError(archive)

    module_name = "bl_ext.nodeforge_ci.nodeforge"
    repo_dir = Path(tempfile.mkdtemp(prefix="nodeforge-extension-ci-"))
    bpy.context.preferences.extensions.repos.new(
        name="NodeForge CI", module="nodeforge_ci", source="USER",
        custom_directory=str(repo_dir),
    )
    result = bpy.ops.extensions.package_install_files(
        filepath=str(archive), repo="nodeforge_ci", enable_on_install=True,
    )
    if result != {"FINISHED"}:
        raise AssertionError(f"Extension installation failed: {result}")
    extension = importlib.import_module(module_name)
    if module_name not in bpy.context.preferences.addons:
        raise AssertionError(f"Extension not enabled: {module_name}")

    # The headless Python entrypoint does not process Blender timer callbacks.
    # Tests explicitly tick the startup catalog refresh that the UI event loop
    # executes after Blender removes its restricted registration context.
    os.environ["NODEFORGE_EXTENSION_MODULE"] = module_name
    import pytest

    checks = Path(__file__).with_name("test_extension_package_install.py")
    result = pytest.main([str(checks), "-q", "-p", "no:cacheprovider"])
    if result != pytest.ExitCode.OK:
        raise RuntimeError(f"Blender release integration checks failed: {result}")
    print(f"PASS: installed and tested built extension {archive.name} ({extension.__name__})")


if __name__ == "__main__":
    main()
