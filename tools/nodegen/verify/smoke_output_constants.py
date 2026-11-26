"""Verify optional Blender source-node constants: omission and explicit assignment.

Only the two zero-input sources in the fixed 64-node selection qualify.
Color is stored in node.value (RNA); Value stores its data in output.default_value.
"""
import argparse, json, math, os, shutil, sys, traceback
from pathlib import Path
import bpy
if os.environ.get('NODEFORGE_CORE_PARENT'):
    sys.path.insert(0,os.environ['NODEFORGE_CORE_PARENT'])
from NodeForge import compiler, packages


def close(actual, expected):
    if isinstance(expected, (tuple,list)):
        return len(actual)==len(expected) and all(close(a,e) for a,e in zip(actual,expected))
    return math.isclose(actual,expected,abs_tol=1e-5) if isinstance(expected,float) else actual==expected


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--package',required=True)
    ap.add_argument('--out',required=True)
    args=ap.parse_args(sys.argv[sys.argv.index('--')+1:])
    out=Path(args.out);out.mkdir(parents=True,exist_ok=True)
    install=out/'installed';shutil.rmtree(install,ignore_errors=True)
    packages.set_packages_dir_for_tests(install);packages.invalidate_caches()
    tests=[]
    cases=[('FunctionNodeInputBool','boolean','boolean=True', 'rna','boolean',True),
           ('FunctionNodeInputInt','integer','integer=7', 'rna','integer',7),
           ('FunctionNodeInputString','string',"string='hello'", 'rna','string','hello'),
           ('FunctionNodeInputVector','vector','vector=(0.2, 0.4, 0.6)', 'rna','vector',(0.2,0.4,0.6)),
           ('FunctionNodeInputRotation','rotation','rotation=(0.2, 0.4, 0.6)', 'rna','rotation_euler',(0.2,0.4,0.6)),
           ('ShaderNodeValue','value','', 'output','Value',0.0),
           ('ShaderNodeValue','value','value=5.25', 'output','Value',5.25),
           ('FunctionNodeInputColor','color','', 'rna','value',(0.5,0.5,0.5,1.0)),
           ('FunctionNodeInputColor','color','value=(0.2, 0.4, 0.6, 1.0)', 'rna','value',(0.2,0.4,0.6,1.0))]
    try:
        packages.install_package_directory(Path(args.package))
        for node_id, fn, kwargs, target, name, expected in cases:
            for grouped in (False,True):
                label=f'{fn}_{"explicit" if kwargs else "default"}_{"grouped" if grouped else "inline"}'
                source=(f'from packages import nodes as gen\n\n'
                        f'def entry():\n    x, = gen.{fn}({kwargs}{", " if kwargs else ""}__inline__={not grouped})\n    return x\n')
                before={g.as_pointer() for g in bpy.data.node_groups}
                result={'name':label,'node':node_id,'source':source,'status':'failed'}
                try:
                    root=compiler.create_expression_group(source,'NF_OutputConst_'+label)
                    groups=[root]
                    groups.extend(n.node_tree for n in root.nodes if n.bl_idname=='GeometryNodeGroup' and n.node_tree)
                    matches=[n for g in groups for n in g.nodes if n.bl_idname==node_id]
                    if len(matches)!=1:raise AssertionError(f'expected one {node_id}, got {len(matches)}')
                    node=matches[0]
                    actual=(getattr(node,name) if target=='rna' else
                            [s for s in node.outputs if s.identifier==name][0].default_value)
                    if not close(actual,expected):
                        raise AssertionError(f'{target}:{name} expected {expected}, got {tuple(actual) if hasattr(actual,"__iter__") else actual}')
                    result['status']='passed'
                except BaseException as e:
                    result['error_type']=type(e).__name__;result['error']=str(e)
                    result['traceback']=traceback.format_exc()
                finally:
                    for g in tuple(bpy.data.node_groups):
                        if g.as_pointer() not in before:bpy.data.node_groups.remove(g,do_unlink=True)
                tests.append(result)
                print('OUTPUT_CONSTANT',bpy.app.version_string,label,result['status'],result.get('error','')[:180],flush=True)
    finally:
        packages.set_packages_dir_for_tests(None);packages.invalidate_caches()
    summary={'blender':bpy.app.version_string,'passed':sum(t['status']=='passed' for t in tests),
             'failed':sum(t['status']=='failed' for t in tests),'cases':tests}
    (out/'constants.json').write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n')
    print('OUTPUT_CONSTANT_SUMMARY',summary['blender'],summary['passed'],summary['failed'],flush=True)

if __name__=='__main__': main()
