#!/usr/bin/env python3
"""Regenerate the bundled Blender Nodes package from Inspector data and optionally test it.

Defaults to the pinned Blender 5.2 inspection. The hand-maintained package
manifest is never regenerated. Run with --verify and --blender to compile the
result against a real Blender installation. Use --all for every configuration.
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory
import zipfile

ROOT = Path(__file__).resolve().parents[2]
TOOL = Path(__file__).resolve().parent
sys.path.insert(0, str(TOOL / 'generator'))
from generate_clean import generate  # noqa: E402

DEFAULT_SPEC = TOOL / 'data' / 'blender-5.2.spec.json.gz'
DEFAULT_PACKAGE = ROOT / 'packages' / 'nodes'
EXCLUDED_NAME_PARTS = ('Simulation', 'Repeat', 'Foreach', 'Bake')
FILES = ('interface.py', 'backend.py', 'semantic.py')


def read_spec(path: Path) -> dict:
    """Read a merged Inspector catalogue in JSON or gzip-compressed JSON."""
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rt', encoding='utf-8') as stream:
        return json.load(stream)


def candidates(spec: dict) -> list[str]:
    """Select ordinary nodes, keeping unsupported cases in the skip report."""
    return [name for name in sorted(spec['nodes'])
            if not any(word in name for word in EXCLUDED_NAME_PARTS)]


def write_archive(package: Path, destination: Path) -> None:
    """Package only declared content with a root-level manifest for the stock installer."""
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, 'w', compression=zipfile.ZIP_DEFLATED,
                         compresslevel=9) as archive:
        for file in sorted(package.rglob('*')):
            if not file.is_file() or '__pycache__' in file.parts or file.suffix in {'.pyc', '.pyo'}:
                continue
            archive.write(file, file.relative_to(package).as_posix())


def build(spec: dict, package: Path, *, check: bool = False,
          archive: bool = True) -> tuple[dict, Path | None]:
    """Replace generated owner files only; never rewrite package metadata."""
    package = package.resolve()
    manifest = package / 'nodeforge_package.json'
    if not manifest.exists():
        if check:
            raise FileNotFoundError(f'Package manifest does not exist: {manifest}')
        package.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(DEFAULT_PACKAGE / 'nodeforge_package.json', manifest)
    metadata = json.loads(manifest.read_text(encoding='utf-8'))
    if metadata['contents'].get('systems') != 'systems':
        raise ValueError('Package manifest must declare contents.systems = "systems"')
    with TemporaryDirectory(prefix='nf-nodegen-') as tmp:
        scratch = Path(tmp)
        source = scratch / 'blender'
        report = generate(spec, candidates(spec), source)
        target = package / 'systems' / 'blender'
        if check:
            for filename in FILES:
                if not (target / filename).is_file() or (target / filename).read_bytes() != (source / filename).read_bytes():
                    raise ValueError(f'Out-of-date generated file: {target / filename}')
        else:
            target.mkdir(parents=True, exist_ok=True)
            for filename in FILES:
                shutil.copyfile(source / filename, target / filename)
        if report['summary']['wrappers'] == 0:
            raise ValueError('No supported wrappers produced')
    output = None
    if archive:
        minimum = metadata['nodeforge_min_version']
        version = metadata['version']
        if package == DEFAULT_PACKAGE.resolve():
            output = ROOT / 'package_install' / f"{metadata['id']}-v{version}_nf-v{minimum}.zip"
        else:
            output = package.parent / f"{metadata['id']}-v{version}_nf-v{minimum}.zip"
        if not check:
            write_archive(package, output)
        elif not output.is_file():
            raise ValueError(f'Missing bundled ZIP: {output}')
        else:
            with TemporaryDirectory(prefix='nf-nodegen-zip-') as tmp:
                expected = Path(tmp) / 'expected.zip'
                write_archive(package, expected)
                # Compare archive members, not timestamps or compression headers.
                with zipfile.ZipFile(output) as old, zipfile.ZipFile(expected) as current:
                    if sorted(old.namelist()) != sorted(current.namelist()) or any(
                        old.read(name) != current.read(name) for name in current.namelist()
                    ):
                        raise ValueError(f'Out-of-date bundled ZIP: {output}')
    return report, output


def verify(blender: Path, package: Path, report: dict, out: Path, *, all_states: bool, limit: int = 0) -> dict:
    """Compile node configurations using the same package installer as users."""
    out.mkdir(parents=True, exist_ok=True)
    catalogue = out / 'generation.json'
    catalogue.write_text(json.dumps(report, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    command = [str(blender.resolve()), '-b', '-t', '1', '--factory-startup', '--python-exit-code', '1',
               '--python', str(TOOL / 'verify' / 'validate_nodes.py'), '--',
               '--generation', str(catalogue), '--package', str(package.resolve()),
               '--out', str(out)]
    if all_states:
        command.append('--all')
    if limit:
        command.extend(['--limit', str(limit)])
    env = os.environ.copy()
    env.setdefault('NODEFORGE_CORE_PARENT', str(ROOT.parent))
    print('VERIFY', ' '.join(command), flush=True)
    with (out / 'blender.log').open('w', encoding='utf-8') as log:
        subprocess.run(command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
    summary = json.loads((out / 'execution.json').read_text(encoding='utf-8'))
    if summary['failed']:
        raise RuntimeError(f"{summary['failed']} node validation failures; see {out}")
    for script, result_file in (('smoke_named_optional.py', 'smoke.json'),
                                ('smoke_output_constants.py', 'constants.json')):
        folder = out / Path(script).stem
        folder.mkdir(exist_ok=True)
        smoke_command = [str(blender.resolve()), '-b', '-t', '1', '--factory-startup',
                         '--python-exit-code', '1', '--python', str(TOOL / 'verify' / script),
                         '--', '--package', str(package.resolve()), '--out', str(folder)]
        with (folder / 'blender.log').open('w', encoding='utf-8') as log:
            subprocess.run(smoke_command, env=env, stdout=log, stderr=subprocess.STDOUT, check=True)
        cases = json.loads((folder / result_file).read_text(encoding='utf-8'))
        if cases['failed']:
            raise RuntimeError(f"{script} has {cases['failed']} failures; see {folder}")
        summary[script] = {'passed': cases['passed'], 'failed': cases['failed']}
    return summary


def main() -> None:
    """Run generation, optional release archive creation and Blender verification."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--spec', type=Path, default=DEFAULT_SPEC)
    parser.add_argument('--output', type=Path, default=DEFAULT_PACKAGE, help='Package root (not system subdirectory)')
    parser.add_argument('--blender', type=Path, help='Blender executable for --verify')
    parser.add_argument('--verify', action='store_true', help='Run real Blender compiler checks')
    parser.add_argument('--all', action='store_true', help='Check every supported configuration (requires --verify)')
    parser.add_argument('--limit', type=int, default=0, help='Limit validation cases for a quick smoke run')
    parser.add_argument('--check', action='store_true', help='Compare generated output without changing it')
    parser.add_argument('--no-zip', action='store_true', help='Deprecated no-op; source packages do not need ZIP')
    parser.add_argument('--results', type=Path, default=ROOT.parent / 'nodegen-results')
    args = parser.parse_args()
    if args.verify and (args.blender is None or not args.blender.is_file()):
        parser.error('--verify requires --blender pointing to a Blender executable')
    if args.all and not args.verify:
        parser.error('--all requires --verify')
    spec = read_spec(args.spec)
    report, bundle = build(spec, args.output, check=args.check, archive=False)
    print(f"GENERATED {report['summary']['wrappers']} functions; "
          f"{report['summary']['states']} representable configurations; "
          f"{len(report['skipped'])} skipped nodes", flush=True)
    if not args.check:
        args.results.mkdir(parents=True, exist_ok=True)
        summary = {
            'blender': spec['blender_version'],
            'generated': report['summary']['wrappers'],
            'configurations': report['summary']['states'],
            'excluded_zones': [node for node in spec['nodes'] if node not in candidates(spec)],
            'skipped': report['skipped'],
            'partial_nodes': [
                {'node': row['node'], 'unsupported_configurations': len(row['unsupported'])}
                for row in report['wrappers'] if row['unsupported']
            ],
        }
        coverage = args.results / 'coverage.json'
        coverage.write_text(json.dumps(summary, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
        print(f'COVERAGE {coverage}', flush=True)
    if bundle is not None:
        print(f'PACKAGE {bundle}', flush=True)
    if args.verify:
        summary = verify(args.blender, args.output, report, args.results, all_states=args.all, limit=args.limit)
        print('VALIDATION', summary, flush=True)


if __name__ == '__main__':
    main()
