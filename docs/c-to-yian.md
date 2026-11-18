# C to YIAN syntax rewrite rules

This guide uses the repository's C programs as references and explains how
C-specific constructs are rewritten in YIAN for the paper experiments.
It is the single maintained specification of these rules; case READMEs and
source comments record their applications and representation differences.

## 1. Syntax with direct counterparts

| C | YIAN | Notes |
| --- | --- | --- |
| `int32_t x = 1;` | `let x: i32 = 1;` | Preserve numeric width and signedness |
| `float`, `double` | `f32`, `f64` | Preserve width, IEEE 754 operations and rounding |
| `int32_t f(int32_t x)` | `fn f(x: i32) -> i32` | Keep application functions as functions, without moving them into impl methods; existing standard-library methods may still be reused |
| `if (...)`, `else if`, `while (...)` | `if ...`, `elif ...`, `while ...` | Conditions must be `bool` |
| Arithmetic, comparisons, bit operations, `&&`, `||` | Corresponding operators | Preserve types, operation order and short-circuit behavior |
| Statements such as `x = y;`, `x += y;` | Corresponding assignments | Expand assignments used as subexpressions |
| `a[i]`, `&x`, `*p`, pointer addition/subtraction | `a[u64(i)]`, `&x`, `*p`, `p + u64(n)` / `p - u64(n)` | Integer operands of indexing and pointer arithmetic must be u64 |
| `p->field` | `p.field` | YIAN field access supports automatic dereferencing |
| Structures, fields and ordinary type aliases | `struct`, fields and `typedef` | Rewrite declaration syntax while preserving field meanings and object relationships |

Declaration details are described in [types](grammar/02.type_system.md),
[variables](grammar/03.variables.md) and [structures](grammar/11.structs_enums.md).
Functions such as `use` and `skip` below stand for original C calls and must
be replaced with the corresponding implementations.

## 2. Logical flags and enumerations

A C `int` used only as a Boolean flag may become `bool`, including array
elements and structure fields; replace corresponding 0/1 writes with
false/true. For example:

```c
int32_t enabled = 0;
enabled = 1;
if (enabled) { use(); }
```

```yian
let enabled: bool = false;
enabled = true;
if enabled { use(); }
```

Use the corresponding integer type for C numeric enumerations that participate
in arithmetic, bit operations, protocol encoding or operations depending on
their underlying values (i32 for ordinary C int enumerations). For example:

```c
enum mode { MODE_EDIT = 1, MODE_SHELL = 2 };
int32_t flags = MODE_EDIT | MODE_SHELL;
```

```yian
const MODE_EDIT: i32 = 1;
const MODE_SHELL: i32 = 2;
let flags: i32 = MODE_EDIT | MODE_SHELL;
```

Use a YIAN `enum` when only variant identity matters:

```c
enum state { STATE_IDLE, STATE_RUNNING };
enum state current = STATE_IDLE;
if (current == STATE_RUNNING) { work(); }
```

```yian
enum State {
    Idle,
    Running,
}

let current: State = State.Idle;
if current == State.Running { work(); }
```

## 3. Storage duration, state and read-only constants

Preserve storage duration and storage class except for the rules below for
mutable static objects and read-only static variables.

**Mutable static objects become heap allocations.** Rewrite mutable C variables
with static storage duration, including file-scope objects and function-local
statics, as heap allocations. Allocate once at program entry or in a state
owner whose lifetime covers all users. Pass references/slices into functions
and reuse them across calls. Preserve initial values, zero initialization,
explicit resets, aliases and shared state. Do not allocate inside each call
or recursion, or free storage while users remain. If cleanup is required,
free after the last use. This rule permits static storage to become heap
storage; it does not move C automatic objects to the heap.

```c
static struct stats {
    int32_t hits;
    int32_t misses;
} counters;

void record(int32_t hit) {
    if (hit) { counters.hits += 1; } else { counters.misses += 1; }
}
```

