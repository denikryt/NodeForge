from helpers import *




def test_addon_register_unregister_paths():
    bpy.ops.preferences.addon_enable(module='NodeForge')
    check('NodeForge' in bpy.context.preferences.addons, 'addon_enable did not register NodeForge')
    bpy.ops.preferences.addon_disable(module='NodeForge')
    check('NodeForge' not in bpy.context.preferences.addons, 'addon_disable did not unregister NodeForge')
    NodeForge.register()
    NodeForge.unregister()
    print('STARTUP_SHUTDOWN_OK')


def test_addon_unregister_is_idempotent_after_manual_lifecycle():
    """Manual lifecycle scripts should not poison Blender shutdown with double unregister."""
    NodeForge.register()
    NodeForge.unregister()
    NodeForge.unregister()
    NodeForge.register()
    NodeForge.unregister()
