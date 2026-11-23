#!/usr/bin/env python3
"""Run the independent Blender RNA/socket probes, retaining every stage JSON.

Requires only a Python 3 interpreter and the selected Blender executable.
For one Blender version, use an empty output directory.
"""
import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SCRIPTS = ROOT / 'scripts'

# Explicit data dependencies, not a list of Blender nodes or socket types.
PIPELINE = (
    ('inventory', 'inspect_geometry_nodes.py', ('inventory.json',)),
    ('rna', 'explore_states.py', ('rna.json',)),
    ('joint', 'explore_joint_states.py', ('rna.json', 'joint.json')),
    ('connections', 'probe_connections.py', ('joint.json', 'rna.json', 'connections.json')),
    ('type_pairs', 'probe_type_pairs.py', ('joint.json', 'type_pairs.json')),
    ('context', 'probe_context_states.py', ('rna.json', 'joint.json', 'connections.json', 'context.json')),
    ('final', 'probe_final_states.py', ('rna.json', 'joint.json', 'final.json')),
)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--blender', type=Path, required=True, help='Path to selected Blender binary')
    ap.add_argument('--out', type=Path, required=True, help='Empty directory for all stage JSON and logs')
    args = ap.parse_args()
    blender = args.blender.resolve()
    if not blender.is_file():
        ap.error(f'Blender executable does not exist: {blender}')
    out = args.out.resolve()
    if out.exists() and any(out.iterdir()):
        ap.error(f'Output directory must be empty to avoid mixing runs: {out}')
    out.mkdir(parents=True, exist_ok=True)
    logs = out / 'logs'
    logs.mkdir()
    for stage, script, names in PIPELINE:
        command = [str(blender), '-b', '-t', '1', '--factory-startup', '--python-exit-code', '1', '--python', str(SCRIPTS / script),
                   '--', *(str(out / name) for name in names)]
        print(f'STAGE {stage}', flush=True)
        with (logs / f'{stage}.log').open('w', encoding='utf-8') as log:
            log.write('COMMAND ' + ' '.join(command) + '\n')
            log.flush()
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=False)
        expected = out / f'{stage}.json'
        if result.returncode or not expected.is_file():
            raise SystemExit(f'Stage {stage} failed (exit={result.returncode}). See {logs / (stage + ".log")}')
    from merge_artifacts import load_sources, merge_documents, restore_stage, write_json, STAGES

    docs, sources = load_sources(out)
    merged = merge_documents(docs, sources)
    for stage in STAGES:
        if restore_stage(merged, stage) != docs[stage]:
            raise RuntimeError(f'Inspector merged specification lost {stage} data')
    version_parts = merged['blender_version'].split('.')
    version = '.'.join(version_parts[:2])
    destination = out / f'blender-{version}.spec.json'
    write_json(destination, merged)
    print(f'INSPECTOR_SPEC {destination}', flush=True)


if __name__ == '__main__':
    main()
