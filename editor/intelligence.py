"""Blender-independent symbol completion and signature help for incomplete DSL text.

Catalog selection comes from the same ResolvedEnvironment used by the compiler.
Only the cursor context and preliminary display of source input declarations are
editor-specific. Dynamic result completion uses pure semantic compilation of
complete fragments, never the physical backend.
"""

from __future__ import annotations

import ast
import inspect
import io
import keyword
import re
import tokenize
from dataclasses import dataclass, field, replace
from pathlib import Path

from ..semantic.builtin_registry import BUILTIN_NAMES
from ..semantic.constants import TYPE_TOKEN_NAMES
from ..result_keys import is_exact_attribute_key, validate_named_result_key
from ..errors import CompileError


@dataclass(frozen=True)
class Parameter:
    """One displayable parameter with an optional usable keyword spelling."""

    name: str
    typ: str = ""
    default: str | None = None
    keyword: str | None = None
    display_label: str | None = None

    def label(self) -> str:
        """Render the source parameter name and optional Blender display label."""
        name = self.name if not self.display_label else f'{self.name} [{self.display_label}]'
        return f"{name}{': ' + self.typ if self.typ else ''}{' = ' + self.default if self.default is not None else ''}"


@dataclass(frozen=True)
class Output:
    """One named return value with a projected or unknown type."""

    name: str
    typ: str
    key: str | None = None


@dataclass(frozen=True)
class Signature:
    """Editor projection of one public callable alternative."""

    name: str
    parameters: tuple[Parameter, ...] = ()
    outputs: tuple[Output, ...] = ()
    source_record: object | None = field(default=None, compare=False, repr=False)
    result_kind: str = "positional"


@dataclass(frozen=True)
class Suggestion:
    """Selectable completion with a source replacement span in the current line."""

    label: str
    insert: str
    kind: str
    detail: str = ""


@dataclass(frozen=True)
class Assistance:
    """Cursor-scoped suggestions and signature help for one text snapshot."""

    candidates: tuple[Suggestion, ...] = ()
    signatures: tuple[Signature, ...] = ()
    active_parameter: int = 0
    replace_start: int = 0
    replace_end: int = 0
    line: int = 0


# A small explicit core subset, not a second exhaustive representation of the
# compiler's built-in call contracts. All other builtins still complete by name.
_CORE_SIGNATURES = {
    "cube": ("size: Vector | Float = 1.0",),
    "points": ("count: Int",),
    "vector": ("x: Float", "y: Float", "z: Float"),
    "transform": ("geometry: Geometry", "translation: Vector", "rotation: Vector", "scale: Vector | Float"),
    "set_position": ("geometry: Geometry", "position: Vector", "selection: Bool"),
    "join": ("geometries",),
}


# Only stable, unambiguous core results are declared here. The authoritative
# runtime contract remains in the semantic frontend.
_CORE_OUTPUT_TYPES = {
    "cube": "Geometry", "points": "Geometry", "vector": "Vector",
    "transform": "Geometry", "set_position": "Geometry", "join": "Geometry",
}


def _core_signature(name: str) -> Signature:
    """Return a curated core signature or an intentionally unspecified one."""
    params = []
    for label in _CORE_SIGNATURES.get(name, ()):
        head, separator, default = label.partition(" = ")
        key, colon, typ = head.partition(": ")
        positional_only = (name == "points" or name == "join" or
                           (name == "set_position" and key != "selection") or
                           (name == "transform" and key == "geometry") or
                           name == "output")
        params.append(Parameter(key, typ if colon else "", default if separator else None,
                                None if positional_only else key))
    typ = _CORE_OUTPUT_TYPES.get(name)
    outputs = (Output("Value", typ),) if typ else ()
    return Signature(name, tuple(params), outputs)


def _socket_selector_name(expr: ast.expr) -> str | None:
    """Return a literal raw-node socket selector label, never evaluating DSL code."""
    if isinstance(expr, ast.Constant) and isinstance(expr.value, str) and expr.value:
        return expr.value
    if (isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name) and
            expr.func.id == 'ID' and len(expr.args) == 1 and not expr.keywords):
        return _socket_selector_name(expr.args[0])
    return None


def _node_output_declarations(call: ast.expr) -> dict[str, Output] | None:
    """Project literal ``node(outputs={...})`` aliases and their types."""
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == 'node'):
        return None
    mapping = next((kw.value for kw in call.keywords if kw.arg == 'outputs'), None)
    if not isinstance(mapping, ast.Dict):
        return None
    outputs = {}
    for key, value in zip(mapping.keys, mapping.values):
        if not (isinstance(key, ast.Constant) and isinstance(key.value, str) and key.value):
            continue
        alias = key.value
        label = alias.replace('_', ' ').title()
        typ = value
        if isinstance(value, ast.Tuple) and len(value.elts) == 2:
            selector, typ = value.elts
            literal = _socket_selector_name(selector)
            if literal is not None:
                label = literal
            elif not (isinstance(selector, ast.Constant) and type(selector.value) is int
                      and selector.value >= 0):
                continue
        if isinstance(typ, ast.Name) and typ.id in TYPE_TOKEN_NAMES:
            outputs[alias] = Output(label, typ.id)
    return outputs


