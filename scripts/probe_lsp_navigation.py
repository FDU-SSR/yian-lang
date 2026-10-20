#!/usr/bin/env python3
"""Minimal end-to-end probe for strict-analysis LSP reading navigation.

The probe opens one two-file fixture, verifies definitions, references, hover,
document symbols, semantic tokens, closed editing methods and the valid to
invalid to repaired transition. It intentionally calls only the public
StdioSession helper and never runs the full protocol matrix.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(SCRIPT_DIR))

from check_lsp import ProtocolError, StdioSession, resolve_native  # noqa: E402

PROVIDER = """\
pub typedef A = i32;
pub typedef B = i32;
pub struct Cell<T> { pub value: T }
impl<T> Cell<T> { pub fn get() -> T { self.value } }
pub enum Tag { One, Two }
"""

MAIN = """\
from probe.provider import A as Imported, B, Cell, Tag;
fn read(value: Imported) -> Imported {
    { let value: B = 2; value; }
    value
}
fn inspect() {
    let a: Imported = read(1);
    let b: B = 2;
    let left: Cell<i32> = Cell<i32>(a);
    let right: Cell<i64> = Cell<i64>(3i64);
    left.get();
    right.get();
    left.value;
    let tag: Tag = Tag.One;
    match tag { Tag.One => {}, Tag.Two => {}, }
    b;
}
"""

UTF16_MAIN = """\
from probe.provider import A as Imported, B;
fn marker() { let text: str = "😀"; let value: Imported = 1; }
"""

TYPED_MAIN = """\
struct Widget { value: i32 }
impl Widget { static pub fn make() -> Widget { Widget(1) } }

fn pair() -> (i32, i32) { (1, 2) }

fn typed_bindings() {
    let inferred = 1;
    let flag = true;
    let letter = 'A';
    let text = "yian";
    let widget = Widget.make();
    let annotated: i32 = 2;
    let (first, second) = pair();
    let items: i32[2] = [1, 2];
    for item in items { item; }
    let opt: Option<i32> = Option<i32>.Some(7);
    match opt {
        Some(payload) => { payload; },
        None => {},
    }
    if let Some(found) = opt { found; }
    let bias: i32 = 10;
    let closure = |captured = bias| (value: i32) -> i32 { captured + value };
    inferred; flag; letter; text; widget; annotated; first; second; closure;
}
"""

PROBE_MANIFEST = """\
[package]
name = "probe"
kind = "bin"
version = "0.1.0"
entry = "src/main.an"

