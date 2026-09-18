"""What can be written at a position (plan §7 P6).

Completion is answered from the same tables as navigation: the declaration index,
the recorded references, and the type space.  What a position *expects* is read
from the token stream (are we after a dot? inside an import? in a type position?),
a question the lexer can answer even for unfinished input — which is why a
half-typed expression still gets sensible candidates instead of an empty list
(plan §7 P6: 未完成表达式时仍返回合理的降级结果).

The compiler side decides *what* the candidates are; turning them into protocol
items (kinds, snippet syntax, ranges) is the LSP layer's job.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from compiler.analysis.index import Declaration, DeclarationKind
from compiler.analysis.navigation import Navigator
from compiler.analysis.session import AnalysisResult
from compiler.analysis.symbol.symbol import Symbol, SymbolKind
from compiler.analysis.ty import ty as Type
from compiler.analysis.view import AnalysisView
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcPosition, SrcSpan

__all__ = [
    "Completion",
    "CompletionKind",
    "CompletionResult",
    "Signature",
    "SignatureInfo",
    "complete",
    "signature_help",
]


class CompletionKind(Enum):
    """What an item is; the LSP layer maps these onto protocol kinds."""

    FUNCTION = "function"
    METHOD = "method"
    FIELD = "field"
    VARIANT = "variant"
    STRUCT = "struct"
    ENUM = "enum"
    TRAIT = "trait"
    ALIAS = "alias"
    VARIABLE = "variable"
    PARAMETER = "parameter"
    MODULE = "module"
    PACKAGE = "package"
    PRIMITIVE = "primitive"
    #: A name offered without the analysis behind it (see :func:`__lexical_items`).
    TEXT = "text"


@dataclass(frozen=True)
class Completion:
    """One candidate: what it is, and the facts an editor shows for it."""

    label: str
    kind: CompletionKind
    #: Rendered type or signature, shown next to the label.
    detail: str | None = None
    #: Parameter names of a callable, in order.  How an editor inserts them — a
    #: snippet with placeholders, a plain name, a signature preview — is its own
    #: decision; the compiler reports the names it knows.
    parameters: tuple[str, ...] = ()


@dataclass(frozen=True)
class CompletionResult:
    """The candidates, plus the text range they replace."""

    items: tuple[Completion, ...] = ()
    #: The identifier (or dotted path) being typed, which the client replaces.
    span: SrcSpan | None = None


@dataclass(frozen=True)
class Signature:
    """One callable's parameters, for signature help."""

    label: str
    parameters: tuple[tuple[str, str], ...]  # (name, rendered type)


@dataclass(frozen=True)
class SignatureInfo:
    """Signature help for one call site, with the argument being written."""

    signatures: tuple[Signature, ...]
    active_parameter: int
    active_signature: int = 0


#: Where the caret is, as far as completion cares.  The value context also
#: offers types, so a type position that the token scan does not recognise still
#: gets usable candidates.
__CONTEXT_VALUE = "value"
__CONTEXT_MEMBER = "member"
__CONTEXT_TYPE = "type"
__CONTEXT_IMPORT_PATH = "import_path"
__CONTEXT_IMPORT_NAME = "import_name"

#: Tokens that end a receiver expression: anything after them is a new one.
__EXPRESSION_BOUNDARIES = frozenset(
    {
        Tok.PunctuatorKind.Equal,
        Tok.PunctuatorKind.LParen,
        Tok.PunctuatorKind.Comma,
        Tok.PunctuatorKind.Semicolon,
        Tok.PunctuatorKind.LBrace,
        Tok.PunctuatorKind.RBrace,
        Tok.PunctuatorKind.Colon,
        Tok.PunctuatorKind.FatArrow,
        Tok.PunctuatorKind.Plus,
        Tok.PunctuatorKind.Minus,
        Tok.PunctuatorKind.Star,
        Tok.PunctuatorKind.Slash,
    }
)

#: Punctuators after which a type is expected.
__TYPE_INTRODUCERS = frozenset(
    {Tok.PunctuatorKind.Colon, Tok.PunctuatorKind.Arrow, Tok.PunctuatorKind.LAngle}
)

