"""Opt-in benchmark: actual Local NumPy erosion through NodeForge's live GN modifier.

Run one configuration per fresh Blender process, for example:
  blender -b --factory-startup --python tests/blender/benchmark_mesh_compute_erosion_runtime.py -- \
    --side 96 --steps 100 --output /tmp/erosion-96-100.json

Includes Local compilation, source prefix, callback, numerical/publication cache,
and full evaluated GN consumer output. Uses the minimal source terrain without
external nodeforge.math, but the Python hydraulic+thermal equations are those
ported from hydraulic_grid_shape_cpu_fast.nf.
"""

from argparse import ArgumentParser
from pathlib import Path
from time import perf_counter
from array import array
import json
import shutil
import sys
import tempfile

import bpy
import numpy as np

sys.path.insert(0,str(Path(__file__).resolve().parents[3]))
from NodeForge import compiler,local_sources
from NodeForge.blender import mesh_compute_runtime as runtime

EXAMPLES=Path(__file__).resolve().parents[2]/'examples/mesh_compute_erosion'


def run(side,steps):
    """Compile and evaluate a real private root, checking output and cache hit."""
    with tempfile.TemporaryDirectory(prefix='nodeforge-erosion-bench-') as tmp:
        root=Path(tmp)/'Local';root.mkdir()
        shutil.copy2(EXAMPLES/'erosion.py',root/'erosion.py')
        old_catalog=local_sources._default_local_catalog_dir
        old_registry=local_sources._local_sources_registry_path
        local_sources._default_local_catalog_dir=lambda: root
        local_sources._local_sources_registry_path=lambda: Path(tmp)/'registry.json'
        try:

            mesh=bpy.data.meshes.new('BenchmarkInput')
            mesh.from_pydata([(0,0,0),(1,0,0),(0,1,0)],[],[(0,1,2)])
            owner=bpy.data.objects.new('BenchmarkObject',mesh)
            bpy.context.scene.collection.objects.link(owner)
            source=(EXAMPLES/'erosion_numpy_smoke.nf').read_text()
            source=source.replace('default=8',f'default={side}').replace('default=2',f'default={steps}')
            t0=perf_counter();compiler.create_mesh_compute_modifier(source,owner);t1=perf_counter()
            published=runtime.evaluate_modifier_binding(owner);t2=perf_counter()
            assert published is not None
            binding=runtime.get_modifier_binding(owner)
            assert binding is not None and binding.status=='READY'
            bpy.context.view_layer.update()
            dg=bpy.context.evaluated_depsgraph_get();evalobj=owner.evaluated_get(dg)
            final=evalobj.to_mesh(preserve_all_data_layers=True,depsgraph=dg)
            try:
                if len(final.vertices)!=side*side:raise AssertionError('output vertex count mismatch')
                for attr in ('height','water','sediment'):
                    layer=final.attributes[attr];values=array('f',[0])*len(final.vertices)
                    layer.data.foreach_get('value',values)
                    if not np.isfinite(np.frombuffer(values,dtype=np.float32)).all():
                        raise AssertionError(f'nonfinite {attr}')
            finally:evalobj.to_mesh_clear()
            t3=perf_counter()
            old_group=binding.group.as_pointer();old_result=binding.prepared.result_proxy.data.as_pointer()
            runtime.evaluate_modifier_binding(owner,skip_unchanged=True)
            t4=perf_counter()
            if binding.group.as_pointer()!=old_group or binding.prepared.result_proxy.data.as_pointer()!=old_result:
                raise AssertionError('unchanged input caused unintended group/Mesh republication')
            return {'side':side,'steps':steps,'compile_ms':round(1000*(t1-t0),3),
                    'first_runtime_ms':round(1000*(t2-t1),3),
                    'consumer_snapshot_ms':round(1000*(t3-t2),3),
                    'unchanged_cache_check_ms':round(1000*(t4-t3),3),
                    'vertices':side*side,'status':binding.status}
        finally:
            local_sources._default_local_catalog_dir=old_catalog
            local_sources._local_sources_registry_path=old_registry


def main():
    """Parse one configuration and persist its runtime measurement."""
    parser=ArgumentParser();parser.add_argument('--side',type=int,default=96)
    parser.add_argument('--steps',type=int,default=100)
    parser.add_argument('--output',type=Path)
    args=parser.parse_args(sys.argv[sys.argv.index('--')+1:] if '--' in sys.argv else [])
    if args.side<3 or args.steps<0:parser.error('side >= 3, steps >= 0')
    result=run(args.side,args.steps)
    print('EROSION_RUNTIME',json.dumps(result),flush=True)
    if args.output:args.output.write_text(json.dumps(result,indent=2))


if __name__=='__main__':main()