def _direct_node_output(call: ast.expr) -> Output | None:
    """Read a single raw-node output selector and type from literal keywords."""
    if not (isinstance(call, ast.Call) and isinstance(call.func, ast.Name)
            and call.func.id == 'node'):
        return None
    keywords = {kw.arg: kw.value for kw in call.keywords if kw.arg is not None}
    typ = keywords.get('typ')
    selector = keywords.get('output')
    if not (isinstance(typ, ast.Name) and typ.id in TYPE_TOKEN_NAMES and selector is not None):
        return None
    return Output(_socket_selector_name(selector) or 'Value', typ.id)


def _source_return_values(fn: ast.FunctionDef, parameters: list[Parameter],
                          returned: ast.expr) -> tuple[Output, ...]:
    """Project the final straight-line return from known arguments and node sockets.

    Bindings are invalidated on assignment and ambiguous control flow. They are
    display metadata, not compiler type inference, and cannot execute source.
    """
    # A binding also tracks whether its label came from a raw socket selector.
    values: dict[str, tuple[Output, bool]] = {
        param.name: (Output(param.name.replace('_', ' ').title(), param.typ), False)
        for param in parameters if param.typ in TYPE_TOKEN_NAMES
    }
    raw_results: dict[str, dict[str, Output]] = {}

    def resolve(expr: ast.expr) -> tuple[Output, bool] | None:
        """Resolve a known value or selected raw socket in the current block."""
        if isinstance(expr, ast.Name):
            return values.get(expr.id)
        if isinstance(expr, ast.Attribute) and isinstance(expr.value, ast.Name):
            output = raw_results.get(expr.value.id, {}).get(expr.attr)
            return (output, True) if output is not None else None
        output = _direct_node_output(expr)
        return (output, True) if output is not None else None

    def invalidate_named_targets(tree: ast.AST) -> None:
        """Discard old metadata for names rebound by unsupported ``:=`` expressions."""
        for item in ast.walk(tree):
            if isinstance(item, ast.NamedExpr) and isinstance(item.target, ast.Name):
                raw_results.pop(item.target.id, None)
                values.pop(item.target.id, None)

    for statement in fn.body[:-1]:
        # Assignment expressions can rebind a name within another statement's RHS.
        invalidate_named_targets(statement)
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            target = statement.targets[0].id
            declaration = _node_output_declarations(statement.value)
            source_results = (raw_results.get(statement.value.id) if isinstance(statement.value, ast.Name)
                              else None)
            bound = resolve(statement.value)
            raw_results.pop(target, None)
            values.pop(target, None)
            if declaration is not None:
                raw_results[target] = declaration
            elif source_results is not None:
                raw_results[target] = source_results.copy()
            elif bound is not None:
                output, from_socket = bound
                name = output.name if from_socket else target.replace('_', ' ').title()
                values[target] = (Output(name, output.typ), from_socket)
        elif isinstance(statement, (ast.Assign, ast.AnnAssign, ast.AugAssign, ast.Delete)):
            targets = (statement.targets if isinstance(statement, (ast.Assign, ast.Delete))
                       else (statement.target,))
            for target in targets:
                for item in ast.walk(target):
                    if isinstance(item, ast.Name):
                        raw_results.pop(item.id, None)
                        values.pop(item.id, None)
        elif not isinstance(statement, (ast.Expr, ast.Pass)):
            # A branch, loop, or other unsupported statement can rebind any local.
            raw_results.clear()
            values.clear()

    # A return tuple can rebind an earlier result before a later element reads it.
    invalidate_named_targets(returned)
    if isinstance(returned, ast.Dict):
        outputs = []
        for key, expr in zip(returned.keys, returned.values):
            if not isinstance(key, ast.Constant):
                return ()
            try:
                validate_named_result_key(key.value)
            except ValueError:
                return ()
            bound = resolve(expr)
            typ = bound[0].typ if bound else "?"
            if typ == "?" and isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
                typ = _CORE_OUTPUT_TYPES.get(expr.func.id, "?")
            outputs.append(Output(key.value, typ, key.value))
        return tuple(outputs) if len({o.key for o in outputs}) == len(outputs) else ()
    elements = returned.elts if isinstance(returned, ast.Tuple) else [returned]
    outputs = []
    duplicates: dict[str, int] = {}
    for index, expr in enumerate(elements):
        bound = resolve(expr)
        name = (bound[0].name if bound is not None else
                expr.id.replace('_', ' ').title() if isinstance(expr, ast.Name) else
                f'Value {index + 1}' if isinstance(returned, ast.Tuple) else 'Value')
        typ = bound[0].typ if bound is not None else '?'
        if typ == '?' and isinstance(expr, ast.Call) and isinstance(expr.func, ast.Name):
            typ = _CORE_OUTPUT_TYPES.get(expr.func.id, '?')
        duplicates[name] = duplicates.get(name, 0) + 1
        outputs.append(Output(name if duplicates[name] == 1 else f'{name} {duplicates[name]}', typ))
    return tuple(outputs)


