# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

YIAN is a custom statically-typed programming language compiler written in Python. The full pipeline covers lexing through LLVM codegen and native executable output.

## Build / Run Commands

```bash
# Install dependencies
pip install -r requirements.txt

# Compile and run (produces build/a.out)
python3 -m compiler.main lib tests/array/access.an

# Compile with log output (per-stage summaries)
python3 -m compiler.main --log-spec "main=DEBUG" lib tests/array/assign.an

# Compile with full tracing
python3 -m compiler.main --log-spec "all=TRACE" lib tests/array/assign.an

# Dump specific channel
python3 -m compiler.main --log-spec "type_check.expr=TRACE" lib tests/array/assign.an

# Output LLVM IR only (no executable)
python3 -m compiler.main -t ll lib tests/array/assign.an
```

Intermediate products are written to `build/`:
- `build/tokens.txt` — token list
- `build/ast.txt` — AST dump
- `build/hir.txt` — HIR dump
- `build/cfg.txt` — CFG dump
- `build/ir.ll` — LLVM IR
- `build/compile.log` — log messages
- `build/a.out` — executable (exe target)

### Running Tests

```bash
# Quick smoke test on a single file
python3 scripts/test.py
```

**Test structure:**

- Test sources: `tests/<category>/<name>.an`
- Expected outputs: `tests/tests_results/<category>/<name>.an.ans` (contains expected return code or stdout)
- Error tests use `.err.an` suffix — compilation is expected to fail
- Multi-file tests (e.g., `op_overload/`, `private/`) are compiled with additional sibling files

## Language Syntax (2026 grammar overhaul)

The lexer is **whitespace-insensitive** — all structural boundaries use explicit delimiters.

### Statements and declarations

```yian
// Imports end with ;
from std.core.option import Option;
from std.core.ops import Index;

// Functions require 'fn' keyword, params use 'name: type'
pub fn calculate_growth_left(capacity: u64) -> u64 {
    return capacity * 3 / 4;
}

// Variable declarations use 'let name: type'
let x: i32 = 42;
let p: Point<i32> = Point<i32>.new(1, 2);

// Simple statements end with ;
return x;
break;
continue;
assert x > 0: "x must be positive";
```

### Structs, enums, traits

```yian
// Struct fields: 'name: type,' (comma-separated)
pub struct HashMap<K, V> {
    buckets: Bucket<K, V>*,
    ctrls: u8*,
    size: u64,
}

// Enum variants: comma-separated, fields use 'name: type,'
pub enum Option<T> {
    None,
    Some { val: T, },
}

// Trait methods: 'fn' keyword, decls end with ;
pub trait Hasher {
    fn finish() -> u64;
    fn write(bytes: u8[]);
}
```

### Control flow

```yian
// Match arms: pattern => { body }, comma-separated
match *self {
    None => { return Option<T>.None; },
    Some(val) => { return val; },
}

// Operators: &&, ||, ! (not 'and', 'or', 'not')
if x > 0 && y < 10 { ... }
return !self.buckets.is_null();
```

### Comments

```yian
// Line comment
/* Block comment — can span multiple lines */
```

## Architecture

The compiler pipeline in `compiler/`:

```text
.an source → Tokens → AST → (desugared) AST → HIR → CFG IR → LLVM IR → exe
```

Full pipeline documentation: `docs/manual/01.pipeline.md`

### 1. Frontend (`compiler/frontend/`)

- **Lexer** (`lex/lexer.py`): Hand-written character-at-a-time lexer. Whitespace-insensitive — no Space or Endl tokens are emitted. Uses whitespace context to distinguish `<`/`>` as generic brackets (`LAngle`/`RAngle`, no preceding whitespace) vs comparison operators (`Less`/`Greater`, preceding whitespace). Token types defined in `lex/token.py`.
- **Parser** (`parse/`): Hand-written recursive-descent Pratt parser. Split into sub-parsers:
  - `parser.py` — top-level program items (imports, functions, structs, enums, traits, impls, typedefs)
  - `parser_type.py` — type expressions
  - `parser_expr.py` — expressions (Pratt parser with operator precedence)
  - `parser_stmt.py` — statements and blocks
  - `stream.py` — `TokenStream` wraps token list for parser consumption
  - `operator.py` — `BinaryOperator`/`UnaryOperator` enums with dict-driven `try_from_token(token)` and precedence tables
  - AST node types are dataclasses in `ast.py`
  - **No backtracking**: `LAngle`/`RAngle` tokens make generic brackets unambiguous at the lexer level, eliminating need for lookahead/disambiguation in the parser.

### 2. Analysis (`compiler/analysis/`)

- **Desugaring** (`passes/desugar.py`): Transforms `for-in`, `while`, `assert`, `elif` chains, `a..b` ranges, and `x in y` member tests into simpler AST constructs. Runs immediately after parsing. Note: `not in` operator has been removed; use `!(x in y)` instead.

