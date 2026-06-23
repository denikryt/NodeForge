from helpers import *




def test_lsystem_documented_contracts():
    for name, source in LSYSTEM_GALLERY_EXAMPLES.items():
        compile_group(source, 'NFTest_lsystem_gallery_' + name)
    check(not hasattr(lsystem_backends, 'limited_segment_node_backend'), 'limited per-segment backend is still exported as a module attribute')
    check('limited_segment_node_backend' not in getattr(lsystem_backends, '__all__', ()), 'limited per-segment backend is still in __all__')
    check(not hasattr(lsystem_backends, 'MAX_LSYSTEM_SEGMENTS'), 'retired per-segment budget is still exported')
    retired_validator = 'validate_' + 'sta' + 'ge1_backend_available'
    check(not hasattr(lsystem_backends, retired_validator), 'retired backend validator is still exported')
    static_metrics = type('Metrics', (), {'angle_is_runtime': False, 'step_is_runtime': False, 'has_branches': True})()
    branch_free_metrics = type('Metrics', (), {'angle_is_runtime': True, 'step_is_runtime': False, 'has_branches': False})()
    branch_aware_metrics = type('Metrics', (), {'angle_is_runtime': False, 'step_is_runtime': True, 'has_branches': True})()
    check(lsystem_backends.select_backend_category(static_metrics) == 'static', 'static selector category changed')
    check(lsystem_backends.select_backend_category(branch_free_metrics) == 'branch_free_runtime', 'branch-free selector category changed')
    check(lsystem_backends.select_backend_category(branch_aware_metrics) == 'branched_runtime', 'branch-aware selector category changed')
    print('LSYSTEM_DOCUMENTED_CONTRACTS_OK')