def _definition_signature(fn: ast.FunctionDef, display_name: str) -> Signature:
    """Project one source function's exact keyword and statically known result contract."""
    arguments = fn.args.args
    offset = len(arguments) - len(fn.args.defaults)
    parameters = []
    for index, arg in enumerate(arguments):
        typ = ast.unparse(arg.annotation) if arg.annotation is not None else ""
        label = None
        if (isinstance(arg.annotation, ast.Subscript)
                and isinstance(arg.annotation.value, ast.Name)
                and isinstance(arg.annotation.slice, ast.Constant)
                and isinstance(arg.annotation.slice.value, str)):
            typ = arg.annotation.value.id
            label = arg.annotation.slice.value
        default = ast.unparse(fn.args.defaults[index - offset]) if index >= offset else None
        parameters.append(Parameter(arg.arg, typ, default, arg.arg, label))
    outputs = ()
    result_kind = "positional"
    if fn.body and isinstance(fn.body[-1], ast.Return) and fn.body[-1].value is not None:
        returned = fn.body[-1].value
        outputs = _source_return_values(fn, parameters, returned)
        if isinstance(returned, ast.Dict):
            result_kind = "named"
        elif not isinstance(returned, ast.Tuple):
            label = None
            if (isinstance(fn.returns, ast.Subscript) and
                    isinstance(fn.returns.value, ast.Name) and
                    isinstance(fn.returns.slice, ast.Constant) and
                    isinstance(fn.returns.slice.value, str) and
                    fn.returns.value.id in TYPE_TOKEN_NAMES):
                label = fn.returns.slice.value
                annotation = fn.returns.value.id
            else:
                annotation = ast.unparse(fn.returns) if fn.returns is not None else None
            inferred = outputs[0]
            # Ordinary single-return labels remain "Value" unless a raw socket
            # or an explicit Type['Label'] gives a more meaningful source label.
            raw_socket = (isinstance(returned, ast.Call) and _direct_node_output(returned) is not None) or (
                isinstance(returned, ast.Attribute) and inferred.typ != '?')
            outputs = (Output(label or (inferred.name if raw_socket else 'Value'),
                              annotation or inferred.typ),)
    return Signature(display_name, tuple(parameters), outputs, result_kind=result_kind)


def _source_definitions(source: str) -> list[ast.FunctionDef]:
    """Retain complete definitions and usable headers in an incomplete editor buffer."""
    try:
        definitions = [statement for statement in ast.parse(source).body
                       if isinstance(statement, ast.FunctionDef)]
    except SyntaxError as exc:
        # Incomplete typing at the caret must not erase fully parsed definitions
        # above the error. Retain the old header fallback for incomplete bodies.
        lines = source.split('\n')
        end = min(len(lines) - 1, max(0, (exc.lineno or 1) - 1))
        while True:
            try:
                prefix = '\n'.join(lines[:end])
                definitions = [statement for statement in ast.parse(prefix).body
                               if isinstance(statement, ast.FunctionDef)]
                break
            except SyntaxError as prefix_error:
                # A truncated body can leave its own incomplete header.
                end = min(end - 1, max(0, (prefix_error.lineno or 1) - 1))
        for line in source.splitlines():
            if not line.startswith('def ') or not line.rstrip().endswith(':'):
                continue
            try:
                definition = ast.parse(line + '\n    pass').body[0]
            except SyntaxError:
                continue
            if isinstance(definition, ast.FunctionDef) and not any(fn.name == definition.name for fn in definitions):
                definitions.append(definition)
    return definitions


def _def_signatures(source: str) -> dict[str, tuple[Signature, ...]]:
    """Index declared signatures, even when the current expression is incomplete."""
    return {fn.name: (_definition_signature(fn, fn.name),) for fn in _source_definitions(source)}


def _source_imports(source: str):
    """Recover complete module imports independently of incomplete expressions."""
    for line in source.splitlines():
        if not line.startswith('from '):
            continue
        try:
            statement = ast.parse(line).body[0]
        except SyntaxError:
            continue
        if isinstance(statement, ast.ImportFrom):
            yield statement


def _typespec_label(spec) -> str:
    """Convert an extension's frozen TypeSpec to a concise display label."""
    if spec.kind == "DYNAMIC_RESULTS":
        return "dynamic results"
    if spec.kind == "NF_SET":
        return " | ".join(sorted(t.value.title() for t in spec.nf_types))
    if spec.kind == "TUPLE_FIXED":
        return "tuple[" + ", ".join(_typespec_label(item) for item in spec.items) + "]"
    if spec.kind == "RECORD":
        return spec.record_type.name
    if spec.kind == "PY_SCALAR":
        return spec.python_scalar_kind.value.title()
    return spec.kind.title()