[dependencies]
"""


class ProbeFailure(Exception):
    pass


def require(condition: bool, message: str) -> None:
    if not condition:
        raise ProbeFailure(message)


def position(lines: list[str], line: int, word: str, occurrence: int = 0) -> dict[str, int]:
    start = 0
    index = -1
    for _ in range(occurrence + 1):
        index = lines[line].index(word, start)
        start = index + 1
    return {"line": line, "character": index + 1}


def same_location(value: Any, uri: str, line: int, start: int, end: int) -> bool:
    if not isinstance(value, dict):
        return False
    if value.get("uri") != uri:
        return False
    span = value.get("range")
    if not isinstance(span, dict):
        return False
    return (
        span.get("start") == {"line": line, "character": start}
        and span.get("end") == {"line": line, "character": end}
    )


def location_of(value: Any) -> Any:
    if isinstance(value, list):
        return value[0] if len(value) == 1 else value
    return value


def run_probe(lsp: Path, compiler_root: Path, timeout: float) -> dict[str, Any]:
    environment = dict(os.environ)
    environment.setdefault("NO_COLOR", "1")
    command = [str(lsp), "--stdio", "--compiler-root", str(compiler_root)]
    report: dict[str, Any] = {}
    with tempfile.TemporaryDirectory(prefix="yian-lsp-navigation-") as directory:
        workspace = Path(directory)
        source_root = workspace / "src"
        source_root.mkdir()
        (workspace / "package.anx").write_text(PROBE_MANIFEST, encoding="utf-8", newline="")
        provider = source_root / "provider.an"
        main = source_root / "main.an"
        provider.write_text(PROVIDER, encoding="utf-8", newline="")
        main.write_text(MAIN, encoding="utf-8", newline="")
        provider_uri = provider.as_uri()
        main_uri = main.as_uri()
        lines = MAIN.splitlines()

        client = StdioSession(command, cwd=workspace, environment=environment, timeout=timeout)
        try:
            initialized = client.initialize(workspace)
            capabilities = initialized.get("capabilities") if isinstance(initialized, dict) else None
            require(isinstance(capabilities, dict), "initialize did not return capabilities")
            for retained in (
                "definitionProvider",
                "hoverProvider",
                "documentSymbolProvider",
                "referencesProvider",
                "documentHighlightProvider",
                "semanticTokensProvider",
                "inlayHintProvider",
            ):
                require(capabilities.get(retained) is not None, f"missing retained capability {retained}")
            for closed in (
                "completionProvider",
                "signatureHelpProvider",
                "renameProvider",
                "codeActionProvider",
                "documentFormattingProvider",
            ):
                require(closed not in capabilities, f"closed capability {closed} is still declared")
            report["capabilities"] = sorted(capabilities)

            for path, text in ((provider, PROVIDER), (main, MAIN)):
                client.notify("textDocument/didOpen", {
                    "textDocument": {
                        "uri": path.as_uri(),
                        "languageId": "yian",
                        "version": 1,
                        "text": text,
                    }
                })
            require(
                client.wait_diagnostics(provider_uri, 1)["params"]["diagnostics"] == [],
                "valid provider produced diagnostics",
            )
            require(
                client.wait_diagnostics(main_uri, 1)["params"]["diagnostics"] == [],
                "valid main produced diagnostics",
            )

            document = {"uri": main_uri}

            def request(method: str, params: dict[str, Any]) -> Any:
                return client.request(method, params)

            def at(line: int, word: str, occurrence: int = 0) -> dict[str, int]:
                return position(lines, line, word, occurrence)

            module_definition = request("textDocument/definition", {
                "textDocument": document, "position": at(0, "provider"),
            })
            require(same_location(module_definition, provider_uri, 0, 0, 0),
                    f"import module did not resolve to the provider file start: {module_definition!r}")

            definition = request("textDocument/definition", {
                "textDocument": document, "position": at(6, "Imported"),
            })
            require(same_location(definition, provider_uri, 0, 12, 13), "Imported did not resolve to provider A")

            definition_b = request("textDocument/definition", {
                "textDocument": document, "position": at(7, "B"),
            })
            require(same_location(definition_b, provider_uri, 1, 12, 13), "B did not resolve to provider B")

            nested = request("textDocument/definition", {
                "textDocument": document, "position": at(2, "value", 1),
            })
            require(same_location(nested, main_uri, 2, 10, 15), "nested binding resolved to an outer declaration")

            outer = request("textDocument/references", {
                "textDocument": document,
                "position": at(1, "value"),
                "context": {"includeDeclaration": True},
            })
            require(
                isinstance(outer, list)
                and same_location(outer[0], main_uri, 1, 8, 13)
                and len(outer) == 2
                and same_location(outer[1], main_uri, 3, 4, 9),
                "parameter references did not respect lexical scope",
            )

            highlights = request("textDocument/documentHighlight", {
                "textDocument": document, "position": at(1, "value"),
            })
            def highlight_matches(item: Any, line: int, start: int, end: int) -> bool:
                if not isinstance(item, dict):
                    return False
                span = item.get("range")
                return (
                    isinstance(span, dict)
                    and span.get("start") == {"line": line, "character": start}
                    and span.get("end") == {"line": line, "character": end}
                )
            require(
                isinstance(highlights, list)
                and len(highlights) == 2
                and highlight_matches(highlights[0], 1, 8, 13)
                and highlight_matches(highlights[1], 3, 4, 9),
                f"document highlights did not separate the declaration and use: {highlights!r}",
            )

            method_definition = request("textDocument/definition", {
                "textDocument": document, "position": at(10, "get"),
            })
            require(same_location(method_definition, provider_uri, 3, 25, 28), "method name did not resolve")

            method_references = request("textDocument/references", {
                "textDocument": document,
                "position": at(10, "get"),
                "context": {"includeDeclaration": True},
            })
            require(
                isinstance(method_references, list) and len(method_references) == 3,
                "method references did not include the declaration and both calls",
            )

            field_definition = request("textDocument/definition", {
                "textDocument": document, "position": at(12, "value"),
            })
            require(same_location(field_definition, provider_uri, 2, 25, 30), "field use did not resolve to its declaration")

            variant_definition = request("textDocument/definition", {
                "textDocument": document, "position": at(13, "One"),
            })
            require(same_location(variant_definition, provider_uri, 4, 15, 18), "variant use did not resolve")

            hover = request("textDocument/hover", {
                "textDocument": document, "position": at(10, "get"),
            })
            hover_text = (
                hover.get("contents", {}).get("value", "")
                if isinstance(hover, dict) and isinstance(hover.get("contents"), dict)
                else ""
            )
            require("fn get" in hover_text and "i32" in hover_text, "method hover did not show generic and concrete signatures")

            symbols = request("textDocument/documentSymbol", {"textDocument": {"uri": provider_uri}})
            names = {item.get("name") for item in symbols} if isinstance(symbols, list) else set()
            require(
                {"A", "B", "Cell", "value", "get", "Tag", "One", "Two"} <= names,
                "document symbols omitted a declaration or member",
            )

            tokens = request("textDocument/semanticTokens/full", {"textDocument": document})
            data = tokens.get("data") if isinstance(tokens, dict) else None
            require(isinstance(data, list) and len(data) > 0 and len(data) % 5 == 0,
                    "semantic tokens are missing or malformed")
            report["semantic_token_values"] = len(data)

            closed = {
                "completion": ("textDocument/completion", {
                    "textDocument": document,
                    "position": {"line": 6, "character": len(lines[6])},
                }),
                "signature_help": ("textDocument/signatureHelp", {
                    "textDocument": document, "position": {"line": 6, "character": 20},
                }),
                "prepare_rename": ("textDocument/prepareRename", {
                    "textDocument": document, "position": at(6, "a"),
                }),
                "rename": ("textDocument/rename", {
                    "textDocument": document, "position": at(6, "a"), "newName": "renamed",
                }),
                "code_action": ("textDocument/codeAction", {
                    "textDocument": document,
                    "range": {"start": at(6, "a"), "end": {"line": 6, "character": 9}},
                    "context": {"diagnostics": []},
                }),
                "formatting": ("textDocument/formatting", {
                    "textDocument": document,
                    "options": {"tabSize": 4, "insertSpaces": True},
                }),
            }
            report["closed_methods"] = {}
            for name, (method, params) in closed.items():
                message = client.request_message(method, params)
                error = message.get("error")
                require(isinstance(error, dict) and error.get("code") == -32601,
                        f"closed method {name} was not rejected with MethodNotFound")
                report["closed_methods"][name] = error["code"]

            broken = PROVIDER.replace("pub typedef A = i32;", "pub typedef A = Missing;")
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": provider_uri, "version": 2},
                "contentChanges": [{"text": broken}],
            })
            # The server clears diagnostics when the revision changes, so skip the empty clear
            # publication and wait for the analysis result of this revision.
            broken_diagnostics = client.until(lambda message: (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == provider_uri
                and message["params"].get("version") == 2
                and message["params"].get("diagnostics")
            ))["params"]["diagnostics"]
            require(len(broken_diagnostics) == 1, "failed analysis did not publish exactly one root diagnostic")
            require(broken_diagnostics[0].get("code") == "E302", "failed analysis lost its diagnostic code")
            require(
                client.wait_diagnostics(main_uri, 1)["params"]["diagnostics"] == [],
                "dependency failure left stale diagnostics on main",
            )
            require(
                request("textDocument/definition", {"textDocument": document, "position": at(6, "Imported")}) is None,
                "failed analysis served a stale definition",
            )
            require(
                request("textDocument/hover", {"textDocument": document, "position": at(6, "Imported")}) is None,
                "failed analysis served stale hover information",
            )
            require(
                request("textDocument/documentSymbol", {"textDocument": {"uri": main_uri}}) == [],
                "failed analysis served stale document symbols",
            )
            require(
                request("textDocument/semanticTokens/full", {"textDocument": document}).get("data") == [],
                "failed analysis served stale semantic tokens",
            )
            report["failure_diagnostics"] = broken_diagnostics

            repaired = PROVIDER.replace("pub typedef A = i32;", "pub typedef A = i32; // repaired")
            repaired_main = MAIN.replace("let a: Imported", "let alpha: Imported").replace("Cell<i32>(a)", "Cell<i32>(alpha)")
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": provider_uri, "version": 3},
                "contentChanges": [{"text": repaired}],
            })
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": main_uri, "version": 2},
                "contentChanges": [{"text": repaired_main}],
            })
            repaired_lines = repaired_main.splitlines()
            repaired_position = {
                "line": 6,
                "character": repaired_lines[6].index("Imported") + 1,
            }
            require(
                client.wait_diagnostics(provider_uri, 3)["params"]["diagnostics"] == [],
                "repaired provider retained diagnostics",
            )
            repaired_main_diagnostics = client.wait_diagnostics(main_uri, 2)["params"]["diagnostics"]
            require(
                repaired_main_diagnostics == [],
                f"repaired main retained diagnostics: {repaired_main_diagnostics!r}",
            )
            repaired_definition = request("textDocument/definition", {
                "textDocument": document, "position": repaired_position,
            })
            require(same_location(repaired_definition, provider_uri, 0, 12, 13),
                    f"navigation did not recover after repair: {repaired_definition!r}")
            repaired_hover = request("textDocument/hover", {
                "textDocument": document, "position": repaired_position,
            })
            repaired_text = (
                repaired_hover.get("contents", {}).get("value", "")
                if isinstance(repaired_hover, dict) and isinstance(repaired_hover.get("contents"), dict)
                else ""
            )
            require("i32" in repaired_text, "repaired hover did not use the restored underlying type")
            report["repaired_diagnostics"] = []

            utf16 = source_root / "utf16.an"
            utf16.write_text(UTF16_MAIN, encoding="utf-8", newline="")
            client.notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": utf16.as_uri(),
                    "languageId": "yian",
                    "version": 1,
                    "text": UTF16_MAIN,
                }
            })
            require(client.wait_diagnostics(utf16.as_uri(), 1)["params"]["diagnostics"] == [],
                    "UTF-16 fixture produced diagnostics")
            utf16_line = UTF16_MAIN.splitlines()[1]
            utf16_position = {"line": 1, "character": utf16_line.index("Imported") + 2}
            utf16_hover = client.request("textDocument/hover", {
                "textDocument": {"uri": utf16.as_uri()},
                "position": utf16_position,
            })
            require(isinstance(utf16_hover, dict), "UTF-16 hover was not resolved after a non-BMP character")
            range_value = utf16_hover.get("range", {})
            expected_character = utf16_position["character"]
            require(
                range_value.get("start", {}).get("character") == expected_character,
                "UTF-16 cursor range does not round-trip the request position",
            )
            utf16_definition = client.request("textDocument/definition", {
                "textDocument": {"uri": utf16.as_uri()},
                "position": utf16_position,
            })
            require(same_location(utf16_definition, provider_uri, 0, 12, 13),
                    "UTF-16 position did not resolve the imported alias")
            report["utf16_character"] = expected_character

            typed = source_root / "typed.an"
            typed.write_text(TYPED_MAIN, encoding="utf-8", newline="")
            typed_uri = typed.as_uri()
            typed_document = {"uri": typed_uri}
            typed_lines = TYPED_MAIN.splitlines()
            client.notify("textDocument/didOpen", {
                "textDocument": {
                    "uri": typed_uri,
                    "languageId": "yian",
                    "version": 1,
                    "text": TYPED_MAIN,
                }
            })
            require(client.wait_diagnostics(typed_uri, 1)["params"]["diagnostics"] == [],
                    "typed fixture produced diagnostics")

            def line_of(marker: str) -> int:
                for index, line in enumerate(typed_lines):
                    if marker in line:
                        return index
                raise ProbeFailure(f"typed fixture is missing {marker!r}")

            def hint_position(line: int, word: str) -> dict[str, int]:
                return {"line": line, "character": typed_lines[line].index(word) + len(word)}

            inferred_line = line_of("let inferred")
            flag_line = line_of("let flag")
            letter_line = line_of("let letter")
            text_line = line_of("let text")
            widget_line = line_of("let widget")
            pair_line = line_of("let (first, second)")
            for_line = line_of("for item in")
            match_line = line_of("Some(payload)")
            if_line = line_of("if let Some(found)")
            closure_line = line_of("let closure")
            expected = [
                (hint_position(inferred_line, "inferred"), ": i32"),
                (hint_position(flag_line, "flag"), ": bool"),
                (hint_position(letter_line, "letter"), ": char"),
                (hint_position(text_line, "text"), ": str"),
                (hint_position(widget_line, "widget"), ": Widget"),
                (hint_position(pair_line, "first"), ": i32"),
                (hint_position(pair_line, "second"), ": i32"),
                (hint_position(for_line, "item"), ": i32&"),
                (hint_position(match_line, "payload"), ": i32"),
                (hint_position(if_line, "found"), ": i32"),
                (hint_position(closure_line, "closure"), ": |captured: i32| (value: i32) -> i32"),
                (hint_position(closure_line, "captured"), ": i32"),
            ]
            whole_range = {
                "start": {"line": 0, "character": 0},
                "end": {"line": len(typed_lines), "character": 0},
            }
            hints = client.request("textDocument/inlayHint", {
                "textDocument": typed_document, "range": whole_range,
            })
            require(isinstance(hints, list), "inlay hints were not an array")
            actual = [
                (
                    (item.get("position") or {}).get("line"),
                    (item.get("position") or {}).get("character"),
                    item.get("label"),
                )
                for item in hints if isinstance(item, dict)
            ]
            require(
                actual == [(position["line"], position["character"], label)
                           for position, label in expected],
                f"inlay hints do not match the inferred bindings: {actual!r}",
            )
            require(
                all(item.get("kind") == 1 and "textEdits" not in item for item in hints),
                "inlay hints must be Type hints without text edits",
            )
            report["inlay_hints"] = len(actual)

            # A static call spells its owner type as the callee; hover and definition resolve
            # that type item rather than the dispatched method.
            widget_character = typed_lines[widget_line].index("Widget") + 1
            widget_hover = client.request("textDocument/hover", {
                "textDocument": typed_document,
                "position": {"line": widget_line, "character": widget_character},
            })
            widget_text = (
                widget_hover.get("contents", {}).get("value", "")
                if isinstance(widget_hover, dict) and isinstance(widget_hover.get("contents"), dict)
                else ""
            )
            require("struct Widget" in widget_text,
                    f"static call receiver hover failed: {widget_hover!r}")
            widget_definition = client.request("textDocument/definition", {
                "textDocument": typed_document,
                "position": {"line": widget_line, "character": widget_character},
            })
            struct_line = line_of("struct Widget")
            struct_column = typed_lines[struct_line].index("Widget")
            require(
                same_location(location_of(widget_definition), typed_uri, struct_line, struct_column,
                              struct_column + len("Widget")),
                f"static call receiver definition failed: {widget_definition!r}",
            )
            report["static_receiver_navigation"] = True

            inferred_line_range = {
                "start": {"line": inferred_line, "character": 0},
                "end": {"line": inferred_line, "character": len(typed_lines[inferred_line])},
            }
            single = client.request("textDocument/inlayHint", {
                "textDocument": typed_document, "range": inferred_line_range,
            })
            require(
                isinstance(single, list) and len(single) == 1 and single[0].get("label") == ": i32",
                f"inlay hint range filtering failed: {single!r}",
            )

            refresh_mark = len(client.messages)
            client.notify("textDocument/didChange", {
                "textDocument": {"uri": typed_uri, "version": 2},
                "contentChanges": [{"text": TYPED_MAIN + "// refresh probe\n"}],
            })
            require(client.wait_diagnostics(typed_uri, 2)["params"]["diagnostics"] == [],
                    "edited typed fixture produced diagnostics")
            # This round trip reads the frames the server wrote after the diagnostics, so the
            # retained transcript has the refresh request even though StdioSession answers it.
            client.request("textDocument/inlayHint", {
                "textDocument": typed_document, "range": whole_range,
            })
            require(
                any(message.get("method") == "workspace/inlayHint/refresh"
                    for message in client.messages[refresh_mark:]),
                "analysis completion did not send workspace/inlayHint/refresh",
            )
            report["inlay_hint_refresh"] = True

            client.notify("textDocument/didChange", {
                "textDocument": {"uri": typed_uri, "version": 3},
                "contentChanges": [{"text": "fn main() { let broken: Missing = 1; }\n"}],
            })
            require(client.until(lambda message: (
                message.get("method") == "textDocument/publishDiagnostics"
                and isinstance(message.get("params"), dict)
                and message["params"].get("uri") == typed_uri
                and message["params"].get("version") == 3
                and message["params"].get("diagnostics")
            )), "invalid typed fixture did not publish a diagnostic")
            require(
                client.request("textDocument/inlayHint", {
                    "textDocument": typed_document, "range": whole_range,
                }) == [],
                "failed analysis served stale inlay hints",
            )

            status = client.shutdown()
            require(status == 0, f"LSP exited with status {status}")
            report["exit_status"] = status
        except ProtocolError as error:
            client.kill()
            raise ProbeFailure(str(error)) from error
        except Exception:
            client.kill()
            raise
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--lsp", type=Path, default=None, help="yian-lsp executable (default: installed command)")
    parser.add_argument("--compiler-root", type=Path, default=ROOT, help="checkout root used for standard-library lookup")
    parser.add_argument("--timeout", type=float, default=20.0, help="per-message protocol timeout in seconds")
    arguments = parser.parse_args()
    lsp = arguments.lsp or resolve_native(os.environ.get("YIAN_LSP_BIN") or "yian-lsp")
    try:
        report = run_probe(Path(lsp), arguments.compiler_root.resolve(), arguments.timeout)
    except ProbeFailure as error:
        print(json.dumps({"ok": False, "error": str(error)}, indent=2))
        return 1
    report["ok"] = True
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
