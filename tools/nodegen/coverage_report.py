"""Render the bundled Nodes coverage reference from Inspector and generator data.

The generator report owns wrapper admission and runtime socket contracts. The
Inspector supplies the additional observed layouts, including collection probes
and nodes that have no generated function. This report never publishes bindings.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path

from build import build, candidates, read_spec
from inspect_contract import marker


def observed_sockets(layout, *, dynamic=False):
    """Return physical socket classes, ignoring disabled and placeholder sockets."""
    return {sock['bl_idname'] for side in ('inputs', 'outputs') for sock in layout[side]
            if sock.get('enabled', True) and not sock.get('is_unavailable')
            and (dynamic or not sock.get('is_inactive'))
            and sock['bl_idname'] != 'NodeSocketVirtual'}


def coverage_rows(spec, report):
    """Join every inspected node to the actual generated wrapper, if present.

    Base-state counts come from the generator, not socket type heuristics.
    Accepted collection probes extend the observed socket inventory without
    counting those probes as additional base configurations.
    """
    wrappers = {row['node']: row for row in report['wrappers']}
    skipped = {row['node']: row['reason'] for row in report['skipped']}
    ordinary = set(candidates(spec))
    rows = []
    for node_id, node in spec['nodes'].items():
        joint = node['joint']
        layouts = joint['socket_states']
        states = [state for state in joint['states'] if state['status'] == 'applied']
        wrapper = wrappers.get(node_id)
        dynamic = wrapper['dynamic'] if wrapper else None
        observed = set()
        exposed = set()
        collections = set()
        multi_inputs = set()
        supported_states = len(wrapper['configurations']) if wrapper else 0
        if wrapper:
            exposed.update(sock['physical'] for cfg in wrapper['configurations']
                           for side in ('inputs', 'outputs') for sock in cfg[side])
            # Ordinary generated parameters carry one value even if Blender's
            # native socket accepts multiple links. Use actual exposed inputs.
            params = {inp['socket']: inp['param'] for inp in wrapper['named_inputs']}
            for cfg in wrapper['configurations']:
                state = joint['states'][cfg['inspector_state']]
                layout = layouts[state['socket_state_id']]
                active_ids = {inp['socket'] for inp in cfg['inputs']} & params.keys()
                multi_inputs.update(params[sock['identifier']] for sock in layout['inputs']
                                    if sock.get('is_multi_input')
                                    and sock['identifier'] in active_ids)
        for state in states:
            observed.update(observed_sockets(layouts[state['socket_state_id']], dynamic=bool(dynamic)))
            for collection in state['collections']:
                name = collection['name']
                collections.add(name)
                for trial in collection['attempts']:
                    if trial['status'] != 'accepted':
                        continue
                    after = observed_sockets(layouts[trial['after']], dynamic=True)
                    observed.update(after)
                    # Capture Attribute's item types are admitted independently
                    # of its base layouts, which contain no capture items.
                    if (dynamic and name == dynamic['name'] and dynamic['kind'] == 'capture'
                            and trial['args'][0] in dynamic['types']):
                        before = observed_sockets(layouts[trial['before']], dynamic=True)
                        item_type = dynamic['types'][trial['args'][0]]
                        exposed.update(physical for physical in after - before
                                       if marker(physical) == item_type)
        # Use the actual contracts as well as the raw observation: sockets may
        # be retained as optional inputs even when their current layout is inactive.
        observed.update(exposed)
        unavailable = observed - exposed
        notes = []
        if not wrapper:
            if node_id not in ordinary:
                notes.append('Excluded zone or bake node')
            else:
                notes.append(skipped[node_id][0].upper() + skipped[node_id][1:])
            status = 'None'
        else:
            if wrapper['unsupported']:
                count = len(wrapper['unsupported'])
                notes.append(f"{count} unsupported base {'state' if count == 1 else 'states'}")
            menu_fields = [field['param'] for field in wrapper['fields'] if field['kind'] == 'menu']
            if menu_fields:
                notes.append('Menu options: ' + ', '.join(f'`{name}`' for name in menu_fields))
            if dynamic:
                if dynamic['kind'] == 'capture':
                    notes.append('Capture item types: ' + ', '.join(f'`{token}`' for token in dynamic['types']))
                else:
                    notes.append('Variable items via `elements`')
            if multi_inputs:
                notes.append('One value per multi-input socket: ' + ', '.join(f'`{name}`' for name in sorted(multi_inputs)))
            missing_collections = collections - ({dynamic['name']} if dynamic else set())
            if missing_collections:
                notes.append('Dynamic items not exposed: ' + ', '.join(f'`{name}`' for name in sorted(missing_collections)))
            incomplete = bool(joint['rna_truncated'] or joint['errors'])
            if incomplete:
                notes.append('Inspection incomplete')
            status = ('Partial' if supported_states != len(states) or unavailable
                      or missing_collections or multi_inputs or incomplete else 'Full')
        rows.append({
            'node': node_id, 'label': node['inventory'].get('label') or node_id,
            'function': wrapper['name'] if wrapper else None, 'status': status,
            'supported': supported_states, 'inspected': len(states),
            'exposed': sorted(exposed), 'unavailable': sorted(unavailable), 'notes': notes,
        })
    return sorted(rows, key=lambda row: (row['label'].casefold(), row['node']))


def socket_list(sockets):
    """Format exact Blender socket class names without their common prefix."""
    return ', '.join(f'`{name.removeprefix("NodeSocket")}`' for name in sorted(sockets)) or '—'


def render_coverage(spec, report, version):
    """Produce a deterministic Markdown reference with bounded coverage claims."""
    rows = coverage_rows(spec, report)
    counts = Counter(row['status'] for row in rows)
    exposed = {physical for row in rows for physical in row['exposed']}
    unavailable = {physical for row in rows for physical in row['unavailable'] if marker(physical) is None}
    runtime_types = ', '.join(f'`{typ}`' for typ in sorted({marker(physical) for physical in exposed}))
    output = [
        '# NodeForge Nodes', '',
        f'Coverage reference for **Nodes {version}** and **Blender {spec["blender_version"]}**.', '',
        'The table maps Blender node names and `bpy` class identifiers to functions in the bundled `nodes` package. '
        'NodeForge installs the bundled library automatically when the extension is enabled. '
        'Import it with `from packages import nodes`. '
        'See the [Package Guide](PACKAGES.md#importing-package-callables) for general import rules. '
        'For node behavior, refer to the [Blender Geometry Nodes manual](https://docs.blender.org/manual/en/latest/modeling/geometry_nodes/index.html).', '',
        'The library is generated from Blender’s Python API (`bpy`): the development Inspector probes node classes, '
        'configuration properties and socket layouts, and the generator produces bindings for supported configurations.', '',
        '## Coverage summary', '',
        '| Measure | Count |', '| --- | ---: |',
        f'| Inspected node classes | {len(rows)} |',
        f'| Generated functions | {report["summary"]["wrappers"]} |',
        f'| Full (within inspected scope) | {counts["Full"]} |',
        f'| Partial | {counts["Partial"]} |',
        f'| No generated function | {counts["None"]} |',
        f'| Of these: skipped by generator | {len(report["skipped"])} |',
        f'| Of these: excluded zone / bake nodes | {len(rows) - len(candidates(spec))} |',
        f'| Representable base configurations | {report["summary"]["states"]} |',
        f'| Applied Inspector base configurations | {sum(row["inspected"] for row in rows)} |', '',
        '## Socket types', '',
        f'Runtime types exposed by Nodes: {runtime_types}. Blender socket subtypes (for example, `FloatAngle` and `VectorEuler`) '
        'use the corresponding NodeForge runtime type.', '',
        f'Blender socket classes observed in the catalogue but not supported as runtime values by this library: {socket_list(unavailable)}. '
        'The core DSL also supports `Bundle` through its own [bundle operations](BUILTINS.md#bundles).', '',
        '`Menu` input options exposed by a wrapper are compile-time string arguments. Runtime Menu socket links and Menu-valued results are not supported. '
        'Dynamic node item collections are a separate mechanism from `Collection` data-block sockets.', '',
        'A fixed wrapper argument supplies one value to a native multi-input socket. '
        'Use the core [`join()`](BUILTINS.md#geometry) operation when joining several geometries.', '',
        '<!-- Generated by tools/nodegen/cli.py coverage. Do not edit this reference by hand. -->', '',
        '| Blender node | `bpy` class | Nodes function | Coverage | States | Exposed sockets | Not exposed | Notes |',
        '| --- | --- | --- | --- | ---: | --- | --- | --- |',
    ]
    for row in rows:
        function = f'`nodes.{row["function"]}()`' if row['function'] else '—'
        label = row['label'].replace('|', r'\|').replace('\n', ' ')
        notes = '; '.join(row['notes']).replace('|', r'\|') or '—'
        output.append(f'| {label} | `{row["node"]}` | {function} | {row["status"]} | '
                      f'{row["supported"]}/{row["inspected"]} | {socket_list(row["exposed"])} | '
                      f'{socket_list(row["unavailable"])} | {notes} |')
    return '\n'.join(output) + '\n'


def write_coverage(spec_path: Path, package: Path, output: Path, *, check=False):
    """Generate or check the reference without modifying any package sources."""
    if output.resolve().is_relative_to(package.resolve()) or output.suffix.lower() != '.md':
        raise ValueError('Coverage output must be a Markdown file outside the package directory')
    spec = read_spec(spec_path)
    report, _ = build(spec, package, check=True, archive=False)
    manifest = json.loads((package / 'nodeforge_package.json').read_text(encoding='utf-8'))
    content = render_coverage(spec, report, manifest['version']).encode('utf-8')
    if check:
        if not output.is_file() or output.read_bytes() != content:
            raise ValueError(f'Out-of-date Nodes coverage reference: {output}')
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(content)
    print(f'COVERAGE {output}')
