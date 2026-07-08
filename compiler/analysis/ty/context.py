from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty import type_ops
from compiler.analysis.ty.generic_inference import GenericInference
from compiler.analysis.ty.impl import Impl, ImplRegistry
from compiler.analysis.ty.name import TypeFormatter
from compiler.analysis.ty.resolver import TypeResolver
from compiler.analysis.ty.space import TypeSpace
from compiler.analysis.unit import hir as HIR
from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.ast_type import ASTType

if TYPE_CHECKING:
    from compiler.analysis.symbol.context import SymbolCtx


class TypeCtx:
    # intrinsic basic type IDs
    never_id: int = 9
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
    null_ptr_id: int = 27

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

    add_assign_id: int = 90
    sub_assign_id: int = 91
    mul_assign_id: int = 92
    div_assign_id: int = 93
    rem_assign_id: int = 94
    bitand_assign_id: int = 95
    bitor_assign_id: int = 96
    bitxor_assign_id: int = 97
    shl_assign_id: int = 98
    shr_assign_id: int = 99

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

        # Caches for hot-path type queries — the type_id fully encodes the
        # generic instantiation, so the cache key is just the type_id.
        self.__span_cache: dict[int, SrcSpan] = {}
        self.__fields_cache: dict[int, list[Type.StructField]] = {}
        self.__variants_cache: dict[int, list[Type.EnumVariant]] = {}
        self.__params_cache: dict[int, list[Type.Parameter]] = {}
        self.__return_type_cache: dict[int, int] = {}
        self.__receiver_type_cache: dict[int, int] = {}
        self.__methods_cache: dict[int, dict[str, int]] = {}
        self.__default_literals_cache: dict[int, int] = {}
        self.__simple_type_cache: dict[int, bool] = {}
        self.__zst_cache: dict[int, bool] = {}

    def __getitem__(self, type_id: int) -> Type.Ty:
        return self.__space[type_id]

    def items(self):
        return self.__space.items()

    def __contains__(self, type_id: int) -> bool:
        return type_id in self.__space

    INTRINSIC_TYPE_DICT: dict[Type.IntrinsicType, int] = {
        Type.IntrinsicType.Never: never_id,
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

        Type.IntrinsicCustomType.AddAssign: add_assign_id,
        Type.IntrinsicCustomType.SubAssign: sub_assign_id,
        Type.IntrinsicCustomType.MulAssign: mul_assign_id,
        Type.IntrinsicCustomType.DivAssign: div_assign_id,
        Type.IntrinsicCustomType.RemAssign: rem_assign_id,
        Type.IntrinsicCustomType.BitAndAssign: bitand_assign_id,
        Type.IntrinsicCustomType.BitOrAssign: bitor_assign_id,
        Type.IntrinsicCustomType.BitXorAssign: bitxor_assign_id,
        Type.IntrinsicCustomType.ShlAssign: shl_assign_id,
        Type.IntrinsicCustomType.ShrAssign: shr_assign_id,

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

    def alloc_const_generic(self, name: str, value_type: int) -> int:
        return self.__space.alloc_const_generic(name, value_type)

    def alloc_literal_value(self, value: int | bool, value_type: int) -> int:
        return self.__space.alloc_literal_value(value, value_type)

    def try_extract_array_length(self, array_type_id: int) -> int | None:
        arr_ty = self[array_type_id]
        assert isinstance(arr_ty, Type.ArrayType)
        length_ty = self[arr_ty.length]
        if isinstance(length_ty, Type.LiteralValueType):
            return length_ty.value
        return None

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

    def alloc_alias(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_alias(name, span)

    def alloc_struct(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_struct(name, span)

    def alloc_unnamed_struct(self, owner: str, field_names: list[str], field_types: list[int], generics: list[int], span: SrcSpan) -> int:
        return self.__space.alloc_unnamed_struct(owner, field_names, field_types, generics, span)

    def alloc_enum(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_enum(name, span)

    def alloc_trait(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_trait(name, span)

    def alloc_method(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_method(name, span)

    def alloc_function(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_function(name, span)

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

    def is_numeric_type(self, type_id: int, include_literals: bool = True) -> bool:
        return type_ops.is_numeric_type(self, type_id, include_literals)

    def is_integer_type(self, type_id: int, include_literals: bool = True) -> bool:
        return type_ops.is_integer_type(self, type_id, include_literals)

    def is_zst(self, type_id: int) -> bool:
        """Return whether a type is a Zero-Sized Type (carries no runtime info).

        See ``type_ops.is_zst`` for the recursive definition. Results are
        cached by resolved type_id in ``__zst_cache``.
        """
        type_id = self.resolve_aliases(type_id)
        cached = self.__zst_cache.get(type_id)
        if cached is not None:
            return cached
        result = type_ops.is_zst(self, type_id)
        self.__zst_cache[type_id] = result
        return result

    def is_simple_type(self, type_id: int) -> bool:
        """Return True for types that support direct bitwise-copy assignment.

        Simple types (§2.1) are always simple. Composite types are simple
        when all their elements / fields are simple (§5.1). User-defined
        structs/enums with @BitCopy annotation are validated at assignment time.

        Results are cached in __simple_type_cache keyed by (resolved) type_id.
        """
        type_id = self.resolve_aliases(type_id)

        cached = self.__simple_type_cache.get(type_id)
        if cached is not None:
            return cached

        # Zero-sized types carry no runtime data, so a bitwise copy of zero
        # bytes is always valid — they are trivially simple-assignable.
        if self.is_zst(type_id):
            self.__simple_type_cache[type_id] = True
            return True

        ty = self[type_id]
        if isinstance(ty, (Type.IntType, Type.FloatType, Type.BoolType,
                           Type.CharType, Type.StrType, Type.PointerType,
                           Type.FunctionPointerType, Type.SliceType)):
            self.__simple_type_cache[type_id] = True
            return True
        if isinstance(ty, Type.ArrayType):
            result = self.is_simple_type(ty.element_type)
            self.__simple_type_cache[type_id] = result
            return result
        if isinstance(ty, Type.TupleType):
            result = all(self.is_simple_type(et) for et in ty.element_types)
            self.__simple_type_cache[type_id] = result
            return result
        if isinstance(ty, Type.StructType) and ty.custom_def.is_bitcopy:
            self.__validate_bitcopy_struct(type_id)
            self.__simple_type_cache[type_id] = True
            return True
        if isinstance(ty, Type.EnumType) and ty.custom_def.is_bitcopy:
            self.__validate_bitcopy_enum(type_id)
            self.__simple_type_cache[type_id] = True
            return True

        self.__simple_type_cache[type_id] = False
        return False

    def __validate_bitcopy_struct(self, type_id: int) -> None:
        """Validate that all fields of a @BitCopy struct are BitCopy types.

        Raises AnalysisError on failure. The caller is responsible for
        checking/updating __simple_type_cache.
        """
        fields = self.get_struct_fields(type_id)
        for field in fields:
            if not self.is_simple_type(field.type_id):
                raise AnalysisError(
                    f"type '{self.get_name(field.type_id)}' is not BitCopy\n"
                    f"note: all fields of a @BitCopy struct must be BitCopy types",
                    self.get_span(type_id)
                )

    def __validate_bitcopy_enum(self, type_id: int) -> None:
        """Validate that all variant payloads of a @BitCopy enum are BitCopy types.

        Raises AnalysisError on failure. The caller is responsible for
        checking/updating __simple_type_cache.
        """
        variants = self.get_enum_variants(type_id)
        for variant in variants:
            if variant.payload_type is None:
                continue
            payload_fields = self.get_struct_fields(variant.payload_type)
            for field in payload_fields:
                if not self.is_simple_type(field.type_id):
                    raise AnalysisError(
                        f"type '{self.get_name(field.type_id)}' in variant '{variant.name}' is not BitCopy\n"
                        f"note: all variant payloads of a @BitCopy enum must be BitCopy types",
                        self.get_span(type_id)
                    )

    def default_literals(self, type_id: int) -> int:
        cached = self.__default_literals_cache.get(type_id)
        if cached is not None:
            return cached
        result = type_ops.default_literals(self, type_id)
        self.__default_literals_cache[type_id] = result
        return result

    def contains_generic(self, type_id: int) -> bool:
        return type_ops.contains_generic(self, type_id)

    # ------------------------------------------------------------------
    # cached type queries
    # ------------------------------------------------------------------

    def get_span(self, type_id: int) -> SrcSpan:
        """Return the source span of a type, with caching."""
        if type_id in self.__span_cache:
            return self.__span_cache[type_id]
        ty = self[type_id]
        assert isinstance(ty, (Type.AliasType, Type.StructType, Type.EnumType, Type.TraitType, Type.MethodType, Type.FunctionType))
        span = ty.custom_def.span
        self.__span_cache[type_id] = span
        return span

    def get_struct_fields(self, type_id: int) -> list[Type.StructField]:
        """Return the fields of a struct type, with caching."""
        if type_id in self.__fields_cache:
            return self.__fields_cache[type_id]
        ty = self[type_id]
        assert isinstance(ty, Type.StructType)
        fields = ty.get_fields(self)
        self.__fields_cache[type_id] = fields
        return fields

    def get_struct_field_by_name(self, type_id: int, name: str) -> Type.StructField | None:
        """Return a struct field by name, with caching (via get_struct_fields)."""
        for field in self.get_struct_fields(type_id):
            if field.name == name:
                return field
        return None

    def get_enum_variants(self, type_id: int) -> list[Type.EnumVariant]:
        """Return the variants of an enum type, with caching."""
        if type_id in self.__variants_cache:
            return self.__variants_cache[type_id]
        ty = self[type_id]
        assert isinstance(ty, Type.EnumType)
        variants = ty.get_variants(self)
        self.__variants_cache[type_id] = variants
        return variants

    def get_enum_variant_by_name(self, type_id: int, name: str) -> Type.EnumVariant | None:
        """Return an enum variant by name, with caching (via get_enum_variants)."""
        for variant in self.get_enum_variants(type_id):
            if variant.name == name:
                return variant
        return None

    def get_params(self, type_id: int) -> list[Type.Parameter]:
        """Return the parameters of a function or method type, with caching."""
        if type_id in self.__params_cache:
            return self.__params_cache[type_id]
        ty = self[type_id]
        if isinstance(ty, Type.FunctionType):
            params = ty.parameters(self)
        elif isinstance(ty, Type.MethodType):
            params = ty.parameters(self)
        else:
            raise ValueError(f"Expected function or method type, got {type(ty).__name__}")
        self.__params_cache[type_id] = params
        return params

    def get_return_type(self, type_id: int) -> int:
        """Return the return type of a function or method type, with caching."""
        if type_id in self.__return_type_cache:
            return self.__return_type_cache[type_id]
        ty = self[type_id]
        if isinstance(ty, Type.FunctionType):
            ret = ty.return_type(self)
        elif isinstance(ty, Type.MethodType):
            ret = ty.return_type(self)
        else:
            raise ValueError(f"Expected function or method type, got {type(ty).__name__}")
        self.__return_type_cache[type_id] = ret
        return ret

    def get_receiver_type(self, type_id: int) -> int:
        """Return the receiver type of a method type, with caching."""
        if type_id in self.__receiver_type_cache:
            return self.__receiver_type_cache[type_id]
        ty = self[type_id]
        assert isinstance(ty, Type.MethodType)
        recv = ty.receiver_type(self)
        self.__receiver_type_cache[type_id] = recv
        return recv

    def get_trait_methods(self, type_id: int) -> dict[str, int]:
        """Return the methods of a trait type, with caching."""
        if type_id in self.__methods_cache:
            return self.__methods_cache[type_id]
        ty = self[type_id]
        assert isinstance(ty, Type.TraitType)
        methods = ty.get_methods(self)
        self.__methods_cache[type_id] = methods
        return methods

    # ------------------------------------------------------------------

    def merge_types(self, type_ids: list[int], span: SrcSpan) -> int:
        return type_ops.merge_types(self, type_ids, span)

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
                        for field in self.get_struct_fields(ty.type_id):
                            visit(field.type_id)
                    case Type.EnumType():
                        for variant in self.get_enum_variants(ty.type_id):
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

    def resolve_aliases(self, type_id: int) -> int:
        """Follow alias chains to the first non-alias concrete type."""
        visited: set[int] = set()
        while True:
            if type_id in visited:
                raise CompilerError(f"Circular type alias detected: {self.get_name(type_id)}")
            visited.add(type_id)
            ty = self[type_id]
            if isinstance(ty, Type.AliasType):
                type_id = ty.custom_def.aliased_type
            else:
                return type_id

    def register_impl(self, span: SrcSpan, generics: list[int], target: int, trait: int | None, conditions: dict[int, list[int]] | None = None) -> Impl:
        return self.__impl_registry.register_impl(span, generics, target, trait, conditions)

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
        raise CompilerError(f"No procedure found for type ID {type_id} with definition ID {def_id}")

    def try_deref(self, type_id: int) -> int | None:
        """
        Perform one dereference step at the type level (no HIR nodes generated).

        Handles two cases:
        - Pointer types: return the pointee type directly
        - Types implementing the Deref trait: return deref()'s return type

        Returns None if the type cannot be dereferenced.
        """
        ty = self[type_id]
        if isinstance(ty, Type.PointerType):
            return ty.pointee_type
        return self.__impl_registry.find_deref_target(type_id)

    def deref_chain(self, type_id: int) -> list[int]:
        """
        Repeatedly apply try_deref until no more dereferences are possible.
        Returns the list of types encountered: [original, deref1, deref2, ...].
        """
        chain = [type_id]
        current = type_id
        while True:
            next_ty = self.try_deref(current)
            if next_ty is None:
                break
            chain.append(next_ty)
            current = next_ty
        return chain

    def method_lookup(self, receiver: HIR.Expr, method_name: str, generic_args: list[int] | None, args: list[HIR.Expr]) -> LookupResult | None:
        """
        Lookup a method for a given caller type. See details in `manual/impl.md`.

        Iterates through the deref chain of the receiver type, searching for
        matching method implementations at each level. Returns the first match
        with the fewest dereferences.
        """
        chain = self.deref_chain(receiver.type_id)

        for deref_count, type_at_level in enumerate(chain):
            candidates: list[LookupResult] = []

            for impl in self.__impl_registry.iter_candidate_impls(type_at_level):
                if method_name not in impl.methods:
                    continue

                receiver_inference = GenericInference(self, receiver.span)
                try:
                    receiver_inference.constrain(impl.target, type_at_level)
                    impl_substs = receiver_inference.substitutions()
                except AnalysisError:
                    continue

                if not self.__impl_registry.check_conditions(impl, impl_substs):
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

                parameters = self.get_params(instantiated_method_id)
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
                candidates.append(LookupResult(method_id=final_method_id, deref_count=deref_count, impl=impl))

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
        receiver = HIR.Ty(span=SrcSpan.empty(), type_id=iter_type_id, is_place=False)
        lookup = self.method_lookup(receiver, "next", None, [])
        if lookup is None:
            raise CompilerError(f"Type '{self.get_name(iter_type_id)}' does not provide a next() method")

        method_ty = self[lookup.method_id]
        if not isinstance(method_ty, Type.MethodType):
            raise CompilerError(f"Method lookup for type '{self.get_name(iter_type_id)}' did not resolve to a method type")

        return_type_id = self.get_return_type(lookup.method_id)
        return_ty = self[return_type_id]
        if isinstance(return_ty, Type.EnumType) and return_ty.custom_def.name == "Option" and len(return_ty.generic_args) == 1:
            return return_ty.generic_args[0]

        raise CompilerError(
            f"Iterator next() for type '{self.get_name(iter_type_id)}' must return Option<T>, got '{self.get_name(return_type_id)}'"
        )


@dataclass
class LookupResult:
    """Result of method lookup"""
    method_id: int
    deref_count: int
    impl: Impl
