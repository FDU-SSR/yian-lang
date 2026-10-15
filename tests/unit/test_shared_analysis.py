"""The compiler and editor consume the same semantic validation stages."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from anx.project import default_stdlib_root as anx_default_stdlib_root
from yianc.analysis.session import AnalysisSession
from yianc.analysis.documents import Document, DocumentStore
from yianc.analysis.editor import EditorEngine, EditorMode, FrozenInputs
from yianc.analysis.queries.context import QueryContext
from yianc.analysis.queries.completion import CompletionKind
from yianc.analysis.queries.index import DeclarationKind
from yianc.analysis.queries.navigation import Navigator
from yianc.analysis.queries.refactor import rename
from yianc.analysis.queries.semantic import SemanticKind, SemanticModifier
from yianc.analysis.source_provenance import build_source_trust, resolve_stdlib_root
from yianc.target_layout import type_size_provider


ROOT = Path(__file__).resolve().parents[2]
STDLIB = ROOT / "lib" / "src"


class SharedAnalysisTests(unittest.TestCase):
    def test_source_roots_keep_checkout_environment_precedence(self) -> None:
        alternate = Path("/tmp/yian alternate checkout")
        alternate_lib = alternate / "custom stdlib"
        with patch.dict(os.environ, {"YIAN_LIB": "", "YIAN_ROOT": ""}, clear=True):
            self.assertEqual(resolve_stdlib_root(), STDLIB.resolve())
            self.assertEqual(anx_default_stdlib_root(), STDLIB)
            self.assertEqual(resolve_stdlib_root(ROOT), (ROOT / "lib" / "src").resolve())

        with patch.dict(os.environ, {"YIAN_LIB": str(alternate_lib), "YIAN_ROOT": str(alternate)}, clear=True):
            self.assertEqual(resolve_stdlib_root(), alternate_lib.resolve())
            self.assertEqual(resolve_stdlib_root(ROOT), (ROOT / "lib" / "src").resolve())

        with patch.dict(os.environ, {"YIAN_ROOT": str(alternate)}, clear=True):
            self.assertEqual(resolve_stdlib_root(), (alternate / "lib" / "src").resolve())

    def test_source_trust_uses_configured_roots_and_exact_test_roots(self) -> None:
        trust = build_source_trust()
        self.assertEqual(trust.stdlib_root, STDLIB.resolve())
        self.assertEqual(
            trust.test_roots,
            (
                (ROOT / "tests" / "basic" / "std").resolve(),
                (ROOT / "tests" / "safety" / "std").resolve(),
            ),
        )
        self.assertTrue(trust.allows_restricted_ops(STDLIB / "core" / "slice.an"))
        self.assertTrue(trust.allows_restricted_ops(ROOT / "tests" / "basic" / "std" / "core" / "slice_prim.an"))

        with tempfile.TemporaryDirectory(prefix="lib tests std ") as directory:
            similar_input = Path(directory) / "lib" / "src" / "restricted.an"
            similar_test = Path(directory) / "tests" / "basic" / "std" / "restricted.an"
            self.assertFalse(trust.allows_restricted_ops(similar_input))
            self.assertFalse(trust.allows_restricted_ops(similar_test))

    def test_anx_project_default_root_is_lazy_and_does_not_import_yianc(self) -> None:
        with tempfile.TemporaryDirectory(prefix="anx project cwd ") as directory:
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(ROOT / "python")
            environment.pop("YIAN_ROOT", None)
            environment.pop("YIAN_LIB", None)
            process = subprocess.run(
                [sys.executable, "-c", "import sys, anx.project; print('yianc' in sys.modules)"],
                cwd=directory,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, process.stderr)
            self.assertEqual(process.stdout.strip(), "False")

    def test_anx_fallback_compiler_imports_checkout_from_external_cwd(self) -> None:
        with tempfile.TemporaryDirectory(prefix="源码 checkout 外部 cwd ") as directory:
            external_cwd = Path(directory)
            project = external_cwd / "project"
            shutil.copytree(
                ROOT / "tests" / "package" / "basic",
                project,
                ignore=shutil.ignore_patterns("build", "__pycache__"),
            )
            environment = os.environ.copy()
            environment["PATH"] = "/usr/bin:/bin"
            environment["PYTHONPATH"] = str(ROOT / "python")
            for name in ("YIAN_ROOT", "YIAN_LIB", "YIANC"):
                environment.pop(name, None)
            process = subprocess.run(
                [sys.executable, "-m", "anx.main", "check", str(project)],
                cwd=external_cwd,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, f"{process.stdout}\n{process.stderr}")

    def test_python_and_native_roots_match_from_unicode_external_cwd(self) -> None:
        prefix_value = os.environ.get("YIAN_INSTALL_PREFIX")
        self.assertTrue(prefix_value, "YIAN_INSTALL_PREFIX must point at the refreshed task toolchain")
        native_compiler = Path(prefix_value) / "bin" / "yianc"
        self.assertTrue(native_compiler.is_file(), native_compiler)

        environment = os.environ.copy()
        environment["PYTHONPATH"] = str(ROOT / "python")
        environment["YIAN_ROOT"] = str(ROOT)
        environment["YIAN_LIB"] = str(STDLIB)
        environment["YIANC"] = str(native_compiler)
        cases = (
            (ROOT / "tests" / "basic" / "array" / "access.an", True),
            (ROOT / "tests" / "basic" / "error" / "restricted_bitcast.err.an", False),
        )

        with tempfile.TemporaryDirectory(prefix="YIAN 外部 cwd ") as directory:
            cwd = Path(directory)
            for raw in (False, True):
                for source, expected_ok in cases:
                    with self.subTest(raw_pointers=raw, source=source.name):
                        flags = ["--analyze", "--json"]
                        if raw:
                            flags.append("--raw-pointers")
                        common = [*flags, "--compiler-root", str(ROOT), str(STDLIB), str(source)]
                        python_process = subprocess.run(
                            [sys.executable, "-m", "yianc.main", *common],
                            cwd=cwd,
                            env=environment,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        native_process = subprocess.run(
                            [str(native_compiler), *common],
                            cwd=cwd,
                            env=environment,
                            capture_output=True,
                            text=True,
                            check=False,
                        )
                        self.assertEqual(python_process.returncode, native_process.returncode)
                        self.assertEqual(python_process.stderr, native_process.stderr)
                        python_result = json.loads(python_process.stdout)
                        native_result = json.loads(native_process.stdout)
                        self.assertEqual(python_result, native_result)
                        self.assertEqual(python_result["ok"], expected_ok)
                        if not expected_ok:
                            self.assertTrue(python_result["diagnostics"])

    def test_editor_queries_use_reliable_receivers_in_partial_documents(self) -> None:
        for raw in (False, True):
            with self.subTest(raw_pointers=raw):
                self.__check_partial_queries(raw)

    def __check_partial_queries(self, raw: bool) -> None:
        declarations = (
            "struct EditorPoint { pub x: i32 }\n"
            "impl EditorPoint { pub fn push(value: i32) {} }\n"
            "fn make_point() -> EditorPoint { EditorPoint(1) }\n"
            "fn helper(value: i32, other: i32) -> i32 { value + other }\n"
        )
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "partial.an"
            cases = [(expression, "") for expression in ("v.p;", "v.", "make_point().p;", "(v).p;", "[v][0].p;",
                               "v.p; helper(", "missing; v.p;", "{ let v = missing; v.p; }",
                               "{ let v = ; v.p; }", "{ let v: EditorPoint = missing; v.p; }", "{ let (v, other) = missing; v.p; }", "v.push(true);", "v.push(",
                               "v.p; helper([1, 2][0], ", "v.p; helper(helper(1, 2), ", "v.p; helper(1 < 2, ")]
            cases.extend(("v.p;", suffix) for suffix in (
                "struct Broken { field: Missing }\n", "fn broken(value: Missing) {}\n",
                "fn unrelated() { missing; }\n", "fn forbidden() { @bitcast<i32>(0); }\n", 'fn damaged() { let value = "unterminated',
                "fn damaged(\npub fn recovered() {}\n",
                "struct Range {}\nenum Option { Local }\ntrait Add { fn local_add(); }\n",
            ))
            cases.append(("v.p; damaged(", "fn damaged(value: i32, broken) {}\n"))
            cases.append(("v.p; v.damaged(", "impl EditorPoint { pub fn damaged(value: i32, broken) {} }\n"))
            for expression, suffix in cases:
                with self.subTest(expression=expression, suffix=suffix):
                    text = declarations + "fn main() { let v = EditorPoint(1); " + expression + " }\n" + suffix
                    inputs = FrozenInputs.freeze(
                        (STDLIB, source),
                        documents=DocumentStore([Document(path=source, text=text, version=1)]),
                        revision=1, topology_revision=1, configuration_identity="partial",
                    )
                    snapshot = EditorEngine(AnalysisSession(
                        compiler_root=ROOT, type_size_factory=type_size_provider, raw_pointers=raw,
                    )).analyze(inputs, EditorMode.FULL)
                    self.assertFalse(snapshot.semantic_complete)
                    column = text.splitlines()[4].index(expression) + expression.index(".") + 1
                    if expression.split(".", 1)[1].startswith("p"):
                        column += 1
                    result = snapshot.complete_at(source, 4, column)
                    candidate = next((item for item in result.items if item.label == "push"), None)
                    if "let v = missing" in expression or "let v = ;" in expression or "let (v, other)" in expression:
                        self.assertEqual(result.items, ())
                    else:
                        self.assertIsNotNone(candidate)
                        self.assertIs(candidate.kind if candidate else None, CompletionKind.METHOD)
                    self.assertNotIn("p", {item.label for item in result.items})
                    self.assertTrue(snapshot.document_symbols_at(source))
                    if "fn recovered" in suffix:
                        self.assertIn("recovered", {item.name for item in snapshot.document_symbols_at(source)})
                    self.assertTrue(snapshot.semantic_tokens_at(source))
                    self.assertFalse(snapshot.prepare_rename_at(source, 4, 16).ok)
                    if "push(" in expression:
                        signature = snapshot.signature_help_at(source, 4, text.splitlines()[4].index("push(") + 5)
                        self.assertIsNotNone(signature)
                        self.assertIn("value: i32", signature.signatures[0].label if signature else "")
                    if "damaged(" in expression:
                        self.assertIsNone(snapshot.signature_help_at(source, 4, text.splitlines()[4].index("damaged(") + 8))
                        if "v.damaged(" not in expression:
                            completion = snapshot.complete_at(source, 4, text.splitlines()[4].index("damaged(") + 7)
                            damaged = next(item for item in completion.items if item.label == "damaged")
                            self.assertIs(damaged.kind, CompletionKind.FUNCTION)
                            self.assertIsNone(damaged.detail)
                            self.assertFalse(damaged.parameters)
                    if expression.endswith(", "):
                        signature = snapshot.signature_help_at(source, 4, text.splitlines()[4].rindex(",") + 1)
                        self.assertIsNotNone(signature)
                        self.assertEqual(signature.active_parameter if signature else None, 1)
                    self.assertIsNotNone(snapshot.queries)
                    if snapshot.queries is not None:
                        state = snapshot.queries.documents[source]
                        self.assertTrue(state.declarations_available)
                        if expression.endswith(".") or expression.endswith("("):
                            self.assertFalse(state.syntax_complete)
                    strict = AnalysisSession(compiler_root=ROOT, type_size_factory=type_size_provider, raw_pointers=raw).analyze(
                        (STDLIB, source), documents=inputs.document_store(),
                    )
                    self.assertFalse(strict.ok())

            text = "trait EditorTrait { fn push(value: i32); }\nfn query(v: EditorTrait&) { v.p; }\n"
            inputs = FrozenInputs.freeze(
                (STDLIB, source), documents=DocumentStore([Document(source, text, 2)]),
                revision=2, topology_revision=1, configuration_identity="trait-members",
            )
            snapshot = EditorEngine(AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider, raw_pointers=raw,
            )).analyze(inputs, EditorMode.FULL)
            column = text.splitlines()[1].index("v.p") + 3
            completion = snapshot.complete_at(source, 1, column)
            self.assertTrue(any(item.label == "push" and item.kind is CompletionKind.METHOD
                                for item in completion.items))

    def test_partial_analysis_preserves_independent_declarations_and_signatures(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            broken = root / "broken.an"
            target = root / "main.an"
            broken.write_text("from absent import Missing;\nfn broken() {}\n")
            target_text = (
                "pub fn helper(value: i32) -> i32 { value + 1 }\n"
                "fn main() { helper(40); }\n"
            )
            target.write_text(target_text)
            inputs = FrozenInputs.freeze(
                (STDLIB, broken, target),
                revision=15,
                topology_revision=3,
                configuration_identity="fat:partial-query",
            )
            snapshot = EditorEngine(AnalysisSession(
                compiler_root=ROOT,
                type_size_factory=type_size_provider,
            )).analyze(inputs, EditorMode.FULL)

            self.assertFalse(snapshot.semantic_complete)
            self.assertIn("E304", {item.code for item in snapshot.result.diagnostics})
            completion = snapshot.complete_at(target, 1, 15)
            helper = next(item for item in completion.items if item.label == "helper")
            self.assertIs(helper.kind, CompletionKind.FUNCTION)
            signature = snapshot.signature_help_at(target, 1, 19)
            self.assertIsNotNone(signature)
            self.assertIn("value: i32", signature.signatures[0].label if signature else "")

    def test_frozen_snapshot_import_quick_fix_removes_only_the_invalid_item(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.an"
            helper = root / "helper.an"
            text = "from helper import Missing, Good;\nfn main() {}\n"
            overlay = DocumentStore(
                [
                    Document(path=main, text=text, version=1),
                    Document(path=helper, text="pub struct Good {}\n", version=1),
                ]
            )
            inputs = FrozenInputs.freeze(
                (STDLIB, main, helper),
                documents=overlay,
                revision=41,
                topology_revision=8,
                configuration_identity="fat:import-quick-fix",
            )
            snapshot = EditorEngine(AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            )).analyze(inputs, EditorMode.FULL)

            self.assertFalse(snapshot.semantic_complete)
            removals = snapshot.import_removals_at(main)
            self.assertEqual(len(removals), 1)
            self.assertEqual(removals[0].diagnostic.code, "E304")
            self.assertEqual(
                (removals[0].span.start.col, removals[0].span.end.col),
                (19, 28),
            )
            self.assertEqual(text[19:28], "Missing, ")

    def test_frozen_snapshot_resolves_and_renames_members_by_declaration(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "members.an"
            text = (
                "struct Point { pub x: i32 }\n"
                "enum Choice { Empty, Value { field: i32 } }\n"
                "impl Point { pub fn editor_member_step(amount: i32) {} }\n"
                "fn main() {\n"
                " let point: Point = Point(1);\n"
                " point.x;\n"
                " point.editor_member_step(1);\n"
                " let choice: Choice = Choice.Value(field=2);\n"
                " match choice {\n"
                " Choice.Value(field=value) => value,\n"
                " Choice.Empty => 0,\n"
                " };\n"
                "}\n"
            )
            overlay = DocumentStore([Document(path=source, text=text, version=1)])
            inputs = FrozenInputs.freeze(
                (STDLIB, source), documents=overlay, revision=36, topology_revision=7,
                configuration_identity="fat:member-query",
            )
            snapshot = EditorEngine(AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            )).analyze(inputs, EditorMode.FULL)
            self.assertTrue(snapshot.semantic_complete)

            outline = snapshot.document_symbols_at(source)
            outline_facts = {
                (declaration.kind, declaration.name, declaration.container)
                for declaration in outline
            }
            self.assertIn((DeclarationKind.STRUCT, "Point", None), outline_facts)
            self.assertIn((DeclarationKind.FIELD, "x", "Point"), outline_facts)
            self.assertIn((DeclarationKind.ENUM, "Choice", None), outline_facts)
            self.assertIn((DeclarationKind.VARIANT, "Value", "Choice"), outline_facts)
            self.assertIn((DeclarationKind.FIELD, "field", "Choice.Value"), outline_facts)
            self.assertIn((DeclarationKind.METHOD, "editor_member_step", "Point"), outline_facts)

            semantic_tokens = snapshot.semantic_tokens_at(source)
            self.assertTrue(any(
                token.kind is SemanticKind.STRUCT
                and (token.span.start.row, token.span.start.col) == (0, 7)
                and SemanticModifier.DECLARATION in token.modifiers
                for token in semantic_tokens
            ))
            self.assertTrue(any(
                token.kind is SemanticKind.FIELD
                and (token.span.start.row, token.span.start.col) == (5, 7)
                and SemanticModifier.DECLARATION not in token.modifiers
                for token in semantic_tokens
            ))

            cases = (
                (5, 7, DeclarationKind.FIELD, 2, "horizontal"),
                (6, 7, DeclarationKind.METHOD, 2, "editor_member_renamed"),
                (7, 29, DeclarationKind.VARIANT, 3, "Selected"),
                (7, 35, DeclarationKind.FIELD, 3, "payload"),
            )
            for row, column, expected_kind, occurrence_count, new_name in cases:
                with self.subTest(row=row, column=column, kind=expected_kind):
                    resolution = snapshot.resolve_at(source, row, column)
                    self.assertIsNotNone(resolution)
                    self.assertIsNotNone(resolution.target if resolution else None)
                    if resolution is not None and resolution.target is not None:
                        self.assertEqual(resolution.target.kind, expected_kind)
                    references = snapshot.references_at(source, row, column)
                    self.assertIsNotNone(references)
                    self.assertEqual(len(references.sites) if references else 0, occurrence_count)
                    renamed = snapshot.rename_at(source, row, column, new_name)
                    self.assertTrue(renamed.ok, renamed.refusal)
                    self.assertEqual(len(renamed.edits), occurrence_count)

    def test_frozen_editor_inputs_build_independent_syntax_and_full_snapshots(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "unsaved.an"
            text = (
                "fn helper(value: i32, other: i32) -> i32 { value + other }\n"
                "fn main() { helper(1, 2); }\n"
            )
            overlay = DocumentStore([Document(path=source, text=text, version=9)])
            inputs = FrozenInputs.freeze(
                (source,),
                documents=overlay,
                revision=12,
                topology_revision=4,
                configuration_identity="fat:test-package-map",
            )
            engine = EditorEngine(AnalysisSession(
                compiler_root=ROOT,
                type_size_factory=type_size_provider,
            ))

            syntax = engine.analyze(inputs, EditorMode.SYNTAX)
            self.assertEqual(syntax.identity, (12, 4, EditorMode.SYNTAX, "fat:test-package-map"))
            self.assertTrue(syntax.result.syntax_only)
            self.assertFalse(syntax.semantic_complete)
            self.assertEqual(syntax.result.sources[source.resolve()], text)

            full = engine.analyze(inputs, EditorMode.FULL)
            self.assertEqual(full.identity, (12, 4, EditorMode.FULL, "fat:test-package-map"))
            self.assertTrue(full.semantic_complete)
            self.assertIsNotNone(full.queries)
            self.assertEqual(full.result.sources[source.resolve()], text)
            self.assertIn("fn helper", full.format_document(source) or "")
            resolution = full.resolve_at(source, 0, 3)
            self.assertIsNotNone(resolution)
            self.assertEqual(resolution.target.name if resolution and resolution.target else None, "helper")
            hover = full.hover_at(source, 0, 3)
            self.assertEqual(hover, resolution)

            definition_use = full.resolve_at(source, 1, 12)
            self.assertIsNotNone(definition_use)
            self.assertEqual(
                definition_use.target.span.start.col
                if definition_use and definition_use.target
                else None,
                3,
            )
            references = full.references_at(source, 0, 3, include_declaration=False)
            self.assertIsNotNone(references)
            self.assertEqual(len(references.sites) if references else 0, 1)
            self.assertEqual(
                references.sites[0].span.start.row if references else None,
                1,
            )
            self.assertEqual(
                references.sites[0].span.start.col if references else None,
                12,
            )

            renamed = full.rename_at(source, 0, 3, "renamed_helper")
            self.assertTrue(renamed.ok)
            self.assertEqual(len(renamed.edits), 2)
            self.assertEqual(renamed.edits[0].new_name, "renamed_helper")
            prepared = full.prepare_rename_at(source, 0, 3)
            self.assertTrue(prepared.ok)
            self.assertIsNotNone(prepared.target)
            self.assertEqual(prepared.target.name if prepared.target else None, "helper")
            completion = full.complete_at(source, 1, 12)
            helper_completion = next(item for item in completion.items if item.label == "helper")
            self.assertEqual(helper_completion.parameters, ("value", "other"))
            signature = full.signature_help_at(source, 1, 22)
            self.assertIsNotNone(signature)
            self.assertEqual(
                signature.signatures[0].label if signature else None,
                "fn helper(value: i32, other: i32) -> i32",
            )
            self.assertEqual(signature.active_parameter if signature else None, 1)

    def test_syntax_snapshot_keeps_parse_diagnostics_and_rejects_formatting(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "broken.an"
            text = "fn broken( {\n"
            overlay = DocumentStore([Document(path=source, text=text, version=3)])
            inputs = FrozenInputs.freeze(
                (source,),
                documents=overlay,
                revision=8,
                topology_revision=2,
                configuration_identity="fat:syntax-error",
            )
            snapshot = EditorEngine(AnalysisSession(
                compiler_root=ROOT,
                type_size_factory=type_size_provider,
            )).analyze(inputs, EditorMode.SYNTAX)

            self.assertFalse(snapshot.semantic_complete)
            self.assertTrue(snapshot.result.diagnostics)
            self.assertIsNone(snapshot.format_document(source))

    def test_existing_comptime_and_assignment_errors(self) -> None:
        for raw in (False, True):
            for name, code in (
                ("comptime_if_div_zero.err.an", "E503"),
                ("defer_uninitialized.err.an", "E502"),
            ):
                with self.subTest(name=name, raw=raw):
                    source = ROOT / "tests" / "basic" / "error" / name
                    result = AnalysisSession(
                        compiler_root=ROOT, raw_pointers=raw,
                        type_size_factory=type_size_provider,
                    ).analyze((STDLIB, source))
                    self.assertIn(code, {diagnostic.code for diagnostic in result.diagnostics})
                    command = [sys.executable, "-m", "yianc.main", "-t", "none"]
                    if raw:
                        command.append("--raw-pointers")
                    process = subprocess.run(
                        [*command, str(STDLIB), str(source)],
                        cwd=ROOT, capture_output=True, text=True, check=False,
                    )
                    self.assertNotEqual(process.returncode, 0)
                    self.assertIn(f"error[{code}]", process.stderr)

    def test_uncalled_concrete_definition_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.an"
            source.write_text(
                "fn unused() { let value: i32; value; }\nfn main() {}\n",
                encoding="utf-8",
            )
            result = AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            ).analyze((STDLIB, source))
            self.assertIn("E502", {diagnostic.code for diagnostic in result.diagnostics})
            process = subprocess.run(
                [sys.executable, "-m", "yianc.main", "-t", "none", str(STDLIB), str(source)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertIn("error[E502]", process.stderr)

    def test_rename_refuses_unchecked_generic_name_uses_from_snapshot_index(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.an"
            source.write_text(
                "fn target() {}\nfn unused<T>(value: T) { target(); }\n",
                encoding="utf-8",
            )
            result = AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            ).analyze((STDLIB, source))
            context = QueryContext(result)
            navigator = Navigator(context)
            navigator.prepare_query_indexes()

            refusal = rename(context, source, 0, 3, "renamed", navigator=navigator)

            self.assertFalse(refusal.ok)
            self.assertIn("analysis did not check uses 'target'", refusal.refusal or "")
            self.assertEqual(len(navigator.view.unchecked_name_uses("target")), 1)

    def test_editor_accepts_entryless_text_but_build_requires_entry(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "library.an"
            source.write_text("fn helper() {}\n", encoding="utf-8")
            result = AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            ).analyze((STDLIB, source))
            self.assertTrue(result.ok())
            process = subprocess.run(
                [sys.executable, "-m", "yianc.main", "-t", "none", str(STDLIB), str(source)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertNotEqual(process.returncode, 0)
            self.assertIn("No 'main' function found", process.stderr)

    def test_uncalled_comptime_condition_is_validated(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            source = Path(directory) / "main.an"
            source.write_text(
                "fn unused() { let x: i32 = comptime if (1i32 / 0i32) == 0i32 { 1 } else { 2 }; }\n"
                "fn main() {}\n",
                encoding="utf-8",
            )
            result = AnalysisSession(
                compiler_root=ROOT, type_size_factory=type_size_provider,
            ).analyze((STDLIB, source))
            self.assertIn("E503", {diagnostic.code for diagnostic in result.diagnostics})
            process = subprocess.run(
                [sys.executable, "-m", "yianc.main", "-t", "none", str(STDLIB), str(source)],
                cwd=ROOT, capture_output=True, text=True, check=False,
            )
            self.assertIn("error[E503]", process.stderr)


if __name__ == "__main__":
    unittest.main()