- **Global Resolution** (`passes/global_resolve.py`): First analysis pass. Three-phase: (1) collect top-level symbols from all units, (2) resolve imports across units (including `lib/` → `std::` namespace mapping), (3) resolve type definitions (struct fields, function signatures, trait methods). Registers everything into `TypeCtx`.

- **Type Checking** (`passes/type_check.py`): Worklist-driven pass starting from `main()`. Walks reachable code, lowering each `DefPoint` (instantiated function/method) from AST into HIR, performing type checking via `ExprChecker` and `StmtChecker`. Newly discovered callees are enqueued for later processing.

- **Type System** (`ty/`):
  - `context.py` — `TypeCtx` is the central type registry. Owns all type definitions (structs, enums, traits, functions, methods), the impl registry, generic inference, and type formatting.
  - `ty.py` — Type representation dataclasses (`IntType`, `StructType`, `EnumType`, `FnType`, `GenericType`, `TraitType`, etc.)
  - `type_ops.py` — Type unification, subtyping, coercion logic
  - `generic_inference.py` — Generic type parameter inference from call arguments
  - `impl.py` — Trait implementation registry, method dispatch

- **HIR** (`unit/hir.py`): High-level IR — a typed AST produced by type checking. Contains semantic information like resolved types and symbols, while still structured like the original program.

### 3. Code Generation (`compiler/codegen/`)

- **CFG IR** (`cfg/ir.py`): Control Flow Graph intermediate representation. SSA form with basic blocks, 22 instruction types, 5 terminator types. Documented in `docs/manual/10.cfg_ir.md`.

- **CFG Builder** (`cfg/builder.py`): Translates HIR function bodies to CFG IR. Handles control flow structuring (if/loop/match), short-circuit logical operators, assignment, and compound assignment. Documented in `docs/manual/11.cfg_builder.md`.

- **LLVM Codegen** (`llvm/`): Translates CFG IR to LLVM IR, then emits native code. Includes `LLTypeCtx` for Yian→LLVM type mapping, `LLTranslator` for instruction translation, and `Emitter` for target output. Documented in `docs/manual/12.llvm_codegen.md`.

### 4. Infrastructure

- **Log System** (`utils/log.py`): Structured, channel-based compiler logging. Configurable via `--log-spec` / `YIAN_LOG` with 6 severity levels and 11 named channels. Documented in `docs/manual/13.log.md`.

- **CLI & Build** (`main.py`): Entry point, argument parsing, build orchestration. All outputs default to `build/` directory. Documented in `docs/manual/14.cli_build.md`.

## Standard Library (`lib/`)

YIAN's standard library is written in YIAN itself (`.an` files):

- `lib/core/` — Fundamental types and operations: `convert`, `fmt`, `hash`, `io`, `iter`, `mem`, `ops`, `option`, `range`, `raw_vec`, `result`, `slice`, `str`, `string`, `vec`
- `lib/collections/` — `hash_map`, `hash_set`
- `lib/num/` — Numeric type specializations: `i8`–`i64`, `u8`–`u64`, `f32`, `f64`, `bool`

## Key Design Patterns

- **Whitespace-insensitive lexer**: The lexer emits `LAngle`/`RAngle` (no preceding whitespace → generic brackets) vs `Less`/`Greater` (preceding whitespace → comparison). This eliminates parser-level disambiguation for `a < b` vs `Type<T>`.
- **`>>` handling**: `>>` with preceding whitespace is lexed as a single `GreaterGreater` token (shift operator). Without preceding whitespace, it's two `RAngle` tokens (nested generic close). No more C++ `>>` ambiguity.
- **Dict-driven operator lookup**: `BinaryOperator.try_from_token(token)` and `UnaryOperator.try_from_token(token)` use module-level dicts keyed by `PunctuatorKind`. No match/case chains.
- **Handler maps**: The type checker uses `dict[type[T], Callable[[T], None]]` to dispatch on IR node types. When adding new IR nodes, register a handler.
- **Worklist-driven type checking**: Functions/methods are only type-checked when they become reachable from `main()`. This naturally handles generics (each monomorphized instance is a separate `DefPoint` enqueued on first use).
- **AST desugaring before analysis**: Syntactic sugar is lowered to simpler constructs before any semantic analysis runs. Each desugar pass is independent — the for-loop pass does not pre-process ranges, and the range pass does not know about for-loops.
- **`UnitData`** bundles a parsed program with its file path, unit ID, and symbol context — it is the unit of compilation passed between analysis passes.

## Code Style

### Imports

- `from __future__ import annotations` at the top of every file
- Standard library imports first, then third-party, then project imports
- `TYPE_CHECKING` guard for imports only needed for type annotations
- Use `from compiler.xxx import yyy` for project imports (not relative imports)

### Naming

