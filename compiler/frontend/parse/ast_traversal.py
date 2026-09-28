"""Typed traversal of AST bodies, expressions, and patterns."""

from __future__ import annotations

from typing import assert_never

from compiler.frontend.parse import ast as AST


class AstVisitor:
    """Visit syntax in source order without changing the tree."""

    def visit_program(self, program: AST.Program) -> None:
        for item in program.items:
            match item:
                case AST.FuncDef():
                    for param in item.params:
                        if isinstance(param, AST.PatternParam):
                            self.visit_pattern(param.pattern)
                    self.visit_expr(item.body)
                case AST.Impl():
                    for method in item.items:
                        for param in method.decl.params:
                            if isinstance(param, AST.PatternParam):
                                self.visit_pattern(param.pattern)
                        self.visit_expr(method.body)
                case AST.TraitDef():
                    for method in item.items:
                        if isinstance(method, AST.MethodDef):
                            for param in method.decl.params:
                                if isinstance(param, AST.PatternParam):
                                    self.visit_pattern(param.pattern)
                            self.visit_expr(method.body)
                case AST.ConstDef():
                    self.visit_expr(item.value)
                case AST.Import() | AST.Alias() | AST.StructDef() | AST.EnumDef() | AST.OpaqueTypeDef() | AST.ExternBlock():
                    pass

    def enter_expr(self, expr: AST.Expr) -> bool:
        """Return false to omit the descendants of this expression."""
        return True

    def leave_expr(self, expr: AST.Expr) -> None:
        pass

    def enter_pattern(self, pattern: AST.Pattern) -> bool:
        """Return false to omit the descendants of this pattern."""
        return True

    def leave_pattern(self, pattern: AST.Pattern) -> None:
        pass

    def visit_expr(self, expr: AST.Expr) -> None:
        if not self.enter_expr(expr):
            return
        match expr:
            case AST.Block():
                for binding in expr.parameter_bindings:
                    self.visit_expr(binding)
                for stmt in expr.stmts:
                    self.visit_expr(stmt)
            case AST.Semi():
                self.visit_expr(expr.expr)
            case AST.VarDecl():
                if expr.init_expr is not None:
                    self.visit_expr(expr.init_expr)
            case AST.PatternLet():
                self.visit_expr(expr.init_expr)
                self.visit_pattern(expr.pattern)
                if expr.else_branch is not None:
                    self.visit_expr(expr.else_branch)
            case AST.Return() | AST.Break():
                if expr.expr is not None:
                    self.visit_expr(expr.expr)
            case AST.If():
                self.visit_condition(expr.condition)
                self.visit_expr(expr.then_branch)
                for condition, branch in expr.elif_branches:
                    self.visit_condition(condition)
                    self.visit_expr(branch)
                if expr.else_branch is not None:
                    self.visit_expr(expr.else_branch)
            case AST.ComptimeIf():
                self.visit_expr(expr.condition)
                self.visit_expr(expr.then_branch)
                self.visit_expr(expr.else_branch)
            case AST.For():
                self.visit_expr(expr.iterable)
                self.visit_pattern(expr.pattern)
                self.visit_expr(expr.body)
            case AST.While():
                self.visit_condition(expr.condition)
                self.visit_expr(expr.body)
            case AST.Loop():
                self.visit_expr(expr.body)
            case AST.Match():
                self.visit_expr(expr.expr)
                for arm in expr.arms:
                    self.visit_pattern(arm.pattern)
                    if arm.guard is not None:
                        self.visit_expr(arm.guard)
                    self.visit_expr(arm.body)
            case AST.Defer():
                self.visit_expr(expr.action)
            case AST.Assert():
                self.visit_expr(expr.condition)
                if expr.message is not None:
                    self.visit_expr(expr.message)
            case AST.Delete():
                self.visit_expr(expr.target)
            case AST.ClosureExpr():
                for capture in expr.captures:
                    self.visit_expr(capture.expr)
                for param in expr.params:
                    if isinstance(param, AST.PatternParam):
                        self.visit_pattern(param.pattern)
                self.visit_expr(expr.body)
            case AST.Binary():
                self.visit_expr(expr.left)
                self.visit_expr(expr.right)
            case AST.Unary():
                self.visit_expr(expr.operand)
            case AST.Call():
                self.visit_expr(expr.callee)
                for arg in expr.args:
                    self.visit_expr(arg.value)
            case AST.Builtin():
                for arg in expr.args:
                    self.visit_expr(arg.value)
            case AST.MethodCall():
                self.visit_expr(expr.receiver)
                for arg in expr.args:
                    self.visit_expr(arg.value)
            case AST.FieldAccess():
                self.visit_expr(expr.receiver)
            case AST.DynValue():
                self.visit_expr(expr.value)
            case AST.DynBuffer():
                self.visit_expr(expr.size)
                self.visit_expr(expr.element)
            case AST.Tuple() | AST.Array():
                for element in expr.elements:
                    self.visit_expr(element)
            case AST.ArrayRepeat():
                self.visit_expr(expr.element)
                self.visit_expr(expr.count)
            case AST.Identifier() | AST.Literal() | AST.TypeItem() | AST.CompileConfig() | AST.Continue():
                pass
            case _:
                assert_never(expr)
        self.leave_expr(expr)

    def visit_condition(self, condition: AST.Expr | AST.LetCondition) -> None:
        if isinstance(condition, AST.LetCondition):
            self.visit_pattern(condition.pattern)
            self.visit_expr(condition.value)
        else:
            self.visit_expr(condition)

    def visit_pattern(self, pattern: AST.Pattern) -> None:
        if not self.enter_pattern(pattern):
            return
        match pattern:
            case AST.BindPattern():
                self.visit_pattern(pattern.inner)
            case AST.OrPattern():
                for alternative in pattern.alternatives:
                    self.visit_pattern(alternative)
            case AST.ConstructPattern():
                for child in pattern.positional or []:
                    self.visit_pattern(child)
                for field in pattern.named or []:
                    self.visit_pattern(field.pattern)
            case AST.TuplePattern():
                for child in pattern.elements:
                    self.visit_pattern(child)
            case AST.SequencePattern():
                for child in pattern.prefix:
                    self.visit_pattern(child)
                for child in pattern.suffix:
                    self.visit_pattern(child)
            case AST.WildcardPattern() | AST.LiteralPattern() | AST.RangePattern() | AST.NamePattern():
                pass
            case _:
                assert_never(pattern)
        self.leave_pattern(pattern)


