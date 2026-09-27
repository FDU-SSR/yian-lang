"""Typed traversal of HIR expressions and patterns."""

from __future__ import annotations

from typing import assert_never

from compiler.analysis.unit import hir as HIR


class HirVisitor:
    """Visit HIR children in their syntactic order without changing them."""

    def enter_expr(self, expr: HIR.Expr) -> bool:
        """Return false to omit the descendants of this expression."""
        return True

    def leave_expr(self, expr: HIR.Expr) -> None:
        pass

    def enter_pattern(self, pattern: HIR.Pattern) -> bool:
        """Return false to omit the descendants of this pattern."""
        return True

    def leave_pattern(self, pattern: HIR.Pattern) -> None:
        pass

    def visit_pattern_condition(self, condition: HIR.Expr) -> None:
        """Visit an expression embedded in a pattern."""
        self.visit_expr(condition)

    def visit_expr(self, expr: HIR.Expr) -> None:
        if not self.enter_expr(expr):
            return
        match expr:
            case HIR.Block():
                for stmt in expr.stmts:
                    self.visit_expr(stmt)
            case HIR.Return() | HIR.Break():
                if expr.value is not None:
                    self.visit_expr(expr.value)
            case HIR.If():
                self.visit_expr(expr.cond)
                self.visit_expr(expr.then_branch)
                if expr.else_branch is not None:
                    self.visit_expr(expr.else_branch)
            case HIR.ComptimeIf():
                self.visit_expr(expr.cond)
                self.visit_expr(expr.then_branch)
                self.visit_expr(expr.else_branch)
            case HIR.Loop():
                self.visit_expr(expr.body)
            case HIR.Defer():
                self.visit_expr(expr.action)
            case HIR.Delete():
                self.visit_expr(expr.target)
            case HIR.Semi():
                self.visit_expr(expr.expr)
            case HIR.Let():
                if expr.init is not None:
                    self.visit_expr(expr.init)
            case HIR.Match():
                self.visit_expr(expr.value)
                for arm in expr.arms:
                    self.visit_pattern(arm.pattern)
                    if arm.guard is not None:
                        self.visit_expr(arm.guard)
                    self.visit_expr(arm.body)
            case HIR.Binary():
                self.visit_expr(expr.left)
                self.visit_expr(expr.right)
            case HIR.Unary():
                self.visit_expr(expr.operand)
            case HIR.Call() | HIR.Builtin():
                for arg in expr.args:
                    self.visit_expr(arg)
            case HIR.StructConstruct():
                for value in expr.field_values.values():
                    self.visit_expr(value)
            case HIR.Invoke():
                self.visit_expr(expr.callable)
                for arg in expr.args:
                    self.visit_expr(arg)
            case HIR.Cast() | HIR.BitCast() | HIR.TraitObjectCoerce() | HIR.DynValue():
                self.visit_expr(expr.value)
            case HIR.MethodCall() | HIR.TraitObjectMethodCall():
                self.visit_expr(expr.receiver)
                for arg in expr.args:
                    self.visit_expr(arg)
            case HIR.VariantConstruct():
                if expr.args is not None:
                    for value in expr.args.values():
                        self.visit_expr(value)
            case HIR.FieldAccess() | HIR.TupleAccess():
                self.visit_expr(expr.receiver)
            case HIR.ArrayAccess():
                self.visit_expr(expr.array)
                self.visit_expr(expr.index)
            case HIR.SliceAccess():
                self.visit_expr(expr.slice)
                self.visit_expr(expr.index)
            case HIR.DynBuffer():
                self.visit_expr(expr.length)
                if expr.element is not None:
                    self.visit_expr(expr.element)
            case HIR.Tuple():
                for value in expr.field_values:
                    self.visit_expr(value)
            case HIR.Array():
                for element in expr.elements:
                    self.visit_expr(element)
            case HIR.ArrayRepeat():
                self.visit_expr(expr.element)
            case HIR.Closure():
                for capture in expr.captures.values():
                    self.visit_expr(capture)
            case (
                HIR.Continue() | HIR.CompileConfig() | HIR.Var() | HIR.IntLiteral()
                | HIR.FloatLiteral() | HIR.CharLiteral() | HIR.StrLiteral()
                | HIR.BoolLiteral() | HIR.Ty()
            ):
                pass
            case _:
                assert_never(expr)
        self.leave_expr(expr)

    def visit_pattern(self, pattern: HIR.Pattern) -> None:
        if not self.enter_pattern(pattern):
            return
        match pattern:
            case HIR.LiteralPattern():
                if pattern.condition is not None:
                    self.visit_pattern_condition(pattern.condition)
            case HIR.BindPattern():
                self.visit_pattern(pattern.inner)
            case HIR.OrPattern():
                for alternative in pattern.alternatives:
                    self.visit_pattern(alternative)
            case HIR.EnumPattern():
                if pattern.fields is not None:
                    for _, child in pattern.fields:
                        self.visit_pattern(child)
            case HIR.StructPattern():
                for _, child in pattern.fields:
                    self.visit_pattern(child)
            case HIR.TuplePattern():
                for child in pattern.elements:
                    self.visit_pattern(child)
            case HIR.SequencePattern():
                for child in pattern.prefix:
                    self.visit_pattern(child)
                for child in pattern.suffix:
                    self.visit_pattern(child)
            case HIR.WildcardPattern() | HIR.RangePattern():
                pass
            case _:
                assert_never(pattern)
        self.leave_pattern(pattern)


