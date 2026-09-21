"""指针类型判定（`T*`/`T&`/切片/函数指针 与 ZST 擦除的胖判定）。

从 `CfgBuilder` 搬出：只依赖 type_ctx / raw_pointers / checks（裸指针判定），
被各下降簇经 host 注入使用。
"""
from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.codegen.cfg import ir as IR
from compiler.codegen.cfg.lower.cfg_ctx import CfgCtx
from compiler.codegen.cfg.lower.checks import CheckState


class PtrPredicates:
    """指针族判定器（无状态，只读查询）。"""

    def __init__(self, ctx: CfgCtx, checks: CheckState) -> None:
        self.__ctx = ctx
        self.__checks = checks

    def is_fat_pointer(self, ptr: IR.Value) -> bool:
        """胖指针判定:PointerType 且 pointee 非 ZST。

        指针-to-ZST 保持 ZST,走既有快路径、无检查;
        FunctionPointerType 非数据指针、不含 5 字段元数据,排除在外。
        诊断模式 raw_pointers 下恒 False:指针一律按裸 8B 处理,全部
        Check* 检查、Delete 与 PtrCmp 路由一并关闭。
        惰性左值路径:裸指针寄存器(未取址左值)同样恒 False——
        裸地址无胖元数据,不可承载检查。
        """
        if self.__ctx.raw_pointers:
            return False
        if self.__checks.is_raw(ptr):
            return False
        ty = self.__ctx.type_ctx[ptr.type_id]
        if not isinstance(ty, Type.PointerType):
            return False
        return not self.__ctx.type_ctx.is_zst(ty.pointee_type)
    def is_fat_view(self, value: IR.Value) -> bool:
        """Whether *value* carries checked slice/str metadata."""
        if self.__ctx.raw_pointers:
            return False
        ty = self.__ctx.type_ctx[value.type_id]
        return isinstance(ty, (Type.SliceType, Type.StrType))
    def is_del_target(self, ptr: IR.Value) -> bool:
        """del 专属目标判定:非 ZST 的 PointerType / SliceType / RefType。

        视图释放路径:T[]/T& 与 T* 同样支持整块释放——释放只按 word 取锁表项
        (key/anchor),三族布局 data/word 前缀相同。两个 raw 守卫完整复刻
        __is_fat_pointer(上方):诊断模式 raw_pointers 恒 False；惰性左值路径中的
        裸指针寄存器也恒为 False，否则 raw 下对裸 8B 指针按胖字段取数会读越界。
        指针-to-ZST 同 __is_fat_pointer 保持非胖(ZST 擦除为空结构,无字段可写、
        无检查可插)。
        """
        if self.__ctx.raw_pointers:
            return False
        if self.__checks.is_raw(ptr):
            return False
        # ZST pointers/references are erased to `{}` in LLVM.  Keep the IR
        # Delete for the backend's no-op path, but do not inspect fat fields.
        if self.__ctx.type_ctx.is_zst(ptr.type_id):
            return False
        ty = self.__ctx.type_ctx[ptr.type_id]
        return isinstance(ty, (Type.PointerType, Type.SliceType, Type.RefType))
