from helpers import *

from NodeForge.function_instances import (
    FUNCTION_DEFINITION_OWNER_PROP,
    FUNCTION_INSTANCE_KEY_PROP,
    FUNCTION_ROOT_OWNER_ID_PROP,
)


def _helper_by_instance_key(instance_key):
    """Return the sole live local helper carrying *instance_key*."""
    matches = [
        group for group in bpy.data.node_groups
        if group.get("nodeforge_generated_kind") == "local_function_helper"
        and str(group.get(FUNCTION_INSTANCE_KEY_PROP) or "") == str(instance_key)
    ]
    check(len(matches) == 1, f"expected one helper for instance key {instance_key}, got {[g.name for g in matches]}")
    return matches[0]
