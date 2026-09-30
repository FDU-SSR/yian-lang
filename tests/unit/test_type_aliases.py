"""Transparent aliases preserve declaration identity, not semantic type identity."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from compiler.analysis.queries.completion import CompletionKind, complete
from compiler.analysis.queries.context import QueryContext
from compiler.analysis.queries.navigation import Navigator
from compiler.analysis.queries.refactor import rename
from compiler.analysis.session import AnalysisResult, AnalysisSession
from compiler.analysis.symbol.symbol import AliasSymbol
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.frontend.lex.position import SrcSpan
from compiler.target_layout import type_size_provider


ROOT = Path(__file__).resolve().parents[2]
STDLIB = ROOT / "lib" / "src"


class TypeAliasTests(unittest.TestCase):
    def __analyze(self, source: str, *, modules: dict[str, str] | None = None,
                  raw: bool = False, ffi: bool = False) -> tuple[AnalysisResult, Path]:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            main = root / "main.an"
            main.write_text(source, encoding="utf-8")
            for name, text in (modules or {}).items():
                (root / name).write_text(text, encoding="utf-8")
            result = AnalysisSession(
                compiler_root=ROOT, raw_pointers=raw, allow_ffi=ffi,
                type_size_factory=type_size_provider,
            ).analyze((STDLIB, root))
            return result, main

    def __alias(self, result: AnalysisResult, path: Path, name: str) -> int:
        unit = next(unit for unit in result.units.values() if unit.path == path)
        symbol = unit.symbol_ctx.lookup(name)
        self.assertIsInstance(symbol, AliasSymbol)
        assert isinstance(symbol, AliasSymbol)
        target = result.aliases[symbol.alias_id].template
        self.assertIsNotNone(target)
        assert target is not None
        return target

    def test_nested_types_share_ids_at_construction(self) -> None:
        source = """
typedef Int = i32;
struct Box<T> { value: T }
trait View<T> { fn get() -> T; }
typedef Left = (Box<Int>, Int*, Int&, Int[], Int[4], Fn<(Int,), Int>, View<Int>&);
typedef Right = (Box<i32>, i32*, i32&, i32[], i32[4], Fn<(i32,), i32>, View<i32>&);
typedef AliasVec = Vec<Int>;
typedef DirectVec = Vec<i32>;
fn main() {}
"""
        for raw in (False, True):
            with self.subTest(raw=raw):
                result, path = self.__analyze(source, raw=raw)
                self.assertTrue(result.ok(), result.formatted())
                self.assertEqual(self.__alias(result, path, "Left"), self.__alias(result, path, "Right"))
                self.assertEqual(self.__alias(result, path, "AliasVec"), self.__alias(result, path, "DirectVec"))
                assert result.type_ctx is not None
                instances = [ty for _, ty in result.type_ctx.items()
                             if isinstance(ty, Type.StructType) and ty.custom_def.name == "Box"
                             and ty.generic_args == (result.type_ctx.i32_id,)]
                self.assertEqual(len(instances), 1)

    def test_templates_apply_declared_binders_not_target_positions(self) -> None:
        result, path = self.__analyze("""
struct Pair<A, B> { left: A, right: B }
typedef Reverse<A, B> = Pair<B, A>;
typedef Duplicate<T> = Pair<T, T>;
typedef First<A, B> = A;
typedef Chain<X, Y> = Reverse<Y, X>;
typedef Count = u64;
typedef Arr<T, const N: Count> = T[N];
typedef Reordered = Chain<i32, u64>;
typedef Expected = Pair<i32, u64>;
typedef Repeated = Duplicate<i32>;
typedef ExpectedRepeated = Pair<i32, i32>;
typedef Unused = First<i32, u64>;
typedef Array = Arr<i32, 4>;
typedef DirectArray = i32[4];
typedef Typed<T, const N: T> = T[N];
typedef Dependent = Typed<u64, 4>;
typedef DirectDependent = u64[4];
fn main() {}
""")
        self.assertTrue(result.ok(), result.formatted())
        for left, right in (
            ("Reordered", "Expected"), ("Repeated", "ExpectedRepeated"),
            ("Array", "DirectArray"), ("Dependent", "DirectDependent"),
        ):
            self.assertEqual(self.__alias(result, path, left), self.__alias(result, path, right))
        assert result.type_ctx is not None
        self.assertEqual(self.__alias(result, path, "Unused"), result.type_ctx.i32_id)

    def test_explicit_and_inferred_calls_generate_one_function(self) -> None:
        result, _ = self.__analyze("""
