
"""Direct, method, and indirect call lowering for CFG."""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.resolver import ExprResolver
from compiler.codegen.cfg.lower.state import FunctionState
from compiler.codegen.cfg.lower.values import ValueLowerer


class CallsLowerer:
    """Call lowering."""

    def __init__(self, state: FunctionState, values: ValueLowerer, resolver: ExprResolver) -> None:
        self.__state = state
        self.__values = values
        self.__resolver = resolver

    def resolve_call(self, expr: HIR.Call) -> IR.Value:
        arg_vals = [self.__resolver.resolve_val(arg) for arg in expr.args]
        result = self.build_call(expr.func, arg_vals, expr.type_id)
        # If the callee returns never, control never returns — terminate block
        if expr.type_id == self.__state.session.type_ctx.never_id:
            self.__state.emitter.terminate(IR.Panic(IR.StringLiteral(value="unreachable: never-returning function returned", type_id=TypeCtx.str_id)))
        return result

    def resolve_invoke(self, expr: HIR.Invoke) -> IR.Value:
        resolved = expr.callable.type_id
        callable_type = self.__state.session.type_ctx[resolved]
        if isinstance(callable_type, Type.ClosureType):
            receiver_addr = self.__values.resolve_addr_fat(expr.callable)
            self.__state.emitter.emit(IR.CheckRequest(
                kind=IR.CHECK_REQUEST_RECEIVER_IN_BOUNDS, operands=[receiver_addr],
            ))
            receiver_ref_type = self.__state.session.type_ctx.alloc_ref(callable_type.struct_type_id)
            receiver_ref = self.__values.build_cast(receiver_addr, receiver_ref_type)
            arg_vals = [self.__resolver.resolve_val(arg) for arg in expr.args]
            result = self.build_call(
                callable_type.call_method_type_id, [receiver_ref] + arg_vals, expr.type_id,
            )
            if expr.type_id == self.__state.session.type_ctx.never_id:
                self.__state.emitter.terminate(IR.Panic(IR.StringLiteral(
                    value="unreachable: never-returning closure returned",
                    type_id=TypeCtx.str_id,
                )))
            return result

        callee = self.__resolver.resolve_val(expr.callable)
        arg_vals = [self.__resolver.resolve_val(arg) for arg in expr.args]
        if isinstance(callable_type, Type.FunctionType):
            result = self.build_call(resolved, arg_vals, expr.type_id)
            if expr.type_id == self.__state.session.type_ctx.never_id:
                self.__state.emitter.terminate(IR.Panic(IR.StringLiteral(value="unreachable: never-returning function returned", type_id=TypeCtx.str_id)))
            return result
        return self.build_invoke(callee, arg_vals, expr.type_id)

    def resolve_method_call(self, expr: HIR.MethodCall) -> IR.Value:
        method_type = self.__state.session.type_ctx[expr.method_id]
        assert isinstance(method_type, Type.MethodType)

        arg_vals = [self.__resolver.resolve_val(arg) for arg in expr.args]

        if method_type.custom_def.is_static:
            return self.build_call(expr.method_id, arg_vals, expr.type_id)

        # non-static: pass receiver address as the first argument.
        # Method signatures use a T& receiver. In fat mode T* is 24 B and T& is 16 B;
        # conversion discards index/size, while receivers already typed T& retain their representation.
        receiver_addr = self.__values.resolve_addr_fat(expr.receiver)
        # Request an in_bounds check before converting a T* receiver to T&.
        # Conversion removes index/size; self access in the method body uses only CheckRefAccess(live),
        # so the bounds semantics of a valid one-past-end pointer (index==size) would otherwise be lost.
        # Request the same precondition as FieldPtr/dereference before converting a T* receiver. The insertion pass
        # decides whether it is fat and whether an existing check on the block makes it redundant. RefType receivers
        # (self.method() in a method body) already use a 16 B fat reference with no bounds metadata; raw mode omits checks.
        self.__state.emitter.emit(IR.CheckRequest(
            kind=IR.CHECK_REQUEST_RECEIVER_IN_BOUNDS, operands=[receiver_addr],
        ))
        ref_type_id = self.receiver_ref_type(receiver_addr)
        receiver_ref = self.__values.build_cast(receiver_addr, ref_type_id)
        return self.build_call(expr.method_id, [receiver_ref] + arg_vals, expr.type_id)

    def resolve_trait_object_method_call(self, expr: HIR.TraitObjectMethodCall) -> IR.Value:
        receiver = self.__resolver.resolve_val(expr.receiver)
        args = [self.__resolver.resolve_val(arg) for arg in expr.args]
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=expr.type_id)
        return self.__state.emitter.emit(IR.TraitObjectInvoke(
            result=result,
            receiver=receiver,
            trait_type_id=expr.trait_type_id,
            method_id=expr.method_id,
            slot_index=expr.slot_index,
            args=args,
        )).result

    def receiver_ref_type(self, receiver_addr: IR.Value) -> int:
        """Receiver conversion target: alloc_ref(receiver value type).

        receiver_addr.type_id is T* for value variables, auto-dereferenced pointer variables, and fields;
        its pointee is the receiver value type. self.method() already uses T& with that same pointee.
        Both use alloc_ref; LLVM dispatches on source/destination to narrow or preserve the representation.
        """
        resolved = receiver_addr.type_id
        addr_ty = self.__state.session.type_ctx[resolved]
        if isinstance(addr_ty, (Type.PointerType, Type.RefType)):
            return self.__state.session.type_ctx.alloc_ref(addr_ty.pointee_type)
        return receiver_addr.type_id

    def build_call(self, callee_type: int, args: list[IR.Value], result_type: int) -> IR.Value:
        # Calls can release objects/write lock slots, invalidating check state; discharge pending InBounds before escape.
        # The insertion pass invalidates state at IR.Call.
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=result_type)
        return self.__state.emitter.emit(IR.Call(result=result, callee_type=callee_type, args=args)).result

    def build_invoke(self, callee: IR.Value, args: list[IR.Value], result_type: int) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=result_type)
        return self.__state.emitter.emit(IR.Invoke(result=result, callee=callee, args=args)).result
