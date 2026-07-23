from helpers import *




def test_import_and_registry_checks():
    for modname in ['compiler', 'expression_compiler', 'statement_compiler', 'local_functions', 'library_calls', 'systems.registry']:
        __import__('NodeForge.' + modname)
    for module in (io, vector, geometry, fields, instancing):
        missing = sorted((name for name in module.NAMES if not registry.has_callable_builtin(name)))
        check(not missing, f'registry missing {module.__name__}: {missing}')
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
