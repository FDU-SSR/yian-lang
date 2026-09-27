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
from compiler.analysis.unit.hir_traversal import HirPatternVisitor, HirVisitor


@dataclass(frozen=True)
class _Constructor:
    kind: str
    key: int | tuple[int, int] | None
    fields: tuple[int, ...]
    label: str
    positions: tuple[int, ...] = ()


class _OpaquePatternVisitor(HirPatternVisitor):
    def __init__(self) -> None:
        self.found = False

    def enter_pattern(self, pattern: HIR.Pattern) -> bool:
        if isinstance(pattern, HIR.LiteralPattern) and pattern.condition is not None:
            self.found = True
        return not self.found


class MatchCoverage(HirVisitor):
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
                self.visit_expr(definition.body)
            except AnalysisError as error:
                if not recover:
                    raise
                errors.append(error)
        return tuple(errors)

    def warnings(self) -> tuple[Diagnostic, ...]:
        return tuple(self.__warnings)

    def enter_expr(self, expr: HIR.Expr) -> bool:
        if isinstance(expr, HIR.Match):
            self.__check_match(expr)
        return True

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
        visitor = _OpaquePatternVisitor()
        visitor.visit_pattern(pattern)
        return visitor.found

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