typedef Int = i32;
struct Box<T> { value: T }
typedef AliasBox<T> = Box<T>;
fn identity<T>(value: T) -> T { value }
fn main() {
    identity<AliasBox<Int>>(AliasBox<Int>(1));
    identity<Box<i32>>(Box<i32>(2));
    identity(AliasBox<i32>(3));
}
""")
        self.assertTrue(result.ok(), result.formatted())
        assert result.type_ctx is not None
        generated = [type_id for type_id in result.generated_def_points
                     if isinstance(result.type_ctx[type_id], Type.FunctionType)
                     and result.type_ctx[type_id].custom_def.name == "identity"]
        self.assertEqual(len(generated), 1)

    def test_definition_scope_and_import_identity(self) -> None:
        result, path = self.__analyze(
            "from provider import Public as Imported;\n"
            "typedef Scalar = i64;\ntypedef Local = Imported;\nfn main() {}\n",
            modules={"provider.an": "typedef Scalar = i32;\npub typedef Public = Scalar;\n"},
        )
        self.assertTrue(result.ok(), result.formatted())
        assert result.type_ctx is not None
        self.assertEqual(self.__alias(result, path, "Local"), result.type_ctx.i32_id)
        context = QueryContext(result)
        resolution = Navigator(context).resolve(path, 2, 16)
        assert resolution is not None and resolution.target is not None
        self.assertEqual(resolution.target.name, "Public")
        self.assertEqual(resolution.target.span.path.name, "provider.an")
        items = complete(context, path, 0, 27).items
        item = next(item for item in items if item.label == "Public")
        self.assertEqual(item.kind, CompletionKind.ALIAS)
        self.assertEqual(item.detail, "typedef Public = Scalar;")

    def test_unused_cycles_and_invalid_bodies_are_diagnosed(self) -> None:
        for source, message in (
            ("typedef A = A;", "A -> A"),
            ("typedef A = B*; typedef B = A[];", "A -> B -> A"),
            ("typedef A<T> = A<T*>;", "A -> A"),
            ("typedef A = Missing;", "Undefined type: Missing"),
        ):
            with self.subTest(source=source):
                result, _ = self.__analyze(source + "\nfn main() {}\n")
                self.assertFalse(result.ok())
                self.assertIn(message, result.formatted())
                self.assertTrue(result.aliases)

    def test_nominal_boundary_allows_recursive_fields(self) -> None:
        result, path = self.__analyze(
            "typedef Node = Link;\nstruct Link { next: Node* }\nfn main() {}\n"
        )
        self.assertTrue(result.ok(), result.formatted())
        assert result.type_ctx is not None
        node = self.__alias(result, path, "Node")
        fields = result.type_ctx.get_struct_fields(node)
        self.assertEqual(fields[0].type_id, result.type_ctx.alloc_pointer(node))

    def test_alias_argument_errors_point_at_use(self) -> None:
        for declaration, use, message in (
            ("typedef A<T> = T;", "A<i32, u64>", "expects 1 generic arguments"),
            ("typedef A<T> = T;", "A<4>", "requires a type argument"),
            ("typedef A<const N: u64> = i32[N];", "A<i32>", "incompatible type"),
            ("typedef A<const N: u64> = i32[N];", "A<4i32>", "incompatible type"),
        ):
            with self.subTest(use=use):
                result, _ = self.__analyze(declaration + "\nfn main() { let x: " + use + "; }\n")
                self.assertFalse(result.ok())
                diagnostic = next(item for item in result.diagnostics if message in item.message)
                self.assertEqual(diagnostic.span.start.row, 1)

    def test_lsp_preserves_alias_names_without_allocating_types(self) -> None:
        source = "typedef A = i32;\ntypedef B = i32;\nfn main() { let a: A = 1; let b: B = 2; }\n"
        result, path = self.__analyze(source)
        self.assertTrue(result.ok(), result.formatted())
        assert result.type_ctx is not None
        context = QueryContext(result)
        before = tuple(result.type_ctx.items())
        for _ in range(3):
            resolution = Navigator(context).resolve(path, 2, 19)
            assert resolution is not None and resolution.target is not None
            self.assertEqual(resolution.target.name, "A")
            self.assertEqual(resolution.target.signature, "typedef A = i32;")
            self.assertEqual(resolution.expression_type, "i32")
            items = complete(context, path, 2, 19).items
            item = next(item for item in items if item.label == "A")
            self.assertEqual(item.kind, CompletionKind.ALIAS)
            self.assertEqual(item.detail, "typedef A = i32;")
            renamed = rename(context, path, 0, 8, "Renamed")
            self.assertTrue(renamed.ok, renamed.refusal)
            self.assertEqual(len(renamed.edits), 2)
            for edit in renamed.edits:
                line = source.splitlines()[edit.span.start.row]
                self.assertEqual(line[edit.span.start.col:edit.span.end.col], "A")
        self.assertEqual(tuple(result.type_ctx.items()), before)
        with self.assertRaises(TypeError):
            key = next(iter(result.aliases))
            result.aliases[key] = result.aliases[key]

    def test_interning_keys_copy_input_and_preserve_nominality(self) -> None:
        ctx = TypeCtx()
        span = SrcSpan.empty()
        first = ctx.alloc_generic("T")
        second = ctx.alloc_generic("T")
        self.assertNotEqual(first, second)
        left = ctx.alloc_struct("Box", span)
        right = ctx.alloc_struct("Box", span)
        ctx.bind_template(left, [first])
        ctx.bind_template(right, [second])
        self.assertEqual(ctx.alloc_instance(left, [first]), left)
        self.assertNotEqual(ctx.alloc_instance(left, [ctx.i32_id]), ctx.alloc_instance(right, [ctx.i32_id]))
        arguments = [ctx.i32_id]
        instance = ctx.alloc_instance(left, arguments)
        tuple_id = ctx.alloc_tuple(arguments)
        function_id = ctx.alloc_function_pointer(arguments, ctx.void_id)
        arguments[0] = ctx.u64_id
        self.assertEqual(ctx[instance].generic_args, (ctx.i32_id,))
        self.assertEqual(ctx[tuple_id].element_types, (ctx.i32_id,))
        self.assertEqual(ctx[function_id].parameter_types, (ctx.i32_id,))
        size = len(tuple(ctx.items()))
        self.assertEqual(ctx.alloc_instance(left, [ctx.i32_id]), instance)
        self.assertEqual(ctx.alloc_tuple([ctx.i32_id]), tuple_id)
        self.assertEqual(ctx.alloc_function_pointer([ctx.i32_id], ctx.void_id), function_id)
        self.assertEqual(len(tuple(ctx.items())), size)
        self.assertFalse(ctx.is_same_type(ctx.c_int_id, ctx.i32_id))

    def test_ffi_alias_does_not_open_normal_function_permissions(self) -> None:
        result, _ = self.__analyze(
            "typedef CInt = c_int;\nfn main() { let x: CInt; }\n", ffi=True,
        )
        self.assertFalse(result.ok())
        self.assertIn("C ABI types require an ffi fn", result.formatted())


if __name__ == "__main__":
    unittest.main()