```yian
struct Stats {
    hits: i32,
    misses: i32,
}

fn record(counters: Stats&, hit: bool) {
    if hit { counters.hits += 1; } else { counters.misses += 1; }
}

fn main() {
    let counters: Stats[] = dyn[1] Stats(0, 0);
    record(&counters[u64(0)], true);
    record(&counters[u64(0)], false);
    del counters;
}
```

Determine mutability from the object itself: the pointer slots in
`static const char *headers[]` are mutable, so the array requires heap
allocation, while its strings remain read-only constants.
`static const char *const headers[]` is not a mutable static object.

**Read-only static variables become constants of corresponding types.**
Rewrite read-only C variables with static storage duration, including
file-scope objects and function-local `static const` variables, as YIAN
module-level constants of corresponding types. For example:

```c
static const uint16_t limit = 4096;
static const uint16_t table[4] = {1, 2, 3, 4};
```

```yian
const LIMIT: u16 = 4096;
const TABLE: u16[4] = [1, 2, 3, 4];
```

Preserve type, bit width and signedness. For arrays, also preserve dimensions,
lengths, element order and complete contents, including implicit zero filling
and any actual trailing NUL. Represent arbitrary byte input as `u8[N]`;
do not change its bytes through Unicode strings. Initializers must satisfy
YIAN compile-time evaluation rules; see [module-level constants](grammar/03.variables.md)
for supported types and nested arrays.

This rule permits a read-only static variable to become a constant value and
the local storage needed by its uses. YIAN constants are copied by value at
use sites; borrowing promotes them to local storage under the temporary-value
rules. There is no corresponding runtime static object. When several users
require shared storage, create one local copy in a scope covering those users
and share references or slices. References must not escape that storage's
lifetime. Repeated references to a constant do not necessarily share an object.
If a program depends on address identity across calls or static storage
duration, or its type/initializer exceeds constant support, report the
specific limitation. Identical contents alone do not establish a completed rewrite.

Report the specific limitation for a read-only static variable that cannot be
represented as a constant. These rules permit reorganizing C state while
preserving the required program behavior.

## 4. Pointer representations, NULL and non-Boolean conditions

Choose the YIAN representation according to the C pointer's use:

| C pointer use | Non-null representation | Nullable representation |
| --- | --- | --- |
| Points to one object | `T&` | `Option<T&>` |
| Points to contiguous objects without pointer arithmetic | `T[]` | `Option<T[]>` |
| Requires pointer addition, movement or differences | `T*` | `Option<T*>` |

Aliases to the same object must still refer to the same storage. Choosing a
reference or slice does not copy the object. Use the original object's actual
extent as the slice length; do not shrink or enlarge it to change access results.

### NULL becomes Option

Represent NULL as `None` and an actual object or region as `Some(value)`
in the selected representation. For a single-object pointer:

```c
int32_t *p = NULL;
p = &value;
if (p == NULL) return 0;
return *p;
```

```yian
let p: Option<i32&> = Option<i32&>.None;
p = Option<i32&>.Some(&value);
match p {
    None => { return 0; },
    Some(ptr) => { return *ptr; },
}
```

| C operation | YIAN construct |
| --- | --- |
| `p == NULL`, `!p` | `p.is_none()` |
| `p != NULL`, `if (p)` | `p.is_some()` |
| `*p` or `p->field` in a non-null branch | `match p` or `if let Some(ptr) = p`, using the payload reference, slice or pointer |
| Return NULL / return a pointer | Return the selected `Option<...>.None` / `Option<...>.Some(value)` |
| `T **` pointing to a mutable nullable pointer slot | Reference the slot, e.g. `Option<T&>&`; choose the slot's inner representation by its use, preserving the distinction between slot address and stored value |

Wrapping and destructuring change representation without copying pointees or
removing aliases. Do not unconditionally `unwrap()` a possible `None`:
this introduces a panic with a different diagnosis and triggering operation
from a memory-safety guard. If the target C error is a NULL dereference,
describe how YIAN blocks it separately; do not claim the same memory-access
guard was reached. A handwritten panic, early exit or error at another
operation does not count as the corresponding operation's safety guard.
Equivalence is not required after C enters undefined behavior.

### Integer, byte and short-circuit conditions

