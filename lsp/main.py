"""``yian-lsp``: the stdio language server process (plan §5.10).

The entry point stays thin: it configures logging on **stderr** (stdout belongs
to JSON-RPC), parses the few options the extension may pass, and hands control to
pygls.  The process exits when the client closes stdin, so closing the editor
window never leaves an orphan behind.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from collections.abc import Sequence
from pathlib import Path

from lsp.server import SERVER_NAME, SERVER_VERSION, create_server

__all__ = ["main"]

#: Trace level of the ``stderr`` logger; ``YIAN_LSP_LOG`` overrides the default.
__LOG_LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
__DEFAULT_LOG_LEVEL = "INFO"


def main(argv: Sequence[str] | None = None) -> int:
    """Run the language server until stdin closes."""
    parser = argparse.ArgumentParser(
        prog=SERVER_NAME,
        description="YIAN language server (stdio transport; not meant to be run by hand)",
    )
    parser.add_argument(
        "--compiler-root",
        type=Path,
        default=None,
        help="YIAN checkout root used to locate the standard library",
    )
    parser.add_argument(
        "--raw-pointers",
        action="store_true",
        help="analyze in raw-pointer mode, matching `yianc -r`",
    )
    parser.add_argument(
        "--log-level",
        choices=__LOG_LEVELS,
        default=os.environ.get("YIAN_LSP_LOG", __DEFAULT_LOG_LEVEL),
        help=f"stderr log level (default: ${'YIAN_LSP_LOG'} or {__DEFAULT_LOG_LEVEL})",
    )
    # `vscode-languageclient` appends this to the command line whenever the
    # transport is stdio (node/main.js pushes `--stdio` for an Executable), and
    # other clients do the same, so the flag has to be accepted.  It selects
    # nothing: stdio is the only transport this server speaks.
    parser.add_argument(
        "--stdio",
        action="store_true",
        help="accepted for compatibility; stdio is the only transport",
    )
    parser.add_argument("--version", action="version", version=f"{SERVER_NAME} {SERVER_VERSION}")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    server = create_server(compiler_root=args.compiler_root, raw_pointers=args.raw_pointers)
    server.start_io()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
