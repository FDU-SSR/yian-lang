# pyright: reportUnknownMemberType=false, reportUnknownArgumentType=false
"""
LLVM Module manager — owns ir.Module, TypeMapper, IntrinsicManager.
"""

from __future__ import annotations

from typing import cast

from llvmlite import ir

from yianc.analysis.ty import ty as Type
from yianc.analysis.ty.context import TypeCtx
from yianc.codegen.abi import lockmech as ABI
from yianc.codegen.cfg import ir as IR
from yianc.codegen.llvm.base.intrinsics import IntrinsicManager
from yianc.codegen.llvm.base.types import LLTypeCtx
from yianc.codegen.llvm.base.value import LLValue
from yianc.error import CompilerError
from yianc.runtime_error import RuntimeErrorCode, runtime_error_message

# Target triple and data layout must be set together on the module.
#
# Setting only the triple leaves IR and target layouts inconsistent: middle-end passes using an empty layout
# fold offsets without ABI alignment, while the backend uses the target layout. Reads and writes can then
# use different offsets, such as a u64 after an enum with a 4-byte tag and an 8-byte pointer:
# the empty layout gives 12 and the target gives 16, silently reading incorrect values at -O1 or higher.
# See docs/llvm_note.md for the relationship between target triples and data layouts.
TARGET_TRIPLE = "x86_64-unknown-linux-gnu"
TARGET_DATA_LAYOUT = "e-m:e-p270:32:32-p271:32:32-p272:64:64-i64:64-i128:128-f80:128-n8:16:32:64-S128"

# llvmlite's IR printer defaults to typed T* pointers for compatibility. This project uses opaque pointers
# (see base/types.py and pipeline/builder.py), so disable typed-pointer printing to align emitted IR
# with LLVM's representation. This switch affects type printing only; llvmlite's pointer classification
# and generated machine code are unchanged.
ir.types.ir_layer_typed_pointers_enabled = False  # type: ignore


def apply_target(module: ir.Module) -> None:
    """Set module's target triple and matching data layout together.

    The module data layout determines TargetData. Compile-time layout queries and emission-time
    optimization/code generation must see the same layout, defined and installed in one place.
    """
    module.triple = TARGET_TRIPLE
    module.data_layout = TARGET_DATA_LAYOUT


class LLFunction:
    """Wraps an ``ir.Function`` with codegen context."""

    def __init__(self, ir_func: ir.Function) -> None:
        self.__ir = ir_func
        self.__var_allocas: dict[int, LLValue] = {}
        self.__entry_block: ir.Block | None = None
        self.__blocks: dict[str, ir.Block] = {}
        self.__regs: dict[str, LLValue] = {}

    @property
    def ir_func(self) -> ir.Function:
        """The underlying llvmlite ``ir.Function``."""
        return self.__ir

    # -- entry block --

    def add_entry_block(self) -> None:
        self.__entry_block = self.new_block("entry")

    @property
    def entry_block(self) -> ir.Block:
        assert self.__entry_block is not None
        return self.__entry_block

    # -- var allocas --

    def set_alloca(self, symbol_id: int, alloca: LLValue) -> None:
        self.__var_allocas[symbol_id] = alloca

    def get_var_ptr(self, symbol_id: int) -> LLValue:
        if symbol_id not in self.__var_allocas:
            raise KeyError(f"Variable with symbol id {symbol_id} not found in function {self.__ir.name}")
        return self.__var_allocas[symbol_id]

    # -- block management --

    def new_block(self, label: str) -> ir.Block:
        """Append a basic block to the function."""
        return self.__ir.append_basic_block(label)

    def add_block(self, label: str, block: ir.Block) -> None:
        self.__blocks[label] = block

    def block(self, label: str) -> ir.Block:
        return self.__blocks[label]

    # -- register management --

    def set_reg(self, name: str, value: LLValue) -> None:
        self.__regs[name] = value

    def reg(self, name: str) -> LLValue:
        return self.__regs[name]