KIND_OF_DECLARATION = {
    DeclarationKind.FUNCTION: CompletionKind.FUNCTION,
    DeclarationKind.METHOD: CompletionKind.METHOD,
    DeclarationKind.FIELD: CompletionKind.FIELD,
    DeclarationKind.VARIANT: CompletionKind.VARIANT,
    DeclarationKind.STRUCT: CompletionKind.STRUCT,
    DeclarationKind.ENUM: CompletionKind.ENUM,
    DeclarationKind.TRAIT: CompletionKind.TRAIT,
    DeclarationKind.ALIAS: CompletionKind.ALIAS,
    DeclarationKind.VARIABLE: CompletionKind.VARIABLE,
    DeclarationKind.PARAMETER: CompletionKind.PARAMETER,
}

#: Kinds an import can bring in; a field or a variant is not one of them.
IMPORTABLE_KINDS = frozenset(
    {
        DeclarationKind.FUNCTION,
        DeclarationKind.STRUCT,
        DeclarationKind.ENUM,
        DeclarationKind.TRAIT,
        DeclarationKind.ALIAS,
    }
)

#: Primitive type names, which the language accepts but no declaration introduces.
PRIMITIVE_TYPE_NAMES = (
    "void",
    "bool",
    "char",
    "str",
    "i8",
    "i16",
    "i32",
    "i64",
    "u8",
    "u16",
    "u32",
    "u64",
    "f16",
    "f32",
    "f64",
    "int",
    "uint",
    "float",
)


def complete(
    result: AnalysisResult, path: Path, row: int, col: int, *, std_root: Path | None = None
) -> CompletionResult:
    """Candidates for the position ``(row, col)`` in *path*.

    *row* is 0-based and *col* is 1-based in code points (the compiler's model).
    """
    navigator = Navigator(result, std_root=std_root)
    text = navigator.text_of(path)
    tokens = navigator.tokens_of(path)
    prefix, prefix_span = __prefix_at(path, text, row, col)
    if not navigator.view.has_symbols(path):
        # The analysis never reached a symbol table — a syntax error stops the
        # pipeline before resolution.  The names in the file itself are still a
        # better answer than nothing while the user is mid-expression, as long as
        # nothing is claimed about what they mean (plan §7 P6: 降级结果).
        return CompletionResult(items=__lexical_items(tokens, prefix), span=prefix_span)
    context = __context_at(path, tokens, row, col)
    if context == __CONTEXT_MEMBER:
        items = __member_items(navigator, path, row, col)
    elif context == __CONTEXT_TYPE:
        items = __type_items(navigator, path)
    elif context == __CONTEXT_IMPORT_PATH:
        items = __import_path_items(navigator)
    elif context == __CONTEXT_IMPORT_NAME:
        items = __import_name_items(navigator, path, row, col)
    else:
        items = __value_items(navigator, path, row, col)
    if prefix:
        items = tuple(item for item in items if item.label.startswith(prefix))
    return CompletionResult(items=items, span=prefix_span)


