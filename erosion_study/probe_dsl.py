"""Record actual compiler rejection messages for relevant unsupported constructs."""
import sys,json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent/'extracted/NodeForge'))
import NodeForge
from NodeForge import compiler
NodeForge.register()
cases={
 'embedded_input':'a = max(input_float("Amount", default=1.0), 0.0)\na',
 'while':'a = 0.0\nwhile a < 1.0:\n    a = a + 0.1\na',
 'break':'a = 0.0\nfor k in repeat_range(10):\n    a = a + 0.1\n    break\na',
 'continue':'a = 0.0\nfor k in repeat_range(10):\n    a = a + 0.1\n    continue\na',
}
results={}
for name,source in cases.items():
    try:
        compiler.create_expression_group(source,'Probe '+name)
        results[name]={'rejected':False}
    except Exception as exc:
        results[name]={'rejected':True,'error':str(exc)}
(ROOT/'dsl_probes.json').write_text(json.dumps(results,indent=2))
print(json.dumps(results,indent=2))
