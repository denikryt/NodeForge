"""Real Blender 5.2 GN-prefix → Local NumPy erosion → native GN suffix tests."""

from array import array
from pathlib import Path
import shutil

import bpy
import numpy as np
import pytest

from NodeForge.compiler import create_mesh_compute_modifier
from NodeForge.blender import mesh_compute_runtime as runtime
from NodeForge.tests.blender.test_mesh_compute_modifier_binding import configured_local, mesh_object

pytestmark = pytest.mark.blender
EXAMPLES = Path(__file__).resolve().parents[2] / 'examples/mesh_compute_erosion'


def _evaluate_layers(owner):
    """Read only the evaluated output Mesh, never the source or proxy ID."""
    bpy.context.view_layer.update()
    depsgraph=bpy.context.evaluated_depsgraph_get()
    evaluated=owner.evaluated_get(depsgraph)
    mesh=evaluated.to_mesh(preserve_all_data_layers=True,depsgraph=depsgraph)
    try:
        count=len(mesh.vertices)
        output={}
        for name in ('height','water','sediment','initial_height'):
            values=array('f',[0])*count
            mesh.attributes[name].data.foreach_get('value',values)
            output[name]=np.frombuffer(values,dtype=np.float32).copy()
        positions=array('f',[0])*(3*count)
        mesh.attributes['position'].data.foreach_get('vector',positions)
        output['xyz']=np.frombuffer(positions,dtype=np.float32).reshape(-1,3).copy()
        return output
    finally:
        evaluated.to_mesh_clear()


def test_numpy_erosion_example_compiles_from_local_to_real_gn(configured_local,mesh_object):
    """Actual selected Local Python executes, and GN suffix applies height to Z."""
    shutil.copy2(EXAMPLES/'erosion.py',configured_local/'erosion.py')
    source=(EXAMPLES/'erosion_numpy_smoke.nf').read_text()
    group=create_mesh_compute_modifier(source,mesh_object)
    result=runtime.evaluate_modifier_binding(mesh_object)
    assert result is not None
    assert runtime.get_modifier_binding(mesh_object).status=='READY'
    values=_evaluate_layers(mesh_object)
    assert values['height'].shape==(64,)
    assert np.all(np.isfinite(values['height']))
    np.testing.assert_allclose(values['xyz'][:,2],values['height'],atol=1e-5,rtol=0)
    np.testing.assert_allclose(values['initial_height'],2.+.07*values['xyz'][:,0]+.11*values['xyz'][:,1],atol=1e-5,rtol=0)
    assert np.any(values['water']>0.)
    assert np.all(values['sediment']>=0.)


def test_erosion_steps_zero_refreshes_named_outputs(configured_local,mesh_object):
    """Zero-step update keeps original height and clears simulation arrays."""
    shutil.copy2(EXAMPLES/'erosion.py',configured_local/'erosion.py')
    source=(EXAMPLES/'erosion_numpy_smoke.nf').read_text().replace('default=2','default=0')
    create_mesh_compute_modifier(source,mesh_object)
    runtime.evaluate_modifier_binding(mesh_object)
    result=_evaluate_layers(mesh_object)
    np.testing.assert_allclose(result['height'],result['initial_height'],atol=1e-6,rtol=0)
    assert not np.any(result['water'])
    assert not np.any(result['sediment'])
