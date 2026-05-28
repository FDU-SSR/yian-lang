from __future__ import annotations

from dataclasses import dataclass

from compiler.analysis.error import AnalysisError
from compiler.analysis.symbol.context import SymbolCtx
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty import type_ops
from compiler.analysis.ty.generic_inference import GenericInference
from compiler.analysis.ty.impl import Impl, ImplRegistry
from compiler.analysis.ty.name import TypeFormatter
from compiler.analysis.ty.space import TypeSpace
from compiler.analysis.ty.resolver import TypeResolver
from compiler.analysis.unit import hir as HIR
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType
from compiler.utils.errors.yian_error import CompilerError
from compiler.utils.IR.position import SrcSpan


class TypeCtx:
    # intrinsic basic type IDs
    void_id: int = 10
    bool_id: int = 11
    char_id: int = 12
    str_id: int = 13

    i8_id: int = 14
    i16_id: int = 15
    i32_id: int = 16
    i64_id: int = 17

    u8_id: int = 18
    u16_id: int = 19
    u32_id: int = 20
    u64_id: int = 21

    f16_id: int = 22
    f32_id: int = 23
    f64_id: int = 24

    int_literal_id: int = 25
    float_literal_id: int = 26

    # intrinsic trait IDs
    add_id: int = 50
    sub_id: int = 51
    mul_id: int = 52
    div_id: int = 53
    rem_id: int = 54
    neg_id: int = 55
    bitand_id: int = 56
    bitor_id: int = 57
    bitxor_id: int = 58
    bitnot_id: int = 59
    shl_id: int = 60
    shr_id: int = 61

    partial_eq_id: int = 70
    partial_ord_id: int = 71

    index_id: int = 80
    contains_id: int = 81
    deref_id: int = 82
    delete_id: int = 83
    drop_id: int = 84

    # intrinsic struct/enum IDs
    Range_id: int = 100
    Option_id: int = 101
    Result_id: int = 102

    def __init__(self):
        self.__space = TypeSpace(self)
        self.__formatter = TypeFormatter(self)

        self.__resolver = TypeResolver(self)
        self.__impl_registry = ImplRegistry(self)
        self.__procedures: dict[int, tuple[AST.Block, int]] = {}  # procedure_id -> procedure block

    def __getitem__(self, type_id: int) -> Type.Ty:
        return self.__space[type_id]

    def __contains__(self, type_id: int) -> bool:
        return type_id in self.__space

    INTRINSIC_TYPE_DICT: dict[Type.IntrinsicType, int] = {
        Type.IntrinsicType.Void: void_id,
        Type.IntrinsicType.Bool: bool_id,
        Type.IntrinsicType.Char: char_id,
        Type.IntrinsicType.Str: str_id,

        Type.IntrinsicType.I8: i8_id,
        Type.IntrinsicType.I16: i16_id,
        Type.IntrinsicType.I32: i32_id,
        Type.IntrinsicType.I64: i64_id,

        Type.IntrinsicType.U8: u8_id,
        Type.IntrinsicType.U16: u16_id,
        Type.IntrinsicType.U32: u32_id,
        Type.IntrinsicType.U64: u64_id,

        Type.IntrinsicType.F16: f16_id,
        Type.IntrinsicType.F32: f32_id,
        Type.IntrinsicType.F64: f64_id,

        Type.IntrinsicType.Int: i64_id,
        Type.IntrinsicType.UInt: u64_id,
        Type.IntrinsicType.Float: f64_id,
    }

    @classmethod
    def intrinsic_type(cls, intrinsic: Type.IntrinsicType) -> int:
        return cls.INTRINSIC_TYPE_DICT[intrinsic]

    INTRINSIC_CUSTOM_TYPE_DICT: dict[Type.IntrinsicCustomType, int] = {
        Type.IntrinsicCustomType.Add: add_id,
        Type.IntrinsicCustomType.Sub: sub_id,
        Type.IntrinsicCustomType.Mul: mul_id,
        Type.IntrinsicCustomType.Div: div_id,
        Type.IntrinsicCustomType.Rem: rem_id,
        Type.IntrinsicCustomType.Neg: neg_id,

        Type.IntrinsicCustomType.BitAnd: bitand_id,
        Type.IntrinsicCustomType.BitOr: bitor_id,
        Type.IntrinsicCustomType.BitXor: bitxor_id,
        Type.IntrinsicCustomType.BitNot: bitnot_id,
        Type.IntrinsicCustomType.Shl: shl_id,
        Type.IntrinsicCustomType.Shr: shr_id,

        Type.IntrinsicCustomType.PartialEq: partial_eq_id,
        Type.IntrinsicCustomType.PartialOrd: partial_ord_id,

        Type.IntrinsicCustomType.Index: index_id,
        Type.IntrinsicCustomType.Contains: contains_id,
        Type.IntrinsicCustomType.Deref: deref_id,
        Type.IntrinsicCustomType.Delete: delete_id,
        Type.IntrinsicCustomType.Drop: drop_id,
    }

    @classmethod
    def intrinsic_custom_type(cls, intrinsic: Type.IntrinsicCustomType) -> int:
        return cls.INTRINSIC_CUSTOM_TYPE_DICT[intrinsic]

    def alloc_generic(self, name: str) -> int:
        return self.__space.alloc_generic(name)

    def alloc_pointer(self, pointee_type: int) -> int:
        return self.__space.alloc_pointer(pointee_type)

    def alloc_slice(self, element_type: int) -> int:
        return self.__space.alloc_slice(element_type)

    def alloc_array(self, element_type: int, length: int) -> int:
        return self.__space.alloc_array(element_type, length)

    def alloc_tuple(self, element_types: list[int]) -> int:
        return self.__space.alloc_tuple(element_types)

    def alloc_function_pointer(self, param_types: list[int], return_type: int) -> int:
        return self.__space.alloc_function_pointer(param_types, return_type)

    def alloc_alias(self, name: str) -> int:
        return self.__space.alloc_alias(name)

    def alloc_struct(self, name: str) -> int:
        return self.__space.alloc_struct(name)

    def alloc_unnamed_struct(self, owner: str, field_names: list[str], field_types: list[int]) -> int:
        return self.__space.alloc_unnamed_struct(owner, field_names, field_types)

    def alloc_enum(self, name: str) -> int:
        return self.__space.alloc_enum(name)

    def alloc_trait(self, name: str) -> int:
        return self.__space.alloc_trait(name)

    def alloc_method(self, name: str) -> int:
        return self.__space.alloc_method(name)

    def alloc_function(self, name: str) -> int:
        return self.__space.alloc_function(name)

    def alloc_range(self, type_id: int) -> int:
        return self.__space.alloc_range(type_id)

    def alloc_instance(self, type_id: int, generic_args: list[int]) -> int:
        return self.__space.alloc_instance(type_id, generic_args)

    def instantiate(self, type_id: int, substs: dict[int, int]) -> int:
        return type_ops.instantiate(self, type_id, substs)

    def get_name(self, type_id: int) -> str:
        return self.__formatter.get_name(type_id)

    def infer_common_type(self, type_ids: list[int], span: SrcSpan, context_name: str) -> int:
        return type_ops.infer_common_type(self, type_ids, span, context_name)

    def is_literal_type(self, type_id: int) -> bool:
        return type_ops.is_literal_type(self, type_id)

    def default_literals(self, type_id: int) -> int:
        return type_ops.default_literals(self, type_id)

    def contains_generic(self, type_id: int) -> bool:
        return type_ops.contains_generic(self, type_id)

    def is_int_literal_type(self, type_id: int) -> bool:
        return type_ops.is_int_literal_type(self, type_id)

    def is_float_literal_type(self, type_id: int) -> bool:
        return type_ops.is_float_literal_type(self, type_id)

    def merge_types(self, left_type_id: int, right_type_id: int, span: SrcSpan) -> int:
        return type_ops.merge_types(self, left_type_id, right_type_id, span)

    def finalize(self) -> None:
        """
        Final check and preparation of the type space before code generation.

        1. Check self-referential types.
        """
        self.__check_self_referential_types()

    def __check_self_referential_types(self) -> None:
        """
        Check for self-referential types that would cause infinite recursion during code generation.
        For example, a struct that contains itself directly or indirectly.

        This is a simple DFS-based cycle detection in the type graph.
        """
        visited: set[int] = set()
        stack: list[int] = []
        in_stack: set[int] = set()

        def visit(type_id: int) -> None:
            if type_id in in_stack:
                chain = " -> ".join(self.get_name(tid) for tid in stack) + f" -> {self.get_name(type_id)}"
                raise CompilerError(f"Self-referential type detected: {chain}")
            if type_id in visited:
                return
            visited.add(type_id)
            stack.append(type_id)
            in_stack.add(type_id)

            try:
                ty = self.__space[type_id]
                match ty:
                    case Type.ArrayType(element_type=element_type):
                        visit(element_type)
                    case Type.TupleType(element_types=element_types):
                        for elem_id in element_types:
                            visit(elem_id)
                    case Type.StructType():
                        for field in ty.get_fields(self):
                            visit(field.type_id)
                    case Type.EnumType():
                        for variant in ty.get_variants(self):
                            if variant.payload_type is not None:
                                visit(variant.payload_type)
                    case _:
                        pass
            finally:
                stack.pop()
                in_stack.remove(type_id)

        for type_id in list(self.__space):
            visit(type_id)

    def is_instance(self, template_id: int, generic_args: list[int], target_id: int) -> bool:
        """
        Check if the instantiated type of `template_id` with `generic_args` is the same as `target_id`.
        """
        template_ty = self.__space[template_id]
        if not isinstance(template_ty, Type.CustomType):
            raise CompilerError(f"Type ID {template_id} is not a custom type and cannot be instantiated")

        target_ty = self.__space[target_id]
        if not isinstance(target_ty, Type.CustomType):
            raise CompilerError(f"Type ID {target_id} is not a custom type and cannot be compared for instance")

        if id(template_ty.custom_def) != id(target_ty.custom_def):
            return False

        for arg_id, target_arg_id in zip(generic_args, target_ty.generic_args):
            if arg_id != target_arg_id:
                return False

        return True

    def resolve_type(self, ty: ASTType, symbol_ctx: SymbolCtx) -> int:
        """
        Resolve an ASTType to a type ID in the type context.

        This is used during type checking to convert the types written in the source code (AST) to the internal type representation.
        """
        return self.__resolver.resolve(ty, symbol_ctx)

    def register_impl(self, span: SrcSpan, generics: list[int], target: int, trait: int | None) -> Impl:
        return self.__impl_registry.register_impl(span, generics, target, trait)

    def check_impls(self) -> None:
        """
        Check the validity of all registered impls.

        This should be called after all impls are registered.
        """
        self.__impl_registry.check_impls()

    def add_procedure(self, type_id: int, body: AST.Block, unit_id: int) -> None:
        ty = self.__space[type_id]
        if isinstance(ty, Type.FunctionType):
            def_id = id(ty.custom_def)
        elif isinstance(ty, Type.MethodType):
            def_id = id(ty.custom_def)
        else:
            raise CompilerError(f"Type ID {type_id} is not a function or method type and cannot be associated with a procedure")

        self.__procedures[def_id] = (body, unit_id)

    def get_procedure(self, type_id: int) -> tuple[AST.Block, int]:
        ty = self.__space[type_id]
        if isinstance(ty, Type.FunctionType):
            def_id = id(ty.custom_def)
        elif isinstance(ty, Type.MethodType):
            def_id = id(ty.custom_def)
        else:
            raise CompilerError(f"Type ID {type_id} is not a function or method type and cannot be associated with a procedure")

        if def_id in self.__procedures:
            return self.__procedures[def_id]
        else:
            raise CompilerError(f"No procedure found for type ID {type_id} with definition ID {def_id}")

    def method_lookup(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr]) -> LookupResult | None:
        """
        Lookup a method for a given caller type. See details in `manual/impl.md`.
        """
        candidates: list[LookupResult] = []

        for impl in self.__impl_registry.iter_impls():
            if method_name not in impl.methods:
                continue

            receiver_inference = GenericInference(self, receiver.span)
            try:
                receiver_inference.constrain(impl.target, receiver.type_id)
                impl_substs = receiver_inference.substitutions()
            except AnalysisError:
                continue

            method_id = impl.methods[method_name]
            instantiated_method_id = self.instantiate(method_id, impl_substs)
            instantiated_method_ty = self[instantiated_method_id]
            assert isinstance(instantiated_method_ty, Type.MethodType)

            if generic_args is not None and len(generic_args) > 0:
                method_generics = instantiated_method_ty.custom_def.generics
                if len(generic_args) > len(method_generics):
                    continue
                explicit_generics = method_generics[len(method_generics) - len(generic_args):]
                explicit_substs = dict(zip(explicit_generics, generic_args))
                instantiated_method_id = self.instantiate(instantiated_method_id, explicit_substs)
                instantiated_method_ty = self[instantiated_method_id]
                assert isinstance(instantiated_method_ty, Type.MethodType)

            parameters = instantiated_method_ty.parameters(self)
            if len(parameters) != len(args):
                continue

            arg_inference = GenericInference(self, receiver.span)
            try:
                for param, arg in zip(parameters, args):
                    arg_inference.constrain(param.type_id, arg.type_id)
                arg_substs = arg_inference.substitutions()
            except AnalysisError:
                continue

            final_substs = impl_substs | arg_substs
            final_method_id = self.instantiate(instantiated_method_id, final_substs)
            candidates.append(LookupResult(method_id=final_method_id, deref_count=0, impl=impl))

        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            raise AnalysisError(f"Ambiguous method '{method_name}' for type '{self.get_name(receiver.type_id)}'", receiver.span)
        return None

    def iter_item_type(self, iter_type_id: int) -> int:
        """
        Get the item type of an iterator type.

        This is used for desugaring for loops, where we need to know the item type of the iterator to type check the loop variable.
        """
        raise NotImplementedError("Iterator item type lookup is not implemented yet")


@dataclass
class LookupResult:
    """Result of method lookup"""
    method_id: int
    deref_count: int
    impl: Impl