def _extension_signatures(name: str, specs) -> tuple[Signature, ...]:
    """Project all overloads of an Extension API callable without importing backends."""
    result = []
    for spec in specs:
        parameters = []
        for param in spec.parameters:
            default = None if param.default is inspect.Parameter.empty else repr(param.default)
            kw = None if param.kind is inspect.Parameter.POSITIONAL_ONLY else param.name
            parameters.append(Parameter(param.name, _typespec_label(param.type_spec), default, kw))
        result_kind = "positional"
        if spec.result.kind == "DYNAMIC_RESULTS":
            result_kind = "dynamic"
            outputs = ()
        elif spec.result.kind == "NAMED_RESULTS":
            result_kind = "named"
            outputs = tuple(Output(key, typ.value.title(), key) for key, typ in spec.result.named_items)
        elif spec.result.kind == "TUPLE_FIXED":
            outputs = tuple(Output(f"Value {i + 1}", _typespec_label(item))
                            for i, item in enumerate(spec.result.items))
        else:
            outputs = (Output("Value", _typespec_label(spec.result)),)
        result.append(Signature(name, tuple(parameters), outputs, result_kind=result_kind))
    return tuple(result)


def _return_preview(signature: Signature) -> str:
    """Show short verified result types beside member-completion names."""
    if signature.result_kind == "dynamic":
        return "→ dynamic result (unresolved)"
    if signature.result_kind == "named":
        return "→ {" + ", ".join(f"{value.key}: {value.typ}" for value in signature.outputs) + "}"
    types = [value.typ for value in signature.outputs]
    if not types or "?" in types:
        return ""
    if len(types) == 1:
        return "→ " + types[0]
    return "→ (" + ", ".join(types) + ")"


class SymbolIndex:
    """Expose names and signatures selected by a compiler environment snapshot."""

    def __init__(self, environment):
        """Retain one immutable environment; resolve imported names on demand."""
        self.environment = environment
        self._source_cache: dict[tuple[Path, str], tuple[int, Signature]] = {}
        self._typed_cache: dict[Path, tuple[int, tuple[Output, ...] | None]] = {}

    def _record_signatures(self, record, name: str, *, definition: str | None = None,
                           snapshot=None) -> tuple[Signature, ...]:
        """Expose a selected `.nf` module definition, never a callable file."""
        path = getattr(record, 'source_path', None)
        if path is None or path.suffix != '.nf':
            return ()
        definition = definition or name
        try:
            if snapshot is not None:
                fn = snapshot.definitions.get(definition)
                if fn is None:
                    return ()
                signature = _definition_signature(fn, name)
            else:
                stamp = path.stat().st_mtime_ns
                key = (path, definition)
                cached = self._source_cache.get(key)
                if cached is not None and cached[0] == stamp:
                    signature = cached[1]
                else:
                    fn_sig = _def_signatures(path.read_text(encoding='utf-8')).get(definition)
                    if fn_sig is None:
                        return ()
                    signature = fn_sig[0]
                    self._source_cache[key] = (stamp, signature)
                signature = replace(signature, name=name)
        except OSError:
            return ()
        return (signature,)

    def _module_members(self, record) -> dict[str, tuple[Signature, ...]]:
        """Expose only real definitions in the explicitly selected `.nf` file."""
        path = getattr(record, 'source_path', None)
        if path is None or path.suffix != '.nf':
            return {}
        try:
            names = _def_signatures(path.read_text(encoding='utf-8'))
        except OSError:
            return {}
        return {name: self._record_signatures(record, name, definition=name) for name in names}

    def _typed_signature(self, signature: Signature) -> Signature:
        """Retain declared parameter types and source-level signature details."""
        return signature

    def _export_signatures(self, export, name: str) -> tuple[Signature, ...]:
        """Read source-backed or Extension API signatures of a selected export."""
        if export.extension_callable_id is not None:
            specs = self.environment.extension_registry.callable_specs(export.extension_callable_id)
            return _extension_signatures(name, specs)
        if export.record is not None:
            return self._record_signatures(export.record, name,
                                           definition=export.definition.name if export.definition else name,
                                           snapshot=export.source_snapshot)
        return ()

    def _package_members(self, namespace, alias, namespaces):
        """Project a frozen canonical package tree into editor qualifier tables.

        Every namespace prefix is offered as a navigable member. Unique short
        aliases remain available on the import alias, while ambiguous leaves
        only appear beneath their exact canonical owner paths.
        """
        root = {
            name: self._export_signatures(export, name)
            for name, export in namespace.exports.items()
        }
        namespaces[alias] = root
        for prefix in sorted(namespace.namespace_prefixes):
            parent, _, leaf = prefix.rpartition('.')
            parent_key = f'{alias}.{parent}' if parent else alias
            namespaces.setdefault(parent_key, {}).setdefault(leaf, ())
            namespaces.setdefault(f'{alias}.{prefix}', {})
        for path, export in sorted(namespace.qualified_exports.items()):
            parent, _, leaf = path.rpartition('.')
            key = f'{alias}.{parent}' if parent else alias
            namespaces.setdefault(key, {})[leaf] = self._export_signatures(export, leaf)

    def _directory_members(self, index, directory, alias, namespaces):
        """Expose module and directory members without treating files as callables."""
        from ..local_index import LocalDirectoryCandidate

        members = {}
        for name in index.path_members.get(directory.import_path, {}):
            try:
                selected = index.resolve_member(directory, name)
            except Exception:
                continue
            if isinstance(selected, LocalDirectoryCandidate):
                members[name] = ()
                self._directory_members(index, selected, f'{alias}.{name}', namespaces)
            elif selected is not None:
                members[name] = ()
                namespaces[f'{alias}.{name}'] = self._module_members(selected)
        namespaces[alias] = members

    def symbols(self, source: str) -> dict[str, dict[str, tuple[Signature, ...]]]:
        """Bind imported packages and Local functions with the compiler's catalog rules.

        The result maps the empty qualifier to top-level names and namespace
        aliases to their member tables. Invalid/ambiguous names are omitted.
        """
        env = self.environment
        top = {name: (_core_signature(name),) for name in BUILTIN_NAMES
               if not name.startswith('input_') and name != 'output'}
        top.update(_def_signatures(source))
        namespaces: dict[str, dict[str, tuple[Signature, ...]]] = {"": top}
        local_index = env.catalog("local").local_index
        # One-line imports are valid even if the expression at the caret isn't.
        for statement in _source_imports(source):
            module = statement.module or ""
            if module == "packages":
                for item in statement.names:
                    namespace = env.package_by_import_name(item.name)
                    if namespace is None or namespace.failure is not None:
                        continue
                    self._package_members(namespace, item.asname or item.name, namespaces)
            elif module == "local" or module.startswith("local."):
                if local_index is None:
                    continue
                from ..local_index import LocalDirectoryCandidate
                path = tuple(module.split('.')[1:])
                try:
                    parent = local_index.resolve_path(path) if path else None
                except Exception:
                    continue
                if parent is not None and not isinstance(parent, LocalDirectoryCandidate):
                    members = self._module_members(parent)
                    for item in statement.names:
                        signature = members.get(item.name, ())
                        if signature:
                            top[item.asname or item.name] = tuple(replace(sig, name=item.asname or item.name)
                                                                   for sig in signature)
                    continue
                for item in statement.names:
                    if item.name == '*':
                        continue
                    try:
                        selected = (local_index.resolve_member(parent, item.name) if parent is not None
                                    else local_index.resolve_root_import(item.name))
                    except Exception:
                        continue
                    if selected is None:
                        continue
                    alias = item.asname or item.name
                    if isinstance(selected, LocalDirectoryCandidate):
                        self._directory_members(local_index, selected, alias, namespaces)
                    else:
                        namespaces[alias] = self._module_members(selected)
            elif module == "examples":
                for item in statement.names:
                    try:
                        record = env.catalog("examples").find(item.name)
                    except Exception:
                        continue
                    if record is not None:
                        namespaces[item.asname or item.name] = self._module_members(record)
        return namespaces


