from __future__ import annotations

from dataclasses import dataclass, field
from typing import TypeAlias

from compiler.analysis.ty.ty import EnumVariant
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.runtime_error import RuntimeErrorCode

# ---------------------------------------------------------------------------
# Statements
# ---------------------------------------------------------------------------


@dataclass
class VarPtr:
    """Address a local slot and synthesize <data=a_x, word=(e_f,k_f), index=0, size=1>.

    data is the slot address a_x; word packs the current frame's lock position and key,
    materialized on function entry. index = 0 and size = 1: taking an address always targets
    a single element, whether its type is scalar T or array T[m].
    Raw mode uses an 8 B pointer without frame locks; frame_word/frame_key are None.
    """
    result: Reg
    var_ref: VarRef
    frame_word: Value | None  # Current frame word, materialized on entry; None in raw mode.
    frame_key: Value | None   # Compatibility input field; fat-pointer identity is encoded in frame_word.
    raw: bool = False         # Lazy lvalue path: taking an unaddressed lvalue's address returns a stack address without a fat value.


@dataclass
class Alloca:
    """Store a temporary value in the stack.

    Fat address-taking temporaries carry the current frame lock; ordinary
    internal address materialization remains explicitly raw.
    """
    result: Reg
    value: Value
    frame_word: Value | None = None
    frame_key: Value | None = None
    raw: bool = False


@dataclass
class FieldPtr:
    """Given ptr to a struct/tuple, get pointer to a field of it"""
    result: Reg
    base: Value
    field_index: int


@dataclass
class ElementPtr:
    """Given ptr, get ptr + offset"""
    result: Reg
    base: Value
    offset: Value


@dataclass
class PtrDiff:
    """Get the offset between two pointers"""
    result: Reg
    lhs: Value
    rhs: Value


@dataclass
class Load:
    """Load value from pointer"""
    result: Reg
    ptr: Value


@dataclass
class Store:
    """Write value to a pointer"""
    ptr: Value
    value: Value


@dataclass
class Malloc:
    """Allocate memory on the heap.

    Fat-pointer CFG semantics: allocate a lock-header/payload block and write its key,
    mu<e> := k (k <- Gen(), heap key MSB 1; lock slot is the block's first word, BlockHeader).
    LLVM constructs <data=b+H, word=(e,k), index=0, size=n>.
    ZST pointees use the fast path (undef, no slot write, key=None).
    """
    result: Reg
    type_id: int
    size: Value
    key: Value | None = None  # k <- Gen(); None selects the ZST fast path (undef, no lock-slot write).


@dataclass
class Realloc:
    """Resize a root heap allocation to ``size`` elements, preserving its prefix."""

    result: Reg
    type_id: int
    ptr: Value
    size: Value


@dataclass
class Binary:
    """Binary operation"""
    result: Reg
    op: BinaryOperator
    lhs: Value
    rhs: Value


@dataclass
class Unary:
    """Unary operation"""
    result: Reg
    op: UnaryOperator
    operand: Value


@dataclass
class ExtractValue:
    """Extract a field from a struct/tuple"""
    result: Reg
    base: Value
    field_index: int


@dataclass
class EnumIsVariant:
    """Test a tagged or niche enum value through its address."""
    result: Reg
    address: Value
    variant: EnumVariant


@dataclass
class EnumPayloadFieldPtr:
    """Address one field of the selected enum payload."""
    result: Reg
    address: Value
    payload_type: int
    field_index: int


@dataclass
class Delete:
    """Delete a pointer"""
    ptr: Value


# ---------------------------------------------------------------------------
# Check insertion and lock-slot nodes: CFG chooses check sites; LLVM emits checks and runtime failures.
#
# Runtime checks cover FieldPtr/ElementPtr/PtrDiff/Load/Store/Delete
# and CheckViewAccess for external I/O.
# LLVM lowers each check to a diagnostic and exit code 1 when its precondition fails.
# This file defines node presence and semantics; LLTranslator implements their lowering.
# Lock-slot interactions: LLVM writes key/anchor for Malloc and increments the generation for Delete.
# ---------------------------------------------------------------------------


