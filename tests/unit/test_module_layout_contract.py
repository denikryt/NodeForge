"""Small filesystem contract for stable NodeForge ownership surfaces."""

from pathlib import Path

import pytest


PACKAGE_ROOT = Path(__file__).resolve().parents[2]

# Keep this list intentionally small. These are public/cross-phase anchors and package
# boundaries; internal implementation filenames are free to evolve inside each owner.
CANONICAL_OWNER_ANCHORS = (
    "compiler.py",
    "catalog.py",
    "local_sources.py",
    "evaluation_modes.py",
    "extension_api.py",
    "extension_annotations.py",
    "extension_semantic_api.py",
    "semantic/__init__.py",
    "extensions/__init__.py",
    "blender/__init__.py",
)

# These namespaces were retired as ownership surfaces. Their return would reintroduce the
# ambiguous routing/layout removed by the ownership split, rather than merely rename an internal file.
RETIRED_OWNER_PATHS = (
    "builtins",
    "systems",
)


@pytest.mark.parametrize("relative_path", CANONICAL_OWNER_ANCHORS)
def test_canonical_owner_anchor_exists(relative_path: str):
    """Public/cross-phase anchors and the three internal owner packages remain available."""
    assert (PACKAGE_ROOT / relative_path).exists(), relative_path


@pytest.mark.parametrize("relative_path", RETIRED_OWNER_PATHS)
def test_retired_owner_namespace_is_absent(relative_path: str):
    """Historical mixed owner namespaces are not reintroduced as compatibility surfaces."""
    assert not (PACKAGE_ROOT / relative_path).exists(), relative_path