def _call_result_signature(call, names, shadowed, bare_signatures):
    """Select only an unambiguous declared result schema for a simple callable path."""
    if not isinstance(call, ast.Call):
        return None
    path = ast.unparse(call.func).split('.')
    if not all(part.isidentifier() for part in path) or path[0] in shadowed:
        return None
    signatures = (bare_signatures(path[0]) if len(path) == 1 else
                  names.get('.'.join(path[:-1]), {}).get(path[-1], ()))
    if not signatures:
        return None
    first = signatures[0]
    if first.result_kind == 'dynamic' and all(s.result_kind == 'dynamic' for s in signatures):
        return first
    if first.result_kind == 'named' and all(s.result_kind == 'named' and s.outputs == first.outputs for s in signatures):
        return first
    return None


def _receiver_context(prefix, frames):
    """Locate a completed receiver in a straight-line module or function body."""
    match = re.search(r'\.([^\W\d]\w*)?$', prefix)
    if not match:
        return None
    partial = match.group(1) or ''
    marker = 'nodeforge_editor_result_probe'
    while marker in prefix:
        marker += '_'
    closing = ''.join({'(': ')', '[': ']', '{': '}'}[frame['open']] for frame in reversed(frames))
    try:
        tree = ast.parse(prefix[:match.start() + 1] + marker + closing)
    except SyntaxError:
        return None
    receiver = next((node.value for node in ast.walk(tree)
                     if isinstance(node, ast.Attribute) and node.attr == marker), None)
    if receiver is None:
        return None
    def contains(statement):
        """Check whether a statement owns the receiver occurrence."""
        return any(node is receiver for node in ast.walk(statement))
    previous = []
    for statement in tree.body:
        if not contains(statement):
            previous.append(statement)
            continue
        if isinstance(statement, ast.FunctionDef):
            prior = []
            for child in statement.body:
                if contains(child):
                    if not isinstance(child, (ast.Assign, ast.AnnAssign, ast.Expr, ast.Return)):
                        return None
                    return receiver, partial, previous, statement, prior
                prior.append(child)
            return None
        if isinstance(statement, (ast.Assign, ast.AnnAssign, ast.Expr)):
            return receiver, partial, previous, None, previous
        return None
    return None


