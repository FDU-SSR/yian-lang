"""The formatter's own invariants, checked against real source.

Formatting is only useful if it is *safe*: the same program, laid out again.  The
four checks here are the machine-checkable form of that claim, and the formatter
owns them — they run over the repository's own `.an` corpus, not over fixtures.

1. **tokens**: re-lexing the output yields the same token sequence (kind and
   spelling alike).  Since the parser is a function of the tokens, identical
   tokens mean an identical syntax tree — which is why this check also stands in
   for "the AST did not change".
2. **idempotence**: formatting the output again changes nothing.
3. **comments**: every comment survives, with its text and its own-line/trailing
   placement.
4. **layout**: no trailing whitespace, no tabs, indentation in whole levels, and
   exactly one newline at the end — outside literal regions, which are verbatim.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from compiler.format.formatter import format_text, lex_tokens
from compiler.format.layout import INDENT, token_text
from compiler.frontend.lex import token as Tok
from compiler.format.trivia import gaps_in
from compiler.frontend.parse.parser import Parser

__all__ = ["Problem", "verify_paths", "verify_text"]


@dataclass(frozen=True)
class Problem:
    """One invariant violation, with enough context to find it."""

    path: Path
    kind: str
    detail: str

    def __str__(self) -> str:
        return f"{self.path}: {self.kind}: {self.detail}"


def verify_paths(paths: list[Path]) -> tuple[Problem, ...]:
    """Check every ``*.an`` file under *paths*.

    ``*.err.an`` files are skipped: they exist to be rejected by the compiler, so
    a formatter refusing them is the expected outcome rather than a finding.
    """
    problems: list[Problem] = []
    for path in __an_files(paths):
        if path.name.endswith(".err.an"):
            continue
        try:
            text = path.read_text()
        except OSError as error:
            problems.append(Problem(path, "read", str(error)))
            continue
        problems.extend(verify_text(text, path))
    return tuple(problems)


def verify_text(text: str, path: Path) -> tuple[Problem, ...]:
    """Check the four invariants for one file."""
    problems: list[Problem] = []
    tokens = lex_tokens(text, path)
    if tokens is None:
        return (Problem(path, "syntax", "does not lex"),)
    try:
        Parser(tokens).parse()
    except Exception as error:  # the corpus must be valid; a parse error is a finding
        return (Problem(path, "syntax", first_line(str(error))),)
    formatted = format_text(text, path=path)
    if formatted is None:
        return (Problem(path, "format", "refused to format valid source"),)

    problems.extend(__tokens_match(text, formatted, path))
    problems.extend(__idempotent(formatted, path))
    problems.extend(__comments_match(text, formatted, path))
    problems.extend(__layout(formatted, tokens, path))
    return tuple(problems)


def __tokens_match(text: str, formatted: str, path: Path) -> list[Problem]:
    before = __sequence(text, path)
    after = __sequence(formatted, path)
    if before is None or after is None:
        return [Problem(path, "tokens", "formatted text does not lex")]
    if before == after:
        return []
    for index, (left, right) in enumerate(zip(before, after)):
        if left != right:
            return [
                Problem(
                    path,
                    "tokens",
                    f"token {index} changed: {left!r} -> {right!r}",
                )
            ]
    return [Problem(path, "tokens", f"token count changed: {len(before)} -> {len(after)}")]


def __idempotent(formatted: str, path: Path) -> list[Problem]:
    again = format_text(formatted, path=path)
    if again == formatted:
        return []
    return [Problem(path, "idempotence", first_difference(formatted, again or ""))]


def __comments_match(text: str, formatted: str, path: Path) -> list[Problem]:
    before = __comment_list(text, path)
    after = __comment_list(formatted, path)
    if before == after:
        return []
    return [Problem(path, "comments", f"{len(before)} -> {len(after)}; first difference: "
                                       f"{first_difference(__render(before), __render(after))}")]


def __layout(formatted: str, tokens: list[Tok.Token], path: Path) -> list[Problem]:
    verbatim = __verbatim_lines(tokens)
    problems: list[Problem] = []
    lines = formatted.split("\n")
    for number, line in enumerate(lines, start=1):
        if number in verbatim:
            continue
        if line.rstrip() != line:
            problems.append(Problem(path, "layout", f"line {number}: trailing whitespace"))
        leading = line[: len(line) - len(line.lstrip())]
        if "\t" in leading:
            problems.append(Problem(path, "layout", f"line {number}: tab indentation"))
        indent = len(line) - len(line.lstrip(" "))
        if line.strip() and indent % INDENT != 0:
            problems.append(Problem(path, "layout", f"line {number}: indent {indent}"))
    if formatted and not formatted.endswith("\n"):
        problems.append(Problem(path, "layout", "no newline at end of file"))
    if formatted.endswith("\n\n"):
        problems.append(Problem(path, "layout", "blank line at end of file"))
    return problems


# ── helpers ────────────────────────────────────────────────────────────────────


def __an_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.an")))
        elif path.suffix == ".an":
            files.append(path)
    return files


def __sequence(text: str, path: Path) -> list[tuple[str, str]] | None:
    tokens = lex_tokens(text, path)
    if tokens is None:
        return None
    return [
        (type(token).__name__ + ":" + __kind(token), token_text(token))
        for token in tokens
        if not __is_eof(token)
    ]


def __kind(token: Tok.Token) -> str:
    kind = getattr(token, "kind", None)
    return kind.name if isinstance(kind, (Tok.PunctuatorKind, Tok.KeywordKind)) else ""


def __comment_list(text: str, path: Path) -> list[tuple[str, bool, bool]]:
    tokens = lex_tokens(text, path) or []
    real = [token for token in tokens if not __is_eof(token)]
    found: list[tuple[str, bool, bool]] = []
    for gap in gaps_in(text, real):
        for comment in gap.comments:
            # The formatter strips the trailing whitespace of a comment's lines, so
            # that is the text this check compares.
            cleaned = "\n".join(line.rstrip() for line in comment.text.split("\n"))
            found.append((cleaned, comment.own_line, comment.block))
    return found


def __verbatim_lines(tokens: list[Tok.Token]) -> set[int]:
    """Lines covered by a token that may contain newlines (literals, f-strings)."""
    lines: set[int] = set()
    for token in tokens:
        if token.span.start.row != token.span.end.row:
            lines.update(range(token.span.start.row + 1, token.span.end.row + 2))
    return lines


def __is_eof(token: Tok.Token) -> bool:
    return isinstance(token, Tok.Punctuator) and token.kind is Tok.PunctuatorKind.EOF


def __render(comments: list[tuple[str, bool, bool]]) -> str:
    return "\n".join(f"{text!r} own_line={own} block={block}" for text, own, block in comments)


def first_line(text: str) -> str:
    return text.splitlines()[0] if text else text


def first_difference(left: str, right: str) -> str:
    """The first line that differs, for a readable failure."""
    left_lines, right_lines = left.splitlines(), right.splitlines()
    for index, (one, two) in enumerate(zip(left_lines, right_lines), start=1):
        if one != two:
            return f"line {index}: {one!r} != {two!r}"
    return f"line count {len(left_lines)} != {len(right_lines)}"
