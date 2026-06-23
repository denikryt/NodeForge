"""L-system-specific Blender pytest fixtures."""

import pytest

from fixtures import lsystem_source


@pytest.fixture
def build_lsystem_source():
    """Return the shared L-system source builder used by Blender tests."""
    return lsystem_source