def _bare_signature_lookup(source, names, index):
    """Select bare signatures through the compiler's declaration-only name resolution."""
    imports = tuple(_source_imports(source))
    if not any(statement.module == 'packages' for statement in imports):
        return lambda name: names[''].get(name, ())

    from ..compiler_identities import ModuleId
    from ..semantic.call_resolution import CallableKind, UNRESOLVED, resolve_simple_callable
    from ..semantic.group import resolve_module_callable_environment
    from ..semantic.source_callable_session import SourceCallableSession
    from ..semantic.source_modules import parse_source_module

    try:
        # Name resolution needs only declarations. Do not compile incomplete
        # bodies or residualize arbitrary module statements to display a schema.
        definitions = [ast.FunctionDef(name=fn.name, args=fn.args,
                       body=[ast.Return(value=ast.Constant(value=0))], decorator_list=[],
                       returns=None, type_comment=None, type_params=[])
                       for fn in _source_definitions(source)]
        module = ast.Module(body=[*imports, *definitions], type_ignores=[])
        snapshot = parse_source_module(ast.unparse(ast.fix_missing_locations(module)),
                                       ModuleId('ROOT_TEXT', 'editor'))
        session = SourceCallableSession(resolved_environment=index.environment)
        environment, _ = resolve_module_callable_environment(snapshot, index.environment, session)
    except (CompileError, SyntaxError, ValueError, TypeError):
        return lambda name: ()

    def signatures(name):
        """Project only the callable selected by the compiler's namespace rules."""
        if name in environment.package_namespaces or name in environment.local_namespaces:
            return ()
        try:
            resolved = resolve_simple_callable(name, environment)
        except CompileError:
            return ()
        if resolved is UNRESOLVED:
            return ()
        if resolved.kind is CallableKind.EXTENSION:
            specs = index.environment.extension_registry.callable_specs(resolved.target)
            return _extension_signatures(name, specs)
        if resolved.kind is CallableKind.MODULE_DEFINITION:
            binding = resolved.target
            return index._record_signatures(binding.record, name, definition=binding.definition.name,
                                            snapshot=binding.snapshot)
        if resolved.kind is CallableKind.LIBRARY:
            binding = resolved.target
            return index._record_signatures(binding.record, name, definition=binding.canonical_name)
        return names[''].get(name, ())

    return signatures


def _straight_line_result(context, names, bare_signatures):
    """Track only definite named bindings, invalidating ambiguous assignments."""
    receiver, _, _, function, prior = context
    bindings = {}
    shadowed = set()
    if function is not None:
        from ..semantic.source_bindings import collect_function_lexical_inventory
        shadowed.update(collect_function_lexical_inventory(function).owned_names)
    def schema(expr):
        """Look up a definite local binding or a declared call schema."""
        if isinstance(expr, ast.Name):
            return bindings.get(expr.id)
        return _call_result_signature(expr, names, shadowed, bare_signatures)
    for statement in prior:
        if isinstance(statement, ast.Assign) and len(statement.targets) == 1 and isinstance(statement.targets[0], ast.Name):
            target = statement.targets[0].id
            selected = schema(statement.value)
            bindings.pop(target, None)
            shadowed.add(target)
            if selected is not None:
                bindings[target] = selected
        elif isinstance(statement, (ast.ImportFrom, ast.FunctionDef, ast.Pass)):
            continue
        else:
            # Do not infer a receiver after a branch, mutation, or unknown rebinding.
            bindings.clear()
            shadowed.update(node.id for node in ast.walk(statement) if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)))
        if any(isinstance(node, ast.NamedExpr) for node in ast.walk(statement)):
            bindings.clear()
            shadowed.update(node.target.id for node in ast.walk(statement) if isinstance(node, ast.NamedExpr) and isinstance(node.target, ast.Name))
    return schema(receiver)


