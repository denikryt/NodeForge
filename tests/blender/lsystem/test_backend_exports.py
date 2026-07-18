from NodeForge.systems.lsystem import backends as lsystem_backends


def test_retired_per_segment_backend_api_is_not_exported():
    assert not hasattr(lsystem_backends, "limited_segment_node_backend")
    assert "limited_segment_node_backend" not in getattr(lsystem_backends, "__all__", ())
    assert not hasattr(lsystem_backends, "MAX_LSYSTEM_SEGMENTS")
    retired_validator = "validate_" + "backend_backend_available"
    assert not hasattr(lsystem_backends, retired_validator)