@dataclass
class GenKey:
    """`k_f <- Gen()`: a monotonic 32-bit key for re-keying on frame entry.

    Only frame locks use this counter; heap identity lives in lock entries updated on allocation/release.
    Reaching FRAME_KEY_LIMIT fails deterministically (R003) without wrapping. LLVM emits global
    counter load/add/store operations and masking.
    """
    result: Reg


@dataclass
class AcquireFrameLock:
    """Acquire a frame-lock slot from the stable shadow stack and write its frame word.

    Depth reaching FrameLockArena.SLOTS reports a resource error and terminates. On success, result
    is the slot's full word (the new key, also carried in the pointer word's high 32 bits).
    LLVM records the slot address for SENTINEL writes on return paths.
    """
    result: Reg
    key: Value


@dataclass
class CheckSafeAccess:
    """Precheck safe_access(p,1) = live(p) and in_bounds(p,1).

    Used for Load/Store. live compares slot keys and is false for word=0;
    in_bounds requires 0 <= index and index+1 <= size. LLVM emits the check.

    live=False means the slot is known to match the key (a pointer into the current frame, whose slot
    changes only on entry/return). Emit only bounds checks, with the same error codes.
    """
    ptr: Value
    live: bool = True


@dataclass
class CheckViewAccess:
    """Validate a slice/str before a trusted external memory operation.

    The view carries its operation span in the size field.  LLVM additionally
    checks heap views against the active allocation extent recorded in the
    block header; stack/global views rely on their live lock and constructor
    range invariant.

    ``live=False``:the call site knows the view belongs to the current frame (the slot matches its key)。
    """
    view: Value
    live: bool = True


@dataclass
class CheckInBounds:
    """in_bounds(p_s,1), the precondition for FieldPtr re-anchoring.

    Taking a field of a one-past-end s reports a safety error. LLVM emits the check.
    """
    ptr: Value


@dataclass
class CheckSliceNonEmpty:
    """Require a fat ``T[]`` value to contain at least one element.

    Inserted before ``T[] -> T&`` degradation.  A reference carries no
    bounds metadata, so its source must establish the single-element spatial
    invariant before the size field is dropped.
    """
    ptr: Value


@dataclass
class CheckRefAccess:
    """T& access precheck: live(r) only, without bounds metadata.

    References always address a single element and have no arithmetic/ordering/delete operations.
    Their two fields <data,word> omit index/size, so access only compares the lock key
    (word=0 makes live false). Used at T& Load/Store/FieldPtr sites.
    """
    ptr: Value


@dataclass
class CheckElementArith:
    """ElementPtr arithmetic validity check: 0 <= index+n <= size.

    Crossing one-past-end or the lower bound reports a safety error. No-wrap is enforced
    through u64 overflow detection (icmp uge sum,index), emitted by LLVM.
    """
    base: Value
    offset: Value


@dataclass
class CheckElementAccess:
    """Combined check: ElementArith, InBounds, and SafeAccess predicates.

    For elem = base + offset (ElementPtr), f = elem.field (FieldPtr), and an adjacent Load/Store of f,
    a matched derivation chain can combine the three checks into one node:
    valid(elem) (u64 no-wrap and upper bound) and in_bounds(elem,1)
    (taking a field from one-past-end reports a safety error) and live(elem).
    Re-anchored field pointers have in_bounds(f,1) true and inherit the lock,
    so live(f)=live(elem). Neither no-wrap nor live may be omitted.
    Nonadjacent access retains its own SafeAccess check. LLVM emits the combined predicate.
    """
    base: Value
    offset: Value
    ptr: Value  # elem: the ElementPtr result, carrying the derived index/size/word.
    live: bool = True  # False omits live when it is known true for a pointer into the current frame.