def _resolved_dynamic_outputs(context, index):
    """Use the existing pure compiler on a complete, straight-line receiver fragment."""
    from ..compiler_identities import GroupCompilationIdentity, ModuleId, module_definition_function_id
    from ..semantic.group import analyze_group_source
    from ..semantic.ir import IRAssign, IRBindLeaves, IRNamedOutputs, IRValue
    from ..semantic.source_callable_session import SourceCallableSession
    from ..semantic.source_modules import parse_source_module
    receiver, _, module_prior, function, prior = context
    allowed = (ast.ImportFrom, ast.FunctionDef, ast.Assign, ast.Pass)
    if any(not isinstance(statement, allowed) for statement in module_prior):
        return ()
    if any(not isinstance(statement, (ast.Assign, ast.Pass)) for statement in prior) and function is not None:
        return ()
    # No loops, branches, mutation, or incomplete expressions are executed by the editor.
    probe = 'nodeforge_editor_probe'
    used = {node.id for statement in [*module_prior, *prior, receiver] for node in ast.walk(statement) if isinstance(node, ast.Name)}
    while probe in used:
        probe += '_'
    assignment = ast.Assign(targets=[ast.Name(id=probe, ctx=ast.Store())], value=receiver)
    session = SourceCallableSession(resolved_environment=index.environment)
    identity = GroupCompilationIdentity(None, 'editor', 'editor', 'editor')
    try:
        if function is None:
            module = ast.Module(body=[*module_prior, assignment], type_ignores=[])
            source = ast.unparse(ast.fix_missing_locations(module))
            prepared = analyze_group_source(source, compilation_identity=identity,
                                            resolved_environment=index.environment, source_callable_session=session)
            statements = prepared.body.statements
        else:
            # Prepare the containing typed function with only its complete preceding body.
            # The probe is the final structural assignment, followed by a scalar return.
            probe_fn = ast.FunctionDef(name=function.name, args=function.args,
                                      body=[*prior, assignment, ast.Return(value=ast.Constant(value=0))],
                                      decorator_list=[], returns=None, type_comment=None, type_params=[])
            module = ast.Module(body=[*module_prior, probe_fn], type_ignores=[])
            source = ast.unparse(ast.fix_missing_locations(module))
            snapshot = parse_source_module(source, ModuleId('ROOT_TEXT', 'editor'))
            environment, constants = session.callable_environment_for_module(snapshot)
            function_id = module_definition_function_id(snapshot.definition_ref(function.name), '[]')
            prepared = session.prepare_definition(function_id=function_id, identity=identity,
                fn=snapshot.definitions[function.name], callable_environment=environment,
                compile_time_values=constants, helper_namespace='editor', source_module=snapshot)
            statements = prepared.group.body.statements[:-1]
        if not statements or not isinstance(statements[-1], (IRAssign, IRBindLeaves)):
            return ()
        result = statements[-1].value.result
        if isinstance(result, IRValue):
            return (Output("Value", result.typ.value.title()),)
        if isinstance(result, IRNamedOutputs):
            return tuple(Output(key, value.typ.value.title(), key) for key, value in result.items)
    except (CompileError, SyntaxError, ValueError, TypeError):
        return ()
    return ()


def _tokens_before(source: str, offset: int):
    """Yield tokens from the incomplete source prefix, ignoring tokenizer EOF errors."""
    text = source[:offset]
    try:
        yield from tokenize.generate_tokens(io.StringIO(text).readline)
    except (tokenize.TokenError, IndentationError):
        return


