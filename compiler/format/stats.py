"""What the corpus actually looks like.

The style rules in `layout.py` are taken from the repository's own sources rather
than invented, so the numbers behind them are reproducible: this module is the
measurement, and ``--stats`` prints its report.  It also shows what a formatter
run is up against — how long lines get, how comments are placed, how common
one-line blocks are.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from compiler.format.formatter import lex_tokens
from compiler.format.layout import INDENT
from compiler.format.trivia import gaps_in
from compiler.frontend.lex import token as Tok

__all__ = ["corpus_stats"]


def corpus_stats(paths: list[Path]) -> str:
    """A human-readable report over every ``*.an`` file under *paths*."""
    return __measure(paths).describe()


@dataclass(frozen=True)
class _Stats:
    """The measurements one corpus run produces."""

    sample: str
    files: int
    lengths: tuple[int, ...]
    line_comments: int
    standalone: int
    trailing: int
    block_comments: int
    odd_indent: int
    tabs: int
    one_line_blocks: int
    fstrings: int
    condition_parens: int
    condition_bare: int
    while_parens: int
    while_bare: int

    def describe(self) -> str:
        lengths = self.lengths

        def percentile(fraction: float) -> int:
            if not lengths:
                return 0
            return lengths[min(len(lengths) - 1, int(len(lengths) * fraction))]

        return "\n".join(
            [
                f"files {self.files}  lines {len(lengths)}  (sample {self.sample})",
                f"line length: median {percentile(0.5)}  p90 {percentile(0.9)}  "
                f"p99 {percentile(0.99)}  max {max(lengths) if lengths else 0}  "
                f">100 {sum(1 for n in lengths if n > 100)}  "
                f">120 {sum(1 for n in lengths if n > 120)}",
                f"comments: line {self.line_comments} (own line {self.standalone}, "
                f"trailing {self.trailing})  block {self.block_comments}",
                f"indent: lines not a multiple of {INDENT} {self.odd_indent}  tabs {self.tabs}",
                f"one-line brace pairs {self.one_line_blocks}  f-strings {self.fstrings}",
                f"if/elif: with parens {self.condition_parens}  bare {self.condition_bare}",
                f"while: with parens {self.while_parens}  bare {self.while_bare}",
            ]
        )


def __measure(paths: list[Path]) -> _Stats:
    """Collect every number in one pass over the corpus."""
    files = __an_files(paths)
    lengths: list[int] = []
    line_comments = standalone = trailing = block_comments = 0
    odd_indent = tabs = one_line_blocks = fstrings = 0
    condition_parens = condition_bare = while_parens = while_bare = 0
    for path in files:
        text = path.read_text()
        tokens = lex_tokens(text, path) or []
        real = [token for token in tokens if not __is_eof(token)]
        lengths.extend(len(line) for line in text.splitlines())
        tabs += text.count("\t")
        fstrings += sum(1 for token in real if isinstance(token, Tok.FStrStart))
        verbatim = __verbatim(real)
        for row, line in enumerate(text.splitlines()):
            stripped = line.strip()
            head = stripped.split(" ", 1)
            if len(head) == 2 and head[0] in ("if", "elif"):
                if head[1].startswith("("):
                    condition_parens += 1
                else:
                    condition_bare += 1
            if len(head) == 2 and head[0] == "while":
                if head[1].startswith("("):
                    while_parens += 1
                else:
                    while_bare += 1
            indent = len(line) - len(line.lstrip(" "))
            if stripped and indent % INDENT != 0 and row not in verbatim:
                odd_indent += 1
        for gap in gaps_in(text, real):
            for comment in gap.comments:
                if comment.block:
                    block_comments += 1
                else:
                    line_comments += 1
                    if comment.own_line:
                        standalone += 1
                    else:
                        trailing += 1
        one_line_blocks += __one_line_blocks(real)
    lengths.sort()
    return _Stats(
        sample=str(files[0]) if files else "-",
        files=len(files),
        lengths=tuple(lengths),
        line_comments=line_comments,
        standalone=standalone,
        trailing=trailing,
        block_comments=block_comments,
        odd_indent=odd_indent,
        tabs=tabs,
        one_line_blocks=one_line_blocks,
        fstrings=fstrings,
        condition_parens=condition_parens,
        condition_bare=condition_bare,
        while_parens=while_parens,
        while_bare=while_bare,
    )


def __an_files(paths: list[Path]) -> list[Path]:
    files: list[Path] = []
    for path in paths:
        if path.is_dir():
            files.extend(sorted(path.rglob("*.an")))
        elif path.suffix == ".an":
            files.append(path)
    return files


def __one_line_blocks(tokens: list[Tok.Token]) -> int:
    stack: list[Tok.Token] = []
    count = 0
    for token in tokens:
        if not isinstance(token, Tok.Punctuator):
            continue
        if token.kind is Tok.PunctuatorKind.LBrace:
            stack.append(token)
        elif token.kind is Tok.PunctuatorKind.RBrace and stack:
            opened = stack.pop()
            if opened.span.start.row == token.span.start.row:
                count += 1
    return count


def __verbatim(tokens: list[Tok.Token]) -> set[int]:
    rows: set[int] = set()
    for token in tokens:
        if token.span.start.row != token.span.end.row:
            rows.update(range(token.span.start.row, token.span.end.row + 1))
    return rows


def __is_eof(token: Tok.Token) -> bool:
    return isinstance(token, Tok.Punctuator) and token.kind is Tok.PunctuatorKind.EOF