def __lexical_items(
    tokens: tuple[Tok.Token, ...], prefix: str
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
        if token.name in seen or (prefix and not token.name.startswith(prefix)):
            continue
        seen.add(token.name)
        labels.append(token.name)
    return tuple(Completion(label=name, kind=CompletionKind.TEXT) for name in labels)


def signature_help(
    result: AnalysisResult, path: Path, row: int, col: int, *, std_root: Path | None = None
) -> SignatureInfo | None:
    """Signature help for the call the position is inside, if any."""
    navigator = Navigator(result, std_root=std_root)
    tokens = navigator.tokens_of(path)
    call = __call_at(path, tokens, row, col)
    if call is None:
        return None
    callee_span, active_parameter = call
    reference = navigator.reference_starting_at(
        path, callee_span.start.row, callee_span.start.col
    )
    if reference is None:
        return None
    type_id = __callable_type_id(reference.target, reference.expression_type)
    signature = __signature_of(navigator.view, type_id)
    if signature is None:
        return None
    return SignatureInfo(signatures=(signature,), active_parameter=active_parameter)


# ── the position ───────────────────────────────────────────────────────────────


def __prefix_at(
    path: Path, text: str, row: int, col: int
) -> tuple[str, SrcSpan | None]:
    """The identifier ending at the caret, and the span it replaces."""
    line = __line(text, row)
    index = min(max(col - 1, 0), len(line))
    start = index
    while start > 0 and __identifier_char(line[start - 1]):
        start -= 1
    prefix = line[start:index]
    if not prefix:
        return "", None
    return prefix, SrcSpan(__position(path, row, start + 1), __position(path, row, index + 1))


def __receiver_type(reference: Type.NameRef) -> tuple[int | None, bool]:
    """The receiver's type, and whether the access is static.

    A name recorded as a *type* (the resolver records written type names with the
    type id as their target) is being used statically: `Pair<Meters>.of` reaches
    the static methods, not the fields of a value.  Everything else is a value
    whose type the recorded reference already carries.
    """
    target = reference.target
    if isinstance(target, int):
        return target, True
    if isinstance(target, Symbol):
        if target.kind is SymbolKind.Type:
            return target.type_id, True
        return reference.expression_type, False
    return reference.expression_type, False


def __receiver_reference(
    navigator: Navigator, path: Path, before: list[Tok.Token], row: int
) -> Type.NameRef | None:
    """The name the member access is applied to.

    Scans back from the dot for the nearest identifier at bracket depth zero.
    That is ``Pair`` in ``Pair<Meters>.of`` (not the type argument inside the
    angle brackets) and ``b`` in ``a.b.`` — the receiver expression's outermost
    name, which is the one whose type the members come from.
    """
    depth = 0
    for token in reversed(before):
        if isinstance(token, Tok.Punctuator):
            if token.kind in (
                Tok.PunctuatorKind.RParen,
                Tok.PunctuatorKind.RBracket,
                Tok.PunctuatorKind.RAngle,
            ):
                depth += 1
                continue
            if token.kind in (
                Tok.PunctuatorKind.LParen,
                Tok.PunctuatorKind.LBracket,
                Tok.PunctuatorKind.LAngle,
            ):
                depth = max(0, depth - 1)
                continue
            if depth == 0 and token.kind in __EXPRESSION_BOUNDARIES:
                return None
            continue
        if isinstance(token, Tok.Identifier) and depth == 0:
            return navigator.reference_starting_at(path, row, token.span.start.col)
    return None


def __context_at(path: Path, tokens: tuple[Tok.Token, ...], row: int, col: int) -> str:
    """Classify the position from the tokens before it."""
    before = __tokens_before(path, tokens, row, col)
    if not before:
        return __CONTEXT_VALUE
    statement = __statement_tokens(before)
    import_index = __last_index(statement, Tok.KeywordKind.Import)
    from_index = __last_index(statement, Tok.KeywordKind.From)
    if from_index is not None and import_index is None:
        # Still writing the path: `from sample.|`
        return __CONTEXT_IMPORT_PATH
    if import_index is not None:
        if from_index is not None:
            # `from a.b import <here>` names things; `from a.<here>` a path.
            last_before = statement[-1].span.start.col
            if last_before < statement[import_index].span.start.col:
                return __CONTEXT_IMPORT_PATH
            return __CONTEXT_IMPORT_NAME
        return __CONTEXT_IMPORT_PATH
    last = before[-1]
    if isinstance(last, Tok.Punctuator):
        if last.kind == Tok.PunctuatorKind.Dot:
            return __CONTEXT_MEMBER
        if last.kind in __TYPE_INTRODUCERS:
            return __CONTEXT_TYPE
    return __CONTEXT_VALUE


def __tokens_before(
    path: Path, tokens: tuple[Tok.Token, ...], row: int, col: int
) -> list[Tok.Token]:
    return [
        token
        for token in tokens
        if token.span.path.resolve() == path.resolve()
        and (token.span.end.row, token.span.end.col) <= (row, col)
    ]


def __statement_tokens(before: list[Tok.Token]) -> list[Tok.Token]:
    """The tokens of the statement the caret is in (back to the last ``;``)."""
    for index in range(len(before) - 1, -1, -1):
        token = before[index]
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Semicolon:
            return before[index + 1 :]
    return before


def __last_index(tokens: list[Tok.Token], keyword: Tok.KeywordKind) -> int | None:
    for index in range(len(tokens) - 1, -1, -1):
        token = tokens[index]
        if isinstance(token, Tok.Keyword) and token.kind == keyword:
            return index
    return None


# ── candidate sources ─────────────────────────────────────────────────────────


def __value_items(
    navigator: Navigator, path: Path, row: int, col: int
) -> tuple[Completion, ...]:
    """Names visible at a value or expression position.

    Locals shadow the module's names, so they go in first and the first entry for
    a label wins (plan §7 P6: 去除同名重复项，处理局部符号遮蔽).
    """
    items: dict[str, Completion] = {}
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
        items.setdefault(declaration.name, __from_declaration(declaration))
    for symbol in navigator.view.symbols_in(path):
        items.setdefault(symbol.name, __from_symbol(navigator.view, symbol))
    for name in PRIMITIVE_TYPE_NAMES:
        items.setdefault(name, Completion(label=name, kind=CompletionKind.PRIMITIVE, detail="type"))
    return tuple(items.values())


def __type_items(navigator: Navigator, path: Path) -> tuple[Completion, ...]:
    """Names that can appear where a type is expected."""
    items: dict[str, Completion] = {}
    for symbol in navigator.view.symbols_in(path):
        item = __from_symbol(navigator.view, symbol)
        if item.kind in (
            CompletionKind.STRUCT,
            CompletionKind.ENUM,
            CompletionKind.TRAIT,
            CompletionKind.ALIAS,
            CompletionKind.PRIMITIVE,
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


def __member_items(
    navigator: Navigator, path: Path, row: int, col: int
) -> tuple[Completion, ...]:
    """Fields and methods of the expression to the left of the dot.

    The receiver's type comes from the recorded reference for that expression, so
    the list matches the *static* type the analysis computed rather than what the
    name looks like (plan §7 P6: 成员补全与接收者静态类型一致).
    """
    view = navigator.view
    if not view.has_types:
        return ()
    tokens = view.tokens_of(path)
    before = __tokens_before(path, tokens, row, col)
    if not before or not isinstance(before[-1], Tok.Punctuator):
        return ()
    dot = before[-1]
    if dot.kind != Tok.PunctuatorKind.Dot:
        return ()
    reference = __receiver_reference(navigator, path, before, dot.span.start.row)
    if reference is None:
        return ()
    type_id, static = __receiver_type(reference)
    if type_id is None:
        return ()
    resolved = view.canonical_type(type_id)
    if not static:
        # Field access auto-dereferences a pointer or reference; completion has
        # to follow the same path or `p.` on a `T*` would offer nothing.
        dereferenced = view.deref_type(resolved)
        if dereferenced is not None:
            resolved = view.canonical_type(dereferenced)
    items: dict[str, Completion] = {}
    resolved_ty = view.type_of(resolved)
    if isinstance(resolved_ty, Type.StructType) and not static:
        for field in view.fields_of(resolved):
            if field.access_mode is Type.AccessMode.Private and not __visible_here(field.span, path):
                continue
            items.setdefault(
                field.name,
                Completion(
                    label=field.name,
                    kind=CompletionKind.FIELD,
                    detail=view.type_name(field.type_id),
                ),
            )
    for name, method_id in view.methods_of(resolved):
        method_ty = view.type_of(method_id)
        if isinstance(method_ty, Type.MethodType) and method_ty.custom_def.is_static != static:
            continue
        items.setdefault(name, __from_callable(view, name, method_id, CompletionKind.METHOD))
    if static and isinstance(resolved_ty, Type.EnumType):
        for variant in view.variants_of(resolved):
            items.setdefault(
                variant.name,
                Completion(
                    label=variant.name,
                    kind=CompletionKind.VARIANT,
                    detail=None
                    if variant.payload_type is None
                    else view.type_name(variant.payload_type),
                ),
            )
    return tuple(items.values())


def __import_path_items(navigator: Navigator) -> tuple[Completion, ...]:
    """Package and module names, for ``import`` and ``from …`` paths."""
    items: dict[str, Completion] = {}
    for package in navigator.view.package_names():
        items.setdefault(package, Completion(label=package, kind=CompletionKind.PACKAGE))
    for module in navigator.view.module_names():
        items.setdefault(module, Completion(label=module, kind=CompletionKind.MODULE))
    return tuple(items.values())


def __import_name_items(
    navigator: Navigator, path: Path, row: int, col: int
) -> tuple[Completion, ...]:
    """Public names of the module named by the import statement being written.

    Only ``pub`` declarations are offered: an import cannot reach further, so the
    list is exactly what the compiler would accept (plan §7 P6: 补全不包含不可见
    的私有符号).
    """
    view = navigator.view
    tokens = view.tokens_of(path)
    statement = __statement_tokens(__tokens_before(path, tokens, row, col))
    dotted = __dotted_path(statement)
    if dotted is None:
        return ()
    target = view.module_file(dotted)
    if target is None:
        return ()
    return tuple(
        __from_declaration(declaration)
        for declaration in view.declarations_in(target)
        if declaration.public and declaration.kind in IMPORTABLE_KINDS
    )


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


# ── items from the analysis ───────────────────────────────────────────────────


def __from_declaration(declaration: Declaration) -> Completion:
    return Completion(
        label=declaration.name,
        kind=KIND_OF_DECLARATION.get(declaration.kind, CompletionKind.VARIABLE),
        detail=declaration.type_name,
    )


def __from_symbol(view: AnalysisView, symbol: Symbol) -> Completion:
    """A symbol's candidate entry, with its kind refined by its type."""
    resolved = view.type_of(symbol.type_id)
    if resolved is None:
        return Completion(label=symbol.name, kind=CompletionKind.VARIABLE)
    match resolved:
        case Type.StructType() | Type.EnumType() | Type.TraitType() | Type.AliasType():
            return Completion(
                label=symbol.name,
                kind=KIND_BY_TYPE[type(resolved).__name__],
                detail=view.type_name(symbol.type_id),
            )
        case Type.FunctionType() | Type.MethodType():
            return __from_callable(view, symbol.name, symbol.type_id, CompletionKind.FUNCTION)
        case _:
            kind = {
                SymbolKind.Function: CompletionKind.FUNCTION,
                SymbolKind.Type: CompletionKind.STRUCT,
            }.get(symbol.kind, CompletionKind.VARIABLE)
            return Completion(
                label=symbol.name, kind=kind, detail=view.type_name(symbol.type_id)
            )


KIND_BY_TYPE = {
    "StructType": CompletionKind.STRUCT,
    "EnumType": CompletionKind.ENUM,
    "TraitType": CompletionKind.TRAIT,
    "AliasType": CompletionKind.ALIAS,
}


def __from_callable(
    view: AnalysisView, name: str, type_id: int, kind: CompletionKind
) -> Completion:
    """A callable candidate: its signature, and a snippet with placeholders.

    Inserting ``name(${1:x})`` is what makes accepting a function item put the
    caret in its first parameter instead of leaving a bare name behind (plan §7
    P6: 补全项的插入文本与参数占位符).
    """
    parameters = view.parameters_of(type_id)
    signature = __render_signature(view, name, type_id)
    return Completion(
        label=name,
        kind=kind,
        detail=signature,
        parameters=tuple(parameter for parameter, _ in parameters),
    )


def __signature_of(view: AnalysisView, type_id: int | None) -> Signature | None:
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


def __callable_type_id(target: Type.NameTarget | None, expression_type: int | None) -> int | None:
    """The callable's type id, from a reference's target or its type."""
    if isinstance(target, int):
        return target
    if isinstance(target, Symbol):
        return target.type_id
    return expression_type


def __call_at(
    path: Path, tokens: tuple[Tok.Token, ...], row: int, col: int
) -> tuple[SrcSpan, int] | None:
    """The callee's name span and the argument index, for the call being written.

    Scans back for the unmatched ``(`` and counts the commas after it: the
    argument the caret follows is the active one.
    """
    depth = 0
    arguments = 0
    open_index: int | None = None
    before = __tokens_before(path, tokens, row, col)
    for index in range(len(before) - 1, -1, -1):
        token = before[index]
        if not isinstance(token, Tok.Punctuator):
            continue
        if token.kind == Tok.PunctuatorKind.RParen:
            depth += 1
        elif token.kind == Tok.PunctuatorKind.LParen:
            if depth == 0:
                open_index = index
                break
            depth -= 1
        elif token.kind == Tok.PunctuatorKind.Comma and depth == 0:
            arguments += 1
    if open_index is None:
        return None
    for index in range(open_index - 1, -1, -1):
        token = before[index]
        if isinstance(token, Tok.Identifier):
            return token.span, arguments
    return None


# ── small helpers ─────────────────────────────────────────────────────────────


def __line(text: str, row: int) -> str:
    lines = text.splitlines()
    return lines[row] if 0 <= row < len(lines) else ""


def __identifier_char(character: str) -> bool:
    return character.isalnum() or character == "_"


def __position(path: Path, row: int, col: int) -> SrcPosition:
    return SrcPosition(row, col, path)


def __visible_here(span: SrcSpan | None, path: Path) -> bool:
    """True when a private declaration is in the file being completed."""
    return span is not None and span.path.resolve() == path.resolve()
