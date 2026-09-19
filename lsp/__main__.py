"""``python -m lsp``: the same entry point as the ``yian-lsp`` script.

The extension starts the server through a *configured interpreter* rather than a
script on ``PATH``, which is what keeps a multi-environment machine
honest: whichever Python the user points at must be the one that has ``pygls``
and ``llvmlite`` installed.  Switching interpreters is then a settings change,
not a reinstall.
"""

from __future__ import annotations

from lsp.main import main

if __name__ == "__main__":
    raise SystemExit(main())