Rewrite C `if (n)` and `while (*p)` as `if n != 0` and
`while *p != 0`; rewrite `!n` as `n == 0`.
Expand `p && p->field` as:

```yian
if let Some(ptr) = p {
    if ptr.field != 0 { use(ptr); }
}
```

Call `use` only when the original condition is true; do not read fields in
the null branch. Preserve every false path when the original has an `else`,
not just the successful branch. Preserve short-circuit evaluation order on
both sides of `&&` and `||`.

## 5. Three-part for becomes while

Expand `for (init; cond; step) { body }` into initialization and a `while`
inside a local scope. The order is: initialize once, test the condition,
execute the body, update, then test again.

```c
for (int32_t i = 0; i < n; ++i) {
    if (skip(i)) continue;
    if (stop(i)) break;
    use(i);
}
```

```yian
{
    let i: u64 = 0;
    while i < u64(n) {
        if skip(i) {
            i += 1;
            continue;
        }
        if stop(i) { break; }
        use(i);
        i += 1;
    }
}
```

- Execute the complete update once before every `continue` targeting this
  C `for`. Leave a nested loop's own `continue` unchanged. Preserve the
  internal order of complex update expressions.
- `break` and `return` do not execute the update. Do not substitute
  `defer step()`, which also runs on `break` and `return`.
- Reevaluate `cond` on each iteration. Use `loop` for an omitted condition.
  Do not invent operations for omitted initialization or update clauses.
- Keep a variable declared in the C loop header inside the new block. For an
  existing variable, preserve its value and visibility after the loop.
- An unbraced C loop controls only the next statement. Determine its body
  from syntax, without extending it based on indentation.
- Use u64 loop indices. The example assumes a nonnegative n. If a signed
  bound can be negative, preserve the original condition's truth value first;
  do not convert a negative bound directly to a huge u64. A decrementing loop
  cannot retain `i >= 0`; exit after processing zero to avoid unsigned
  wraparound. See Section 11 for index/offset conversions, and preserve the
  exit behavior of negative sentinels in loop control separately.

A nonnegative counting loop `for (int32_t i = 0; i < n; ++i)` may become
`for i in 0u64..u64(n)` only if the bound stays constant during the loop,
the step is one, the body does not modify the counter, and the counter's
post-loop value is unused. A range's upper bound is evaluated when the range
is constructed; it cannot replace a C condition that changes each iteration
or has side effects.

## 6. do-while becomes while with a first-iteration flag

C `do { body } while (cond);` executes the body at least once.
A `continue` jumps to the condition; a `break` exits directly. Use:

```c
do {
    if (skip()) continue;
    work();
} while (again());
```

```yian
{
    let first: bool = true;
    while first || again() {
        first = false;
        if skip() { continue; }
        work();
    }
}
```

Short-circuit evaluation skips `again()` on the first iteration. Thereafter
it is evaluated only after normal body completion or `continue`; `break`
and `return` do not evaluate it. Choose a fresh `first` name that does not
shadow an original variable. Without `continue`, the form
`loop { body; if !cond { break; } }` is also valid. With `continue`, that
form would incorrectly skip the condition.

## 7. Increment, decrement, assignment and comma expressions

Rewrite standalone `i++` and `++i` as `i += 1`, and decrements as
`i -= 1`. In expressions, distinguish old and new values explicitly and
evaluate the original operand only once:

| C | YIAN expansion |
| --- | --- |
| `x = i++;` | `let old = i; i += 1; x = old;` |
| `x = ++i;` | `i += 1; x = i;` |
| `if (--count == 0)` | `count -= 1; if count == 0` |
| `x = *p++;` | `let old = p; p += u64(1); x = *old;` |
| `a = b = value;` | `let assigned = value; b = assigned; a = assigned;` |
| `(f(), g())` | Call `f();` first, then use the result of `g()` |

For complex lvalues such as `a[index()]++`, save the index or address first
to avoid repeated evaluation. Decompose side effects only where C determines
their order; an arbitrarily chosen order for unsequenced C side effects does
not establish equivalence. Keep assignments on a short-circuit right-hand
side inside the corresponding branch. For example:

