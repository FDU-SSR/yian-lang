from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty import type_ops
from compiler.analysis.ty.generic_inference import GenericInference
from compiler.analysis.ty.impl import Impl, ImplRegistry
from compiler.analysis.ty.intrinsics import IntrinsicIds
from compiler.analysis.ty.name import TypeFormatter
from compiler.analysis.ty.space import TypeSpace
from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan


class _AmbiguousMethod:
    """Cache marker: a method lookup resolved to multiple candidates."""

    __slots__ = ()


_AMBIGUOUS_METHOD = _AmbiguousMethod()


class TypeCtx(IntrinsicIds):
    def __init__(self, raw_pointers: bool = False):
        self.__raw_pointers = raw_pointers

        self.__space = TypeSpace(self)
        self.__formatter = TypeFormatter(self)

        self.__impl_registry = ImplRegistry(self)

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
        self.__zst_cache: dict[int, bool] = {}

        # Type and impl registration is complete after GlobalResolve. These
        # caches stay disabled until finalize() so a partially built type
        # space can never produce a stale negative result.
        self.__memoize_enabled = False
        self.__method_lookup_cache: dict[tuple[object, ...], LookupResult | None | _AmbiguousMethod] = {}
        self.__deref_chain_cache: dict[int, tuple[int, ...]] = {}

    @property
    def raw_pointers(self) -> bool:
        """True when --raw-pointers mode is active (bare 8B pointers, no fat-pointer checks)."""
        return self.__raw_pointers

    def __getitem__(self, type_id: int) -> Type.Ty:
        return self.__space[type_id]

    def contains_ffi_type(self, type_id: int) -> bool:
        """Whether a signature exposes a C-only type rather than an opaque YIAN wrapper."""
        ty = self[type_id]
        if isinstance(ty, (Type.CScalarType, Type.CPtrType, Type.OpaqueType)):
            return True
        if isinstance(ty, (Type.PointerType, Type.RefType)):
            return self.contains_ffi_type(ty.pointee_type)
        if isinstance(ty, (Type.ArrayType, Type.SliceType)):
            return self.contains_ffi_type(ty.element_type)
        if isinstance(ty, Type.TupleType):
            return any(self.contains_ffi_type(item) for item in ty.element_types)
        if isinstance(ty, Type.FunctionPointerType):
            return any(self.contains_ffi_type(item) for item in ty.parameter_types) \
                or self.contains_ffi_type(ty.return_type)
        if isinstance(ty, (Type.StructType, Type.EnumType)):
            return any(self.contains_ffi_type(item) for item in ty.generic_args)
        return False

    def contains_bare_opaque(self, type_id: int) -> bool:
        """Opaque C declarations have no value representation outside cptr<T>."""
        ty = self[type_id]
        if isinstance(ty, Type.OpaqueType):
            return True
        if isinstance(ty, Type.CPtrType):
            return False
        if isinstance(ty, (Type.PointerType, Type.RefType)):
            return self.contains_bare_opaque(ty.pointee_type)
        if isinstance(ty, (Type.ArrayType, Type.SliceType)):
            return self.contains_bare_opaque(ty.element_type)
        if isinstance(ty, Type.TupleType):
            return any(self.contains_bare_opaque(item) for item in ty.element_types)
        if isinstance(ty, Type.FunctionPointerType):
            return any(self.contains_bare_opaque(item) for item in ty.parameter_types) \
                or self.contains_bare_opaque(ty.return_type)
        if isinstance(ty, (Type.StructType, Type.EnumType)):
            return any(self.contains_bare_opaque(item) for item in ty.generic_args)
        return False

    def is_c_abi_type(self, type_id: int, *, result: bool = False) -> bool:
        """Validate one scalar or native pointer in an extern C declaration."""
        ty = self[type_id]
        if result and isinstance(ty, Type.VoidType):
            return True
        if isinstance(ty, Type.CPtrType):
            pointee = self[ty.pointee_type]
            return isinstance(pointee, (Type.IntType, Type.OpaqueType)) \
                or isinstance(pointee, Type.FloatType) and pointee.size in (4, 8) \
                or isinstance(pointee, Type.CPtrType) and self.is_c_abi_type(ty.pointee_type)
        if isinstance(ty, Type.IntType):
            return True
        return isinstance(ty, Type.FloatType) and ty.size in (4, 8)

    def items(self):
        return self.__space.items()

    def __contains__(self, type_id: int) -> bool:
        return type_id in self.__space

    INTRINSIC_TYPE_DICT: dict[Type.IntrinsicType, int] = {
        Type.IntrinsicType.Never: IntrinsicIds.never_id,
        Type.IntrinsicType.Void: IntrinsicIds.void_id,
        Type.IntrinsicType.Error: IntrinsicIds.error_id,
        Type.IntrinsicType.Bool: IntrinsicIds.bool_id,
        Type.IntrinsicType.Char: IntrinsicIds.char_id,
        Type.IntrinsicType.Str: IntrinsicIds.str_id,

        Type.IntrinsicType.I8: IntrinsicIds.i8_id,
        Type.IntrinsicType.I16: IntrinsicIds.i16_id,
        Type.IntrinsicType.I32: IntrinsicIds.i32_id,
        Type.IntrinsicType.I64: IntrinsicIds.i64_id,

        Type.IntrinsicType.U8: IntrinsicIds.u8_id,
        Type.IntrinsicType.U16: IntrinsicIds.u16_id,
        Type.IntrinsicType.U32: IntrinsicIds.u32_id,
        Type.IntrinsicType.U64: IntrinsicIds.u64_id,

        Type.IntrinsicType.F16: IntrinsicIds.f16_id,
        Type.IntrinsicType.F32: IntrinsicIds.f32_id,
        Type.IntrinsicType.F64: IntrinsicIds.f64_id,

        Type.IntrinsicType.Int: IntrinsicIds.i64_id,
        Type.IntrinsicType.UInt: IntrinsicIds.u64_id,
        Type.IntrinsicType.Float: IntrinsicIds.f64_id,
    }

    @classmethod
    def intrinsic_type(cls, intrinsic: Type.IntrinsicType) -> int:
        return cls.INTRINSIC_TYPE_DICT[intrinsic]

    INTRINSIC_CUSTOM_TYPE_DICT: dict[Type.IntrinsicCustomType, int] = {
        Type.IntrinsicCustomType.Add: IntrinsicIds.add_id,
        Type.IntrinsicCustomType.Sub: IntrinsicIds.sub_id,
        Type.IntrinsicCustomType.Mul: IntrinsicIds.mul_id,
        Type.IntrinsicCustomType.Div: IntrinsicIds.div_id,
        Type.IntrinsicCustomType.Rem: IntrinsicIds.rem_id,
        Type.IntrinsicCustomType.Neg: IntrinsicIds.neg_id,

        Type.IntrinsicCustomType.BitAnd: IntrinsicIds.bitand_id,
        Type.IntrinsicCustomType.BitOr: IntrinsicIds.bitor_id,
        Type.IntrinsicCustomType.BitXor: IntrinsicIds.bitxor_id,
        Type.IntrinsicCustomType.BitNot: IntrinsicIds.bitnot_id,
        Type.IntrinsicCustomType.Shl: IntrinsicIds.shl_id,
        Type.IntrinsicCustomType.Shr: IntrinsicIds.shr_id,

        Type.IntrinsicCustomType.PartialEq: IntrinsicIds.partial_eq_id,
        Type.IntrinsicCustomType.PartialOrd: IntrinsicIds.partial_ord_id,

        Type.IntrinsicCustomType.AddAssign: IntrinsicIds.add_assign_id,
        Type.IntrinsicCustomType.SubAssign: IntrinsicIds.sub_assign_id,
        Type.IntrinsicCustomType.MulAssign: IntrinsicIds.mul_assign_id,
        Type.IntrinsicCustomType.DivAssign: IntrinsicIds.div_assign_id,
        Type.IntrinsicCustomType.RemAssign: IntrinsicIds.rem_assign_id,
        Type.IntrinsicCustomType.BitAndAssign: IntrinsicIds.bitand_assign_id,
        Type.IntrinsicCustomType.BitOrAssign: IntrinsicIds.bitor_assign_id,
        Type.IntrinsicCustomType.BitXorAssign: IntrinsicIds.bitxor_assign_id,
        Type.IntrinsicCustomType.ShlAssign: IntrinsicIds.shl_assign_id,
        Type.IntrinsicCustomType.ShrAssign: IntrinsicIds.shr_assign_id,

        Type.IntrinsicCustomType.Index: IntrinsicIds.index_id,
        Type.IntrinsicCustomType.Contains: IntrinsicIds.contains_id,
        Type.IntrinsicCustomType.Deref: IntrinsicIds.deref_id,
        Type.IntrinsicCustomType.Delete: IntrinsicIds.delete_id,
    }

    @classmethod
    def intrinsic_custom_type(cls, intrinsic: Type.IntrinsicCustomType) -> int:
        return cls.INTRINSIC_CUSTOM_TYPE_DICT[intrinsic]

    def alloc_generic(self, name: str) -> int:
        return self.__space.alloc_generic(name)

    def alloc_self_type(self, trait_type_id: int) -> int:
        return self.__space.alloc_self_type(trait_type_id)

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

    def alloc_cptr(self, pointee_type: int) -> int:
        return self.__space.alloc_cptr(pointee_type)

    def alloc_opaque(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_opaque(name, span)

    def alloc_ref(self, pointee_type: int) -> int:
        if isinstance(self[pointee_type], Type.TraitType):
            return self.alloc_trait_object(pointee_type)
        return self.__space.alloc_ref(pointee_type)

    def alloc_trait_object(self, trait_type_id: int) -> int:
        if not isinstance(self[trait_type_id], Type.TraitType):
            raise CompilerError(f"{self.get_name(trait_type_id)} is not a trait type")
        return self.__space.alloc_trait_object(trait_type_id)

    def alloc_slice(self, element_type: int) -> int:
        return self.__space.alloc_slice(element_type)

    def alloc_array(self, element_type: int, length: int) -> int:
        return self.__space.alloc_array(element_type, length)

    def alloc_tuple(self, element_types: Sequence[int]) -> int:
        return self.__space.alloc_tuple(element_types)

    def alloc_function_pointer(self, param_types: Sequence[int], return_type: int) -> int:
        return self.__space.alloc_function_pointer(param_types, return_type)

    def try_builtin_ctor(self, name: str, arg_ids: list[int]) -> int | None:
        """Resolve a hardcoded built-in type constructor.

        ``Tuple<A, B, ...>`` and ``Fn<(A, B), R>`` are variadic or
        type-structure-unpacking and cannot be expressed as library
        ``typedef``.  Return the concrete type id, or ``None`` if
        *name* is not a hardcoded constructor.
        """
        if name == "Tuple":
            return self.alloc_tuple(arg_ids)
        if name == "Fn":
            if len(arg_ids) != 2:
                raise CompilerError("Fn<...> requires exactly two arguments: (param_tuple, return_type)")
            params_ty = self[arg_ids[0]]
            if not isinstance(params_ty, Type.TupleType):
                raise CompilerError("The first argument to Fn<...> must be a tuple type")
            return self.alloc_function_pointer(params_ty.element_types, arg_ids[1])
        return None


    def alloc_struct(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_struct(name, span)

    def alloc_unnamed_struct(self, owner: str, field_names: list[str], field_types: list[int], generics: Sequence[int], span: SrcSpan, field_spans: list[SrcSpan] | None = None) -> int:
        return self.__space.alloc_unnamed_struct(owner, field_names, field_types, generics, span, field_spans)

    def alloc_enum(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_enum(name, span)

    def alloc_trait(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_trait(name, span)

    def alloc_method(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_method(name, span)

    def alloc_function(self, name: str, span: SrcSpan) -> int:
        return self.__space.alloc_function(name, span)

    def alloc_closure(self, captured_vars: list[Type.CapturedVar], parameters: list[Type.Parameter], return_type: int, span: SrcSpan) -> int:
        return self.__space.alloc_closure(captured_vars, parameters, return_type, span)

    def alloc_range(self, type_id: int) -> int:
        return self.__space.alloc_range(type_id)

    def bind_template(self, type_id: int, generics: Sequence[int], arguments: Sequence[int] | None = None) -> None:
        self.__space.bind_template(type_id, generics, arguments)

    def alloc_instance(self, type_id: int, generic_args: Sequence[int]) -> int:
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

    def methods_of(self, type_id: int) -> Sequence[tuple[str, int]]:
        """Methods that can apply to *type_id*, as ``(name, method type id)``.

        Impls whose target cannot be this receiver are filtered out, so completion
        does not offer a generic type's methods on an unrelated one.  A
        conditional generic impl can still be listed; evaluating its conditions
        per name is what :meth:`method_lookup` does, and completion trades that
        precision for listing every reachable member.
        """
        ty = self[type_id]
        if isinstance(ty, Type.SelfType):
            return tuple(self.get_trait_methods(ty.trait_type_id).items())
        return self.__impl_registry.methods_of(type_id)


    def is_same_type(self, left: int, right: int) -> bool:
        """Compare type identity, accepting the error type during recovery."""
        return type_ops.same(self, left, right)

    def is_zst(self, type_id: int) -> bool:
        """Return whether a type is a Zero-Sized Type (carries no runtime info).

        See ``type_ops.is_zst`` for the recursive definition. Results are
        cached by resolved type_id in ``__zst_cache``.
        """
        cached = self.__zst_cache.get(type_id)
        if cached is not None:
            return cached
        result = type_ops.is_zst(self, type_id)
        self.__zst_cache[type_id] = result
        return result

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
        assert isinstance(ty, (Type.StructType, Type.EnumType, Type.TraitType, Type.MethodType, Type.FunctionType))
        span = ty.custom_def.span
        self.__span_cache[type_id] = span
        return span

    def get_struct_fields(self, type_id: int) -> list[Type.StructField]:
        """Return the instantiated fields of a struct type, with caching."""
        if type_id in self.__fields_cache:
            return self.__fields_cache[type_id]
        ty = self[type_id]
        assert isinstance(ty, Type.StructType)
        fields = ty.get_fields(self)
        self.__fields_cache[type_id] = fields
        return fields

    def invalidate_struct_fields_cache(self, type_id: int) -> None:
        self.__fields_cache.pop(type_id, None)

    def get_struct_field_by_name(self, type_id: int, name: str) -> Type.StructField | None:
        """Return a struct field by name, with caching (via get_struct_fields)."""
        for field in self.get_struct_fields(type_id):
            if field.name == name:
                return field
        return None

    def get_enum_variants(self, type_id: int) -> list[Type.EnumVariant]:
        """Return the instantiated variants of an enum type, with caching."""
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
        """Return the instantiated parameters of a function or method, with caching."""
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

    def get_trait_impl(self, target_type_id: int, trait_type_id: int) -> tuple[Impl, dict[int, int]] | None:
        """Find the unique concrete impl selected for a trait-object conversion."""
        return self.__impl_registry.find_trait_impl(target_type_id, trait_type_id)

    def check_trait_object_safe(self, trait_type_id: int, span: SrcSpan) -> None:
        """Validate the trait restrictions required by its dynamic method table."""
        trait_ty = self[trait_type_id]
        if not isinstance(trait_ty, Type.TraitType):
            raise AnalysisError(f"'{self.get_name(trait_type_id)}' is not a trait", span)
        if len(trait_ty.generic_args) != len(trait_ty.custom_def.generics):
            raise AnalysisError(
                f"trait object type '{self.get_name(trait_type_id)}' requires all generic arguments",
                span,
            )

        def contains_self(type_id: int, seen: set[int]) -> bool:
            if type_id in seen:
                return False
            seen.add(type_id)
            ty = self[type_id]
            if isinstance(ty, Type.SelfType):
                owner_ty = self[ty.trait_type_id]
                return isinstance(owner_ty, Type.TraitType) \
                    and owner_ty.custom_def is trait_ty.custom_def
            if isinstance(ty, (Type.PointerType, Type.RefType)):
                return contains_self(ty.pointee_type, seen)
            if isinstance(ty, Type.TraitObjectType):
                return contains_self(ty.trait_type_id, seen)
            if isinstance(ty, Type.SliceType):
                return contains_self(ty.element_type, seen)
            if isinstance(ty, Type.ArrayType):
                return contains_self(ty.element_type, seen) or contains_self(ty.length, seen)
            if isinstance(ty, Type.TupleType):
                return any(contains_self(item, seen) for item in ty.element_types)
            if isinstance(ty, Type.FunctionPointerType):
                return any(contains_self(item, seen) for item in ty.parameter_types) \
                    or contains_self(ty.return_type, seen)
            if isinstance(ty, Type.CustomType):
                return any(contains_self(item, seen) for item in ty.generic_args)
            return False

        for method_name, method_id in self.get_trait_methods(trait_type_id).items():
            method_ty = self[method_id]
            assert isinstance(method_ty, Type.MethodType)
            if method_ty.custom_def.is_static:
                continue
            method_generic_count = len(method_ty.custom_def.generics) - len(trait_ty.custom_def.generics)
            if method_generic_count > 0:
                raise AnalysisError(
                    f"trait '{trait_ty.custom_def.name}' is not object-safe: instance method "
                    f"'{method_name}' has generic parameters",
                    method_ty.custom_def.span,
                )
            for parameter in self.get_params(method_id):
                if contains_self(parameter.type_id, set()):
                    raise AnalysisError(
                        f"trait '{trait_ty.custom_def.name}' is not object-safe: instance method "
                        f"'{method_name}' uses Self in a parameter",
                        parameter.span or trait_ty.custom_def.span,
                    )
            return_type = self.get_return_type(method_id)
            if contains_self(return_type, set()):
                raise AnalysisError(
                    f"trait '{trait_ty.custom_def.name}' is not object-safe: instance method "
                    f"'{method_name}' returns Self",
                    method_ty.custom_def.span,
                )

    # ------------------------------------------------------------------

    def merge_types(self, type_ids: list[int], span: SrcSpan) -> int:
        return type_ops.merge_types(self, type_ids, span)

    def finalize(self) -> None:
        """
        Check finalized type constraints and enable memoized type and impl queries.
        """
        self.__check_self_referential_types()
        self.__check_unsized_trait_values()
        for _, ty in list(self.__space.items()):
            if isinstance(ty, Type.TraitObjectType):
                self.check_trait_object_safe(ty.trait_type_id, self.get_span(ty.trait_type_id))
        self.__memoize_enabled = True
        self.__impl_registry.enable_memoization()

    def __check_unsized_trait_values(self) -> None:
        """Reject trait markers in runtime value positions; only Trait& is sized."""
        def contains_unsized_trait(type_id: int, visiting: set[int]) -> bool:
            if type_id in visiting:
                return False
            visiting.add(type_id)
            ty = self[type_id]
            if isinstance(ty, Type.TraitType):
                return True
            if isinstance(ty, Type.TraitObjectType):
                return False
            if isinstance(ty, (Type.PointerType, Type.RefType)):
                return contains_unsized_trait(ty.pointee_type, visiting)
            if isinstance(ty, Type.SliceType):
                return contains_unsized_trait(ty.element_type, visiting)
            if isinstance(ty, Type.ArrayType):
                return contains_unsized_trait(ty.element_type, visiting)
            if isinstance(ty, Type.TupleType):
                return any(contains_unsized_trait(item, visiting) for item in ty.element_types)
            if isinstance(ty, Type.FunctionPointerType):
                return any(contains_unsized_trait(item, visiting) for item in ty.parameter_types) \
                    or contains_unsized_trait(ty.return_type, visiting)
            if isinstance(ty, Type.CustomType):
                return any(contains_unsized_trait(item, visiting) for item in ty.generic_args)
            return False

        for _, ty in list(self.__space.items()):
            if isinstance(ty, (Type.ArrayType, Type.TupleType, Type.PointerType, Type.RefType, Type.FunctionPointerType)):
                if contains_unsized_trait(ty.type_id, set()):
                    raise AnalysisError(
                        f"type '{self.get_name(ty.type_id)}' contains an unsized trait value; use 'Trait&'",
                        SrcSpan.empty(),
                    )
            elif isinstance(ty, Type.StructType):
                for field in self.get_struct_fields(ty.type_id):
                    if contains_unsized_trait(field.type_id, set()):
                        raise AnalysisError(
                            f"field '{field.name}' has unsized trait type; use 'Trait&'",
                            field.span or ty.custom_def.span,
                        )
            elif isinstance(ty, Type.EnumType):
                for variant in self.get_enum_variants(ty.type_id):
                    if variant.payload_type is not None and contains_unsized_trait(variant.payload_type, set()):
                        raise AnalysisError(
                            f"variant '{variant.name}' has unsized trait payload; use 'Trait&'",
                            variant.span or ty.custom_def.span,
                        )
            elif isinstance(ty, (Type.FunctionType, Type.MethodType)):
                for parameter in self.get_params(ty.type_id):
                    if contains_unsized_trait(parameter.type_id, set()):
                        raise AnalysisError(
                            f"parameter '{parameter.name}' has unsized trait type; use 'Trait&'",
                            parameter.span or ty.custom_def.span,
                        )
                if contains_unsized_trait(self.get_return_type(ty.type_id), set()):
                    raise AnalysisError(
                        f"function '{ty.custom_def.name}' returns unsized trait type; use 'Trait&'",
                        ty.custom_def.span,
                    )

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


    def register_impl(self, span: SrcSpan, generics: list[int], target: int, trait: int | None, conditions: dict[int, list[int]] | None = None, *, automatic: bool = False) -> Impl:
        return self.__impl_registry.register_impl(span, generics, target, trait, conditions, automatic=automatic)

    def check_impls(self) -> tuple[tuple[int, int], ...]:
        """
        Check the validity of all registered impls.

        This should be called after all impls are registered.
        """
        return self.__impl_registry.check_impls()

    def try_deref(self, type_id: int) -> int | None:
        """
        Perform one dereference step at the type level (no HIR nodes generated).

        Handles two cases:
        - Pointer / reference types: return the pointee type directly
        - Types implementing the Deref trait: return deref()'s return type

        Returns None if the type cannot be dereferenced.
        """
        ty = self[type_id]
        if isinstance(ty, (Type.PointerType, Type.RefType)):
            return ty.pointee_type
        return self.__impl_registry.find_deref_target(type_id)

    def deref_chain(self, type_id: int) -> list[int]:
        """
        Repeatedly apply try_deref until no more dereferences are possible.
        Returns the list of types encountered: [original, deref1, deref2, ...].
        """
        if self.__memoize_enabled:
            cached = self.__deref_chain_cache.get(type_id)
            if cached is not None:
                return list(cached)
        chain = [type_id]
        current = type_id
        while True:
            next_ty = self.try_deref(current)
            if next_ty is None:
                break
            chain.append(next_ty)
            current = next_ty
        if self.__memoize_enabled:
            self.__deref_chain_cache[type_id] = tuple(chain)
        return chain

    def method_lookup(self, receiver_type_id: int, receiver_span: SrcSpan, method_name: str, generic_args: list[int] | None, arg_type_ids: list[int]) -> LookupResult | None:
        """
        Lookup a method for a given caller type. See details in `manual/impl.md`.

        Iterates through the deref chain of the receiver type, searching for
        matching method implementations at each level. Returns the first match
        with the fewest dereferences.
        """
        receiver_type = receiver_type_id
        cache_key = (receiver_type, method_name, tuple(generic_args or ()), tuple(arg_type_ids))
        if self.__memoize_enabled:
            cache = self.__method_lookup_cache
            if cache_key in cache:
                cached = cache[cache_key]
                if isinstance(cached, _AmbiguousMethod):
                    raise AnalysisError(
                        f"Ambiguous method '{method_name}' for type '{self.get_name(receiver_type_id)}'",
                        receiver_span,
                    )
                return cached

        chain = self.deref_chain(receiver_type)

        result: LookupResult | None = None
        for deref_count, type_at_level in enumerate(chain):
            candidates: list[LookupResult] = []

            candidate_impls = self.__impl_registry.iter_candidate_impls(type_at_level)
            type_at_level_ty = self[type_at_level]
            if isinstance(type_at_level_ty, Type.TraitObjectType):
                self.check_trait_object_safe(type_at_level_ty.trait_type_id, receiver_span)
                dynamic_lookup = self.__trait_object_method_lookup(
                    receiver_span,
                    type_at_level,
                    type_at_level_ty.trait_type_id,
                    method_name,
                    generic_args,
                    arg_type_ids,
                )
                if dynamic_lookup is not None:
                    dynamic_lookup.deref_count = deref_count
                    result = dynamic_lookup
                    break
                continue
            if isinstance(type_at_level_ty, Type.SelfType):
                trait_ty = self[type_at_level_ty.trait_type_id]
                if not isinstance(trait_ty, Type.TraitType):
                    raise CompilerError("Self type owner is not a trait")
                candidate_impls = [
                    Impl(
                        span=trait_ty.custom_def.span,
                        generics=[],
                        target=type_at_level,
                        trait=type_at_level_ty.trait_type_id,
                        methods=self.get_trait_methods(type_at_level_ty.trait_type_id),
                    ),
                    *candidate_impls,
                ]

            for impl in candidate_impls:
                if method_name not in impl.methods:
                    continue

                receiver_inference = GenericInference(self, receiver_span)
                try:
                    receiver_inference.constrain(impl.target, type_at_level)
                    impl_substs = receiver_inference.substitutions()
                except AnalysisError:
                    continue

                if impl.trait is not None and self.contains_generic(self.instantiate(impl.trait, impl_substs)):
                    conditioned_substs = self.__impl_registry.infer_condition_substs(impl, impl_substs, receiver_span)
                    if conditioned_substs is None:
                        continue
                    impl_substs = conditioned_substs
                elif not self.__impl_registry.check_conditions(impl, impl_substs):
                    continue

                method_id = impl.methods[method_name]
                instantiated_method_id = self.instantiate(method_id, impl_substs)
                instantiated_method_ty = self[instantiated_method_id]
                assert isinstance(instantiated_method_ty, Type.MethodType)

                if generic_args is not None and len(generic_args) > 0:
                    method_generics = self.__declared_method_generics(impl, method_name, instantiated_method_ty)
                    if len(generic_args) > len(method_generics):
                        continue
                    explicit_generics = method_generics[len(method_generics) - len(generic_args):]
                    explicit_substs = dict(zip(explicit_generics, generic_args))
                    instantiated_method_id = self.instantiate(instantiated_method_id, explicit_substs)
                    instantiated_method_ty = self[instantiated_method_id]
                    assert isinstance(instantiated_method_ty, Type.MethodType)

                parameters = self.get_params(instantiated_method_id)
                if len(parameters) != len(arg_type_ids):
                    continue

                arg_inference = GenericInference(self, receiver_span)
                try:
                    for param, arg_type_id in zip(parameters, arg_type_ids):
                        arg_inference.constrain(param.type_id, arg_type_id)
                    arg_substs = arg_inference.substitutions()
                except AnalysisError:
                    continue

                final_substs = impl_substs | arg_substs
                final_method_id = self.instantiate(instantiated_method_id, final_substs)
                candidates.append(LookupResult(method_id=final_method_id, deref_count=deref_count, impl=impl))

            if len(candidates) == 1:
                result = candidates[0]
                break
            if len(candidates) > 1:
                if self.__memoize_enabled:
                    self.__method_lookup_cache[cache_key] = _AMBIGUOUS_METHOD
                raise AnalysisError(f"Ambiguous method '{method_name}' for type '{self.get_name(receiver_type_id)}'", receiver_span)

        if self.__memoize_enabled:
            self.__method_lookup_cache[cache_key] = result
        return result

    def __declared_method_generics(self, impl: Impl, method_name: str, method_ty: Type.MethodType) -> Sequence[int]:
        """Return method-owned generics without enclosing trait or impl parameters."""
        generics = method_ty.custom_def.generics
        if impl.trait is not None:
            trait_ty = self[impl.trait]
            assert isinstance(trait_ty, Type.TraitType)
            trait_method_id = self.get_trait_methods(impl.trait).get(method_name)
            if trait_method_id is not None:
                trait_method_ty = self[trait_method_id]
                assert isinstance(trait_method_ty, Type.MethodType)
                template_generics = trait_method_ty.custom_def.generics
                if generics[:len(template_generics)] == template_generics:
                    return template_generics[len(trait_ty.custom_def.generics):]
        return generics[len(impl.generics):]

    def trait_method_lookup(
        self, self_type_id: int, trait_type_id: int, trait_args: list[int | None],
        method_name: str, generic_args: list[int], arg_type_ids: list[int], span: SrcSpan,
    ) -> LookupResult:
        """Select a static method by trait identity and exact Self type.

        Missing trait arguments are inferred from the selected implementation,
        independently of the expected result type of the surrounding expression.
        """
        trait_ty = self[trait_type_id]
        if not isinstance(trait_ty, Type.TraitType):
            raise AnalysisError("trait-qualified call requires a trait", span)
        if len(trait_args) != len(trait_ty.custom_def.generics):
            raise AnalysisError("trait-qualified call requires all trait arguments", span)
        declared_id = self.get_trait_methods(trait_ty.type_id).get(method_name)
        if declared_id is None:
            raise AnalysisError(f"Unknown trait method '{method_name}'", span)
        declared = self[declared_id]
        assert isinstance(declared, Type.MethodType)
        if not declared.is_static:
            raise AnalysisError("trait-qualified calls require a static method", span)

        candidates: list[LookupResult] = []
        incomplete = False
        for impl in self.__impl_registry.iter_candidate_impls(self_type_id):
            if impl.trait is None:
                continue
            impl_trait = self[impl.trait]
            if not isinstance(impl_trait, Type.TraitType) or impl_trait.custom_def is not trait_ty.custom_def:
                continue
            method_id = impl.methods[method_name]
            method_ty = self[method_id]
            assert isinstance(method_ty, Type.MethodType)
            method_generics = self.__declared_method_generics(impl, method_name, method_ty)
            if len(generic_args) > len(method_generics):
                continue
            parameters = self.get_params(method_id)
            if len(parameters) != len(arg_type_ids):
                continue

            inference = GenericInference(self, span)
            try:
                inference.constrain(impl.target, self_type_id)
                for pattern, actual in zip(impl_trait.generic_args, trait_args):
                    if actual is not None:
                        inference.constrain(pattern, actual)
                for pattern, actual in zip(method_generics, generic_args):
                    inference.constrain(pattern, actual)
                substs = inference.substitutions()
                argument_inference = GenericInference(self, span)
                for parameter, actual in zip(parameters, arg_type_ids):
                    argument_inference.constrain(self.instantiate(parameter.type_id, substs), actual)
                substs |= argument_inference.substitutions()
            except AnalysisError:
                continue

            # Concrete call constraints precede condition inference so a known
            # source type cannot become ambiguous among unrelated conversions.
            conditioned = self.__impl_registry.infer_condition_substs(impl, substs, span)
            if conditioned is None:
                continue
            resolved_target = self.instantiate(impl.target, conditioned)
            if resolved_target != self_type_id:
                continue
            resolved_trait = self.instantiate(impl.trait, conditioned)
            final_method_id = self.instantiate(method_id, conditioned)
            if self.contains_generic(resolved_trait) or self.contains_generic(final_method_id):
                incomplete = True
                continue
            candidates.append(LookupResult(method_id=final_method_id, deref_count=0, impl=impl))

        explicit = [candidate for candidate in candidates if not candidate.impl.automatic]
        if explicit:
            candidates = explicit
        description = f"trait '{trait_ty.custom_def.name}' for type '{self.get_name(self_type_id)}'"
        if len(candidates) > 1:
            raise AnalysisError(f"Ambiguous implementation of {description}", span)
        if not candidates:
            if incomplete:
                raise AnalysisError(f"cannot infer trait call types for {description}", span)
            raise AnalysisError(f"No matching implementation of {description} for '{method_name}'", span)
        return candidates[0]

    def __trait_object_method_lookup(
        self,
        receiver_span: SrcSpan,
        receiver_type: int,
        trait_type_id: int,
        method_name: str,
        generic_args: list[int] | None,
        arg_type_ids: list[int],
    ) -> LookupResult | None:
        if generic_args:
            return None
        trait_ty = self[trait_type_id]
        assert isinstance(trait_ty, Type.TraitType)
        methods = self.get_trait_methods(trait_type_id)
        method_id = methods.get(method_name)
        if method_id is None:
            return None
        method_ty = self[method_id]
        assert isinstance(method_ty, Type.MethodType)
        if method_ty.custom_def.is_static:
            return None
        method_generic_count = len(method_ty.custom_def.generics) - len(trait_ty.custom_def.generics)
        if method_generic_count > 0:
            return None
        parameters = self.get_params(method_id)
        if len(parameters) != len(arg_type_ids):
            return None
        inference = GenericInference(self, receiver_span)
        try:
            for parameter, arg_type_id in zip(parameters, arg_type_ids):
                inference.constrain(parameter.type_id, arg_type_id)
            method_id = inference.instantiate(method_id)
        except AnalysisError:
            return None
        synthetic_impl = Impl(
            span=trait_ty.custom_def.span,
            generics=[],
            target=receiver_type,
            trait=trait_type_id,
            methods=methods,
        )
        return LookupResult(method_id=method_id, deref_count=0, impl=synthetic_impl, dynamic=True)

    def iter_item_type(self, iter_type_id: int) -> int:
        """
        Get the item type of an iterator type.

        This is used for desugaring for loops, where we need to know the item type of the iterator to type check the loop variable.
        """
        lookup = self.method_lookup(iter_type_id, SrcSpan.empty(), "next", None, [])
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
    dynamic: bool = False
