"""Runtime-link and grouped smoke probes for dynamic-collection Blender node wrappers."""
import argparse,json,os,shutil,sys
from pathlib import Path
import bpy
if os.environ.get('NODEFORGE_CORE_PARENT'):sys.path.insert(0,os.environ['NODEFORGE_CORE_PARENT'])
sys.path.insert(0,str(Path(__file__).parent))
from NodeForge import packages
from validate_nodes import run_case

def main():
    a=argparse.ArgumentParser();a.add_argument('--generation',required=True);a.add_argument('--package',required=True);a.add_argument('--out',required=True)
    args=a.parse_args(sys.argv[sys.argv.index('--')+1:]);out=Path(args.out);out.mkdir(exist_ok=True,parents=True)
    generation=json.loads(Path(args.generation).read_text())
    pd=out/'installed';shutil.rmtree(pd,ignore_errors=True)
    packages.set_packages_dir_for_tests(pd);packages.invalidate_caches()
    rows=[]
    try:
        packages.install_package_directory(Path(args.package))
        for item in generation['wrappers']:
            dyn=item['dynamic']
            scenarios=[(None,None)]
            if dyn:
                kind=dyn['kind']
                if kind=='index':scenarios=[(0,None),(1,None),(4,None)]
                elif kind=='capture':scenarios=[('FLOAT:Scalar,VECTOR:Direction',None),('ROTATION:Orientation',None)]
                elif kind=='menu':scenarios=[('A,B,C','C'),('Z,X','X'),('A','A')]
            for elements,menu in scenarios:
                for mode in ('inline','grouped'):
                    for runtime in (False,True):
                        # One representative base state, multiple dynamic shapes.
                        row=run_case(item,item['configurations'][0],mode,runtime,elements,menu)
                        rows.append(row)
                        print('NODEGEN_SMOKE',item['node'],str(elements),mode,runtime,row['status'],row.get('error','')[:130],flush=True)
    finally:
        packages.set_packages_dir_for_tests(None);packages.invalidate_caches()
    (out/'runtime-smoke.json').write_text(json.dumps({'version':bpy.app.version_string,'rows':rows},indent=2,ensure_ascii=False)+'\n')
    print('NODEGEN_SMOKE_RESULT',len(rows),sum(x['status']=='passed' for x in rows),flush=True)
if __name__=='__main__':main()
