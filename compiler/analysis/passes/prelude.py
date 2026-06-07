"""
Prelude injection pass.

Automatically prepends standard library imports to non-standard-library
source files so that commonly-used symbols (Option, Result, Vec, etc.)
are available without explicit imports.
"""

from __future__ import annotations

from pathlib import Path

from compiler.frontend.parse import ast as AST
from compiler.frontend.lex.position import SrcPosition, SrcSpan

# Prelude imports injected into every non-lib source file.
# Each entry is (path_segments, target_name) representing:
#     from <path_segments joined by .> import <target_name>
PRELUDE_IMPORTS: list[tuple[list[str], str]] = [
    (["std", "core", "option"], "Option"),
    (["std", "core", "result"], "Result"),
    (["std", "core", "vec"], "Vec"),
    (["std", "core", "string"], "String"),
    (["std", "core", "range"], "Range"),
    (["std", "core", "ops"], "Ordering"),
]


def __make_identifier(name: str, span: SrcSpan) -> AST.Identifier:
    return AST.Identifier(span=span, name=name)


def __make_span(path: Path) -> SrcSpan:
    """Create a zero-width span pointing at the given file.

    Uses (0, 0) position — the prelude imports are compiler-generated
    and have no corresponding source location. Using the target file's
    path ensures that error messages point somewhere meaningful if a
    prelude import ever fails to resolve.
    """
    pos = SrcPosition(0, 0, path)
    return SrcSpan(pos, pos)


def __is_stdlib_file(path: Path) -> bool:
    """Check whether a source file is part of the standard library.

    Uses the same logic as GlobalResolve.__build_std_lookup:
    a file is a stdlib file iff its resolved path contains 'lib'
    as a path component.
    """
    return "lib" in path.resolve().parts


def __build_existing_imports(program: AST.Program) -> set[tuple[tuple[str, ...], str]]:
    """Collect the set of (path_tuple, target_name) already imported in the program."""
    existing: set[tuple[tuple[str, ...], str]] = set()
    for item in program.items:
        if isinstance(item, AST.Import):
            path_tuple = tuple(seg.name for seg in item.paths)
            existing.add((path_tuple, item.target.name))
    return existing


def inject_prelude(src_files: list[Path], programs: list[AST.Program]) -> None:
    """Inject prelude imports into non-stdlib programs.

    Modifies programs in-place by prepending AST.Import nodes for
    each prelude symbol. Skips any import that already exists in
    the program to avoid duplicate symbol errors.

    Prelude injection is only performed when at least one stdlib file
    is present in the compilation — otherwise the std:: imports would
    have nothing to resolve against.

    Args:
        src_files: Source file paths corresponding to each program.
        programs: Parsed AST programs (modified in-place).
    """
    # Only inject prelude if stdlib files are available to resolve against
    if not any(__is_stdlib_file(f) for f in src_files):
        return

    for src_file, program in zip(src_files, programs):
        if __is_stdlib_file(src_file):
            continue

        existing = __build_existing_imports(program)

        prelude_imports: list[AST.Import] = []
        for paths, target_name in PRELUDE_IMPORTS:
            path_tuple = tuple(paths)
            if (path_tuple, target_name) in existing:
                continue

            span = __make_span(src_file)
            import_node = AST.Import(
                span=span,
                paths=[__make_identifier(seg, span) for seg in paths],
                target=__make_identifier(target_name, span),
                alias=None,
            )
            prelude_imports.append(import_node)

        # Prepend to the beginning of the program items
        program.items = prelude_imports + program.items
