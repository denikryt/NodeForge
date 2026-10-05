from helpers import *


def test_import_and_registry_checks():
    """Permanent compiler modules import and the declarative builtin registry is populated."""
    for modname in [
        'compiler',
        'semantic_analysis',
        'semantic_body',
        'semantic_group',
        'source_callables',
        'callable_contracts',
        'blender_ir_lowering',
        'systems',
    ]:
        __import__('NodeForge.' + modname)
    check(registry.CALLABLE_BUILTIN_NAMES, 'callable builtin registry is empty')
    check(registry.BUILTIN_NAMES >= registry.CALLABLE_BUILTIN_NAMES, 'builtin registry lost callable names')
    print('IMPORT_AND_REGISTRY_OK')



def test_helpers_star_import_exports_private_migration_helpers():
    """Split test modules rely on helper star imports for migrated private helpers."""
    namespace = {}
    exec("from helpers import *", namespace)
    for name in (
        "_manifest_refs",
        "_owned_generated_id_keys",
    ):
        assert name in namespace