class LLModule:
    """Manages a single LLVM module."""

    def __init__(self, module: ir.Module, type_ctx: TypeCtx, ll_type_ctx: LLTypeCtx, entry_type_id: int | None = None) -> None:
        self.__module = module
        self.__type_ctx = type_ctx
        self.__ll_type_ctx = ll_type_ctx
        self.__entry_type_id = entry_type_id
        self.__intrinsics = IntrinsicManager(module)
        self.__functions: dict[int, LLFunction] = {}  # type_id → LLFunction
        self.__trait_vtables: dict[tuple[int, int], ir.GlobalVariable] = {}
        self.__trait_thunks: dict[tuple[int, int, int], ir.Function] = {}
        self.__string_counter = 0
        self.__strings: dict[bytes, ir.GlobalVariable] = {}
        self.__argc_global: ir.GlobalVariable | None = None
        self.__argv_global: ir.GlobalVariable | None = None
        self.__env_lock_global: ir.GlobalVariable | None = None
        self.__key_stack_global: ir.GlobalVariable | None = None
        self.__runtime_fail_func: ir.Function | None = None
        self.__panic_func: ir.Function | None = None
        self.__lit_lock_global: ir.GlobalVariable | None = None
        self.__pool_head_global: ir.GlobalVariable | None = None
        self.__pool_alloc_func: ir.Function | None = None
        self.__pool_alloc_class_func: ir.Function | None = None
        self.__pool_release_func: ir.Function | None = None
        self.__frame_lock_arena_global: ir.GlobalVariable | None = None
        self.__frame_lock_depth_global: ir.GlobalVariable | None = None
        self.__lock_bump_global: ir.GlobalVariable | None = None
        self.__lock_free_head_global: ir.GlobalVariable | None = None
        self.__lock_new_func: ir.Function | None = None
        self.__lock_release_func: ir.Function | None = None

    # -- properties --

    @property
    def intrinsics(self) -> IntrinsicManager:
        return self.__intrinsics

    @property
    def functions(self) -> dict[int, LLFunction]:
        return self.__functions

    @property
    def triple(self) -> str:
        return self.__module.triple

    def __str__(self) -> str:
        return str(self.__module)

    # -- string literals --

    def get_string_global(self, value: bytes) -> ir.GlobalVariable:
        """Return a global constant for the given string bytes."""
        if value in self.__strings:
            return self.__strings[value]
        storage = value if value else b"\0"
        string_type = ir.ArrayType(ir.IntType(8), len(storage))  # type: ignore
        global_var = ir.GlobalVariable(self.__module, string_type, name=f"str.{self.__string_counter}")
        self.__string_counter += 1
        global_var.linkage = "private"
        global_var.global_constant = True
        global_var.unnamed_addr = True
        global_var.initializer = ir.Constant(string_type, bytearray(storage))  # type: ignore[arg-type]
        self.__strings[value] = global_var
        return global_var

    # -- function declaration --

    def declare(self, cfg_func: IR.Function) -> LLFunction:
        func_ir_type = self.__ll_type_ctx.get_ll_func_type(cfg_func.type_id)
        # The program entry is identified by its type id, not by its name: a
        # dependency may define its own `main`, which is an ordinary function
        # here (renamed to `main.<type_id>`).
        if cfg_func.type_id == self.__entry_type_id:
            llvm_name = "__yian_main"
        else:
            llvm_name = f"{cfg_func.name}.{cfg_func.type_id}"
        ir_func = ir.Function(self.__module, func_ir_type, name=llvm_name)
        if llvm_name != "__yian_main":
            # The whole program compiles into one module and executable; YIAN functions need no external ABI visibility
            # except the entry function called by the C runtime's main wrapper.
            ir_func.linkage = "internal"
        if llvm_name.startswith("index."):
            # Force-inline indexing/check helpers to avoid extra calls on element-access paths.
            ir_func.attributes.add("alwaysinline")
        func = LLFunction(ir_func)
        self.__functions[cfg_func.type_id] = func
        return func

    def get_func(self, type_id: int) -> LLFunction:
        found = self.__functions.get(type_id)
        if found is not None:
            return found
        ty = self.__type_ctx[type_id]
        if not isinstance(ty, Type.FunctionType) or not ty.custom_def.is_extern:
            raise KeyError(type_id)
        signature = self.__ll_type_ctx.get_ll_func_type(type_id)
        name = ty.custom_def.name
        existing = cast(ir.GlobalValue | None, self.__module.globals.get(name))
        if existing is None:
            function = ir.Function(self.__module, signature, name=name)
        elif isinstance(existing, ir.Function) and existing.function_type == signature:
            function = existing
        else:
            raise CompilerError(f"conflicting declarations for C symbol '{name}'")
        declared = LLFunction(function)
        self.__functions[type_id] = declared
        return declared

    def get_trait_vtable(self, concrete_type_id: int, trait_type_id: int) -> ir.GlobalVariable:
        """Return the read-only vtable for one concrete type/trait instance."""
        key = (concrete_type_id, trait_type_id)
        cached = self.__trait_vtables.get(key)
        if cached is not None:
            return cached

        impl_match = self.__type_ctx.get_trait_impl(concrete_type_id, trait_type_id)
        if impl_match is None:
            raise ValueError(
                f"missing impl for {self.__type_ctx.get_name(concrete_type_id)}: "
                f"{self.__type_ctx.get_name(trait_type_id)}"
            )
        impl, substitutions = impl_match
        object_type_id = self.__type_ctx.alloc_trait_object(trait_type_id)
        trait_methods = self.__type_ctx.get_trait_methods(trait_type_id)
        thunks: list[ir.Function] = []
        slot = 0
        for name, trait_method_id in trait_methods.items():
            trait_method_ty = self.__type_ctx[trait_method_id]
            assert isinstance(trait_method_ty, Type.MethodType)
            if trait_method_ty.custom_def.is_static:
                continue
            impl_method_id = impl.methods[name]
            concrete_method_id = self.__type_ctx.instantiate(impl_method_id, substitutions)
            thunks.append(self.__get_or_create_trait_thunk(
                concrete_type_id,
                trait_type_id,
                slot,
                object_type_id,
                trait_method_id,
                concrete_method_id,
            ))
            slot += 1

        pointer_type = ir.PointerType()  # type: ignore
        array_type = ir.ArrayType(pointer_type, len(thunks))  # type: ignore
        vtable = ir.GlobalVariable(
            self.__module,
            array_type,
            name=f"trait.vtable.{concrete_type_id}.{trait_type_id}",
        )
        vtable.linkage = "internal"
        vtable.global_constant = True
        vtable.unnamed_addr = True
        vtable.initializer = ir.Constant(array_type, thunks)  # type: ignore[arg-type]
        self.__trait_vtables[key] = vtable
        return vtable

    def __get_or_create_trait_thunk(
        self,
        concrete_type_id: int,
        trait_type_id: int,
        slot: int,
        object_type_id: int,
        trait_method_id: int,
        impl_method_id: int,
    ) -> ir.Function:
        key = (concrete_type_id, trait_type_id, slot)
        cached = self.__trait_thunks.get(key)
        if cached is not None:
            return cached
        implementation = self.get_func(impl_method_id).ir_func
        signature = self.__ll_type_ctx.get_trait_object_method_type(
            trait_method_id, object_type_id,
        )
        thunk = ir.Function(
            self.__module,
            signature,
            name=f"trait.thunk.{concrete_type_id}.{trait_type_id}.{slot}",
        )
        thunk.linkage = "internal"
        self.__trait_thunks[key] = thunk

        entry = thunk.append_basic_block("entry")
        builder = ir.IRBuilder(entry)
        object_value = thunk.args[0]
        data_index = 0
        word_index = 1
        data = builder.extract_value(object_value, data_index)

        reference_type_id = self.__type_ctx.alloc_ref(concrete_type_id)
        call_args: list[ir.Value] = []
        if not self.__type_ctx.is_zst(concrete_type_id):
            reference_type = self.__ll_type_ctx.get_ll_type(reference_type_id).ir_type
            if self.__type_ctx.raw_pointers:
                reference = data
            else:
                word = builder.extract_value(object_value, word_index)
                reference = ir.Constant(reference_type, ir.Undefined)
                reference = builder.insert_value(reference, data, 0)
                reference = builder.insert_value(reference, word, 1)
            call_args.append(reference)

        # The thunk signature already erased ZST parameters. Pairing these
        # arguments with the unfiltered semantic parameter list would shift
        # every argument after the first ZST.
        call_args.extend(thunk.args[1:])
        call = builder.call(implementation, call_args)
        result_type_id = self.__type_ctx.get_return_type(trait_method_id)
        if self.__type_ctx.is_zst(result_type_id):
            builder.ret_void()
        else:
            builder.ret(call)
        return thunk

    @property
    def argc_global(self) -> ir.GlobalVariable:
        if self.__argc_global is None:
            global_var = ir.GlobalVariable(self.__module, ir.IntType(32), name="__yian_argc")
            global_var.linkage = "external"
            self.__argc_global = global_var
        return self.__argc_global

    @property
    def argv_global(self) -> ir.GlobalVariable:
        if self.__argv_global is None:
            argv_type = ir.PointerType()
            global_var = ir.GlobalVariable(self.__module, argv_type, name="__yian_argv")
            global_var.linkage = "external"
            self.__argv_global = global_var
        return self.__argv_global

    def get_env_lock(self) -> ir.GlobalVariable:
        """Return the process-lifetime lock slot used by argv byte slices."""
        if self.__env_lock_global is None:
            global_var = ir.GlobalVariable(self.__module, ir.IntType(64), name="__yian_env_lock")
            global_var.linkage = "external"
            self.__env_lock_global = global_var
        return self.__env_lock_global

    # -- Fat-pointer mechanism globals (LLVM layer) --

    def get_key_counter(self) -> ir.GlobalVariable:
        """Global monotonic frame-key counter (__yian_key_stack, defined by the runtime).

        LLVM emits k_f <- Gen() as load, limit check, increment, and store. The global counter
        remains monotonic across functions and slot reuse. Heap identity uses per-entry
        generations instead of this counter.
        """
        if self.__key_stack_global is None:
            self.__key_stack_global = self.__new_key_counter("__yian_key_stack")
        return self.__key_stack_global

    def __new_key_counter(self, name: str) -> ir.GlobalVariable:
        global_var = ir.GlobalVariable(self.__module, ir.IntType(64), name=name)  # type: ignore
        global_var.linkage = "external"
        return global_var

    def get_runtime_fail(self) -> ir.Function:
        """Return the runtime fail-stop helper declaration used by safety checks."""
        if self.__runtime_fail_func is not None:
            return self.__runtime_fail_func

        i8_ptr = ir.PointerType()  # type: ignore
        i64 = ir.IntType(64)  # type: ignore
        fn = ir.Function(
            self.__module,
            ir.FunctionType(ir.VoidType(), [i8_ptr, i64]),
            name="__yian_runtime_fail",
        )
        fn.attributes.add("cold")
        fn.attributes.add("noreturn")
        fn.attributes.add("nounwind")
        message, length = fn.args
        message.name = "message"
        length.name = "length"
        self.__runtime_fail_func = fn
        return fn

    def get_panic(self) -> ir.Function:
        """Return the runtime panic helper declaration."""
        if self.__panic_func is not None:
            return self.__panic_func

        i8_ptr = ir.PointerType()  # type: ignore
        i64 = ir.IntType(64)  # type: ignore
        fn = ir.Function(
            self.__module,
            ir.FunctionType(ir.VoidType(), [i8_ptr, i64]),
            name="__yian_panic",
        )
        fn.attributes.add("cold")
        fn.attributes.add("noreturn")
        fn.attributes.add("nounwind")
        message, length = fn.args
        message.name = "message"
        length.name = "length"
        self.__panic_func = fn
        return fn

    def emit_runtime_fail(self, builder: ir.IRBuilder, code: RuntimeErrorCode) -> None:
        """Emit a call with a private, canonical diagnostic string."""
        message = runtime_error_message(code)
        global_var = self.get_string_global(message)
        ptr = builder.gep(
            global_var,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), 0)],
        )
        builder.call(
            self.get_runtime_fail(),
            [ptr, ir.Constant(ir.IntType(64), len(message))],
        )

    def get_lit_lock(self) -> ir.GlobalVariable:
        """String-literal lock slot: a runtime-owned global i64, initialized to LITERAL_KEY(1), never written.

        Literal data is read-only global storage lasting for the whole program, so its key always
        matches the slot and derived pointers remain live. Literal and frame keys are compared
        only within their respective slots, addressed independently.
        """
        if self.__lit_lock_global is None:
            global_var = ir.GlobalVariable(self.__module, ir.IntType(64), name="__yian_lit_lock")  # type: ignore
            global_var.linkage = "external"
            self.__lit_lock_global = global_var
        return self.__lit_lock_global

    # -- single-threaded stable frame-lock shadow stack --

    def get_lock_table(self) -> ir.GlobalVariable:
        """Return the external lock-table declaration.

        Entries are 8 B {key:u32,anchor_lo32:u32}; index ranges encode kind:
        frame slots occupy [0,FRAME_LOCK_SLOTS), then two constant slots, then allocator-issued heap slots.
        The runtime owns the BSS table; only touched pages become resident.
        """
        if self.__frame_lock_arena_global is None:
            arena_type = ir.ArrayType(
                ir.IntType(64), ABI.LOCK_TABLE_SLOTS  # type: ignore
            )
            global_var = ir.GlobalVariable(
                self.__module, arena_type, name="__secl_lock_table"
            )
            global_var.linkage = "external"
            global_var.align = ABI.LockEntry.BYTES  # type: ignore
            self.__frame_lock_arena_global = global_var
        return self.__frame_lock_arena_global

    def get_lock_bump(self) -> ir.GlobalVariable:
        """Return the lock-table bump cursor declaration (next unused heap index)."""
        if self.__lock_bump_global is None:
            global_var = ir.GlobalVariable(
                self.__module, ir.IntType(64), name="__secl_lock_bump"
            )
            global_var.linkage = "external"
            global_var.align = 8  # type: ignore
            self.__lock_bump_global = global_var
        return self.__lock_bump_global

    def get_lock_free_head(self) -> ir.GlobalVariable:
        """Return the lock-table free-list head declaration (0 means empty; otherwise index+1)."""
        if self.__lock_free_head_global is None:
            global_var = ir.GlobalVariable(
                self.__module, ir.IntType(64), name="__secl_lock_free_head"
            )
            global_var.linkage = "external"
            global_var.align = 8  # type: ignore
            self.__lock_free_head_global = global_var
        return self.__lock_free_head_global

    def get_lock_bump_take(self) -> ir.Function:
        """Return the bump declaration (take a new index when the free list is empty; exhaustion reports R003)."""
        if self.__lock_new_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.IntType(64), []),
                name="__secl_lock_bump_take",
            )
            # Failures terminate through noreturn __yian_runtime_fail without unwinding.
            fn.attributes.add("nounwind")
            self.__lock_new_func = fn
        return self.__lock_new_func

    def get_lock_release(self) -> ir.Function:
        """Return the lock-table release declaration (release an entry identified by word)."""
        if self.__lock_release_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.VoidType(), [ir.IntType(64)]),
                name="__secl_lock_release",
            )
            fn.args[0].name = "word"
            # Failures terminate through noreturn __yian_runtime_fail without unwinding.
            fn.attributes.add("nounwind")
            self.__lock_release_func = fn
        return self.__lock_release_func

    def get_frame_lock_depth(self) -> ir.GlobalVariable:
        """Return the active-depth cursor declaration for the frame-lock segment."""
        if self.__frame_lock_depth_global is None:
            i64 = ir.IntType(64)  # type: ignore
            global_var = ir.GlobalVariable(
                self.__module, i64, name="__secl_frame_lock_depth"
            )
            global_var.linkage = "external"
            global_var.align = ABI.FrameLockArena.SLOT_BYTES  # type: ignore
            self.__frame_lock_depth_global = global_var
        return self.__frame_lock_depth_global

    # -- single-threaded stable-header heap pool --

    def get_pool_alloc(self) -> ir.Function:
        """Return the runtime allocator declaration for stable-header blocks.

        The runtime's size-class arena returns a block base (lock-slot address). The compiler writes
        the lock slot and active_size; payload = base + BlockHeader.BYTES.
        """
        if self.__pool_alloc_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.PointerType(), [ir.IntType(64)]),
                name="__secl_pool_alloc",
            )
            fn.args[0].name = "requested"
            # Failures terminate through noreturn __yian_runtime_fail without unwinding.
            fn.attributes.add("nounwind")
            self.__pool_alloc_func = fn
        return self.__pool_alloc_func

    def get_pool_alloc_class(self) -> ir.Function:
        """Return the size-class allocator declaration (class index computed by the compiler).

        Allocations with compile-time sizes pass a class index directly, avoiding runtime byte-size lookup.
        See runtime_lib.CLASS_BYTES for class indices.
        """
        if self.__pool_alloc_class_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.PointerType(), [ir.IntType(32)]),
                name="__secl_pool_alloc_class",
            )
            fn.args[0].name = "class_index"
            # Failures terminate through noreturn __yian_runtime_fail without unwinding.
            fn.attributes.add("nounwind")
            self.__pool_alloc_class_func = fn
        return self.__pool_alloc_class_func

    def get_pool_release(self) -> ir.Function:
        """Return the runtime release declaration for whole blocks; the runtime reclaims physical pages."""
        if self.__pool_release_func is None:
            fn = ir.Function(
                self.__module,
                ir.FunctionType(ir.VoidType(), [ir.PointerType()]),
                name="__secl_pool_release",
            )
            fn.args[0].name = "block"
            # Failures terminate through noreturn __yian_runtime_fail without unwinding.
            fn.attributes.add("nounwind")
            self.__pool_release_func = fn
        return self.__pool_release_func
