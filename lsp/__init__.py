"""Language Server Protocol adapter for YIAN (plan §5.7, §5.9).

This package is the only place that speaks LSP: it turns protocol messages into
calls on the compiler's analysis session and on the ``anx`` project model, and
contains no language rules of its own.  The dependency direction is one-way —
``lsp/`` imports ``compiler/`` and ``anx/``, never the other way round.

Standard output belongs to JSON-RPC (plan §5.10), so nothing here may write to
it; diagnostics and progress go to the client as protocol messages, logs go to
standard error.
"""
