#!/usr/bin/env python3
"""Inspect Blender, regenerate the bundled Nodes library, and run compiler tests.

This is the entrypoint for the in-repository NodeForge node-generation
pipeline. The Inspector and generator remain independent tools.

Commands:
    inspect    run the Inspector and save its merged Blender specification
    generate   regenerate the package from a saved Inspector specification
    test       validate the current generated package without modifying it
    build      inspect + generate + test (full pipeline)

An installable library ZIP is NOT generated or tracked here. The add-on embeds
its source package in packages/nodes and installs it from that directory.
"""

from __future__ import annotations

import argparse
from pathlib import Path
import subprocess
import sys
import uuid

TOOLS = Path(__file__).resolve().parent
ROOT = TOOLS.parents[1]
DEFAULT_SPEC = TOOLS / 'data' / 'blender-5.2.spec.json.gz'
DEFAULT_PACKAGE = ROOT / 'packages' / 'nodes'
DEFAULT_RESULTS = ROOT.parent / 'nodegen-results'


def current_spec() -> Path:
    """Use the latest saved Blender 5.2 inspection, otherwise the pinned spec."""
    folder = TOOLS / 'data' / 'inspection'
    inspected = list(folder.rglob('blender-5.2.spec.json')) if folder.exists() else []
    return max(inspected, key=lambda path: path.stat().st_mtime_ns) if inspected else DEFAULT_SPEC


def inspector(blender: Path, dest: Path) -> Path:
    """Invoke the existing Blender Inspector and return the merged JSON spec.

    The Inspector owns its internal seven-stage bpy workflow; this CLI only
    drives it and selects the merged artifact for the generator.
    """
    runner = TOOLS / 'inspector' / 'run_inspector.py'
    if not runner.is_file():
        raise FileNotFoundError(f'Inspector runner is missing: {runner}')
    if not blender.is_file():
        raise FileNotFoundError(f'Blender executable is missing: {blender}')
    dest.mkdir(parents=True, exist_ok=True)
    # An isolated run directory prevents accidentally using stale specifications
    # left behind by earlier Blender inspections.
    run_dir = dest / f'run_{uuid.uuid4().hex[:12]}'
    run_dir.mkdir(parents=True, exist_ok=False)
    subprocess.run([sys.executable, str(runner), '--blender', str(blender.resolve()),
                    '--out', str(run_dir.resolve())], check=True)
    specs = sorted(run_dir.rglob('*.spec.json'))
    if len(specs) != 1:
        raise RuntimeError(f'Expected one merged Inspector spec in {run_dir}; found {len(specs)}')
    return specs[0]


def generation(spec: Path, output: Path, results: Path, *, check: bool = False) -> None:
    """Update or check Python owner files, without creating a library archive."""
    builder = TOOLS / 'build.py'
    if not builder.is_file():
        raise FileNotFoundError(f'NodeForge generator runner is missing: {builder}')
    command = [sys.executable, str(builder), '--spec', str(spec.resolve()),
               '--output', str(output.resolve()), '--results', str(results.resolve()), '--no-zip']
    if check:
        command.append('--check')
    subprocess.run(command, check=True)


def validation(spec: Path, output: Path, blender: Path, results: Path, *, all_states: bool = True) -> None:
    """Run existing Blender compiler tests without regenerating the library."""
    builder = TOOLS / 'build.py'
    if not builder.is_file():
        raise FileNotFoundError(f'NodeForge generator runner is missing: {builder}')
    if not blender.is_file():
        raise FileNotFoundError(f'Blender executable is missing: {blender}')
    command = [sys.executable, str(builder), '--spec', str(spec.resolve()),
               '--output', str(output.resolve()), '--results', str(results.resolve()),
               '--no-zip', '--check', '--verify', '--blender', str(blender.resolve())]
    if all_states:
        command.append('--all')
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    for name in ('inspect', 'generate', 'test', 'build'):
        p = sub.add_parser(name)
        if name in ('inspect', 'test', 'build'):
            p.add_argument('--blender', required=True, type=Path)
        if name in ('inspect', 'build'):
            p.add_argument('--inspect-output', type=Path, default=TOOLS / 'data' / 'inspection')
        if name in ('generate', 'test', 'build'):
            if name != 'build':
                p.add_argument('--spec', type=Path)
            p.add_argument('--output', type=Path, default=DEFAULT_PACKAGE)
            p.add_argument('--results', type=Path, default=DEFAULT_RESULTS)
        if name == 'generate':
            p.add_argument('--check', action='store_true')
        if name == 'test':
            p.add_argument('--quick', action='store_true', help='Use representative cases, not all configurations')
    args = parser.parse_args()
    if args.command == 'inspect':
        path = inspector(args.blender, args.inspect_output)
        print(f'INSPECTOR_SPEC {path}')
    elif args.command == 'generate':
        generation(args.spec or current_spec(), args.output, args.results, check=args.check)
    elif args.command == 'test':
        validation(args.spec or current_spec(), args.output, args.blender, args.results,
                   all_states=not args.quick)
    else:
        spec = inspector(args.blender, args.inspect_output)
        generation(spec, args.output, args.results)
        validation(spec, args.output, args.blender, args.results)


if __name__ == '__main__':
    main()
