from helpers import *




def test_import_and_registry_checks():
    for modname in ['compiler', 'expression_compiler', 'statement_compiler', 'local_functions', 'library_calls', 'systems.registry', 'systems.lsystem.compiler']:
        __import__('NodeForge.' + modname)
    expected_lsystem = {'ls_system', 'ls_axiom', 'ls_rule', 'ls_iterations', 'ls_angle', 'ls_step', 'ls_param', 'ls_marker', 'ls_points'}
    check(expected_lsystem.issubset(systems_registry.constructor_names()), 'systems registry names drifted')
    check({'sin', 'sqrt', 'clamp', 'map_range', 'noise', 'random_value'}.issubset(systems_registry.constructor_names()), 'standard callable package names missing')
    for module in (io, vector, geometry, fields, instancing):
        missing = sorted((name for name in module.NAMES if not registry.has_callable_builtin(name)))
        check(not missing, f'registry missing {module.__name__}: {missing}')
    print('IMPORT_AND_REGISTRY_OK')


def test_helpers_star_import_exports_private_migration_helpers():
    """Split test modules rely on helper star imports for migrated private helpers."""
    namespace = {}
    exec("from helpers import *", namespace)
    for name in (
        "_static_lsystem_source",
        "_manifest_refs",
        "_owned_generated_id_keys",
        "_runtime_branched_source",
    ):
        assert name in namespace