class AstRewriter:
    """Rewrite AST expressions in place, preserving unaffected node identities."""

    def rewrite_expr(self, expr: AST.Expr) -> AST.Expr:
        self.rewrite_children(expr)
        return expr

    def rewrite_block(self, block: AST.Block) -> AST.Block:
        rewritten = self.rewrite_expr(block)
        if not isinstance(rewritten, AST.Block):
            raise TypeError("an AST block must remain a block")
        return rewritten

    def rewrite_condition(self, condition: AST.Expr | AST.LetCondition) -> AST.Expr | AST.LetCondition:
        if isinstance(condition, AST.LetCondition):
            condition.pattern = self.rewrite_pattern(condition.pattern)
            condition.value = self.rewrite_expr(condition.value)
            return condition
        return self.rewrite_expr(condition)

    def rewrite_statement_expressions(self, stmt: AST.Expr) -> None:
        """Rewrite expression fields of a statement without replacing its root."""
        match stmt:
            case AST.VarDecl():
                if stmt.init_expr is not None:
                    stmt.init_expr = self.rewrite_expr(stmt.init_expr)
            case AST.PatternLet():
                stmt.init_expr = self.rewrite_expr(stmt.init_expr)
                if stmt.else_branch is not None:
                    stmt.else_branch = self.rewrite_block(stmt.else_branch)
            case AST.Return():
                if stmt.expr is not None:
                    stmt.expr = self.rewrite_expr(stmt.expr)
            case AST.If():
                stmt.condition = self.rewrite_condition(stmt.condition)
                stmt.elif_branches = [
                    (self.rewrite_condition(cond), branch) for cond, branch in stmt.elif_branches
                ]
            case AST.While():
                stmt.condition = self.rewrite_condition(stmt.condition)
            case AST.ComptimeIf() | AST.Assert():
                stmt.condition = self.rewrite_expr(stmt.condition)
                if isinstance(stmt, AST.Assert) and stmt.message is not None:
                    stmt.message = self.rewrite_expr(stmt.message)
            case AST.Match():
                stmt.expr = self.rewrite_expr(stmt.expr)
                for arm in stmt.arms:
                    if arm.guard is not None:
                        arm.guard = self.rewrite_expr(arm.guard)
            case AST.Delete():
                stmt.target = self.rewrite_expr(stmt.target)
            case AST.For():
                stmt.iterable = self.rewrite_expr(stmt.iterable)
                stmt.pattern = self.rewrite_pattern(stmt.pattern)
            case AST.Binary():
                stmt.left = self.rewrite_expr(stmt.left)
                stmt.right = self.rewrite_expr(stmt.right)
            case AST.Unary():
                stmt.operand = self.rewrite_expr(stmt.operand)
            case AST.Call():
                stmt.callee = self.rewrite_expr(stmt.callee)
                for arg in stmt.args:
                    arg.value = self.rewrite_expr(arg.value)
            case AST.Builtin():
                for arg in stmt.args:
                    arg.value = self.rewrite_expr(arg.value)
            case AST.MethodCall():
                stmt.receiver = self.rewrite_expr(stmt.receiver)
                for arg in stmt.args:
                    arg.value = self.rewrite_expr(arg.value)
            case AST.FieldAccess():
                stmt.receiver = self.rewrite_expr(stmt.receiver)
            case AST.Tuple() | AST.Array():
                stmt.elements = [self.rewrite_expr(value) for value in stmt.elements]
            case AST.ArrayRepeat():
                stmt.element = self.rewrite_expr(stmt.element)
                stmt.count = self.rewrite_expr(stmt.count)
            case AST.DynValue():
                stmt.value = self.rewrite_expr(stmt.value)
            case AST.DynBuffer():
                stmt.size = self.rewrite_expr(stmt.size)
                stmt.element = self.rewrite_expr(stmt.element)
            case AST.Defer():
                stmt.action = self.rewrite_expr(stmt.action)
            case _:
                pass

    def rewrite_children(self, expr: AST.Expr) -> None:
        """Rewrite immediate children; subclasses decide whether to replace the root."""
        match expr:
            case AST.Block():
                for binding in expr.parameter_bindings:
                    self.rewrite_children(binding)
                expr.stmts = [self.rewrite_expr(stmt) for stmt in expr.stmts]
            case AST.Semi():
                expr.expr = self.rewrite_expr(expr.expr)
            case AST.VarDecl():
                if expr.init_expr is not None:
                    expr.init_expr = self.rewrite_expr(expr.init_expr)
            case AST.PatternLet():
                expr.init_expr = self.rewrite_expr(expr.init_expr)
                expr.pattern = self.rewrite_pattern(expr.pattern)
                if expr.else_branch is not None:
                    expr.else_branch = self.rewrite_block(expr.else_branch)
            case AST.Return() | AST.Break():
                if expr.expr is not None:
                    expr.expr = self.rewrite_expr(expr.expr)
            case AST.If():
                expr.condition = self.rewrite_condition(expr.condition)
                expr.then_branch = self.rewrite_block(expr.then_branch)
                expr.elif_branches = [
                    (self.rewrite_condition(condition), self.rewrite_block(branch))
                    for condition, branch in expr.elif_branches
                ]
                if expr.else_branch is not None:
                    expr.else_branch = self.rewrite_block(expr.else_branch)
            case AST.ComptimeIf():
                expr.condition = self.rewrite_expr(expr.condition)
                expr.then_branch = self.rewrite_block(expr.then_branch)
                expr.else_branch = self.rewrite_block(expr.else_branch)
            case AST.For():
                expr.iterable = self.rewrite_expr(expr.iterable)
                expr.pattern = self.rewrite_pattern(expr.pattern)
                expr.body = self.rewrite_block(expr.body)
            case AST.While():
                expr.condition = self.rewrite_condition(expr.condition)
                expr.body = self.rewrite_block(expr.body)
            case AST.Loop():
                expr.body = self.rewrite_block(expr.body)
            case AST.Match():
                expr.expr = self.rewrite_expr(expr.expr)
                for arm in expr.arms:
                    arm.pattern = self.rewrite_pattern(arm.pattern)
                    if arm.guard is not None:
                        arm.guard = self.rewrite_expr(arm.guard)
                    arm.body = self.rewrite_block(arm.body)
            case AST.Defer():
                expr.action = self.rewrite_expr(expr.action)
            case AST.Assert():
                expr.condition = self.rewrite_expr(expr.condition)
                if expr.message is not None:
                    expr.message = self.rewrite_expr(expr.message)
            case AST.Delete():
                expr.target = self.rewrite_expr(expr.target)
            case AST.ClosureExpr():
                for capture in expr.captures:
                    capture.expr = self.rewrite_expr(capture.expr)
                for param in expr.params:
                    if isinstance(param, AST.PatternParam):
                        param.pattern = self.rewrite_pattern(param.pattern)
                expr.body = self.rewrite_block(expr.body)
            case AST.Binary():
                expr.left = self.rewrite_expr(expr.left)
                expr.right = self.rewrite_expr(expr.right)
            case AST.Unary():
                expr.operand = self.rewrite_expr(expr.operand)
            case AST.Call():
                expr.callee = self.rewrite_expr(expr.callee)
                for arg in expr.args:
                    arg.value = self.rewrite_expr(arg.value)
            case AST.Builtin():
                for arg in expr.args:
                    arg.value = self.rewrite_expr(arg.value)
            case AST.MethodCall():
                expr.receiver = self.rewrite_expr(expr.receiver)
                for arg in expr.args:
                    arg.value = self.rewrite_expr(arg.value)
            case AST.FieldAccess():
                expr.receiver = self.rewrite_expr(expr.receiver)
            case AST.DynValue():
                expr.value = self.rewrite_expr(expr.value)
            case AST.DynBuffer():
                expr.size = self.rewrite_expr(expr.size)
                expr.element = self.rewrite_expr(expr.element)
            case AST.Tuple() | AST.Array():
                expr.elements = [self.rewrite_expr(element) for element in expr.elements]
            case AST.ArrayRepeat():
                expr.element = self.rewrite_expr(expr.element)
                expr.count = self.rewrite_expr(expr.count)
            case AST.Identifier() | AST.Literal() | AST.TypeItem() | AST.CompileConfig() | AST.Continue():
                pass
            case _:
                assert_never(expr)

    def rewrite_pattern(self, pattern: AST.Pattern) -> AST.Pattern:
        self.rewrite_pattern_children(pattern)
        return pattern

    def rewrite_pattern_children(self, pattern: AST.Pattern) -> None:
        """Rewrite pattern children without replacing the pattern root."""
        match pattern:
            case AST.BindPattern():
                pattern.inner = self.rewrite_pattern(pattern.inner)
            case AST.OrPattern():
                pattern.alternatives = [self.rewrite_pattern(child) for child in pattern.alternatives]
            case AST.ConstructPattern():
                if pattern.positional is not None:
                    pattern.positional = [self.rewrite_pattern(child) for child in pattern.positional]
                if pattern.named is not None:
                    for field in pattern.named:
                        field.pattern = self.rewrite_pattern(field.pattern)
            case AST.TuplePattern():
                pattern.elements = [self.rewrite_pattern(child) for child in pattern.elements]
            case AST.SequencePattern():
                pattern.prefix = [self.rewrite_pattern(child) for child in pattern.prefix]
                pattern.suffix = [self.rewrite_pattern(child) for child in pattern.suffix]
            case AST.WildcardPattern() | AST.LiteralPattern() | AST.RangePattern() | AST.NamePattern():
                pass
            case _:
                assert_never(pattern)