- **Classes**: `PascalCase` — `TypeCtx`, `GlobalResolve`, `ExprChecker`
- **Functions/methods**: `snake_case` — `handle_call`, `check_block`
- **Private members**: double underscore `__` prefix — `__handle_named_call`, `__type_ctx`, `__process_for_loops`
- **Constants**: `UPPER_SNAKE_CASE` — `BUILTIN_EXPR_NAMES`
- **Variables**: `snake_case` — `type_id`, `src_files`

### Types

- Strict typing throughout (`pyright typeCheckingMode: "strict"`)
- Always annotate parameters and return types
- Use `| None` (not `Optional[...]`)
- Use `list[T]`, `dict[K, V]`, `tuple[A, B]` (not `List`, `Dict`, `Tuple` from typing)
- `TypeAlias` for union types: `GenericParam: TypeAlias = TypeGenericParam | ConstGenericParam`

### Dataclasses

- All AST nodes, IR nodes, and type representations are `@dataclass`
- Fields use type annotations without defaults for required fields
- Define `__repr__` for readable debug output (not `__str__`)
- AST nodes that changed field order in the grammar overhaul: `VarInfo(name, var_type)`, `FieldInfo(name, field_type)`, `VarDecl(name, var_type, init_expr)` — name comes before type.

### Match statements

- Preferred over if/else chains for dispatching on node types and enum variants
- Must be exhaustive — add `case _: pass` (or `case _: raise`) for unhandled cases

### Error handling

- `AnalysisError(message, span)` for user-facing errors (type errors, undefined symbols, etc.)
- `assert` for internal invariants, usually after `isinstance` checks for type narrowing
- Fatal errors (lex/parse errors) call `sys.exit(-1)` via `__print_source_error`

### Class structure

- Public methods first, private (`__`) methods after
- `__init__` stores dependencies as private attributes
- Handler map pattern: register `Callable` handlers keyed by node type

### Docstrings

- `"""triple-quoted"""` for classes and public methods
- Brief one-liner for simple cases; multi-line for complex logic
- Private methods may omit docstrings if the name is self-explanatory

### Comments

- Sparse; code should be self-documenting through good names and docstrings
- Comments explain "why", not "what"

## Project Conventions

- The `bak/` directory is explicitly ignored by git and pylint — it contains legacy/experimental code.
- The `build/` directory is used for intermediate compiler output (tokens, AST, HIR dumps). It is gitignored.
- `.conda/` is a local conda environment — do not modify.
- `scripts/migrate.py` and `scripts/migrate_syntax.py` are migration scripts used during the grammar overhaul. They may be useful as reference but are not part of the compiler.

## Rules for Coding

DO NOT USE:

- `Any`, `object` type
- `hasattr()`
- `import` that is not at the top of the file

## Documentation

When working on the compiler, consult these resources:

### Language reference
- `docs/grammar/index.md` — 15-chapter Yian language syntax reference (quick start, lexicon, types, functions, control flow, generics, stdlib, etc.)
- `docs/grammar/00.quick_start.md` — 13 compilable examples with source references

### Compiler internals
- `docs/manual/index.md` — 19-chapter development manual covering every module in the pipeline
- `docs/manual/01.pipeline.md` — Full pipeline diagram and `main()` flow
- `docs/manual/19.coding_style.md` — Python/Yian coding standards (import order, naming, types, class structure)

### Design documents (bak/)
- `bak/quest_log.md` — Log system design with 5-phase implementation plan
- `bak/quest_doc_refactor.md` — Documentation refactoring plan with per-chapter test references
- `bak/quest_code_audit.md` — Coding standard compliance audit report (P0/P1 fixed, P2 open)

### Key files for debugging
- `build/compile.log` — Default log output (INFO level by default, use `--log-spec "all=DEBUG"` for details)
- `build/tokens.txt`, `build/ast.txt`, `build/hir.txt`, `build/cfg.txt`, `build/ir.ll` — Per-stage dumps

## Lessons Learned

### "为什么" ≠ "修一下" — distinguish analysis from action

When the user shows an error and asks "why" or "明明已经X了为什么还有Y", they are asking for root cause analysis, not for a fix. Answer the question, trace the logic, and stop there. Do not start editing code unless explicitly asked.

### Don't fix what the user didn't ask to fix

Even when a fix seems obvious or "small", if the user didn't ask for it, don't do it. Each unrequested fix consumes review bandwidth, may introduce bugs, and signals that you're operating on your own initiative rather than following direction. The user will ask for a fix when they want one.

### Prefer lexer-level disambiguation over parser lookahead

When a token can have multiple meanings depending on context (e.g., `<` as generic bracket vs comparison), resolve it at the lexer level using available context (like whitespace) rather than in the parser with forward-scan + backtrack. This eliminates complex lookahead code and makes both lexer and parser simpler. The `LAngle`/`RAngle` vs `Less`/`Greater` design is an example of this principle.
