"""Candidates from checked scopes, declaration identities and callable signatures."""

from __future__ import annotations

from pathlib import Path
from compiler.analysis.facts.names import NameTarget
from compiler.analysis.queries.index import Declaration, DeclarationKind
from compiler.analysis.queries.navigation import Navigator
from compiler.analysis.symbol.symbol import AliasSymbol, Symbol, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.queries.view import AnalysisView
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan
from compiler.analysis.queries.completion_position import statement_tokens, tokens_before
from compiler.analysis.queries.completion_model import Completion, CompletionKind, IMPORTABLE_KINDS, KIND_BY_TYPE, KIND_OF_DECLARATION, PRIMITIVE_TYPE_NAMES, Signature

def lexical_items(
    tokens: tuple[Tok.Token, ...], prefix: str, editing: SrcSpan | None = None
) -> tuple[Completion, ...]:
    """Names that appear in the file, offered without any analysis behind them.

    This is the fallback for input the parser cannot handle yet: the labels are
    real names from the same file, but no kind or type is claimed for them, so a
    wrong guess cannot be shown as if it were resolved.
    """
    labels: list[str] = []
    seen: set[str] = set()
    for token in tokens:
        if not isinstance(token, Tok.Identifier):
            continue
        if editing is not None and token.span.start == editing.start:
            continue
        if token.name in seen or (prefix and not token.name.startswith(prefix)):
            continue
        seen.add(token.name)
        labels.append(token.name)
    return tuple(Completion(label=name, kind=CompletionKind.TEXT) for name in labels)


def value_items(
    navigator: Navigator, path: Path, row: int, col: int
) -> tuple[Completion, ...]:
    """Names visible at a value or expression position.

    Locals shadow the module's names, so they go in first and the first entry for
    a label wins (deduplicate labels and respect local shadowing).
    """
    items: dict[str, Completion] = {}
    bindings = navigator.view.visible_bindings_at(path, row, col)
    if bindings is not None:
        return tuple(__from_symbol(navigator.view, symbol) for symbol in bindings)
    for declaration in __visible_declarations_at(navigator, path, row, col):
        items.setdefault(declaration.name, __from_declaration(declaration))
    for symbol in navigator.view.symbols_in(path):
        items.setdefault(symbol.name, __from_symbol(navigator.view, symbol))
    for name in PRIMITIVE_TYPE_NAMES:
        items.setdefault(name, Completion(label=name, kind=CompletionKind.PRIMITIVE, detail="type"))
    return tuple(items.values())


def __visible_declarations_at(
    navigator: Navigator, path: Path, row: int, col: int
) -> tuple[Declaration, ...]:
    """Return source-local bindings visible at a cursor in declaration order."""
    visible: list[Declaration] = []
    container = navigator.enclosing_definition_name(path, row, col)
    for declaration in navigator.declarations_in(path):
        if declaration.span is None:
            continue
        if declaration.kind not in (DeclarationKind.VARIABLE, DeclarationKind.PARAMETER):
            continue
        if (declaration.span.start.row, declaration.span.start.col) > (row, col):
            continue
        # Locals belong to one definition: another function's parameter is not a
        # candidate here (block nesting is not modelled, so a local from a
        # sibling block inside the same definition still is).
        if declaration.container != container:
            continue
        visible.append(declaration)
    return tuple(visible)


def type_items(navigator: Navigator, path: Path, row: int, col: int) -> tuple[Completion, ...]:
    """Names that can appear where a type is expected."""
    items: dict[str, Completion] = {}
    bindings = navigator.view.visible_bindings_at(path, row, col)
    for symbol in bindings if bindings is not None else navigator.view.symbols_in(path):
        item = __from_symbol(navigator.view, symbol)
        if item.kind in (
            CompletionKind.STRUCT,
            CompletionKind.ENUM,
            CompletionKind.TRAIT,
            CompletionKind.ALIAS,
            CompletionKind.PRIMITIVE,
            CompletionKind.TYPE_PARAMETER,
        ):
            items.setdefault(symbol.name, item)
    for declaration in navigator.declarations_in(path):
        if declaration.kind in (
            DeclarationKind.STRUCT,
            DeclarationKind.ENUM,
            DeclarationKind.TRAIT,
            DeclarationKind.ALIAS,
        ):
            items.setdefault(declaration.name, __from_declaration(declaration))
    for name in PRIMITIVE_TYPE_NAMES:
        items.setdefault(name, Completion(label=name, kind=CompletionKind.PRIMITIVE, detail="type"))
    return tuple(items.values())


def import_path_items(navigator: Navigator) -> tuple[Completion, ...]:
    """Package and module names, for ``import`` and ``from …`` paths."""
    items: dict[str, Completion] = {}
    for package in navigator.view.package_names():
        items.setdefault(package, Completion(label=package, kind=CompletionKind.PACKAGE))
    for module in navigator.view.module_names():
        items.setdefault(module, Completion(label=module, kind=CompletionKind.MODULE))
    return tuple(items.values())


