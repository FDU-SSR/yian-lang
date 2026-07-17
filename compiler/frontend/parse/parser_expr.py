from __future__ import annotations

from compiler.frontend.lex import token as Tok
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import (SEP_COMMA, SEP_PIPE,
                                            TERM_FAT_ARROW, TERM_RANGLE,
                                            TERM_RBRACE, TERM_RBRACKET,
                                            TERM_RPAREN, TokenStream)


class ExprParser:
    def __init__(self, stream: TokenStream, type_parser: TypeParser):
        self.__stream = stream
        self.__type_parser = type_parser

    def parse_expr(self) -> AST.Expr:
        """Parses an expression from the token stream."""
        return self.__parse_expr_bp(1)

    def __parse_expr_bp(self, min_bp: int) -> AST.Expr:
        """Pratt parser for expressions with operator precedence."""
        # parse the left-hand side (LHS) of the expression
        lhs = self.__parse_prefix()

        while True:

            op = BinaryOperator.try_from_token(self.__stream.peek())

            if op is None:
                break

            if op.lbp < min_bp:
                break

            self.__stream.advance()

            rhs = self.__parse_expr_bp(op.rbp)
            lhs = AST.Binary(span=lhs.span + rhs.span, op=op, left=lhs, right=rhs)

        return lhs

    def __parse_prefix(self) -> AST.Expr:
        """Parses the prefix part of an expression."""
        # try to parse a unary operator
        op = UnaryOperator.try_from_token(self.__stream.peek())
        if op is not None:
            self.__stream.advance()

            operand = self.__parse_expr_bp(op.rbp)
            return AST.Unary(span=operand.span, op=op, operand=operand)

        # try to parse a dyn expression
        token = self.__stream.peek()
        if isinstance(token, Tok.Keyword) and token.kind == Tok.KeywordKind.Dyn:
            return self.__parse_dyn()

        # parse a primary expression
        expr = self.__parse_primary()
        return self.__parse_postfix(expr)

    def __parse_dyn(self) -> AST.Expr:
        """Parses a dyn expression."""
        self.__stream.consume_keyword(Tok.KeywordKind.Dyn)

        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.LBracket:
            # dyn[array_size] Type
            self.__stream.consume_punctuator(Tok.PunctuatorKind.LBracket)
            array_size = self.parse_expr()
            self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)

            target_type = self.__type_parser.parse_type()
            return AST.DynBuffer(span=token.span, target_type=target_type, size=array_size)

        # dyn value
        value = self.parse_expr()
        return AST.DynValue(span=token.span, value=value)

    def __parse_primary(self) -> AST.Expr:
        """Parses a primary expression."""
        token = self.__stream.peek()
        match token:
            case Tok.Punctuator(kind=Tok.PunctuatorKind.LBrace):
                return self.parse_block()
            case Tok.Keyword(kind=Tok.KeywordKind.Return):
                return self.__parse_return()
            case Tok.Keyword(kind=Tok.KeywordKind.If):
                return self.__parse_if()
            case Tok.Keyword(kind=Tok.KeywordKind.For):
                return self.__parse_for()
            case Tok.Keyword(kind=Tok.KeywordKind.While):
                return self.__parse_while()
            case Tok.Keyword(kind=Tok.KeywordKind.Loop):
                return self.__parse_loop()
            case Tok.Keyword(kind=Tok.KeywordKind.Match):
                return self.__parse_match()
            case Tok.Keyword(kind=Tok.KeywordKind.Break):
                return self.__parse_break()
            case Tok.Keyword(kind=Tok.KeywordKind.Continue):
                return self.__parse_continue()
            case Tok.Keyword(kind=Tok.KeywordKind.Assert):
                return self.__parse_assert()
            case Tok.Keyword(kind=Tok.KeywordKind.Del):
                return self.__parse_delete()
            case Tok.Keyword(kind=Tok.KeywordKind.Let):
                return self.__parse_var_decl()
            case Tok.Punctuator(kind=Tok.PunctuatorKind.Pipe):
                return self.__parse_closure()
            case Tok.Punctuator(kind=Tok.PunctuatorKind.PipePipe):
                return self.__parse_closure()
            case Tok.Keyword(kind=Tok.KeywordKind.True_):
                self.__stream.consume_keyword(Tok.KeywordKind.True_)
                return AST.Literal(span=token.span, literal=Tok.BoolLiteral(raw="true", span=token.span, value=True))
            case Tok.Keyword(kind=Tok.KeywordKind.False_):
                self.__stream.consume_keyword(Tok.KeywordKind.False_)
                return AST.Literal(span=token.span, literal=Tok.BoolLiteral(raw="false", span=token.span, value=False))
            case Tok.Keyword(kind=Tok.KeywordKind.Nullptr):
                self.__stream.consume_keyword(Tok.KeywordKind.Nullptr)
                return AST.Literal(span=token.span, literal=Tok.NullptrLiteral(raw="nullptr", span=token.span))
            case Tok.Keyword(kind=Tok.KeywordKind.Sizeof):
                return self.__parse_sizeof()
            case Tok.Keyword(kind=Tok.KeywordKind.Bitcast):
                return self.__parse_bitcast()
            case Tok.Identifier() | Tok.Keyword():
                ident = self.__stream.consume_identifier()

                next_token = self.__stream.peek()
                if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.LAngle:
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.LAngle)
                    generics = self.__stream.consume_separated(self.__type_parser.parse_generic_arg, SEP_COMMA, TERM_RANGLE)
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RAngle)
                    return AST.TypeItem(span=ident.span, name=ident, generics=generics)

                return ident
            case Tok.IntLiteral() | Tok.FloatLiteral() | Tok.CharLiteral() | Tok.StrLiteral():
                self.__stream.advance()
                return AST.Literal(span=token.span, literal=token)
            case Tok.FStrStart():
                return self.__parse_fstring(token)
            case Tok.Punctuator(kind=Tok.PunctuatorKind.LParen):
                self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                expr = self.parse_expr()

                next_token = self.__stream.peek()
                if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Comma:
                    # tuple expression
                    items: list[AST.Expr] = [expr]
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.Comma)
                    next_token = self.__stream.peek()
                    if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.RParen:
                        # single-element tuple (e.g., (expr,))
                        self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
                        return AST.Tuple(span=token.span, elements=items)

                    items += self.__stream.consume_separated(self.parse_expr, SEP_COMMA, TERM_RPAREN)
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
                    return AST.Tuple(span=token.span, elements=items)

                # parenthesized expression
                self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
                return expr
            case Tok.Punctuator(kind=Tok.PunctuatorKind.LBracket):
                self.__stream.consume_punctuator(Tok.PunctuatorKind.LBracket)

                # [] — empty array literal (rejected later by type checker)
                next_tok = self.__stream.peek()
                if isinstance(next_tok, Tok.Punctuator) and next_tok.kind == Tok.PunctuatorKind.RBracket:
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)
                    return AST.Array(span=token.span, elements=[])

                first = self.parse_expr()
                next_tok = self.__stream.peek()

                # [value; count] — array repeat syntax
                if isinstance(next_tok, Tok.Punctuator) and next_tok.kind == Tok.PunctuatorKind.Semicolon:
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.Semicolon)
                    count = self.parse_expr()
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)
                    return AST.ArrayRepeat(span=token.span, element=first, count=count)

                # [elem1, elem2, ...] or [elem] — comma-separated array
                items = [first]
                if isinstance(next_tok, Tok.Punctuator) and next_tok.kind == Tok.PunctuatorKind.Comma:
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.Comma)
                    rest = self.__stream.consume_separated(self.parse_expr, SEP_COMMA, TERM_RBRACKET)
                    items.extend(rest)
                self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)
                return AST.Array(span=token.span, elements=items)
            case _:
                raise ParseError(f"Unexpected token '{token}' while parsing expression", token.span)

    def __parse_fstring(self, start_token: Tok.FStrStart) -> AST.Expr:
        """Parse an f-string: FStrStart (FStrLiteral | FStrExprBegin expr* FStrExprEnd)* FStrEnd

        Desugars into a chain of ``+`` operations:
            String.new() + literal1 + expr1.to_string() + literal2 + ...
        """
        span = start_token.span
        self.__stream.advance()

        segments: list[AST.Expr] = []

        while True:
            token = self.__stream.peek()

            if isinstance(token, Tok.FStrLiteral):
                self.__stream.advance()
                lit = Tok.StrLiteral(
                    raw=token.value,
                    span=token.span,
                    value=token.value,
                )
                segments.append(AST.Literal(span=token.span, literal=lit))
                continue

            if isinstance(token, Tok.FStrExprBegin):
                self.__stream.advance()
                expr = self.parse_expr()
                self.__stream.advance()  # FStrExprEnd
                method_call = AST.MethodCall(
                    span=expr.span,
                    receiver=expr,
                    method_name=AST.Identifier(expr.span, "to_string"),
                    generics=[],
                    args=[],
                )
                segments.append(method_call)
                continue

            if isinstance(token, Tok.FStrEnd):
                self.__stream.advance()
                break

            raise ParseError(f"Unexpected token '{token}' in f-string", token.span)

        str_new = AST.MethodCall(
            span=span,
            receiver=AST.TypeItem(
                span=span,
                name=AST.Identifier(span, "String"),
                generics=[],
            ),
            method_name=AST.Identifier(span, "new"),
            generics=[],
            args=[],
        )

        result: AST.Expr = str_new
        for seg in segments:
            result = AST.Binary(
                span=span,
                left=result,
                op=BinaryOperator.Add,
                right=seg,
            )

        return result

    def __parse_closure(self) -> AST.ClosureExpr:
        """Parse a closure expression ``|captures| (params) -> R { body }``."""
        start_token = self.__stream.peek()
        captures: list[AST.CaptureItem] = []

        if isinstance(start_token, Tok.Punctuator) and start_token.kind == Tok.PunctuatorKind.PipePipe:
            # ``||`` — empty capture list, both pipes consumed at once
            start = self.__stream.consume_punctuator(Tok.PunctuatorKind.PipePipe)
        else:
            # ``|`` — may have captures or be ``||`` as two separate tokens
            start = self.__stream.consume_punctuator(Tok.PunctuatorKind.Pipe)
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Pipe:
                # ``||`` as two separate Pipe tokens — empty capture list
                self.__stream.consume_punctuator(Tok.PunctuatorKind.Pipe)
            else:
                # parse capture items: ident = expr, ...
                while True:
                    cap_name = self.__stream.consume_identifier()
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)
                    cap_expr = self.__parse_expr_bp(5)  # min_bp=5 excludes | (BitOr lbp=4)
                    captures.append(AST.CaptureItem(
                        span=cap_name.span + cap_expr.span,
                        name=cap_name,
                        expr=cap_expr,
                    ))

                    cap_next = self.__stream.peek()
                    if isinstance(cap_next, Tok.Punctuator) and cap_next.kind == Tok.PunctuatorKind.Comma:
                        self.__stream.consume_punctuator(Tok.PunctuatorKind.Comma)
                    else:
                        break

                # consume closing pipe
                self.__stream.consume_punctuator(Tok.PunctuatorKind.Pipe)

        # --- parameter list ---
        self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
        params: list[AST.VarInfo] = []
        param_next = self.__stream.peek()
        if not (isinstance(param_next, Tok.Punctuator) and param_next.kind == Tok.PunctuatorKind.RParen):
            while True:
                param_name = self.__stream.consume_identifier()
                self.__stream.consume_punctuator(Tok.PunctuatorKind.Colon)
                param_type = self.__type_parser.parse_type()
                params.append(AST.VarInfo(
                    span=param_name.span,
                    name=param_name,
                    var_type=param_type,
                ))

                p_next = self.__stream.peek()
                if isinstance(p_next, Tok.Punctuator) and p_next.kind == Tok.PunctuatorKind.Comma:
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.Comma)
                else:
                    break
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

        # --- return type (optional, defaults to void) ---
        return_type: AST.ASTType | None = None
        ret_next = self.__stream.peek()
        if isinstance(ret_next, Tok.Punctuator) and ret_next.kind == Tok.PunctuatorKind.Arrow:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Arrow)
            return_type = self.__type_parser.parse_type()

        # --- body ---
        body = self.parse_block()

        return AST.ClosureExpr(
            span=start.span + body.span,
            captures=captures,
            params=params,
            return_type=return_type,
            body=body,
        )

    def __parse_postfix(self, expr: AST.Expr) -> AST.Expr:
        """Parses postfix expressions (e.g., function calls, field access)."""
        # absorb postfix as long as possible
        while True:
            token = self.__stream.peek()
            match token:
                case Tok.Punctuator(kind=Tok.PunctuatorKind.LParen):
                    # function call
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                    args = self.__stream.consume_separated(self.parse_arg, SEP_COMMA, TERM_RPAREN)
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

                    expr = AST.Call(span=expr.span + token.span, callee=expr, args=args)

                case Tok.Punctuator(kind=Tok.PunctuatorKind.Dot):
                    # field access or method call
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.Dot)

                    field_or_method_name = self.__stream.consume_identifier()
                    next_token = self.__stream.peek()
                    match next_token:
                        case Tok.Punctuator(kind=Tok.PunctuatorKind.LParen):
                            # method call
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                            args = self.__stream.consume_separated(self.parse_arg, SEP_COMMA, TERM_RPAREN)
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

                            expr = AST.MethodCall(span=expr.span, receiver=expr, method_name=field_or_method_name, generics=[], args=args)
                        case Tok.Punctuator(kind=Tok.PunctuatorKind.LAngle):
                            # Generic method call: expr.name<T>(args)
                            # LAngle means no preceding whitespace — always generic opening
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.LAngle)
                            generics = self.__stream.consume_separated(self.__type_parser.parse_type, SEP_COMMA, TERM_RANGLE)
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.RAngle)

                            self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                            args = self.__stream.consume_separated(self.parse_arg, SEP_COMMA, TERM_RPAREN)
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

                            expr = AST.MethodCall(span=expr.span, receiver=expr, method_name=field_or_method_name, generics=generics, args=args)
                        case _:
                            # field access
                            expr = AST.FieldAccess(span=expr.span, receiver=expr, field_name=field_or_method_name)

                case Tok.Punctuator(kind=Tok.PunctuatorKind.LBracket):
                    # array indexing
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.LBracket)
                    index_expr = self.parse_expr()
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)

                    expr = AST.Binary(span=expr.span, op=BinaryOperator.Index, left=expr, right=index_expr)

                case _:
                    return expr

    def __parse_sizeof(self) -> AST.SizeOf:
        """Parse ``sizeof(type)`` — argument is unconditionally a type."""
        kw = self.__stream.consume_keyword(Tok.KeywordKind.Sizeof)
        self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
        ty = self.__type_parser.parse_type()
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
        return AST.SizeOf(span=kw.span, ty=ty)

    def __parse_bitcast(self) -> AST.BitCast:
        """Parse ``bitcast<type>(expr)`` — reinterpret a pointer as a different pointer type."""
        kw = self.__stream.consume_keyword(Tok.KeywordKind.Bitcast)
        self.__stream.consume_punctuator(Tok.PunctuatorKind.LAngle)
        ty = self.__type_parser.parse_type()
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RAngle)
        self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
        value = self.parse_expr()
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
        return AST.BitCast(span=kw.span, target_type=ty, value=value)

    def parse_arg(self) -> AST.Arg:
        """Parses a single argument, which can be either positional (expr) or named (name=expr)."""
        token = self.__stream.peek()
        if isinstance(token, Tok.Identifier):
            # could be a named argument or a positional argument
            # look ahead for '='
            ahead = self.__stream.peek_nth(1)
            if isinstance(ahead, Tok.Punctuator) and ahead.kind == Tok.PunctuatorKind.Equal:
                # named argument
                name = self.__stream.consume_identifier()
                self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)
                value = self.parse_expr()
                return AST.Arg(span=name.span + value.span, name=name, value=value)

        # positional argument
        value = self.parse_expr()
        return AST.Arg(span=value.span, name=None, value=value)

    # ------------------------------------------------------------------
    # block / control-flow parsing (expression-oriented)
    # ------------------------------------------------------------------

    def __parse_expr_stmt(self) -> AST.Expr:
        """Parse an expression; if followed by ``;``, wrap in :class:`AST.Semi`.

        This is the fundamental building block for contexts where ``expr``
        and ``expr;`` are both valid (e.g. inside a block).
        """
        item = self.parse_expr()
        next_tok = self.__stream.peek()
        if isinstance(next_tok, Tok.Punctuator) and next_tok.kind == Tok.PunctuatorKind.Semicolon:
            self.__stream.consume_semicolon()
            return AST.Semi(span=item.span, expr=item)
        return item

    def parse_block(self) -> AST.Block:
        """Parses a block expression ``{ ... }``.

        Each item is parsed via :meth:`__parse_expr_stmt`, which wraps
        ``expr;`` in :class:`AST.Semi`.  The block's type is determined
        by the last item (``Semi`` → void, bare expr → the expr's type).
        """
        span = self.__stream.consume_punctuator(Tok.PunctuatorKind.LBrace).span
        stmts = list(self.__stream.consume_until(self.__parse_expr_stmt, TERM_RBRACE))
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RBrace)
        return AST.Block(span=span, stmts=stmts)

    def __parse_return(self) -> AST.Return:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Return).span
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Semicolon:
            expr = None
        else:
            expr = self.parse_expr()
        return AST.Return(span=span, expr=expr)

    def __parse_if(self) -> AST.If:
        span = self.__stream.consume_keyword(Tok.KeywordKind.If).span
        condition = self.parse_expr()
        then_branch = self.parse_block()

        elif_branches: list[tuple[AST.Expr, AST.Block]] = []
        while True:
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.Keyword) and next_token.kind == Tok.KeywordKind.Elif:
                self.__stream.consume_keyword(Tok.KeywordKind.Elif)
                elif_condition = self.parse_expr()
                elif_block = self.parse_block()
                elif_branches.append((elif_condition, elif_block))
            else:
                break

        else_branch: AST.Block | None = None
        next_token = self.__stream.peek()
        if isinstance(next_token, Tok.Keyword) and next_token.kind == Tok.KeywordKind.Else:
            self.__stream.consume_keyword(Tok.KeywordKind.Else)
            else_branch = self.parse_block()

        return AST.If(span=span, condition=condition, then_branch=then_branch, elif_branches=elif_branches, else_branch=else_branch)

    def __parse_for(self) -> AST.For:
        span = self.__stream.consume_keyword(Tok.KeywordKind.For).span
        var_name = self.__stream.consume_identifier()
        self.__stream.consume_keyword(Tok.KeywordKind.In)
        iterable = self.parse_expr()
        body = self.parse_block()
        return AST.For(span=span, var_name=var_name, iterable=iterable, body=body)

    def __parse_while(self) -> AST.While:
        span = self.__stream.consume_keyword(Tok.KeywordKind.While).span
        condition = self.parse_expr()
        body = self.parse_block()
        return AST.While(span=span, condition=condition, body=body)

    def __parse_loop(self) -> AST.Loop:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Loop).span
        body = self.parse_block()
        return AST.Loop(span=span, body=body)

    def __parse_match(self) -> AST.Match:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Match).span
        expr = self.parse_expr()
        self.__stream.consume_punctuator(Tok.PunctuatorKind.LBrace)
        arms = self.__stream.consume_until(self.__parse_match_arm, TERM_RBRACE)
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RBrace)
        return AST.Match(span=span, expr=expr, arms=arms)

    def __parse_break(self) -> AST.Break:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Break).span
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind in (Tok.PunctuatorKind.Semicolon, Tok.PunctuatorKind.RBrace):
            return AST.Break(span=span)
        expr = self.parse_expr()
        return AST.Break(span=span, expr=expr)

    def __parse_continue(self) -> AST.Continue:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Continue).span
        return AST.Continue(span=span)

    def __parse_assert(self) -> AST.Assert:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Assert).span
        condition = self.parse_expr()
        next_token = self.__stream.peek()
        if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Colon:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Colon)
            message = self.parse_expr()
        else:
            message = None
        return AST.Assert(span=span, condition=condition, message=message)

    def __parse_delete(self) -> AST.Delete:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Del).span
        target = self.parse_expr()
        return AST.Delete(span=span, target=target)

    def __parse_var_decl(self) -> AST.VarDecl:
        self.__stream.consume_keyword(Tok.KeywordKind.Let)
        var_name = self.__stream.consume_identifier()

        next_token = self.__stream.peek()
        if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Colon:
            # Explicit type annotation: let x: Type [= expr]
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Colon)
            var_type = self.__type_parser.parse_type()
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Equal:
                self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)
                init_expr = self.parse_expr()
            else:
                init_expr = None
        elif isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Equal:
            # Type inference: let x = expr
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)
            var_type = ASTTy.DeducedType(span=var_name.span)
            init_expr = self.parse_expr()
        else:
            raise ParseError("Expected ':' or '=' after variable name", next_token.span)

        return AST.VarDecl(span=var_name.span, name=var_name, var_type=var_type, init_expr=init_expr)

    # ------------------------------------------------------------------
    # match-arm / pattern helpers
    # ------------------------------------------------------------------

    def __parse_match_arm(self) -> tuple[AST.Pattern, AST.Block]:
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Comma:
            self.__stream.advance()
        pattern = self.__parse_pattern()
        self.__stream.consume_punctuator(Tok.PunctuatorKind.FatArrow)
        # 如果 arm 体以 '{' 开头，解析为 block；否则解析为裸表达式并包装为 block
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.LBrace:
            block = self.parse_block()
        else:
            expr = self.parse_expr()
            block = AST.Block(span=expr.span, stmts=[expr])
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Comma:
            self.__stream.advance()
        return pattern, block

    def __parse_pattern(self) -> AST.Pattern:
        next_token = self.__stream.peek()
        match next_token:
            case Tok.IntLiteral():
                values = self.__stream.consume_separated(self.__parse_int_pattern_value, SEP_COMMA, TERM_FAT_ARROW)
                return AST.IntPattern(span=values[0].span, values=values)
            case Tok.Punctuator(kind=Tok.PunctuatorKind.Minus):
                values = self.__stream.consume_separated(self.__parse_int_pattern_value, SEP_COMMA, TERM_FAT_ARROW)
                return AST.IntPattern(span=values[0].span, values=values)
            case Tok.CharLiteral():
                values = self.__stream.consume_separated(self.__parse_char_pattern_value, SEP_COMMA, TERM_FAT_ARROW)
                return AST.CharPattern(span=values[0].span, values=values)
            case Tok.StrLiteral():
                values = self.__stream.consume_separated(self.__parse_str_pattern_value, SEP_COMMA, TERM_FAT_ARROW)
                return AST.StrPattern(span=values[0].span, values=values)
            case Tok.Identifier():
                next_next_token = self.__stream.peek_nth(1)
                if isinstance(next_next_token, Tok.Punctuator) and next_next_token.kind == Tok.PunctuatorKind.LParen:
                    return self.__parse_enum_payload_pattern()
                variants = self.__stream.consume_separated(self.__stream.consume_identifier, SEP_PIPE, TERM_FAT_ARROW)
                return AST.EnumPattern(span=variants[0].span, variants=variants)
            case Tok.Keyword(kind=Tok.KeywordKind.Underscore):
                span = self.__stream.consume_keyword(Tok.KeywordKind.Underscore).span
                return AST.WildcardPattern(span=span)
            case _:
                raise ParseError(f"Unexpected token '{next_token}' in pattern", next_token.span)

    def __parse_int_pattern_value(self) -> Tok.IntLiteral:
        token = self.__stream.peek()
        if isinstance(token, Tok.IntLiteral):
            self.__stream.advance()
            return token
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Minus:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Minus)
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.IntLiteral):
                self.__stream.advance()
                return Tok.IntLiteral(span=token.span + next_token.span, raw="-" + next_token.raw, value=-next_token.value, suffix=next_token.suffix)
            raise ParseError(f"Expected integer literal after '-' in pattern but got '{next_token}'", token.span)
        raise ParseError(f"Expected integer literal or '-' in pattern but got '{token}'", token.span)

    def __parse_char_pattern_value(self) -> Tok.CharLiteral:
        token = self.__stream.peek()
        if isinstance(token, Tok.CharLiteral):
            self.__stream.advance()
            return token
        raise ParseError(f"Expected character literal in pattern but got '{token}'", token.span)

    def __parse_str_pattern_value(self) -> Tok.StrLiteral:
        token = self.__stream.peek()
        if isinstance(token, Tok.StrLiteral):
            self.__stream.advance()
            return token
        raise ParseError(f"Expected string literal in pattern but got '{token}'", token.span)

    def __parse_enum_payload_pattern(self) -> AST.PayloadPattern:
        variant_token = self.__stream.consume_identifier()
        self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
        fields = self.__stream.consume_separated(self.__stream.consume_identifier, SEP_COMMA, TERM_RPAREN)
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
        return AST.PayloadPattern(span=variant_token.span, variant=variant_token, fields=fields)