@dataclass
class CheckRawBounds:
    """Raw-array bounds check: index < length, where length is compile-time constant.

    Unaddressed array access creates no fat pointer or index/size metadata, so it cannot carry
    CheckElementArith / CheckSafeAccess. One unsigned comparison index < N matches
    their combined fat-path semantics: 0 <= index <= N and index+1 <= N.
    The index is already coerced to u64 and cannot be negative.
    LLVM emits the check.
    """
    index: Value
    length: int


# ---------------------------------------------------------------------------
# Check request markers: lowering records semantics; the insertion pass chooses the final form.
# ---------------------------------------------------------------------------

CHECK_REQUEST_PTRDIFF = "ptrdiff"        # operands=[lhs, rhs]
CHECK_REQUEST_PTRCMP = "ptrcmp"          # operands=[lhs, rhs]
CHECK_REQUEST_RAW_BOUNDS = "raw_bounds"  # operands=[index], extra=compile-time length.
CHECK_REQUEST_VIEW = "view"              # operands=[view], live=whether to check lifetime.
CHECK_REQUEST_SLICE_NONEMPTY = "slice_nonempty"  # operands=[ptr]
CHECK_REQUEST_IN_BOUNDS = "in_bounds"    # operands=[ptr]
CHECK_REQUEST_RECEIVER_IN_BOUNDS = "receiver_in_bounds"  # operands=[ptr], for call-site receiver conversion.
CHECK_REQUEST_ELEMENT_ARITH = "element_arith"    # operands=[base, offset]


@dataclass
class LiveKnownBegin:
    """Begin a fresh-allocation window: pointers derived from root are known live inside it.

    Windows cover closed regions without del/calls, such as the fill loop for dyn[n] value.
    The insertion pass pairs Begin/End markers and never extends a window across functions.
    """
    root: Value


@dataclass
class LiveKnownEnd:
    """End a fresh-allocation window, paired with LiveKnownBegin; no del occurs inside."""
    root: Value


@dataclass
class CheckRequest:
    """Check request marker: lowering emits markers; passes/insert_checks.py chooses checks.

    operands/extra retain semantic facts not recoverable from IR alone: array lengths, view bounds,
    conversion preconditions, and source spans. The pass materializes, combines, and hoists checks.
    """

    kind: str
    operands: list[Value]
    extra: int = 0
    live: bool = True

@dataclass
class CheckPtrDiff:
    """PtrDiff preconditions: equal data, valid arithmetic, and no wrap.

    Differences between objects report a safety error. This operation does not access memory,
    so it does not check allocation liveness; dereferencing the result performs access checks. LLVM emits it.
    """
    lhs: Value
    rhs: Value


@dataclass
class CheckPtrCmp:
    """Ordering precondition: equal data.

    Cross-object ordering reports a safety error in LLVM. Equality compares (data,index)
    without this check. Pointer comparison does not access memory and therefore
    does not check allocation liveness.
    """
    lhs: Value
    rhs: Value


@dataclass
class PtrCmp:
    """Pointer comparison.

    Equality compares (data,index); ordering compares index under CheckPtrCmp's precondition.
    LLVM has no aggregate icmp, so pointer lowering extracts fields and checks preconditions.
    """
    result: Reg
    op: BinaryOperator
    lhs: Value
    rhs: Value


@dataclass
class CheckDelete:
    """Delete preconditions: is_heap(p), live(p), and is_raw(p).

    is_heap tests word's table index; live compares keys; is_raw verifies that data matches
    the allocation anchor and index = 0. Double release, stack release, offset release,
    and null release report safety errors. LLVM emits the check.
    """
    ptr: Value


@dataclass
class Call:
    """Call with a return value"""
    result: Reg
    callee_type: int  # type id of the function/method
    args: list[Value]