def import_name_items(
    navigator: Navigator, path: Path, row: int, col: int
) -> tuple[Completion, ...]:
    """Public names of the module named by the import statement being written.

    Only ``pub`` declarations are offered: an import cannot reach further, so the
    list is exactly what the compiler would accept (completion excludes inaccessible
    private symbols).
    """
    view = navigator.view
    tokens = view.tokens_of(path)
    statement = statement_tokens(tokens_before(tokens, row, col))
    dotted = __dotted_path(statement)
    if dotted is None:
        return ()
    target = view.module_file(dotted)
    if target is None:
        return ()
    items: list[Completion] = []
    for declaration in view.declarations_in(target):
        if not declaration.public or declaration.kind not in IMPORTABLE_KINDS:
            continue
        symbol = view.symbol_named_in(target, declaration.name)
        items.append(__from_declaration(declaration) if symbol is None else __from_symbol(view, symbol))
    return tuple(items)


def __dotted_path(statement: list[Tok.Token]) -> str | None:
    """The dotted module path written in an import statement."""
    names: list[str] = []
    for token in statement:
        if isinstance(token, Tok.Keyword):
            if token.kind == Tok.KeywordKind.From:
                continue
            if token.kind == Tok.KeywordKind.Import:
                break
        elif isinstance(token, Tok.Identifier):
            names.append(token.name)
    return ".".join(names) or None


def __from_declaration(declaration: Declaration) -> Completion:
    return Completion(
        label=declaration.name,
        kind=KIND_OF_DECLARATION.get(declaration.kind, CompletionKind.VARIABLE),
        detail=declaration.type_name,
    )


def __from_symbol(view: AnalysisView, symbol: Symbol | AliasSymbol) -> Completion:
    """A symbol's candidate entry, with its kind refined by its type."""
    if isinstance(symbol, AliasSymbol):
        return Completion(label=symbol.name, kind=CompletionKind.ALIAS, detail=view.alias_signature(symbol))
    resolved = view.type_of(symbol.type_id)
    if resolved is None:
        kind = CompletionKind.FUNCTION if symbol.kind is SymbolKind.Function else CompletionKind.VARIABLE
        declared = view.classification_type(symbol.type_id)
        if isinstance(declared, (Type.StructType, Type.EnumType, Type.TraitType)):
            kind = KIND_BY_TYPE[type(declared).__name__]
        return Completion(label=symbol.name, kind=kind)
    match resolved:
        case Type.GenericType() | Type.SelfType():
            return Completion(label=symbol.name, kind=CompletionKind.TYPE_PARAMETER)
        case Type.ConstGenericType():
            return Completion(label=symbol.name, kind=CompletionKind.CONSTANT, detail=view.type_name(resolved.value_type))
        case Type.StructType() | Type.EnumType() | Type.TraitType():
            return Completion(
                label=symbol.name,
                kind=KIND_BY_TYPE[type(resolved).__name__],
                detail=view.type_name(symbol.type_id),
            )
        case Type.FunctionType() | Type.MethodType():
            return from_callable(view, symbol.name, symbol.type_id, CompletionKind.FUNCTION)
        case _:
            kind = {
                SymbolKind.Function: CompletionKind.FUNCTION,
                SymbolKind.Type: CompletionKind.PRIMITIVE,
                SymbolKind.Constant: CompletionKind.CONSTANT,
            }.get(symbol.kind, CompletionKind.VARIABLE)
            return Completion(
                label=symbol.name, kind=kind, detail=view.type_name(symbol.type_id)
            )


def from_callable(
    view: AnalysisView, name: str, type_id: int, kind: CompletionKind
) -> Completion:
    """Render a reliable signature and parameter names for a callable snippet."""
    parameters = view.parameters_of(type_id)
    signature = __render_signature(view, name, type_id)
    return Completion(
        label=name,
        kind=kind,
        detail=signature,
        parameters=tuple(parameter for parameter, _ in parameters),
    )


def signature_of(view: AnalysisView, type_id: int | None) -> Signature | None:
    if type_id is None or view.type_of(type_id) is None:
        return None
    if not isinstance(view.type_of(type_id), (Type.FunctionType, Type.MethodType)):
        return None
    parameters = view.parameters_of(type_id)
    return Signature(
        label=__render_signature(view, view.callable_name(type_id), type_id),
        parameters=parameters,
    )


def __render_signature(view: AnalysisView, name: str, type_id: int) -> str:
    parameters = ", ".join(
        f"{parameter[0]}: {parameter[1]}" for parameter in view.parameters_of(type_id)
    )
    return f"fn {name}({parameters}) -> {view.return_type_name(type_id)}"


def callable_type_id(target: NameTarget | None, expression_type: int | None) -> int | None:
    """The callable's type id, from a reference's target or its type."""
    if isinstance(target, int):
        return target
    if isinstance(target, Symbol):
        return target.type_id
    return expression_type
