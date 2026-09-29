"""Build a method body that compares the variants of a unit enum."""

from __future__ import annotations

from compiler.analysis.ty import ty as Type
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.lex.token import BoolLiteral
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.operator import UnaryOperator


def build_unit_enum_equality_body(enum_type: Type.EnumType) -> AST.Block:
    span = SrcSpan.empty()

    def __name(value: str) -> AST.Identifier:
        return AST.Identifier(span, value, synthetic=True)

    def __boolean(value: bool) -> AST.Literal:
        raw = "true" if value else "false"
        return AST.Literal(span, BoolLiteral(raw, span, value))

    def __body(expr: AST.Expr) -> AST.Block:
        return AST.Block(span, [expr])

    def __deref(value: str) -> AST.Unary:
        return AST.Unary(span, UnaryOperator.Deref, __name(value))

    def __variant(name_text: str) -> AST.ConstructPattern:
        return AST.ConstructPattern(span, __name(name_text), None, None, None)

    arms: list[AST.MatchArm] = []
    for value in enum_type.custom_def.variants:
        same = AST.MatchArm(span, __variant(value.name), None, __body(__boolean(True)), AST.MatchArmOrigin.SYNTHETIC)
        different = AST.MatchArm(span, AST.WildcardPattern(span), None, __body(__boolean(False)), AST.MatchArmOrigin.SYNTHETIC)
        inner = AST.Match(span, __deref("other"), [same, different])
        arms.append(AST.MatchArm(span, __variant(value.name), None, __body(inner), AST.MatchArmOrigin.SYNTHETIC))
    if not arms:
        return __body(__boolean(False))
    return __body(AST.Match(span, __deref("self"), arms))