```c
while ((c = next()) != 0) { use(c); }
```

```yian
loop {
    c = next();
    if c == 0 { break; }
    use(c);
}
```

## 8. Conditional expressions become if expressions

```c
int32_t value = cond ? left() : right();
```

```yian
let value: i32 = if cond { left() } else { right() };
```

Convert integer/pointer conditions to Boolean conditions under Section 4.
Evaluate only the selected branch; do not call both branch functions in
advance. Explicitly preserve the numeric conversions performed by C's
conditional expression in the YIAN branches.

## 9. switch becomes match with explicit fall-through expansion

A `switch` whose branches end with `break` becomes `match`; C's
`default` becomes `_`. Several labels may share an arm, e.g.
`0 | 1 => ...`. Remove a `break` that only exits the switch; do not turn
it into a break from an enclosing loop. Evaluate the selector once.

With fall-through, do not independently map each case to an arm. For example:

```c
switch (kind()) {
case 0: first(); /* fall through */
case 1: second(); break;
default: other(); break;
}
```

```yian
match kind() {
    0 => { first(); second(); },
    1 => { second(); },
    _ => { other(); },
}
```

The branch code is duplicated, but runtime call counts remain the same as C.
For conditional fall-through, shared locals or jumps within branches,
restructure control flow while preserving operation order and exit targets
after entry at each label.

## 10. goto and cleanup paths

YIAN has no direct counterpart to C labels and `goto`. Forward jumps to a
shared cleanup section can use a single-iteration `loop` with `break`:
both normal and error paths leave that block before executing the original
cleanup. Multiple jump targets may use state variables with `loop`/`match`.
Preserve skipped application operations, return values and required cleanup
results for each jump.

Use `defer` only when cleanup runs on the same scope-exit paths. It reads
variables' latest values at exit and executes in reverse registration order.
Safety errors and abnormal termination do not execute cleanup, so it cannot
mechanically replace every `goto cleanup`. See
[control flow](grammar/06.control_flow.md) for language behavior.

Cleanup may be rewritten by function; individual frees or traversal order
need not match internally. Preserve required cleanup results and the
vulnerability's freed object, invalidated aliases and subsequent accesses.
Do not replace freeing a pointer slot's address with freeing its stored pointer,
or clear aliases early to remove the trigger. Preserve a specific cleanup
order when program behavior depends on it.

## 11. C casts, arrays and strings

- Rewrite numeric casts such as `(uint16_t)x` as `u16(x)`. Preserve cast
  position and truncation width; do not replace narrowing before computation
  with widening before computation to avoid overflow. Explicitly add the
  conversions required by C integer promotion and mixed-sign comparisons.
- Integer conversions reduce modulo the target width. Negative values become
  unsigned through two's-complement interpretation, e.g. `(size_t)-1`
  becomes `u64(-1)`. Unsigned arithmetic wraps modulo 2^N, as do YIAN
  `u32`/`u64` runtime operations. Signed overflow is undefined in C and
  needs no equivalent behavior. Arithmetic overflow in constant expressions
  is a compile error under Section 3; do not use constants to reproduce wrapping.
- Map byte-valued C `char` to `i8` or `u8` according to signedness.
  YIAN `char` is a Unicode code point and cannot directly replace a byte
  buffer. Use `0u8` for a NUL byte.
- Map C stack arrays `uint8_t a[N]` to fixed YIAN arrays
  `let a: u8[N] = ...;`, without moving them to the heap. If initialization
  must be added, explain why the added values are not read first on the
  original path. Explicitly use `&a[u64(0)]` when an initial element pointer
  is needed and an element actually exists. Do not add an element access
  merely to obtain the address of a zero-capacity array.
- Rewrite aggregate initializers `T x = {a, b};`, `T x = {.field = v};`
  and `T x = {0};` using `T(a, b)` or `T(field=v)`. Supply every field
  by order or name, zero-filling omitted C fields. Write array initializers
  as `[...]` in element order, completing partial initializers with zeros
  and explaining why added values are not read first on the original path.
