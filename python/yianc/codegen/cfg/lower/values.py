"""Address, aggregate, cast, and frame-lock lowering for CFG values."""
from __future__ import annotations

from yianc.analysis.ty import ty as Type
from yianc.analysis.ty.context import TypeCtx
from yianc.analysis.unit import hir as HIR
from yianc.codegen.cfg import ir as IR
from yianc.codegen.cfg.lower.memory import MemoryOps
from yianc.codegen.cfg.lower.resolver import ExprResolver
from yianc.codegen.cfg.lower.state import FunctionState
from yianc.codegen.error import CodegenError
from yianc.frontend.parse.operator import UnaryOperator
from yianc.utils.log import CompilerLog


def _ch_block():
    """cfg.block logging channel (single underscore permits references from class bodies)."""
    return CompilerLog.get("cfg.block")


class ValueLowerer:
    """Lazy lvalue/address, aggregate, construction, and conversion lowering with frame-lock state."""

    def __init__(self, state: FunctionState, memory: MemoryOps, resolver: ExprResolver) -> None:
        self.__state = state
        self.__memory = memory
        self.__resolver = resolver
        self.__frame_lock: tuple[IR.Value, IR.Value] | None = None  # <e_f,k_f>: frame lock materialized at function entry.

    @property
    def frame_lock(self) -> tuple[IR.Value, IR.Value] | None:
        """Frame-lock register pair; the builder writes IR.Function.frame_lock at the end of build()."""
        return self.__frame_lock

    def resolve_addr(self, expr: HIR.Expr) -> IR.Value:
        """Lower *expr* to an address.

        - for lvalue expressions, this is the address of the lvalue
        - for rvalue expressions, allocates a temporary and copies the value to it
        """
        if isinstance(expr, HIR.ArrayAccess):
            return self.resolve_array_access_addr(expr)
        if isinstance(expr, HIR.SliceAccess):
            return self.resolve_slice_access_addr(expr)
        if isinstance(expr, HIR.Closure):
            value = self.__resolver.resolve_val(expr)
            return self.build_alloca(value, fat=False)

        if not expr.is_place:
            val = self.__resolver.resolve_val(expr)
            return self.build_alloca(val, fat=False)

        # resolve expr that produces an address
        match expr:
            case HIR.Unary() if expr.op == UnaryOperator.Deref:
                return self.resolve_deref_addr(expr)
            case HIR.FieldAccess():
                return self.resolve_field_access_addr(expr)
            case HIR.TupleAccess():
                return self.resolve_tuple_access_addr(expr)
            case HIR.Var():
                return self.resolve_var_addr(expr)
            case HIR.Ty():
                return self.__state.build_func_ptr(expr.type_id)
            case _:
                raise CodegenError(f"Cannot resolve address of expression: {expr}", expr.span)

    def resolve_addr_fat(self, expr: HIR.Expr) -> IR.Value:
        """Lazy lvalue path: resolve fat addresses for explicit &x and method receivers.

        Unlike __resolve_addr, which returns raw addresses for bare variables, this path always
        synthesizes four-field fat pointers, lazily materializing the frame lock on first use.
        Derived field/array/deref addresses propagate the fat base and retain check metadata.
        """
        # SliceAccess is value-shaped in HIR because ordinary indexing loads an
        # element.  When it is the operand of AddrOf, however, preserve the
        # element address instead of materializing a temporary and taking that
        # temporary's address.
        if isinstance(expr, HIR.SliceAccess):
            return self.resolve_slice_access_addr(expr, fat=True)
        if isinstance(expr, HIR.Closure):
            value = self.__resolver.resolve_val(expr)
            return self.build_alloca(value, fat=True)
        if not expr.is_place:
            val = self.__resolver.resolve_val(expr)
            return self.build_alloca(val, fat=True)

        match expr:
            case HIR.Unary() if expr.op == UnaryOperator.Deref:
                return self.resolve_deref_addr(expr)
            case HIR.FieldAccess():
                return self.resolve_field_access_addr(expr, fat=True)
            case HIR.TupleAccess():
                return self.resolve_tuple_access_addr(expr, fat=True)
            case HIR.ArrayAccess():
                return self.resolve_array_access_addr(expr, fat=True)
            case HIR.Var():
                return self.resolve_var_addr(expr, fat=True)
            case HIR.Ty():
                return self.__state.build_func_ptr(expr.type_id)
            case _:
                raise CodegenError(f"Cannot resolve address of expression: {expr}", expr.span)

    # ------------------------------------------------------------------
    # value resolvors
    # ------------------------------------------------------------------

    def resolve_deref_addr(self, expr: HIR.Unary) -> IR.Value:
        return self.__resolver.resolve_val(expr.operand)

    def resolve_field_access_addr(self, expr: HIR.FieldAccess, *, fat: bool = False) -> IR.Value:
        # Lazy lvalues: fat=True (explicit & / method receiver) propagates the fat base;
        # natural resolution preserves the base representation: bare variable -> raw field address, dereferenced fat pointer -> fat.
        base_addr = self.resolve_addr_fat(expr.receiver) if fat else self.resolve_addr(expr.receiver)
        return self.__memory.build_field_ptr(base_addr, expr.field.index, expr.type_id)

    def resolve_tuple_access_addr(self, expr: HIR.TupleAccess, *, fat: bool = False) -> IR.Value:
        base_addr = self.resolve_addr_fat(expr.receiver) if fat else self.resolve_addr(expr.receiver)
        return self.__memory.build_field_ptr(base_addr, expr.index, expr.type_id)

    def resolve_array_access_addr(self, expr: HIR.ArrayAccess, *, fat: bool = False) -> IR.Value:
        # T[N] element addresses: decay the array pointer to T*, then index; LLVM uses data as the base
        # and sets size=N when decaying the array.
        # Lazy lvalues: bare array bases (ordinary array variables) use raw bitcasts and compile-time
        # bounds checks; fat bases after Deref use decay and ElementPtr validity checks.
        base_addr = self.resolve_addr_fat(expr.array) if fat else self.resolve_addr(expr.array)
        elem_ptr_type = self.__state.session.type_ctx.alloc_pointer(expr.element_type)
        elem_base = self.build_cast(base_addr, elem_ptr_type)
        index_val = self.__resolver.resolve_val(expr.index)
        if not fat and self.__state.pointers.is_raw(base_addr):
            self.__state.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_RAW_BOUNDS, operands=[index_val], extra=expr.length,
            ))
            _ch_block().debug(lambda: "check insert ArrayAccess(raw): index < length (裸数组越界)")
        return self.__memory.build_element_ptr(elem_base, index_val, elem_ptr_type)

    def resolve_slice_access_addr(self, expr: HIR.SliceAccess, *, fat: bool = False) -> IR.Value:
        """Built-in resolution of T[] element addresses for inline slice indexing.

        Mirror slice.an's as_struct/ptr/index chain at the call site, avoiding full stack calls
        when emit_object runs without optimization:
        1. Resolve the slice value (fat {data,word,size}, raw {data,size}).
        2. Extract data; __slice_ptr_fat synthesizes a four-field pointer inheriting the slice's
           actual lock, as as_struct does. Raw mode uses a bare pointer.
        3. __build_element_ptr emits CheckElementArith and ElementPtr for the element address.
        The caller's fieldptr/load/store triggers CheckInBounds/CheckSafeAccess.
        """
        slice_val = self.__resolver.resolve_val(expr.slice)
        index_val = self.__resolver.resolve_val(expr.index)
        elem_ptr_type = self.__state.session.type_ctx.alloc_pointer(expr.element_type)
        data = self.__memory.build_extract_value(slice_val, 0, elem_ptr_type)
        return self.__memory.build_element_ptr(data, index_val, elem_ptr_type)

    def resolve_var_addr(self, expr: HIR.Var, *, fat: bool = False) -> IR.Value:
        captured_field = self.__state.closure_capture_fields.get(expr.symbol_id)
        if captured_field is not None:
            receiver_id = self.__state.closure_receiver_id
            receiver_ref_type_id = self.__state.closure_receiver_ref_type_id
            struct_type_id = self.__state.closure_struct_type_id
            assert receiver_id is not None
            assert receiver_ref_type_id is not None
            assert struct_type_id is not None
            receiver = HIR.Var(
                span=expr.span,
                symbol_id=receiver_id,
                type_id=receiver_ref_type_id,
                is_place=True,
            )
            dereference = HIR.Unary(
                span=expr.span,
                op=UnaryOperator.Deref,
                operand=receiver,
                type_id=struct_type_id,
                is_place=True,
            )
            field_access = HIR.FieldAccess(
                span=expr.span,
                receiver=dereference,
                field=captured_field,
                type_id=captured_field.type_id,
                is_place=expr.is_place,
            )
            return self.resolve_field_access_addr(field_access, fat=fat)
        if expr.symbol_id not in self.__state.emitter.func.local_vars:
            raise CodegenError(f"Undefined variable: {expr.symbol_id}", expr.span)
        var_ref = self.__state.emitter.func.local_vars[expr.symbol_id]
        if fat:
            return self.build_var_ptr_fat(var_ref)
        return self.build_var_ptr_raw(var_ref)

    # ------------------------------------------------------------------
    # ir building helpers
    # ------------------------------------------------------------------

    def build_var_ptr_fat(self, var_ref: IR.VarRef) -> IR.Value:
        """Address a local slot and synthesize <a_x, word(e_f,k_f), 0, 1>.

        data is the slot address; word packs the frame-lock position and key, lazily materialized
        at function entry on first address-taking. index=0 and size=1, including array addresses.
        LLVM lowers the value. This path serves explicit &x and method receivers.
        """
        e_f, k_f = self.emit_frame_lock()
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=self.__state.session.type_ctx.alloc_pointer(var_ref.type_id))
        fat = self.__state.emitter.emit(IR.VarPtr(
            result=result, var_ref=var_ref, frame_word=e_f, frame_key=k_f,
        )).result
        return fat

    def build_var_ptr_raw(self, var_ref: IR.VarRef) -> IR.Value:
        """Lazy lvalue path: return the raw stack-slot address for unaddressed lvalues.
        Assignment, reads, and field-base derivation need no fat pointer or frame lock. Materialization
        waits for AddrOf/method receivers; raw addresses omit metadata and checks.
        """
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=self.__state.session.type_ctx.alloc_pointer(var_ref.type_id))
        raw_ptr = self.__state.emitter.emit(IR.VarPtr(
            result=result, var_ref=var_ref, frame_word=None, frame_key=None, raw=True,
        )).result
        return raw_ptr

    def build_alloca(self, value: IR.Value, *, fat: bool) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=self.__state.session.type_ctx.alloc_pointer(value.type_id))
        if fat and not self.__state.session.raw_pointers:
            frame_word, frame_key = self.emit_frame_lock()
            addr = self.__state.emitter.emit(IR.Alloca(
                result=result,
                value=value,
                frame_word=frame_word,
                frame_key=frame_key,
            )).result
        else:
            addr = self.__state.emitter.emit(IR.Alloca(result=result, value=value, raw=True)).result
        return addr

    def emit_frame_lock(self) -> tuple[IR.Value | None, IR.Value | None]:
        """Materialize a frame lock: k_f <- Gen() (stack key MSB 0), pushing
        a u64 slot on the independent stable shadow stack and writing k_f. Trigger lazily
        on the first VarPtr and prepend materialization statements to the entry block,
        before the body and terminator. Functions without address-taking have no frame-lock nodes.
        LLVM writes SENTINEL and pops the slot on every return path.
        Raw mode returns None frame fields without materializing
        GenKey/AcquireFrameLock."""
        if self.__state.session.raw_pointers:
            return (None, None)
        if self.__frame_lock is not None:
            return self.__frame_lock
        saved_block = self.__state.emitter.current_block
        self.__state.emitter.current_block = self.__state.emitter.func.entry
        k_f = self.build_gen_key()
        e_f_result = IR.Reg(
            name=self.__state.emitter.new_name(),
            type_id=TypeCtx.u64_id,  # Frame word (full word).
        )
        e_f = self.__state.emitter.emit(IR.AcquireFrameLock(result=e_f_result, key=k_f)).result
        self.__state.emitter.current_block = saved_block
        entry = self.__state.emitter.func.entry
        frame_stmts = entry.stmts[-2:]
        del entry.stmts[-2:]
        entry.stmts[0:0] = frame_stmts
        self.__frame_lock = (e_f, k_f)
        return (e_f, k_f)

    def build_gen_key(self) -> IR.Value:
        """k_f <- Gen(): the 32-bit key used to re-key on frame entry."""
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=TypeCtx.u64_id)
        return self.__state.emitter.emit(IR.GenKey(result=result)).result

    def resolve_cast(self, expr: HIR.Cast) -> IR.Value:
        value = self.__resolver.resolve_val(expr.value)
        return self.build_cast(value, expr.target_type)

    def resolve_trait_object_coerce(self, expr: HIR.TraitObjectCoerce) -> IR.Value:
        value = self.__resolver.resolve_val(expr.value)
        source_ty = self.__state.session.type_ctx[
            value.type_id
        ]
        reference_type_id = self.__state.session.type_ctx.alloc_ref(expr.concrete_type_id)
        if isinstance(source_ty, Type.PointerType):
            if not self.__state.session.raw_pointers:
                self.__state.emitter.emit(IR.CheckRequest(
                    kind=IR.CHECK_REQUEST_IN_BOUNDS,
                    operands=[value],
                ))
            reference = self.build_cast(value, reference_type_id)
        elif isinstance(source_ty, Type.RefType):
            reference = value
        else:
            raise CodegenError(
                "trait-object coercion source is not a concrete pointer or reference",
                expr.span,
            )
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.TraitObjectConstruct(
            result=result,
            reference=reference,
            concrete_type_id=expr.concrete_type_id,
            trait_type_id=expr.trait_type_id,
        )).result

    def build_cast(self, value: IR.Value, to_type: int) -> IR.Value:
        # CFG pointer casts: non-ZST ptr-to-T <-> ptr-to-U are identity conversions
        #   with the same LLVM {ptr,i64,i32,i32} representation. Casts involving ptr-to-ZST
        #   produce undef to avoid LLVM size mismatches. CFG defines semantics; LLVM emits them.
        # Lazy lvalues: mark raw-source casts raw for LLVM bitcasts without fat-value synthesis;
        # raw representation propagates through casts and derived addresses of decayed bare arrays.
        to_resolved = to_type
        if isinstance(self.__state.session.type_ctx[to_resolved], Type.PointerType):
            _ch_block().debug(lambda: "cast ptr→ptr: identity (fat representation) / ptr-to-ZST 例外 = undef")
        raw = self.__state.pointers.is_raw(value)
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=to_type)
        cast = self.__state.emitter.emit(IR.Cast(result=result, value=value, to_type=to_type, raw=raw)).result
        # The insertion pass propagates pending obligations along pointer-to-pointer identity casts
        # by following IR.Cast edges.
        return cast

    def resolve_bit_cast(self, expr: HIR.Builtin | HIR.BitCast) -> IR.Value:
        if isinstance(expr, HIR.Builtin):
            source_expr = expr.args[0]
            target_type_id = expr.type_id
        else:
            source_expr = expr.value
            target_type_id = expr.target_type
        value = self.__resolver.resolve_val(source_expr)
        source_type = self.__state.session.type_ctx[value.type_id]
        target_type = self.__state.session.type_ctx[target_type_id]
        if not self.__state.session.raw_pointers:
            if isinstance(source_type, Type.PointerType) and isinstance(target_type, Type.RefType):
                # T& drops index/size, so the source must denote a real element
                # rather than the legal one-past pointer value.
                self.__state.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_IN_BOUNDS, operands=[value]))
            elif isinstance(source_type, Type.SliceType) and isinstance(target_type, Type.RefType):
                # An empty slice has no element from which a reference can be
                # formed.  Establish this before dropping the size field.
                self.__state.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_SLICE_NONEMPTY, operands=[value]))
        return self.build_cast(value, target_type_id)

    def resolve_undef(self, expr: HIR.Builtin) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.Undef(result=result, type_id=expr.type_id)).result

    def resolve_dangling(self, expr: HIR.Builtin) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.Dangling(result=result, type_id=expr.type_id)).result

    def build_size_of(self, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=TypeCtx.u64_id)
        return self.__state.emitter.emit(IR.SizeOf(result=result, type_id=type_id)).result

    def build_aggregate_construct(self, type_id: int, fields: list[IR.Value]) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=type_id)
        return self.__state.emitter.emit(IR.AggregateConstruct(result=result, type_id=type_id, fields=fields)).result

    def build_array_construct(self, type_id: int, elements: list[IR.Value]) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=type_id)
        return self.__state.emitter.emit(IR.ArrayConstruct(result=result, type_id=type_id, elements=elements)).result

    def build_variant_construct(self, enum_type: int, variant: Type.EnumVariant, payload_fields: list[IR.Value] | None, result_type: int) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=result_type)
        return self.__state.emitter.emit(IR.VariantConstruct(result=result, enum_type=enum_type, variant=variant, payload_fields=payload_fields)).result

    def resolve_tuple(self, expr: HIR.Tuple) -> IR.Value:
        field_vals = [self.__resolver.resolve_val(field) for field in expr.field_values]
        tuple_type = self.__state.session.type_ctx[expr.type_id]
        if (
            isinstance(tuple_type, (Type.SliceType, Type.StrType))
            and len(field_vals) == 2
            and self.__state.pointers.is_fat_pointer(field_vals[0])
        ):
            # ``@slice_from_parts``/``@str_from_parts`` are trusted metadata
            # constructors.  Keep the requested view within the source
            # pointer's remaining extent before publishing its size field.
            self.__state.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_ELEMENT_ARITH, operands=[field_vals[0], field_vals[1]],
            ))
            _ch_block().debug(lambda: "check insert slice construction: source extent + requested length")
        return self.build_aggregate_construct(expr.type_id, field_vals)

    def resolve_array(self, expr: HIR.Array) -> IR.Value:
        elements = [self.__resolver.resolve_val(element) for element in expr.elements]
        return self.build_array_construct(expr.type_id, elements)

    def resolve_array_repeat(self, expr: HIR.ArrayRepeat) -> IR.Value:
        elem_val = self.__resolver.resolve_val(expr.element)
        array_ty = self.__state.session.type_ctx[expr.type_id]
        assert isinstance(array_ty, Type.ArrayType)
        length_ty = self.__state.session.type_ctx[array_ty.length]
        assert isinstance(length_ty, Type.LiteralValueType), (
            f"array repeat count must be concrete at codegen, got {type(length_ty).__name__}"
        )
        elements = [elem_val] * length_ty.value
        return self.build_array_construct(expr.type_id, elements)

    def resolve_struct_construct(self, expr: HIR.StructConstruct) -> IR.Value:
        struct_type = self.__state.session.type_ctx[expr.struct_id]
        assert isinstance(struct_type, Type.StructType)
        fields = self.__state.session.type_ctx.get_struct_fields(expr.struct_id)
        field_vals = [self.__resolver.resolve_val(expr.field_values[field.name]) for field in fields]
        return self.build_aggregate_construct(expr.struct_id, field_vals)

    def resolve_closure(self, expr: HIR.Closure) -> IR.Value:
        """Build the ordinary capture-environment aggregate for a closure."""
        closure_type = self.__state.session.type_ctx[expr.type_id]
        assert isinstance(closure_type, Type.ClosureType)
        fields = self.__state.session.type_ctx.get_struct_fields(closure_type.struct_type_id)
        field_values = [self.__resolver.resolve_val(expr.captures[field.name]) for field in fields]
        return self.build_aggregate_construct(closure_type.struct_type_id, field_values)

    def resolve_variant_construct(self, expr: HIR.VariantConstruct) -> IR.Value:
        if expr.args is None:
            payload_fields = None
        else:
            assert expr.variant.payload_type is not None
            payload_type = self.__state.session.type_ctx[expr.variant.payload_type]
            assert isinstance(payload_type, Type.StructType)
            fields = self.__state.session.type_ctx.get_struct_fields(expr.variant.payload_type)
            payload_fields = [self.__resolver.resolve_val(expr.args[field.name]) for field in fields]

        return self.build_variant_construct(expr.enum_id, expr.variant, payload_fields, expr.type_id)

    def resolve_size_of(self, expr: HIR.Builtin) -> IR.Value:
        return self.build_size_of(expr.type_args[0])
