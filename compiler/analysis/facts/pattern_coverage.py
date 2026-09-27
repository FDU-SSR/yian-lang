"""Reusable coverage queries for typed patterns."""

from __future__ import annotations

from dataclasses import dataclass

from compiler.analysis.ty import ty as Type
from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit import hir as HIR
from compiler.analysis.unit.hir_traversal import HirPatternVisitor


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


class PatternCoverageQueries:
    """Answer conservative usefulness questions for typed patterns."""

    def __init__(self, type_ctx: TypeCtx) -> None:
        self.__ctx = type_ctx

    def contains_opaque(self, pattern: HIR.Pattern) -> bool:
        visitor = _OpaquePatternVisitor()
        visitor.visit_pattern(pattern)
        return visitor.found

    def witness(
        self, rows: list[list[HIR.Pattern]], pattern: HIR.Pattern, type_id: int,
    ) -> list[str] | None:
        return self.__useful(rows, [pattern], [type_id])

    def is_irrefutable(self, pattern: HIR.Pattern, type_id: int) -> bool:
        if self.contains_opaque(pattern):
            return False
        wildcard = HIR.WildcardPattern(pattern.span, type_id)
        return self.witness([[pattern]], wildcard, type_id) is None

    def can_match(self, pattern: HIR.Pattern, type_id: int) -> bool:
        if self.__ctx.contains_generic(type_id) or self.contains_opaque(pattern):
            return True
        return self.witness([], pattern, type_id) is not None

    def __useful(
        self, rows: list[list[HIR.Pattern]], query: list[HIR.Pattern], types: list[int],
        seen_uncovered: frozenset[int] = frozenset(),
    ) -> list[str] | None:
        skipped = 0
        while query and isinstance(query[0], HIR.WildcardPattern) and all(
            isinstance(row[0], HIR.WildcardPattern) for row in rows
        ):
            ty = self.__ctx[self.__ctx.resolve_aliases(types[0])]
            # A wildcard column covered by every row cannot affect usefulness,
            # even when its type contains recursive references.
            if not rows and not isinstance(ty, (
                Type.BoolType, Type.CharType, Type.IntType, Type.FloatType,
                Type.StrType, Type.PointerType,
            )):
                break
            rows = [row[1:] for row in rows]
            query = query[1:]
            types = types[1:]
            skipped += 1
        if not query:
            return ["_"] * skipped if not rows else None
        head = query[0]
        if not rows and isinstance(head, HIR.WildcardPattern):
            type_id = self.__ctx.resolve_aliases(types[0])
            if type_id in seen_uncovered:
                # A repeated wildcard column adds no finite structural
                # constraint; sibling columns may still be uninhabited.
                found = self.__useful(rows, query[1:], types[1:], seen_uncovered)
                return ["_"] * (skipped + 1) + found if found is not None else None
            seen_uncovered = seen_uncovered | {type_id}
        if isinstance(head, HIR.BindPattern):
            found = self.__useful(rows, [head.inner] + query[1:], types, seen_uncovered)
            return ["_"] * skipped + found if found is not None else None
        if isinstance(head, HIR.OrPattern):
            for alternative in head.alternatives:
                found = self.__useful(rows, [alternative] + query[1:], types, seen_uncovered)
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
                seen_uncovered,
            )
            if found is not None:
                child_count = len(constructor.fields)
                children = found[:child_count]
                if constructor.kind in ("tuple", "struct", "enum", "array", "slice", "ref") and child_count:
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
        if isinstance(ty, Type.NeverType):
            return []
        if isinstance(ty, Type.RefType):
            return [_Constructor("ref", None, (ty.pointee_type,), "ref")]
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
        if isinstance(pattern, HIR.RefPattern):
            return [pattern.inner] if constructor.kind == "ref" else None
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