class HirPatternVisitor(HirVisitor):
    """Visit pattern nodes without descending into embedded expressions."""

    def visit_pattern_condition(self, condition: HIR.Expr) -> None:
        pass


class HirRewriter:
    """Rewrite HIR children in place, leaving unchanged node identities intact."""

    def rewrite_expr(self, expr: HIR.Expr) -> HIR.Expr:
        self.rewrite_children(expr)
        return expr

    def rewrite_block(self, block: HIR.Block) -> HIR.Block:
        rewritten = self.rewrite_expr(block)
        if not isinstance(rewritten, HIR.Block):
            raise TypeError("a HIR block must remain a block")
        return rewritten

    def rewrite_children(self, expr: HIR.Expr) -> None:
        match expr:
            case HIR.Block():
                expr.stmts = [self.rewrite_expr(stmt) for stmt in expr.stmts]
            case HIR.Return() | HIR.Break():
                if expr.value is not None:
                    expr.value = self.rewrite_expr(expr.value)
            case HIR.If():
                expr.cond = self.rewrite_expr(expr.cond)
                expr.then_branch = self.rewrite_block(expr.then_branch)
                if expr.else_branch is not None:
                    expr.else_branch = self.rewrite_block(expr.else_branch)
            case HIR.ComptimeIf():
                expr.cond = self.rewrite_expr(expr.cond)
                expr.then_branch = self.rewrite_block(expr.then_branch)
                expr.else_branch = self.rewrite_block(expr.else_branch)
            case HIR.Loop():
                expr.body = self.rewrite_block(expr.body)
            case HIR.Defer():
                expr.action = self.rewrite_expr(expr.action)
            case HIR.Delete():
                expr.target = self.rewrite_expr(expr.target)
            case HIR.Semi():
                expr.expr = self.rewrite_expr(expr.expr)
            case HIR.Let():
                if expr.init is not None:
                    expr.init = self.rewrite_expr(expr.init)
            case HIR.Match():
                expr.value = self.rewrite_expr(expr.value)
                for arm in expr.arms:
                    arm.pattern = self.rewrite_pattern(arm.pattern)
                    if arm.guard is not None:
                        arm.guard = self.rewrite_expr(arm.guard)
                    arm.body = self.rewrite_block(arm.body)
            case HIR.Binary():
                expr.left = self.rewrite_expr(expr.left)
                expr.right = self.rewrite_expr(expr.right)
            case HIR.Unary():
                expr.operand = self.rewrite_expr(expr.operand)
            case HIR.Call() | HIR.Builtin():
                expr.args = [self.rewrite_expr(arg) for arg in expr.args]
            case HIR.StructConstruct():
                expr.field_values = {name: self.rewrite_expr(value) for name, value in expr.field_values.items()}
            case HIR.Invoke():
                expr.callable = self.rewrite_expr(expr.callable)
                expr.args = [self.rewrite_expr(arg) for arg in expr.args]
            case HIR.Cast() | HIR.BitCast() | HIR.TraitObjectCoerce() | HIR.DynValue():
                expr.value = self.rewrite_expr(expr.value)
            case HIR.MethodCall() | HIR.TraitObjectMethodCall():
                expr.receiver = self.rewrite_expr(expr.receiver)
                expr.args = [self.rewrite_expr(arg) for arg in expr.args]
            case HIR.VariantConstruct():
                if expr.args is not None:
                    expr.args = {name: self.rewrite_expr(value) for name, value in expr.args.items()}
            case HIR.FieldAccess() | HIR.TupleAccess():
                expr.receiver = self.rewrite_expr(expr.receiver)
            case HIR.ArrayAccess():
                expr.array = self.rewrite_expr(expr.array)
                expr.index = self.rewrite_expr(expr.index)
            case HIR.SliceAccess():
                expr.slice = self.rewrite_expr(expr.slice)
                expr.index = self.rewrite_expr(expr.index)
            case HIR.DynBuffer():
                expr.length = self.rewrite_expr(expr.length)
                if expr.element is not None:
                    expr.element = self.rewrite_expr(expr.element)
            case HIR.Tuple():
                expr.field_values = [self.rewrite_expr(value) for value in expr.field_values]
            case HIR.Array():
                expr.elements = [self.rewrite_expr(element) for element in expr.elements]
            case HIR.ArrayRepeat():
                expr.element = self.rewrite_expr(expr.element)
            case HIR.Closure():
                expr.captures = {name: self.rewrite_expr(value) for name, value in expr.captures.items()}
            case (
                HIR.Continue() | HIR.CompileConfig() | HIR.Var() | HIR.IntLiteral()
                | HIR.FloatLiteral() | HIR.CharLiteral() | HIR.StrLiteral()
                | HIR.BoolLiteral() | HIR.Ty()
            ):
                pass
            case _:
                assert_never(expr)

    def rewrite_pattern(self, pattern: HIR.Pattern) -> HIR.Pattern:
        self.rewrite_pattern_children(pattern)
        return pattern

    def rewrite_pattern_children(self, pattern: HIR.Pattern) -> None:
        """Rewrite pattern children without replacing the pattern root."""
        match pattern:
            case HIR.LiteralPattern():
                if pattern.condition is not None:
                    pattern.condition = self.rewrite_expr(pattern.condition)
            case HIR.BindPattern():
                pattern.inner = self.rewrite_pattern(pattern.inner)
            case HIR.OrPattern():
                pattern.alternatives = [self.rewrite_pattern(child) for child in pattern.alternatives]
            case HIR.EnumPattern():
                if pattern.fields is not None:
                    pattern.fields = [(index, self.rewrite_pattern(child)) for index, child in pattern.fields]
            case HIR.StructPattern():
                pattern.fields = [(index, self.rewrite_pattern(child)) for index, child in pattern.fields]
            case HIR.TuplePattern():
                pattern.elements = [self.rewrite_pattern(child) for child in pattern.elements]
            case HIR.SequencePattern():
                pattern.prefix = [self.rewrite_pattern(child) for child in pattern.prefix]
                pattern.suffix = [self.rewrite_pattern(child) for child in pattern.suffix]
            case HIR.WildcardPattern() | HIR.RangePattern():
                pass
            case _:
                assert_never(pattern)