@dataclass
class Invoke:
    """Invoke a callable value (function pointer, closure, etc.) with a return value"""
    result: Reg
    callee: Value
    args: list[Value]


@dataclass
class TraitObjectConstruct:
    """Build a non-owning trait-object handle for a concrete implementation."""

    result: Reg
    reference: Value
    concrete_type_id: int
    trait_type_id: int


@dataclass
class TraitObjectInvoke:
    """Call one vtable slot using the trait method's dynamic ABI."""

    result: Reg
    receiver: Value
    trait_type_id: int
    method_id: int
    slot_index: int
    args: list[Value]


@dataclass
class Cast:
    """Cast a value to a different type

    - integer/char <=> integer/char
    - integer/float <=> integer/float
    - pointer <=> pointer
    """
    result: Reg
    value: Value
    to_type: int
    raw: bool = False  # Lazy lvalue path: raw pointer cast (array decay T[N]* -> T* bitcast, without fat synthesis).


@dataclass
class Undef:
    """Undefined value of type type_id (LLVM undef)."""
    result: Reg
    type_id: int


@dataclass
class SizeOf:
    """Get the size of a type in bytes"""
    result: Reg
    type_id: int


@dataclass
class Dangling:
    """Dangling pointer to type_id (address equals the type's alignment and is always nonzero)."""
    result: Reg
    type_id: int


@dataclass
class AggregateConstruct:
    """Construct an aggregate value (struct or tuple) from field values."""
    result: Reg
    type_id: int
    fields: list[Value]


@dataclass
class ArrayConstruct:
    """Construct an array from element values."""
    result: Reg
    type_id: int
    elements: list[Value]


@dataclass
class VariantConstruct:
    """Construct an enum variant"""
    result: Reg
    enum_type: int
    variant: EnumVariant
    payload_fields: list[Value] | None  # None = no payload


@dataclass
class SysWrite:
    fd: Value
    buf: Value


@dataclass
class SysWriteBytes:
    result: Reg
    fd: Value
    buf: Value


@dataclass
class MemCopy:
    """Byte-level memory copy — ``@memcpy(dest, src, count)``."""
    dest: Value
    src: Value
    count: Value


@dataclass
class FfiAddr:
    result: Reg
    reference: Value


@dataclass
class FfiParts:
    result: Reg
    view: Value


@dataclass
class FfiNull:
    result: Reg


@dataclass
class FfiPtrCast:
    result: Reg
    pointer: Value


@dataclass
class FfiCopy:
    destination: Value
    source: Value
    count: Value
    from_c: bool


@dataclass
class MemSetPattern:
    """Repeat a typed value over a freshly allocated element buffer."""
    dest: Value
    value: Value
    count: Value


@dataclass
class SysRead:
    result: Reg
    fd: Value
    buf: Value


@dataclass
class SysReadBytes:
    result: Reg
    fd: Value
    buf: Value


@dataclass
class Open:
    result: Reg
    path: Value
    flags: Value


@dataclass
class Close:
    result: Reg
    fd: Value


@dataclass
class Sqrt:
    """Square root of an ``f64`` value."""
    result: Reg
    value: Value


@dataclass
class Sin:
    """Sine of an ``f64`` value."""
    result: Reg
    value: Value


@dataclass
class Cos:
    """Cosine of an ``f64`` value."""
    result: Reg
    value: Value


@dataclass
class ArgCount:
    """Load the process argument count."""
    result: Reg


@dataclass
class ArgBytes:
    """Load one process argument as a borrowed byte slice."""
    result: Reg
    index: Value


@dataclass
class FuncPtr:
    """Create a function pointer from a function type."""
    result: Reg
    func_type_id: int


@dataclass
class Phi:
    """Phi node"""
    result: Reg
    incoming: list[tuple[Block, Value]]