- C string literals include a trailing NUL; YIAN `str` does not add it.
  For C byte sequences, use `let text: str = "abc\0";` with
  `text.as_slice()`, or an explicit `[97u8, 98, 99, 0]` array.
  Preserve read-only/writable use; writable stack strings use fixed byte
  arrays. Library-internal string representation and scanning may be
  simplified under Section 12. Translate explicit C access expressions
  according to their original operations.
- Rewrite `sizeof(T)` as `@sizeof<T>`; check byte counts and element
  counts separately. Physical size changes from fat pointers cannot simply
  replace C protocol lengths, offsets or allocation formulas. Pointer
  reinterpretation, `void*` and integer addresses have no universal template:
  establish a typed representation for actual accesses and preserve objects,
  offsets and access widths.

For dynamic arrays supporting both raw and fat modes, first allocate with
`let allocation = dyn[n] value;`, then form a full-range slice with
`let view: T[] = allocation[0u64..u64(n)];`. Raw mode does not support
implicit conversion directly from a general pointer to a slice.

Translate fixed C character arrays according to their actual initialized
bytes. A length just sufficient for the text may exclude its literal's final
NUL, as in revcomp's 387-byte input.

### Integer types for indexing and pointer arithmetic

Integer operands of indexing and pointer addition/subtraction must be `u64`.
Convert the source integer expression explicitly at its use:
`a[i]` becomes `a[u64(i)]`, `p + n` becomes `p + u64(n)`, and
`p - n` becomes `p - u64(n)`. Variables used directly, including loop
indices, must themselves be `u64`.

Compute the integer value in the original C expression's calculation and
conversion order, then convert at the index/offset use. For example,
`a[n - 1]` becomes `a[u64(n - 1)]`, without moving the conversion to
`a[u64(n) - 1]`. Negative integers follow the same rule; obtaining a large
unsigned value does not itself violate the rewrite rule. Actual out-of-bounds
operations are handled by YIAN checks. A pointer difference `p - q` has no
integer offset operand and is outside this conversion rule.

### C attributes, unions and callback structures

These constructs may be rewritten by function. Preserve an attribute's effect
when the program or experiment explicitly depends on it.

| C construct | YIAN rewrite | Required effect |
| --- | --- | --- |
| Packed structure | Parse bytes at fixed offsets, or use suitable fields | Preserve protocol byte meanings, widths and offsets; the in-memory structure need not also be packed |
| Union or tag with several fields | Use an enum or another typed representation | Preserve required variants and data; if overlapping bytes are read, preserve their meanings |
| Function pointer and void* callback context | Ordinary functions, function pointers or explicit context parameters | Preserve dispatch, arguments, state updates and call timing; application functions remain functions |
| Compiler attributes such as inline/noinline | Handle according to program and experiment needs | If a call-overhead experiment depends on a real call boundary, verify it remains; equal results alone are insufficient |

Unused attributes or branches do not require the same underlying mechanisms.
Record the chosen representation and experimental dependencies; functional
substitution does not establish identical layouts or generated code.

## 12. Library functions, dynamic containers, allocation and C entry points

C library functions need only support the functionality and inputs used by
the program; complete interfaces or identical internal operations are not
required. Prefer existing YIAN standard-library functionality and otherwise
provide sufficient helpers. Omitting unused branches or changing internal
scanning/representation does not itself violate the rewrite rules.
Substitutes must support the actual inputs, outputs and required side effects.
Preserve the target behavior used for evaluation when library code performs
the triggering read, write or free.

Dynamic containers need not match C capacity, growth strategy, layout or
individual allocations/frees, provided they meet program needs. Reuse
standard containers such as `Vec` and `String` where practical. Check
required elements, operations, aliases and resource handling; different
allocation counts or capacities alone are not violations. Container
substitution does not permit changing surrounding objects' storage duration;
see Section 3 for mutable statics. It also must not copy or clear aliases
to remove the required vulnerability trigger.