def cursor_assistance(source: str, line: int, column: int, index: SymbolIndex) -> Assistance:
    """Return member/name/keyword completions and signature help at the caret.

    Token depth, rather than comma counting in raw text, identifies the current
    argument even with nested calls, lists and strings. Parsing incomplete
    Python is intentionally not required.
    """
    lines = source.split("\n")
    if not 0 <= line < len(lines):
        return Assistance()
    column = min(column, len(lines[line]))
    starts = [0]
    for item in lines:
        starts.append(starts[-1] + len(item) + 1)
    offset = starts[line] + column
    prefix = source[:offset]
    tokens = list(_tokens_before(source, offset))
    for token in tokens:
        if token.start[0] - 1 > line:
            continue
        if token.type == tokenize.COMMENT and token.end[0] - 1 == line:
            return Assistance(line=line)
        if token.type == tokenize.ERRORTOKEN and token.string in {"'", '"'}:
            # An unmatched quote before the caret is an incomplete string.
            return Assistance(line=line)
        if token.type == tokenize.STRING:
            first = starts[token.start[0] - 1] + token.start[1]
            last = starts[token.end[0] - 1] + token.end[1]
            if first <= offset < last:
                return Assistance(line=line)
    # Inside an unfinished call tokenize may stop at '(' before emitting the
    # unterminated string. Check only the un-tokenized tail for an open quote.
    last_token_end = 0
    if tokens:
        last = tokens[-1]
        if 0 < last.end[0] <= len(lines):
            last_token_end = starts[last.end[0] - 1] + last.end[1]
    quote = None
    escaped = False
    for character in prefix[last_token_end:]:
        if quote:
            if escaped:
                escaped = False
            elif character == "\\":
                escaped = True
            elif character == quote:
                quote = None
        elif character in {"'", '"'}:
            quote = character
        elif character == "#":
            break
    if quote:
        return Assistance(line=line)

    # Import completion uses the same frozen Local index and package inventory
    # as compilation, without requiring the import line to be parseable yet.
    import_match = re.fullmatch(
        r"\s*from\s+(packages|local(?:\.[A-Za-z_]\w*)*|examples)\s+import\s*([A-Za-z_]\w*)?",
        prefix.split("\n")[-1],
    )
    if import_match:
        module, partial = import_match.groups()
        partial = partial or ""
        env = index.environment
        available = set()
        if module == "packages":
            available = {ns.import_name for ns in env.package_namespaces.values()}
        elif module == "examples":
            available = set(env.catalog("examples").names())
        else:
            local_index = env.catalog("local").local_index
            if local_index is not None:
                if module == "local":
                    available.update(local_index.flat_names())
                    available.update(local_index.path_members.get((), {}))
                    def selected(name):
                        """Resolve one Local root import without accepting collisions."""
                        return local_index.resolve_root_import(name)
                else:
                    directory = local_index.resolve_directory(tuple(module.split(".")[1:]))
                    available.update(local_index.path_members.get(directory.import_path, {}))
                    def selected(name):
                        """Resolve one qualified Local member without accepting collisions."""
                        return local_index.resolve_member(directory, name)
                unique = set()
                for name in available:
                    try:
                        if selected(name) is not None:
                            unique.add(name)
                    except Exception:
                        continue
                available = unique
        candidates = tuple(Suggestion(name, name, "import") for name in sorted(available) if name.startswith(partial) and name != partial)
        return Assistance(candidates, (), 0, column - len(partial), column, line)

    names = index.symbols(source)
    lookup = None

    def bare_signatures(name):
        """Share one declaration environment across bare calls in this cursor snapshot."""
        nonlocal lookup
        if lookup is None:
            lookup = _bare_signature_lookup(source, names, index)
        return lookup(name)

    frames: list[dict] = []
    for tok in tokens:
        if tok.type != tokenize.OP:
            continue
        position = starts[tok.start[0] - 1] + tok.start[1]
        if tok.string in "([{":
            before = source[:position]
            match = re.search(r"([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\s*$", before)
            name = match.group(1) if tok.string == "(" and match else None
            frames.append({"open": tok.string, "name": name, "position": 0,
                           "arg_start": position + 1, "used": set()})
        elif tok.string in ")]}":
            if frames:
                frames.pop()
        elif tok.string == "," and frames and frames[-1]["open"] == "(":
            frame = frames[-1]
            segment = source[frame["arg_start"]:position]
            previous = re.match(r"\s*([A-Za-z_]\w*)\s*=", segment)
            if previous:
                frame["used"].add(previous.group(1))
            frame["position"] += 1
            frame["arg_start"] = position + 1

    frame = next((item for item in reversed(frames) if item["name"]), None)
    signatures = ()
    active_parameter = 0
    if frame is not None:
        path = frame["name"].split(".")
        declared = (bare_signatures(path[0]) if len(path) == 1 else
                    names.get(".".join(path[:-1]), {}).get(path[-1], ()))
        signatures = tuple(index._typed_signature(signature) for signature in declared)
        active_parameter = frame["position"]
        segment = prefix[frame["arg_start"]:]
        named = re.match(r"\s*([A-Za-z_]\w*)\s*=", segment)
        used = set(frame["used"])
        if named:
            used.add(named.group(1))
            for signature in signatures:
                for i, parameter in enumerate(signature.parameters):
                    if parameter.keyword == named.group(1):
                        active_parameter = i
                        break
        if signatures and not named:
            partial_match = re.fullmatch(r"\s*([A-Za-z_]\w*)?", segment)
            if partial_match:
                partial = partial_match.group(1) or ""
                seen = {}
                for signature in signatures:
                    for parameter in signature.parameters:
                        key = parameter.keyword
                        if key and key not in used and key.startswith(partial):
                            seen[key] = Suggestion(key + "=", key + "=", "parameter", parameter.label())
                candidates = tuple(seen.values())
                return Assistance(candidates, signatures, active_parameter, column - len(partial), column, line)

    receiver_context = _receiver_context(prefix, frames)
    if receiver_context is not None:
        receiver, partial, module_prior, function, prior = receiver_context
        signature = _straight_line_result(receiver_context, names, bare_signatures)
        if signature is not None:
            outputs = signature.outputs
            if signature.result_kind == 'dynamic':
                outputs = _resolved_dynamic_outputs(receiver_context, index)
                if outputs:
                    signature = replace(signature, outputs=outputs,
                                        result_kind="named" if outputs[0].key is not None else "positional")
                if not signatures:
                    signatures = (signature,)
            candidates = tuple(Suggestion(output.key, output.key, 'field', output.typ)
                               for output in outputs if is_exact_attribute_key(output.key)
                               and output.key.startswith(partial) and output.key != partial)
            return Assistance(candidates, signatures, active_parameter, column - len(partial), column, line)

    suffix = prefix.split("\n")[-1]
    match = re.search(r"(?<![\w.])(?:([A-Za-z_]\w*(?:\.[A-Za-z_]\w*)*)\.)?([A-Za-z_]\w*)?$", suffix)
    if match:
        qualifier, partial = match.groups()
        partial = partial or ""
        table = names.get(qualifier or "", {})
        if qualifier is not None or len(partial) >= 2:
            candidates = tuple(
                Suggestion(name, name, "function", _return_preview(sigs[0]) if sigs else "")
                for name, sigs in sorted(table.items()) if name.startswith(partial) and name != partial
            )
            return Assistance(candidates, signatures, active_parameter, column - len(partial), column, line)
    return Assistance((), signatures, active_parameter, column, column, line)