Stmt: TypeAlias = (
    VarPtr | FieldPtr | ElementPtr | PtrDiff | Alloca | Malloc | Realloc
    | Load | Store
    | Binary | Unary | ExtractValue | EnumIsVariant | EnumPayloadFieldPtr | Delete
    | Call | Invoke | TraitObjectConstruct | TraitObjectInvoke
    | Cast | SizeOf | Undef | Dangling | FuncPtr
    | AggregateConstruct | ArrayConstruct | VariantConstruct
    | SysWrite | SysWriteBytes | SysRead | SysReadBytes | Open | Close | Sqrt | Sin | Cos | ArgCount | ArgBytes
    | MemCopy | MemSetPattern | FfiAddr | FfiParts | FfiNull | FfiPtrCast | FfiCopy
    | GenKey | AcquireFrameLock
    | CheckSafeAccess | CheckViewAccess | CheckInBounds | CheckSliceNonEmpty
    | CheckElementArith | CheckPtrDiff | CheckDelete
    | CheckPtrCmp | PtrCmp | CheckRefAccess
    | CheckElementAccess
    | CheckRawBounds
    | CheckRequest
    | LiveKnownBegin
    | LiveKnownEnd
)

# ---------------------------------------------------------------------------
# Terminators
# ---------------------------------------------------------------------------


@dataclass
class Ret:
    """Return value from function."""
    value: Value


@dataclass
class Br:
    """Unconditional branch."""
    target: Block


@dataclass
class CondBr:
    """Conditional branch:  br %cond, then, else"""
    cond: Value  # must be bool
    then_block: Block
    else_block: Block


@dataclass
class Panic:
    """Panic statement"""
    message: Value  # must be `str` type


@dataclass
class RuntimeFail:
    """Terminate with a canonical runtime error diagnostic."""
    code: RuntimeErrorCode


@dataclass
class ProcessExit:
    """Terminate the process with an explicit status code."""
    code: Value


@dataclass
class Unreachable:
    """A statically exhaustive match has no fallthrough value."""


Terminator: TypeAlias = Ret | Br | CondBr | Panic | RuntimeFail | ProcessExit | Unreachable

# ---------------------------------------------------------------------------
# Basic Data Structures
# ---------------------------------------------------------------------------


@dataclass
class Block:
    """Basic block"""
    label: str
    phis: list[Phi] = field(default_factory=list[Phi])
    stmts: list[Stmt] = field(default_factory=list[Stmt])
    terminator: Terminator | None = None


@dataclass
class VarRef:
    """Reference to a local variable"""
    name: str
    symbol_id: int
    type_id: int


@dataclass
class Reg:
    """SSA register"""
    name: str
    type_id: int


@dataclass
class IntLiteral:
    """Integer literal"""
    value: int
    type_id: int


@dataclass
class FloatLiteral:
    """Float literal"""
    value: float
    type_id: int


@dataclass
class BoolLiteral:
    """Bool literal"""
    value: bool
    type_id: int


@dataclass
class CharLiteral:
    """Char literal"""
    value: str
    type_id: int


@dataclass
class StringLiteral:
    """String literal"""
    value: str
    type_id: int


Literal: TypeAlias = IntLiteral | FloatLiteral | BoolLiteral | CharLiteral | StringLiteral

Value: TypeAlias = Literal | Reg

# ---------------------------------------------------------------------------
# Top Level
# ---------------------------------------------------------------------------


@dataclass
class Function:
    """Functions and methods are all lowwered to this node."""
    name: str
    type_id: int  # type id of the function/method
    blocks: list[Block]  # all basic blocks in the function/method
    entry: Block  # entry block of the function/method, also included in `blocks`
    local_vars: dict[int, VarRef] = field(default_factory=dict[int, VarRef])  # symbol id -> VarRef for all local variables (including parameters)
    params: list[int] = field(default_factory=list[int])  # symbol ids of parameters, in order
    frame_lock: tuple[Value, Value] | None = None  # <e_f,k_f>: the function owns a shadow-stack frame lock; LLVM writes SENTINEL and pops on every return.
