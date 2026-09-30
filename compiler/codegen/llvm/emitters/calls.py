# pyright: reportUnknownMemberType=false
"""Direct, indirect, and trait-object call lowering."""

from __future__ import annotations

from llvmlite import ir

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.abi import lockmech as ABI
from compiler.codegen.llvm.pointer.safety import FatSafety
from compiler.codegen.llvm.function.core import FunctionCore
from compiler.codegen.llvm.base.module import LLFunction
from compiler.codegen.llvm.pointer.representation import PointerRepresentation
from compiler.codegen.llvm.base.value import LLValue
from compiler.codegen.llvm.base.types import LLTypeCtx


class _FunctionCallTarget:
    """Attach a known signature to an opaque function pointer call site."""

    def __init__(self, value: ir.Value, function_type: ir.FunctionType) -> None:
        self.__value = value
        self.type = ir.PointerType()
        self.function_type = function_type

    def get_reference(self) -> str:
        return self.__value.get_reference()  # type: ignore[attr-defined]


class CallEmitter:
    """Emit calls and package concrete references as trait objects."""

    def __init__(
        self,
        core: FunctionCore,
        pointers: PointerRepresentation,
        fat_safety: FatSafety,
    ) -> None:
        self.__core = core
        self.__pointers = pointers
        self.__fat_safety = fat_safety

    @property
    def __type_ctx(self) -> TypeCtx:
        return self.__core.context.type_ctx

    @property
    def __ll_type_ctx(self) -> LLTypeCtx:
        return self.__core.context.ll_type_ctx

    @property
    def __raw_pointers(self) -> bool:
        return self.__core.context.raw_pointers

    @property
    def __builder(self) -> ir.IRBuilder:
        return self.__core.flow.builder

    def call(self, callee: LLFunction, args: list[LLValue], return_type_id: int) -> LLValue:
        resolved = [self.__pointers.promote_fat(arg).ir_val for arg in args]
        ir_value = self.__builder.call(callee.ir_func, resolved)  # type: ignore
        if self.__ll_type_ctx.is_zst(return_type_id):
            return self.__core.ir.undef(return_type_id)
        return LLValue(return_type_id, ir_value)

    def call_func(self, callee_type: int, args: list[LLValue], return_type_id: int) -> LLValue:
        callee = self.__core.context.module.get_func(callee_type)
        assert callee is not None, f"Function callee_type={callee_type} not declared"
        return self.call(callee, args, return_type_id)

    def call_value(self, callee: LLValue, args: list[LLValue], return_type_id: int) -> LLValue:
        resolved_args = [self.__pointers.promote_fat(arg).ir_val for arg in args]
        ir_value = self.__builder.call(callee.ir_val, resolved_args)  # type: ignore
        if self.__ll_type_ctx.is_zst(return_type_id):
            return self.__core.ir.undef(return_type_id)
        return LLValue(return_type_id, ir_value)

    def trait_object_construct(
        self,
        reference: LLValue,
        result_type_id: int,
        concrete_type_id: int,
        trait_type_id: int,
    ) -> LLValue:
        vtable = self.__core.context.module.get_trait_vtable(concrete_type_id, trait_type_id)
        object_ir_type = self.__ll_type_ctx.get_ll_type(result_type_id).ir_type
        data: ir.Value
        word: ir.Value | None = None
        if self.__type_ctx.is_zst(concrete_type_id):
            data = ir.Constant(self.__ll_type_ctx.ptr_type, None)  # type: ignore
            if not self.__raw_pointers:
                word = ir.Constant(ir.IntType(64), ABI.ENV_WORD)  # type: ignore
        elif self.__raw_pointers:
            data = reference.ir_val
        else:
            data = self.__builder.extract_value(reference.ir_val, 0)  # type: ignore
            word = self.__builder.extract_value(reference.ir_val, 1)  # type: ignore

        aggregate = ir.Constant(object_ir_type, ir.Undefined)  # type: ignore
        aggregate = self.__builder.insert_value(aggregate, data, 0)  # type: ignore
        if self.__raw_pointers:
            vtable_index = 1
        else:
            assert word is not None
            aggregate = self.__builder.insert_value(aggregate, word, 1)  # type: ignore
            vtable_index = 2
        aggregate = self.__builder.insert_value(aggregate, vtable, vtable_index)  # type: ignore
        return LLValue(result_type_id, aggregate)

    def trait_object_call(
        self,
        receiver: LLValue,
        trait_type_id: int,
        method_type_id: int,
        slot_index: int,
        args: list[LLValue],
        return_type_id: int,
    ) -> LLValue:
        if not self.__raw_pointers:
            data = self.__builder.extract_value(receiver.ir_val, 0)  # type: ignore
            word = self.__builder.extract_value(receiver.ir_val, 1)  # type: ignore
            reference_type_id = self.__type_ctx.alloc_ref(self.__type_ctx.u8_id)
            reference_ir_type = self.__ll_type_ctx.get_ll_type(reference_type_id).ir_type
            reference = ir.Constant(reference_ir_type, ir.Undefined)  # type: ignore
            reference = self.__builder.insert_value(reference, data, 0)  # type: ignore
            reference = self.__builder.insert_value(reference, word, 1)  # type: ignore
            self.__fat_safety.check_ref_access(LLValue(reference_type_id, reference))

        vtable_index = 1 if self.__raw_pointers else 2
        vtable = self.__builder.extract_value(receiver.ir_val, vtable_index)  # type: ignore
        trait_methods = self.__type_ctx.get_trait_methods(trait_type_id)
        slot_count = 0
        for method_id in trait_methods.values():
            method_type = self.__type_ctx[method_id]
            if isinstance(method_type, Type.MethodType) and not method_type.custom_def.is_static:
                slot_count += 1
        table_type = ir.ArrayType(self.__ll_type_ctx.ptr_type, slot_count)  # type: ignore
        slot_ptr = self.__builder.gep(
            vtable,
            [ir.Constant(ir.IntType(32), 0), ir.Constant(ir.IntType(32), slot_index)],  # type: ignore
            inbounds=True,
            source_etype=table_type,
        )
        function_type = self.__ll_type_ctx.get_trait_object_method_type(
            method_type_id, receiver.type_id,
        )
        callee = self.__builder.load(slot_ptr, typ=self.__ll_type_ctx.ptr_type)  # type: ignore
        call_args = [receiver.ir_val]
        call_args.extend(
            self.__pointers.promote_fat(argument).ir_val
            for argument in args
            if not self.__ll_type_ctx.is_zst(argument.type_id)
        )
        call = ir.instructions.CallInstr(
            self.__builder.block,  # type: ignore[attr-defined]
            _FunctionCallTarget(callee, function_type),  # type: ignore[arg-type]
            call_args,
        )
        self.__builder._insert(call)  # type: ignore[attr-defined]
        if self.__ll_type_ctx.is_zst(return_type_id):
            return self.__core.ir.undef(return_type_id)
        return LLValue(return_type_id, call)

    @staticmethod
    def func_ptr(func: LLFunction, func_ptr_type_id: int) -> LLValue:
        return LLValue(func_ptr_type_id, func.ir_func)  # type: ignore

    def func_ptr_by_type(self, func_type_id: int, func_ptr_type_id: int) -> LLValue:
        callee = self.__core.context.module.get_func(func_type_id)
        assert callee is not None, f"FuncPtr func_type_id={func_type_id} not declared"
        return self.func_ptr(callee, func_ptr_type_id)