| C | Candidate YIAN construct | Required behavior |
| --- | --- | --- |
| `malloc(n * sizeof(T))` | `dyn[n] initial_value` or a suitable standard container | For standalone buffers, check count, element type and aliases; apply the functional rules above to containers |
| `calloc(n, sizeof(T))` | `dyn[n] zero_value` | Supply a typed zero value and check count and byte meaning |
| `free(p)` | `del p` | Release the same allocation without deep copying or clearing other aliases |
| `free(p)` for a nullable object/region | `if let Some(value) = p { del value; }` | Do nothing for NULL; do not reset to None after freeing and thereby remove the required dangling state |
| `int main(...)` | `fn main()` with the original application entry | Read arguments through the environment API; preserve ordinary exit codes with `exit(code)` as needed |

`dyn[0]` allocates zero elements: every element read/write fails, while
`del` still frees it. Results of C `malloc(0)`, `calloc(0, n)` and
`realloc(p, 0)` are implementation-defined. For example, glibc's
`malloc(0)` returns non-NULL, while `realloc(p, 0)` frees the old block
and returns NULL. Choose according to the case's actual dependency and record
it in the README. Preserve the distinction between a zero-capacity allocation
and NULL.

Allocation failure in `dyn` terminates the process rather than returning
NULL. Ordinary experiment paths may assume successful allocation. If failure
participates in the original C error-cleanup path, explicitly preserve that
state and subsequent operations instead of deleting the branch.
`realloc` may use allocate/copy/free/update or container operations without
reproducing its exact in-place growth or relocation choices, provided required
behavior is preserved. Retain the vulnerability's release, old-alias
invalidation and subsequent access. Match copied ranges and allocation-failure
handling where the program actually needs them.

Replace `#include` and function declarations with existing package/module
`from ... import ...` declarations. Simple numeric macros become
module-level `const`. For function-like macros, preserve actual evaluation
counts after expansion; do not directly replace repeated evaluation with a
single-evaluation function call. These organizational changes do not change
the original computation or storage duration.

Read command-line arguments with `std.core.env.args()`; do not silently
replace runtime arguments with source constants:

```yian
from std.core.env import args, exit;

fn main() {
    let argv = args();
    let iterations: u64 = 10;
    if argv.len() > 1 {
        match u64.parse(argv[u64(1)].as_str()) {
            Ok(value) => { iterations = value; },
            Err(_) => { exit(2); },
        }
    }
    // Execute the workload using iterations.
    for i in 0u64..iterations { /* original body */ }
    for argument in &argv { argument.drop(); }
    argv.drop();
}
```

Replace the example's default value and error code with the original program's
conventions. `args()` includes the program name. The caller releases the
returned strings; `Vec.drop()` does not automatically free their String buffers.

### Formatted output: printf subset to interpolation specifications

For fixed field representations, prefer interpolation specifications over
handwritten rounding, zero-padding or space loops for `printf` calls.
The supported subset covers fixed-point decimals, minimum width, left/right
alignment and numeric zero-padding:

| C | YIAN | Notes |
| --- | --- | --- |
| `printf("%.9f\n", value)` | `@println(f"{value:.9f}")` | Round the exact binary value to nearest, ties to even; preserve negative zero's sign |
| `printf("%08.2f", value)` | `f"{value:08.2f}"` | Width is a minimum; the sign precedes zero-padding; longer output is not truncated |
| `printf("%5d", value)` | `f"{value:5}"` | Numbers are right-aligned by default; `<` selects left alignment |
| `printf("%-5s", text)` | `f"{text:5}"` | Text is left-aligned by default, with width counted in Unicode scalars |
| `printf("%*s", width, "")` for spaces | `f"{empty:>{width}}"` | Dynamic width comes from a `u64` or signed integer argument |

The `f` specifier applies only to `f16`/`f32`/`f64`; integers,
`char`, `str` and `String` accept only width and alignment. Maximum
precision is 1024 and maximum width is 65535. Integer bases, explicit plus
signs/prefixes, arbitrary fill characters, centering, string truncation and
terminal-column width are unsupported. Out-of-range literals are compile
errors; dynamic arguments are checked before conversion, and invalid values
return `IoErrorKind.InvalidInput`. This subset does not promise full `printf`
or locale behavior. Use the default shortest floating-point representation
when scientific notation is needed.
