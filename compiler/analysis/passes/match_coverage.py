"""Coverage and usefulness analysis for typed match patterns."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from compiler.analysis.diagnostics import Diagnostic, Severity, W501_UNREACHABLE_PATTERN
from compiler.analysis.error import AnalysisError
from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.def_point import DefPoint


@dataclass(frozen=True)
class _Constructor:
    kind: str
    key: int | tuple[int, int] | None
    fields: tuple[int, ...]
    label: str
    positions: tuple[int, ...] = ()


class MatchCoverage:
    """Check matches after compile-time branches have been selected."""

    def __init__(self, def_points: Mapping[int, DefPoint], type_ctx: TypeCtx) -> None:
        self.__def_points = def_points
        self.__ctx = type_ctx
        self.__warnings: list[Diagnostic] = []

    def run(self, *, recover: bool = False) -> tuple[AnalysisError, ...]:
        errors: list[AnalysisError] = []
        for definition in self.__def_points.values():
            if definition.body is None:
                continue
            try:
                self.__visit(definition.body)
            except AnalysisError as error:
                if not recover:
                    raise
                errors.append(error)
        return tuple(errors)

    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(self.__warnings)

    def __check_match(self, expr: HIR.Match) -> None:
        value_type = self.__ctx[self.__ctx.resolve_aliases(expr.value.type_id)]
        type_id = value_type.pointee_type if expr.is_ref and isinstance(value_type, Type.RefType) else expr.value.type_id
        needs_fallback = any(
            arm.guard is not None or self.__opaque(arm.pattern) for arm in expr.arms
        )
        if needs_fallback and (not expr.arms or not self.__is_fallback(expr.arms[-1])):
            raise AnalysisError(
                "Guarded or opaque match requires a final unguarded '_ => ...' or 'name @ _ => ...' arm",
                expr.span,
            )

        rows: list[list[HIR.Pattern]] = []
        side_effect_seen = False
        unconditional_catchall = False
        for arm in expr.arms:
            if unconditional_catchall or (not side_effect_seen and self.__useful(rows, [arm.pattern], [type_id]) is None):
                self.__warnings.append(Diagnostic(
                    code=W501_UNREACHABLE_PATTERN,
                    severity=Severity.WARNING,
                    message="unreachable match arm",
                    span=arm.span,
                ))
            if self.__is_fallback(arm):
                unconditional_catchall = True
            if arm.guard is not None or self.__opaque(arm.pattern):
                rows.clear()
                side_effect_seen = True
            elif not side_effect_seen:
                rows.append([arm.pattern])

        if needs_fallback:
            return
        wildcard = HIR.WildcardPattern(expr.span, type_id)
        witness = self.__useful(rows, [wildcard], [type_id])
        if witness is not None:
            example = witness[0] if witness else "_"
            raise AnalysisError(f"non-exhaustive match: missing {example}", expr.span)

    def __is_fallback(self, arm: HIR.MatchArm) -> bool:
        if arm.guard is not None:
            return False
        pattern = arm.pattern
        return isinstance(pattern, HIR.WildcardPattern) or (
            isinstance(pattern, HIR.BindPattern) and isinstance(pattern.inner, HIR.WildcardPattern)
        )

    def __opaque(self, pattern: HIR.Pattern) -> bool:
        match pattern:
            case HIR.LiteralPattern() if pattern.condition is not None:
                return True
            case HIR.BindPattern():
                return self.__opaque(pattern.inner)
            case HIR.OrPattern():
                return any(self.__opaque(sub) for sub in pattern.alternatives)
            case HIR.EnumPattern() if pattern.fields is not None:
                return any(self.__opaque(sub) for _, sub in pattern.fields)
            case HIR.StructPattern():
                return any(self.__opaque(sub) for _, sub in pattern.fields)
            case HIR.TuplePattern():
                return any(self.__opaque(sub) for sub in pattern.elements)
            case HIR.SequencePattern():
                return any(self.__opaque(sub) for sub in pattern.prefix + pattern.suffix)
            case _:
                return False

    def __useful(
        self, rows: list[list[HIR.Pattern]], query: list[HIR.Pattern], types: list[int],
    ) -> list[str] | None:
        skipped = 0
        while query and isinstance(query[0], HIR.WildcardPattern) and all(
            isinstance(row[0], HIR.WildcardPattern) for row in rows
        ):
            ty = self.__ctx[self.__ctx.resolve_aliases(types[0])]
            if not isinstance(ty, (Type.BoolType, Type.CharType, Type.IntType, Type.FloatType, Type.StrType, Type.PointerType)):
                break
            rows = [row[1:] for row in rows]
            query = query[1:]
            types = types[1:]
            skipped += 1
        if not query:
            return ["_"] * skipped if not rows else None
        head = query[0]
        if isinstance(head, HIR.BindPattern):
            found = self.__useful(rows, [head.inner] + query[1:], types)
            return ["_"] * skipped + found if found is not None else None
        if isinstance(head, HIR.OrPattern):
            for alternative in head.alternatives:
                found = self.__useful(rows, [alternative] + query[1:], types)
                if found is not None:
                    return ["_"] * skipped + found
            return None
        expanded: list[list[HIR.Pattern]] = []
        for row in rows:
            self.__expand_row(row, expanded)
        for constructor in self.__constructors(types[0], [row[0] for row in expanded] + [head]):
            projected_query = self.__specialize(head, constructor, query=True)
            if projected_query is None:
                continue
            projected_rows: list[list[HIR.Pattern]] = []
            for row in expanded:
                projected = self.__specialize(row[0], constructor, query=False)
                if projected is not None:
                    projected_rows.append(projected + row[1:])
            found = self.__useful(
                projected_rows,
                projected_query + query[1:],
                list(constructor.fields) + types[1:],
            )
            if found is not None:
                child_count = len(constructor.fields)
                children = found[:child_count]
                if constructor.kind in ("tuple", "struct", "enum", "array", "slice") and child_count:
                    label = f"{constructor.label}({', '.join(children)})"
                else:
                    label = constructor.label
                return ["_"] * skipped + [label] + found[child_count:]
        return None

    def __expand_row(self, row: list[HIR.Pattern], out: list[list[HIR.Pattern]]) -> None:
        head = row[0]
        if isinstance(head, HIR.BindPattern):
            self.__expand_row([head.inner] + row[1:], out)
        elif isinstance(head, HIR.OrPattern):
            for alternative in head.alternatives:
                self.__expand_row([alternative] + row[1:], out)
        else:
            out.append(row)

    def __constructors(self, type_id: int, patterns: list[HIR.Pattern]) -> list[_Constructor]:
        ctx = self.__ctx
        ty = ctx[ctx.resolve_aliases(type_id)]
        if isinstance(ty, Type.EnumType):
            result: list[_Constructor] = []
            for variant in ty.get_variants(ctx):
                fields = () if variant.payload_type is None else tuple(
                    field.type_id for field in ctx.get_struct_fields(variant.payload_type)
                )
                result.append(_Constructor("enum", variant.discriminant, fields, variant.name))
            return result
        if isinstance(ty, Type.BoolType):
            return [_Constructor("bool", 0, (), "false"), _Constructor("bool", 1, (), "true")]
        if isinstance(ty, (Type.IntType, Type.CharType)):
            if isinstance(ty, Type.CharType):
                domains = [(0, 0xD800), (0xE000, 0x110000)]
            else:
                bits = ty.size * 8
                domains = [(-(1 << (bits - 1)), 1 << (bits - 1))] if ty.signed else [(0, 1 << bits)]
            intervals: list[_Constructor] = []
            for low, high in domains:
                cuts = {low, high}
                for pattern in patterns:
                    if isinstance(pattern, HIR.LiteralPattern) and pattern.condition is None:
                        value = ord(pattern.value) if isinstance(pattern.value, str) else pattern.value
                        cuts.update((max(low, min(high, value)), max(low, min(high, value + 1))))
                    elif isinstance(pattern, HIR.RangePattern):
                        cuts.update((max(low, min(high, pattern.lower)), max(low, min(high, pattern.upper))))
                ordered = sorted(cuts)
                for start, end in zip(ordered, ordered[1:]):
                    if start < end:
                        label = repr(chr(start)) if isinstance(ty, Type.CharType) else str(start)
                        intervals.append(_Constructor("scalar", (start, end), (), label))
            return intervals
        if isinstance(ty, Type.TupleType):
            return [_Constructor("tuple", None, tuple(ty.element_types), "tuple")]
        if isinstance(ty, Type.StructType):
            return [_Constructor("struct", None, tuple(field.type_id for field in ty.get_fields(ctx)), ty.custom_def.name)]
        if isinstance(ty, Type.ArrayType):
            length = ctx.try_extract_array_length(type_id)
            if length is None:
                return [_Constructor("open", None, (), "_")]
            return [self.__sequence_constructor("array", length, ty.element_type, patterns)]
        if isinstance(ty, Type.SliceType):
            maximum = max((len(p.prefix) + len(p.suffix) for p in patterns if isinstance(p, HIR.SequencePattern)), default=0)
            return [
                self.__sequence_constructor("slice", length, ty.element_type, patterns)
                for length in range(maximum + 2)
            ]
        return [_Constructor("open", None, (), "_")]

    def __sequence_constructor(
        self, kind: str, length: int, element_type: int, patterns: list[HIR.Pattern],
    ) -> _Constructor:
        positions = self.__sequence_positions(length, patterns)
        label = "array" if kind == "array" else f"slice[{length}]"
        return _Constructor(kind, length, (element_type,) * len(positions), label, positions)

    def __sequence_positions(self, length: int, patterns: list[HIR.Pattern]) -> tuple[int, ...]:
        # A homogeneous sequence needs only the elements mentioned by a pattern.
        # One representative element also detects an uninhabited element type.
        positions = {0} if length else set[int]()
        for pattern in patterns:
            if not isinstance(pattern, HIR.SequencePattern):
                continue
            needed = len(pattern.prefix) + len(pattern.suffix)
            if length < needed or (not pattern.rest and length != needed):
                continue
            positions.update(range(len(pattern.prefix)))
            positions.update(range(length - len(pattern.suffix), length))
        return tuple(sorted(positions))

    def __specialize(
        self, pattern: HIR.Pattern, constructor: _Constructor, *, query: bool,
    ) -> list[HIR.Pattern] | None:
        if isinstance(pattern, HIR.BindPattern):
            return self.__specialize(pattern.inner, constructor, query=query)
        if isinstance(pattern, HIR.WildcardPattern):
            return [HIR.WildcardPattern(pattern.span, type_id) for type_id in constructor.fields]
        if isinstance(pattern, HIR.LiteralPattern):
            if pattern.condition is not None:
                return [] if query else None
            if constructor.kind == "bool":
                return [] if isinstance(pattern.value, bool) and int(pattern.value) == constructor.key else None
            if constructor.kind == "scalar" and isinstance(constructor.key, tuple):
                value = ord(pattern.value) if isinstance(pattern.value, str) else pattern.value
                return [] if constructor.key[0] <= value < constructor.key[1] else None
            return None
        if isinstance(pattern, HIR.RangePattern):
            bounds = constructor.key
            return [] if constructor.kind == "scalar" and isinstance(bounds, tuple) and pattern.lower <= bounds[0] and bounds[1] <= pattern.upper else None
        if isinstance(pattern, HIR.EnumPattern):
            if constructor.kind != "enum" or constructor.key != pattern.variant.discriminant:
                return None
            result: list[HIR.Pattern] = [HIR.WildcardPattern(pattern.span, ty) for ty in constructor.fields]
            for index, sub in pattern.fields or []:
                result[index] = sub
            return result
        if isinstance(pattern, HIR.StructPattern) and constructor.kind == "struct":
            result: list[HIR.Pattern] = [HIR.WildcardPattern(pattern.span, ty) for ty in constructor.fields]
            for index, sub in pattern.fields:
                result[index] = sub
            return result
        if isinstance(pattern, HIR.TuplePattern) and constructor.kind == "tuple":
            return pattern.elements
        if isinstance(pattern, HIR.SequencePattern) and constructor.kind in ("array", "slice"):
            length = constructor.key
            assert isinstance(length, int)
            needed = len(pattern.prefix) + len(pattern.suffix)
            if length < needed or (not pattern.rest and length != needed):
                return None
            result: list[HIR.Pattern] = [HIR.WildcardPattern(pattern.span, ty) for ty in constructor.fields]
            slots = {position: slot for slot, position in enumerate(constructor.positions)}
            for index, sub in enumerate(pattern.prefix):
                result[slots[index]] = sub
            for index, sub in enumerate(pattern.suffix):
                result[slots[length - len(pattern.suffix) + index]] = sub
            return result
        return None

    def __visit_pattern(self, pattern: HIR.Pattern) -> None:
        match pattern:
            case HIR.LiteralPattern() if pattern.condition is not None:
                self.__visit(pattern.condition)
            case HIR.BindPattern():
                self.__visit_pattern(pattern.inner)
            case HIR.OrPattern():
                for sub in pattern.alternatives:
                    self.__visit_pattern(sub)
            case HIR.EnumPattern() if pattern.fields is not None:
                for _, sub in pattern.fields:
                    self.__visit_pattern(sub)
            case HIR.StructPattern():
                for _, sub in pattern.fields:
                    self.__visit_pattern(sub)
            case HIR.TuplePattern():
                for sub in pattern.elements:
                    self.__visit_pattern(sub)
            case HIR.SequencePattern():
                for sub in pattern.prefix + pattern.suffix:
                    self.__visit_pattern(sub)
            case _:
                pass

    def __visit(self, expr: HIR.Expr) -> None:
        match expr:
            case HIR.Match():
                self.__check_match(expr)
                self.__visit(expr.value)
                for arm in expr.arms:
                    self.__visit_pattern(arm.pattern)
                    if arm.guard is not None:
                        self.__visit(arm.guard)
                    self.__visit(arm.body)
            case HIR.Block():
                for sub in expr.stmts:
                    self.__visit(sub)
            case HIR.If():
                self.__visit(expr.cond)
                self.__visit(expr.then_branch)
                if expr.else_branch is not None:
                    self.__visit(expr.else_branch)
            case HIR.Loop():
                self.__visit(expr.body)
            case HIR.Return() if expr.value is not None:
                self.__visit(expr.value)
            case HIR.Break() if expr.value is not None:
                self.__visit(expr.value)
            case HIR.Defer():
                self.__visit(expr.action)
            case HIR.Semi():
                self.__visit(expr.expr)
            case HIR.Let() if expr.init is not None:
                self.__visit(expr.init)
            case HIR.Binary():
                self.__visit(expr.left)
                self.__visit(expr.right)
            case HIR.Unary():
                self.__visit(expr.operand)
            case HIR.Call() | HIR.Builtin() | HIR.MethodCall() | HIR.TraitObjectMethodCall():
                if isinstance(expr, (HIR.MethodCall, HIR.TraitObjectMethodCall)):
                    self.__visit(expr.receiver)
                for arg in expr.args:
                    self.__visit(arg)
            case HIR.Invoke():
                self.__visit(expr.callable)
                for arg in expr.args:
                    self.__visit(arg)
            case HIR.Cast() | HIR.BitCast() | HIR.TraitObjectCoerce() | HIR.DynValue():
                self.__visit(expr.value)
            case HIR.Delete():
                self.__visit(expr.target)
            case HIR.StructConstruct():
                for sub in expr.field_values.values():
                    self.__visit(sub)
            case HIR.VariantConstruct() if expr.args is not None:
                for sub in expr.args.values():
                    self.__visit(sub)
            case HIR.FieldAccess() | HIR.TupleAccess():
                self.__visit(expr.receiver)
            case HIR.ArrayAccess():
                self.__visit(expr.array)
                self.__visit(expr.index)
            case HIR.SliceAccess():
                self.__visit(expr.slice)
                self.__visit(expr.index)
            case HIR.DynBuffer():
                self.__visit(expr.length)
                if expr.element is not None:
                    self.__visit(expr.element)
            case HIR.Tuple():
                for sub in expr.field_values:
                    self.__visit(sub)
            case HIR.Array():
                for sub in expr.elements:
                    self.__visit(sub)
            case HIR.ArrayRepeat():
                self.__visit(expr.element)
            case HIR.Closure():
                for sub in expr.captures.values():
                    self.__visit(sub)
            case _:
                pass
