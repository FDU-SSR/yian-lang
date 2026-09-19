"""Rendering the analyzed answer as LSP navigation payloads.

The compiler side answers "what does this position resolve to" once
(:class:`~compiler.analysis.navigation.Navigator`); this module renders that one
answer in the shapes the client asks for — a `Location`, a `Hover`, and a
`DocumentSymbol` tree — so definition and hover can never disagree about what a
position means.
"""

from __future__ import annotations

from pathlib import Path

from lsprotocol import types

from compiler.analysis.index import Declaration, DeclarationKind
from compiler.analysis.navigation import Navigator, Resolution, Target
from compiler.analysis.positions import path_to_uri, to_lsp_range
from compiler.frontend.lex.position import SrcSpan

__all__ = ["document_symbols", "hover", "location"]

#: Declaration kinds that belong in the outline: a file's top-level definitions
#: and their members.  Locals and parameters are deliberately absent — an outline
#: describes the structure of a file, not the inside of one body.
OUTLINE_KINDS = frozenset(
    {
        DeclarationKind.STRUCT,
        DeclarationKind.ENUM,
        DeclarationKind.TRAIT,
        DeclarationKind.ALIAS,
        DeclarationKind.FUNCTION,
        DeclarationKind.METHOD,
        DeclarationKind.FIELD,
        DeclarationKind.VARIANT,
    }
)

#: Members that may nest under their container in the outline.
MEMBER_KINDS = frozenset(
    {DeclarationKind.FIELD, DeclarationKind.VARIANT, DeclarationKind.METHOD}
)

SYMBOL_KINDS = {
    DeclarationKind.FUNCTION: types.SymbolKind.Function,
    DeclarationKind.METHOD: types.SymbolKind.Method,
    DeclarationKind.STRUCT: types.SymbolKind.Struct,
    DeclarationKind.ENUM: types.SymbolKind.Enum,
    DeclarationKind.TRAIT: types.SymbolKind.Interface,
    DeclarationKind.ALIAS: types.SymbolKind.Class,
    DeclarationKind.FIELD: types.SymbolKind.Field,
    DeclarationKind.VARIANT: types.SymbolKind.EnumMember,
}

KIND_LABELS = {
    DeclarationKind.FUNCTION: "fn",
    DeclarationKind.METHOD: "fn",
    DeclarationKind.STRUCT: "struct",
    DeclarationKind.ENUM: "enum",
    DeclarationKind.TRAIT: "trait",
    DeclarationKind.ALIAS: "typedef",
    DeclarationKind.FIELD: "field",
    DeclarationKind.VARIANT: "variant",
    DeclarationKind.VARIABLE: "let",
    DeclarationKind.PARAMETER: "parameter",
    DeclarationKind.MODULE: "module",
    DeclarationKind.IMPORT: "import",
}


def location(target: Target, navigator: Navigator) -> types.Location:
    """The declaration as a jump target.

    The range is measured against the *target's* own file, which the analysis
    also read: jumping into the standard library works without the client ever
    having opened it.
    """
    return types.Location(
        uri=path_to_uri(target.span.path),
        range=to_range(target.span, navigator),
    )


def hover(resolution: Resolution, navigator: Navigator, span: SrcSpan) -> types.Hover | None:
    """The declaration and the expression's type, as markdown.

    Everything shown comes from the analysis (悬停类型来自分析结果):
    the kind and name from the declaration, the signature or type from the type
    in hand, and a note when the declaration is the standard library's.
    """
    lines: list[str] = []
    target = resolution.target
    if target is not None:
        lines.append("```yian")
        lines.append(declaration_text(target))
        lines.append("```")
        origin = "the standard library" if target.stdlib else navigator.module_of(target.span.path)
        if origin:
            lines.append(f"*declared in {origin}*")
    expression_type = resolution.expression_type
    redundant = target is not None and (
        target.signature is not None or target.type_name == expression_type
    )
    if expression_type is not None and not redundant:
        lines.append(f"`{expression_type}`")
    if not lines:
        return None
    return types.Hover(
        contents=types.MarkupContent(kind=types.MarkupKind.Markdown, value="\n\n".join(lines)),
        range=to_range(span, navigator),
    )


def document_symbols(
    declarations: tuple[Declaration, ...], navigator: Navigator
) -> list[types.DocumentSymbol]:
    """The file's declarations as a tree, nested by their container.

    A member nests under its container when that container is declared in the
    same file — `Point`'s fields and methods under `Point` — and stands alone
    otherwise, which is what an `impl` in another file looks like from here.
    """
    entries: list[tuple[Declaration, types.DocumentSymbol]] = [
        (declaration, document_symbol(declaration, navigator))
        for declaration in declarations
        if __outline_candidate(declaration, declarations)
    ]
    by_name: dict[str, list[types.DocumentSymbol]] = {}
    for declaration, item in entries:
        by_name.setdefault(declaration.name, []).append(item)
    roots: list[types.DocumentSymbol] = []
    for declaration, item in entries:
        # A method's container is the impl target as written (``Pair<T>``), while
        # the struct itself is declared as ``Pair``: nest by the base name.
        container = __base_name(declaration.container or "")
        parents = by_name.get(container, [])
        parent = parents[0] if parents else None
        if parent is not None and parent is not item and declaration.kind in MEMBER_KINDS:
            parent.children = [*(parent.children or []), item]
        else:
            roots.append(item)
    return roots


def document_symbol(declaration: Declaration, navigator: Navigator) -> types.DocumentSymbol:
    """One declaration as a document symbol, positioned at its name."""
    assert declaration.span is not None
    rendered = to_range(declaration.span, navigator)
    return types.DocumentSymbol(
        name=declaration.name,
        kind=SYMBOL_KINDS.get(declaration.kind, types.SymbolKind.Object),
        range=rendered,
        selection_range=rendered,
        detail=declaration.type_name or KIND_LABELS.get(declaration.kind),
    )


def to_range(span: SrcSpan, navigator: Navigator) -> types.Range:
    """Convert a compiler span to an LSP range, in the span's own document."""
    rendered = to_lsp_range(span, navigator.text_of(span.path))
    return types.Range(
        start=types.Position(
            line=rendered["start"]["line"], character=rendered["start"]["character"]
        ),
        end=types.Position(line=rendered["end"]["line"], character=rendered["end"]["character"]),
    )


def declaration_text(target: Target) -> str:
    """The code-block line hover shows for a declaration."""
    if target.signature is not None:
        return target.signature
    label = KIND_LABELS.get(target.kind, target.kind.value)
    if target.type_name is not None:
        return f"{label} {target.name}: {target.type_name}"
    return f"{label} {target.name}"


def __outline_candidate(declaration: Declaration, declarations: tuple[Declaration, ...]) -> bool:
    if declaration.kind not in OUTLINE_KINDS or declaration.span is None:
        return False
    file = __file_of(declarations)
    return file is None or declaration.span.path.resolve() == file


def __base_name(container: str) -> str:
    """``Pair<T>`` → ``Pair``: the declaration a container label refers to."""
    return container.split("<", 1)[0].strip()


def __file_of(declarations: tuple[Declaration, ...]) -> Path | None:
    for declaration in declarations:
        if declaration.span is not None:
            return declaration.span.path.resolve()
    return None
