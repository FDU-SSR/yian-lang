"""Type-check structural patterns against an expected value type."""

from __future__ import annotations

from enum import Enum
from typing import TYPE_CHECKING

from compiler.analysis.error import AnalysisError
from compiler.analysis.lowering.state import DefinitionState
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.frontend.lex import token as Tok
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy

if TYPE_CHECKING:
    from compiler.analysis.lowering.expr_checker import ExprChecker
    from compiler.analysis.lowering.op_builder import OpBuilder


class PatternRoot(Enum):
    TEST = "test"
    BINDING = "binding"


class PatternChecker:
    """Check one pattern; the caller owns the surrounding lexical scope."""

    def __init__(self, ctx: DefinitionState, expr: ExprChecker, ops: OpBuilder) -> None:
        self.__ctx = ctx
        self.__expr = expr
        self.__op_builder = ops

    def check(
        self, pattern: AST.Pattern, type_id: int, *, root_mode: PatternRoot, by_ref: bool,
    ) -> HIR.Pattern:
        ty = self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(type_id)]
        root_enum = root_mode is PatternRoot.TEST and isinstance(ty, Type.EnumType)
        return self.__check_pattern(pattern, type_id, root_enum, by_ref, {}, set(), False)

    def __check_pattern(
        self, pat: AST.Pattern, type_id: int, root_enum: bool, by_ref: bool,
        bindings: dict[str, int], seen: set[str], reuse: bool,
    ) -> HIR.Pattern:
        ctx = self.__ctx.type_ctx
        ty = ctx[ctx.resolve_aliases(type_id)]
        if isinstance(ty, Type.RefType) and isinstance(pat, (
            AST.LiteralPattern, AST.RangePattern, AST.ConstructPattern,
            AST.TuplePattern, AST.SequencePattern,
        )):
            inner = self.__check_pattern(
                pat, ty.pointee_type, False, True, bindings, seen, reuse,
            )
            return HIR.RefPattern(pat.span, type_id, inner)
        match pat:
            case AST.WildcardPattern():
                return HIR.WildcardPattern(pat.span, type_id)
            case AST.NamePattern():
                if root_enum:
                    return self.__check_variant(pat.span, pat.name, None, None, False, type_id, by_ref, bindings, seen, reuse)
                return self.__bind_pattern(pat.name, HIR.WildcardPattern(pat.span, type_id), type_id, by_ref, bindings, seen, reuse)
            case AST.BindPattern():
                inner = self.__check_pattern(pat.inner, type_id, root_enum, by_ref, bindings, seen, reuse)
                return self.__bind_pattern(pat.name, inner, type_id, by_ref, bindings, seen, reuse)
            case AST.OrPattern():
                original = set(seen)
                first_seen = set(seen)
                alternatives = [self.__check_pattern(pat.alternatives[0], type_id, root_enum, by_ref, bindings, first_seen, reuse)]
                expected = first_seen - original
                for alternative in pat.alternatives[1:]:
                    branch_seen = set(original)
                    alternatives.append(self.__check_pattern(alternative, type_id, root_enum, by_ref, bindings, branch_seen, True))
                    if branch_seen - original != expected:
                        raise AnalysisError("OR alternatives must bind the same names", alternative.span)
                seen.update(expected)
                return HIR.OrPattern(pat.span, type_id, alternatives)
            case AST.LiteralPattern():
                return self.__check_literal_pattern(pat, type_id)
            case AST.RangePattern():
                if not isinstance(ty, (Type.IntType, Type.CharType)):
                    raise AnalysisError("Range pattern requires an integer or char", pat.span)
                lower = self.__pattern_endpoint(pat.lower, type_id)
                upper = self.__pattern_endpoint(pat.upper, type_id)
                if lower > upper:
                    raise AnalysisError("Range lower bound exceeds upper bound", pat.span)
                return HIR.RangePattern(pat.span, type_id, lower, upper)
            case AST.ConstructPattern():
                if isinstance(ty, Type.EnumType):
                    if pat.qualifier is not None and not ctx.is_same_type(self.__ctx.resolve_type(pat.qualifier), type_id):
                        raise AnalysisError("Enum pattern qualifier does not match the scrutinee type", pat.span)
                    return self.__check_variant(pat.span, pat.name, pat.positional, pat.named, pat.rest, type_id, by_ref, bindings, seen, reuse)
                if isinstance(ty, Type.StructType):
                    named_type = pat.qualifier if pat.qualifier is not None else ASTTy.NamedType(span=pat.name.span, name=pat.name)
                    if not ctx.is_same_type(self.__ctx.resolve_type(named_type), type_id):
                        raise AnalysisError("Struct pattern type does not match the scrutinee type", pat.span)
                    if pat.positional is not None and pat.positional:
                        raise AnalysisError("Struct patterns use named fields", pat.span)
                    fields = self.__check_named_fields(pat.named or [], ty, pat.rest, by_ref, bindings, seen, reuse, pat.span)
                    return HIR.StructPattern(pat.span, type_id, fields)
                raise AnalysisError("Constructor pattern requires a struct or enum", pat.span)
            case AST.TuplePattern():
                if not isinstance(ty, Type.TupleType) or len(ty.element_types) != len(pat.elements):
                    raise AnalysisError("Tuple pattern has the wrong type or arity", pat.span)
                elements = [self.__check_pattern(sub, sub_type, False, by_ref, bindings, seen, reuse)
                            for sub, sub_type in zip(pat.elements, ty.element_types)]
                return HIR.TuplePattern(pat.span, type_id, elements)
            case AST.SequencePattern():
                if not isinstance(ty, (Type.ArrayType, Type.SliceType)):
                    raise AnalysisError("Sequence pattern requires an array or slice", pat.span)
                element_type = ty.element_type
                prefix = [self.__check_pattern(sub, element_type, False, by_ref, bindings, seen, reuse) for sub in pat.prefix]
                suffix = [self.__check_pattern(sub, element_type, False, by_ref, bindings, seen, reuse) for sub in pat.suffix]
                return HIR.SequencePattern(pat.span, type_id, prefix, suffix, pat.rest)
            case _:
                raise AnalysisError(f"Unsupported pattern type {type(pat).__name__}", pat.span)

    def __bind_pattern(
        self, name: AST.Identifier, inner: HIR.Pattern, type_id: int, by_ref: bool,
        bindings: dict[str, int], seen: set[str], reuse: bool,
    ) -> HIR.BindPattern:
        if name.name in seen:
            raise AnalysisError(f"Duplicate pattern binding '{name.name}'", name.span)
        seen.add(name.name)
        binding_type = self.__ctx.type_ctx.alloc_ref(type_id) if by_ref else type_id
        if reuse:
            symbol_id = bindings.get(name.name)
            if symbol_id is None or self.__ctx.symbol_ctx is None or not self.__ctx.type_ctx.is_same_type(self.__ctx.symbol_ctx.get(symbol_id).type_id, binding_type):
                raise AnalysisError(f"OR binding '{name.name}' has a different type or mode", name.span)
        else:
            symbol_id = self.__ctx.declare_local(name, binding_type)
            bindings[name.name] = symbol_id
        return HIR.BindPattern(name.span, type_id, symbol_id, inner)

    def __check_literal_pattern(self, pat: AST.LiteralPattern, type_id: int) -> HIR.LiteralPattern:
        ty = self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(type_id)]
        literal = pat.literal
        if isinstance(literal, Tok.IntLiteral):
            self.__check_integer_literal(literal, type_id)
            return HIR.LiteralPattern(pat.span, type_id, literal.value)
        if isinstance(literal, Tok.CharLiteral) and isinstance(ty, Type.CharType):
            return HIR.LiteralPattern(pat.span, type_id, literal.value)
        if isinstance(literal, Tok.BoolLiteral) and isinstance(ty, Type.BoolType):
            return HIR.LiteralPattern(pat.span, type_id, literal.value)
        if isinstance(literal, Tok.StrLiteral) and isinstance(ty, Type.StrType):
            hidden = AST.Identifier(span=pat.span, name=f"$match_condition_{id(pat)}")
            symbol_id = self.__ctx.declare_local(hidden, type_id)
            value = HIR.Var(span=pat.span, symbol_id=symbol_id, type_id=type_id, is_place=True)
            expected = HIR.StrLiteral(span=pat.span, value=literal.value, type_id=type_id, is_place=False)
            condition = self.__expr.call_eq(value, expected)
            return HIR.LiteralPattern(pat.span, type_id, literal.value, condition, symbol_id)
        raise AnalysisError("Literal pattern does not match the scrutinee type", pat.span)

    def __check_integer_value(self, value: int, ty: Type.Ty, span: SrcSpan) -> None:
        if not isinstance(ty, Type.IntType):
            raise AnalysisError("Integer pattern requires an integer", span)
        bits = ty.size * 8
        minimum = -(1 << (bits - 1)) if ty.signed else 0
        maximum = (1 << (bits - 1)) - 1 if ty.signed else (1 << bits) - 1
        if not minimum <= value <= maximum:
            raise AnalysisError("Integer pattern is outside the scrutinee type's range", span)

    def __check_integer_literal(self, literal: Tok.IntLiteral, type_id: int) -> None:
        ctx = self.__ctx.type_ctx
        if literal.suffix is not None:
            intrinsic = Type.IntrinsicType.from_str(literal.suffix)
            if intrinsic is None:
                raise AnalysisError(f"Unknown intrinsic type suffix '{literal.suffix}'", literal.span)
            if not ctx.is_same_type(TypeCtx.intrinsic_type(intrinsic), type_id):
                raise AnalysisError("Integer pattern suffix does not match the scrutinee type", literal.span)
        self.__check_integer_value(literal.value, ctx[ctx.resolve_aliases(type_id)], literal.span)

    def __pattern_endpoint(self, literal: Tok.IntLiteral | Tok.CharLiteral, type_id: int) -> int:
        ty = self.__ctx.type_ctx[self.__ctx.type_ctx.resolve_aliases(type_id)]
        if isinstance(literal, Tok.IntLiteral):
            self.__check_integer_literal(literal, type_id)
            return literal.value
        if not isinstance(ty, Type.CharType):
            raise AnalysisError("Character range requires a char scrutinee", literal.span)
        return ord(literal.value)

    def __check_variant(
        self, span: SrcSpan, name: AST.Identifier, positional: list[AST.Pattern] | None,
        named: list[AST.FieldPattern] | None, rest: bool, type_id: int, by_ref: bool,
        bindings: dict[str, int], seen: set[str], reuse: bool,
    ) -> HIR.EnumPattern:
        ctx = self.__ctx.type_ctx
        enum_ty = ctx[ctx.resolve_aliases(type_id)]
        assert isinstance(enum_ty, Type.EnumType)
        variant = enum_ty.get_variant_by_name(name.name, ctx)
        if variant is None:
            raise AnalysisError(f"Unknown enum variant '{name.name}'", name.span)
        self.__ctx.names.record(name.span, variant, synthetic=name.synthetic)
        if positional is None and named is None and not rest:
            return HIR.EnumPattern(span, variant, type_id, None)
        if variant.payload_type is None:
            raise AnalysisError(f"Variant '{name.name}' has no payload", span)
        payload = ctx[ctx.resolve_aliases(variant.payload_type)]
        assert isinstance(payload, Type.StructType)
        fields = payload.get_fields(ctx)
        if positional is not None:
            if len(positional) != len(fields):
                raise AnalysisError("Positional payload pattern has the wrong arity", span)
            checked: list[tuple[int, HIR.Pattern]] = []
            for sub, field in zip(positional, fields):
                self.__op_builder.check_field_visible(payload, field, sub.span)
                checked.append((field.index, self.__check_pattern(sub, field.type_id, False, by_ref, bindings, seen, reuse)))
            return HIR.EnumPattern(span, variant, type_id, checked)
        checked = self.__check_named_fields(named or [], payload, rest, by_ref, bindings, seen, reuse, span)
        return HIR.EnumPattern(span, variant, type_id, checked)

    def __check_named_fields(
        self, fields: list[AST.FieldPattern], ty: Type.StructType, rest: bool,
        by_ref: bool, bindings: dict[str, int], seen: set[str], reuse: bool, span: SrcSpan,
    ) -> list[tuple[int, HIR.Pattern]]:
        all_fields = ty.get_fields(self.__ctx.type_ctx)
        used: set[str] = set()
        checked: list[tuple[int, HIR.Pattern]] = []
        for entry in fields:
            if entry.name.name in used:
                raise AnalysisError(f"Duplicate field '{entry.name.name}' in pattern", entry.span)
            used.add(entry.name.name)
            field = ty.get_field_by_name(entry.name.name, self.__ctx.type_ctx)
            if field is None:
                raise AnalysisError(f"Unknown field '{entry.name.name}' in pattern", entry.span)
            self.__op_builder.check_field_visible(ty, field, entry.span)
            checked.append((field.index, self.__check_pattern(entry.pattern, field.type_id, False, by_ref, bindings, seen, reuse)))
        if not rest and len(used) != len(all_fields):
            raise AnalysisError("Field pattern must list every field or end with '..'", span)
        return checked
