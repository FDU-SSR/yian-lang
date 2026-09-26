
"""Memory and pointer operations emitted while lowering HIR to CFG."""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.frontend.parse.operator import BinaryOperator
from compiler.analysis.ty.context import TypeCtx
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.state import FunctionState
from compiler.utils.log import CompilerLog


def _ch_block():
    """cfg.block 日志通道（类体内引用，按约定用单下划线）。"""
    return CompilerLog.get("cfg.block")


class MemoryOps:
    """内存/指针原语下降器。"""

    def __init__(self, state: FunctionState) -> None:
        self.__state = state

    def build_load(self, ptr: IR.Value) -> IR.Value:
        ptr_type = self.__state.session.type_ctx[self.__state.session.type_ctx.resolve_aliases(ptr.type_id)]
        assert isinstance(ptr_type, (Type.PointerType, Type.RefType))
        # 访问前检由检查插入 pass 依 IR 重建（T& 只查 live；胖指针查
        # safe_access(p,1) 或 ElementArith∧InBounds∧live 合取）。
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=ptr_type.pointee_type)
        return self.__state.emitter.emit(IR.Load(result=result, ptr=ptr)).result

    def build_store(self, value: IR.Value, ptr: IR.Value) -> None:
        # 访问前检由检查插入 pass 依 IR 重建（分派同 Load）。
        self.__state.emitter.emit(IR.Store(ptr=ptr, value=value))

    def build_malloc(self, type_id: int, size: IR.Value) -> IR.Value:
        # CFG 层:Malloc 返回 4 字段聚合 ⟨data=b+H, word, index=0, size=n⟩(LLVM 层构造)。
        # word 由 LLVM 层根据块首地址和块头中的上一代生成(分配处 +1)。
        # pointee 为 ZST 时维持快路径(undef,不写块头);raw 模式无块头。
        key: IR.Value | None = None
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=self.__state.session.type_ctx.alloc_pointer(type_id))
        malloc = self.__state.emitter.emit(IR.Malloc(result=result, type_id=type_id, size=size, key=key)).result
        return malloc

    def build_realloc(self, type_id: int, ptr: IR.Value, size: IR.Value) -> IR.Value:
        result = IR.Reg(
            name=self.__state.emitter.new_name(),
            type_id=self.__state.session.type_ctx.alloc_pointer(type_id),
        )
        realloc = self.__state.emitter.emit(
            IR.Realloc(result=result, type_id=type_id, ptr=ptr, size=size)
        ).result
        return realloc

    def build_element_ptr(self, base: IR.Value, offset: IR.Value, result_type: int) -> IR.Value:
        # 良构检查（0 ≤ index+n ≤ size）与嵌套派生链的义务补发由检查插入 pass
        # 依 `IR.ElementPtr` 边重建；这里只发节点与登记出处。
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=result_type)
        elem_ptr = self.__state.emitter.emit(IR.ElementPtr(result=result, base=base, offset=offset)).result
        return elem_ptr

    def build_field_ptr(self, base: IR.Value, field_index: int, field_type: int) -> IR.Value:
        # 重锚定前提（in_bounds(p_s,1) / T& 的 live）与其合取合并由检查插入 pass
        # 依 `IR.FieldPtr` 边重建；这里只发节点与登记出处。
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=self.__state.session.type_ctx.alloc_pointer(field_type))
        field_ptr = self.__state.emitter.emit(IR.FieldPtr(result=result, base=base, field_index=field_index)).result
        return field_ptr

    def build_ptr_diff(self, lhs: IR.Value, rhs: IR.Value) -> IR.Value:
        # CFG 层插入检查:data 相等 + 良构 + 无回绕(异对象指针差失败)。
        # 与 ElementPtr 算术一致:该运算不访问内存,不检查 allocation live。
        if self.__state.pointers.is_fat_pointer(lhs) and self.__state.pointers.is_fat_pointer(rhs):
            self.__state.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_PTRDIFF, operands=[lhs, rhs]))
            _ch_block().debug(lambda: "check insert PtrDiff: data 相等 + 良构 + 无回绕")
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=TypeCtx.i64_id)
        return self.__state.emitter.emit(IR.PtrDiff(result=result, lhs=lhs, rhs=rhs)).result

    def build_ptr_cmp(self, op: BinaryOperator, lhs: IR.Value, rhs: IR.Value, type_id: int) -> IR.Value:
        # 指针序比较检查:序比较先查 data 相等(前提,跨对象序比较失败);
        # 相等比较 按 (data, index) 二元组、无前提检查。
        # 与 ElementPtr 算术一致:比较本身不访问内存,不检查 allocation live。
        if op in (BinaryOperator.Lt, BinaryOperator.Gt, BinaryOperator.Leq, BinaryOperator.Geq):
            self.__state.emitter.emit(IR.CheckRequest(kind=IR.CHECK_REQUEST_PTRCMP, operands=[lhs, rhs]))
            _ch_block().debug(lambda: "check insert PtrCmp: data 相等")
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=type_id)
        return self.__state.emitter.emit(IR.PtrCmp(result=result, op=op, lhs=lhs, rhs=rhs)).result


    def build_extract_value(self, base: IR.Value, field_index: int, type_id: int) -> IR.Value:
        result = IR.Reg(name=self.__state.emitter.new_name(), type_id=type_id)
        return self.__state.emitter.emit(IR.ExtractValue(result=result, base=base, field_index=field_index)).result
