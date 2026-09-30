from __future__ import annotations

from compiler.frontend.lex import token as Tok
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse import ast_type as ASTTy
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import (SEP_COMMA, TERM_RANGLE,
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
            # dyn[element_count] value — allocate `count` elements, all
            # initialized from `value` (a type name here is diagnosed during
            # lowering: uninitialized allocation is spelled `@alloc<T>(n)`).
            self.__stream.consume_punctuator(Tok.PunctuatorKind.LBracket)
            array_size = self.parse_expr()
            self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)

            element = self.__parse_dyn_element()
            return AST.DynBuffer(span=token.span, size=array_size, element=element)

        # dyn value
        value = self.parse_expr()
        return AST.DynValue(span=token.span, value=value)

    def __parse_dyn_element(self) -> AST.Expr:
        """Parse the initializer of ``dyn[n] value``.

        ``dyn[n]`` takes a value initializer. Plain type names reach lowering and
        are diagnosed there; a pointer/array type instead fails to parse
        (``T*`` looks like multiplication without a right operand). When the
        expression parse fails but a type parses cleanly to the end of the
        element, report the unsupported type-directed form rather than the raw
        syntax error.
        """
        mark = self.__stream.mark()
        try:
            return self.parse_expr()
        except ParseError:
            self.__stream.reset(mark)
            if not self.__looks_like_unsupported_dyn_type():
                raise
            raise ParseError(
                "'dyn[n]' initializes every element from a value, not a type: "
                "write 'dyn[n] <initial value>'",
                self.__stream.peek().span,
            ) from None

    def __looks_like_unsupported_dyn_type(self) -> bool:
        """True when an unsupported type-directed form consumes the initializer."""
        element_mark = self.__stream.mark()
        try:
            self.__type_parser.parse_type()
        except ParseError:
            self.__stream.reset(element_mark)
            return False
        ends_element = self.__at_initializer_end()
        self.__stream.reset(element_mark)
        return ends_element

    def __at_initializer_end(self) -> bool:
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind in (
            Tok.PunctuatorKind.Semicolon,
            Tok.PunctuatorKind.RParen,
            Tok.PunctuatorKind.Comma,
            Tok.PunctuatorKind.RBracket,
            Tok.PunctuatorKind.RBrace,
        ):
            return True
        return self.__stream.at_end()

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
            case Tok.Keyword(kind=Tok.KeywordKind.Comptime):
                return self.__parse_comptime_if()
            case Tok.Keyword(kind=Tok.KeywordKind.IsRawMode):
                self.__stream.consume_keyword(Tok.KeywordKind.IsRawMode)
                return AST.CompileConfig(span=token.span, name=Tok.KeywordKind.IsRawMode.value)
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
            case Tok.Keyword(kind=Tok.KeywordKind.Defer):
                raise ParseError("'defer' is only allowed as a statement", token.span)
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
            case Tok.Punctuator(kind=Tok.PunctuatorKind.At):
                return self.__parse_builtin()
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

                # [] — the type checker cannot infer an element type for an empty array literal.
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
                if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.EOF:
                    # A half-written expression: report it as end-of-file rather
                    # than as a stray token, which is what the editor shows
                    # while the user is typing.
                    raise ParseError("Unexpected end of file while parsing expression", token.span)
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
        params: list[AST.VarInfo | AST.PatternParam] = []
        param_next = self.__stream.peek()
        if not (isinstance(param_next, Tok.Punctuator) and param_next.kind == Tok.PunctuatorKind.RParen):
            while True:
                param_pattern = self.parse_binding_pattern()
                self.__stream.consume_punctuator(Tok.PunctuatorKind.Colon)
                param_type = self.__type_parser.parse_type()
                if isinstance(param_pattern, AST.NamePattern):
                    params.append(AST.VarInfo(param_pattern.span, param_pattern.name, param_type))
                else:
                    params.append(AST.PatternParam(param_pattern.span, param_pattern, param_type))

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
                case Tok.Punctuator(kind=Tok.PunctuatorKind.Question):
                    self.__stream.advance()
                    expr = AST.TryExpr(span=expr.span + token.span, operand=expr)
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

    def __parse_builtin(self) -> AST.Expr:
        """Parse the shared ``@name<types>(values)`` instruction form."""
        at = self.__stream.consume_punctuator(Tok.PunctuatorKind.At)
        name = self.__stream.consume_identifier()
        kind = AST.BuiltinKind.try_from_name(name.name)
        if kind is None:
            raise ParseError(f"unknown builtin '{name.name}' (expected '@name')", name.span)

        type_args: list[ASTTy.ASTType] = []
        end_span = name.span
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.LAngle:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.LAngle)
            type_args = self.__stream.consume_separated(
                self.__type_parser.parse_type, SEP_COMMA, TERM_RANGLE
            )
            end_span = self.__stream.consume_punctuator(Tok.PunctuatorKind.RAngle).span

        args: list[AST.Arg] = []
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.LParen:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
            args = self.__stream.consume_separated(self.parse_arg, SEP_COMMA, TERM_RPAREN)
            end_span = self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen).span

        return AST.Builtin(
            span=at.span + end_span,
            kind=kind,
            type_args=type_args,
            args=args,
        )

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
        token = self.__stream.peek()
        if isinstance(token, Tok.Keyword) and token.kind == Tok.KeywordKind.Defer:
            return self.__parse_defer()

        item = self.parse_expr()
        next_tok = self.__stream.peek()
        if isinstance(next_tok, Tok.Punctuator) and next_tok.kind == Tok.PunctuatorKind.Semicolon:
            self.__stream.consume_semicolon()
            return AST.Semi(span=item.span, expr=item)
        return item

    def __parse_defer(self) -> AST.Defer:
        """Parse a lexical-scope defer statement with its required terminator."""
        span = self.__stream.consume_keyword(Tok.KeywordKind.Defer).span
        action = self.parse_expr()
        self.__stream.consume_punctuator(Tok.PunctuatorKind.Semicolon)
        return AST.Defer(span=span, action=action)

    def parse_block(self) -> AST.Block:
        """Parses a block expression ``{ ... }``.

        Each item is parsed via :meth:`__parse_expr_stmt`, which wraps
        ``expr;`` in :class:`AST.Semi`.  The block's type is determined
        by the last item (``Semi`` → void, bare expr → the expr's type).
        """
        span = self.__stream.consume_punctuator(Tok.PunctuatorKind.LBrace).span
        stmts = list(self.__stream.consume_until(self.__parse_expr_stmt, TERM_RBRACE))
        # The closing brace is ours to see: record where the block ends so a
        # consumer can ask for the body's extent instead of scanning tokens.
        close = self.__stream.consume_punctuator(Tok.PunctuatorKind.RBrace).span
        return AST.Block(span=span, stmts=stmts, end=close.end.clone())

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
        condition = self.__parse_condition()
        then_branch = self.parse_block()

        elif_branches: list[tuple[AST.Expr | AST.LetCondition, AST.Block]] = []
        while True:
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.Keyword) and next_token.kind == Tok.KeywordKind.Elif:
                self.__stream.consume_keyword(Tok.KeywordKind.Elif)
                elif_condition = self.__parse_condition()
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

    def __parse_condition(self) -> AST.Expr | AST.LetCondition:
        token = self.__stream.peek()
        if isinstance(token, Tok.Keyword) and token.kind == Tok.KeywordKind.Let:
            span = self.__stream.consume_keyword(Tok.KeywordKind.Let).span
            pattern = self.parse_binding_pattern()
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)
            return AST.LetCondition(span=span, pattern=pattern, value=self.parse_expr())
        return self.parse_expr()

    def __parse_comptime_if(self) -> AST.ComptimeIf:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Comptime).span
        self.__stream.consume_keyword(Tok.KeywordKind.If)
        condition = self.parse_expr()
        then_branch = self.parse_block()
        self.__stream.consume_keyword(Tok.KeywordKind.Else)
        else_branch = self.parse_block()
        return AST.ComptimeIf(
            span=span,
            condition=condition,
            then_branch=then_branch,
            else_branch=else_branch,
        )

    def __parse_for(self) -> AST.For:
        span = self.__stream.consume_keyword(Tok.KeywordKind.For).span
        pattern = self.parse_binding_pattern()
        self.__stream.consume_keyword(Tok.KeywordKind.In)
        iterable = self.parse_expr()
        body = self.parse_block()
        return AST.For(span=span, pattern=pattern, iterable=iterable, body=body)

    def __parse_while(self) -> AST.While:
        span = self.__stream.consume_keyword(Tok.KeywordKind.While).span
        condition = self.__parse_condition()
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

    def __parse_var_decl(self) -> AST.VarDecl | AST.PatternLet:
        let_span = self.__stream.consume_keyword(Tok.KeywordKind.Let).span
        pattern = self.parse_binding_pattern()

        next_token = self.__stream.peek()
        if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Colon:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Colon)
            var_type = self.__type_parser.parse_type()
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Equal:
                self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)
                init_expr = self.parse_expr()
            else:
                init_expr = None
        elif isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Equal:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)
            var_type = ASTTy.DeducedType(span=pattern.span)
            init_expr = self.parse_expr()
        else:
            raise ParseError("Expected ':' or '=' after let pattern", next_token.span)

        else_branch = None
        next_token = self.__stream.peek()
        if isinstance(next_token, Tok.Keyword) and next_token.kind == Tok.KeywordKind.Else:
            self.__stream.advance()
            else_branch = self.parse_block()
        if isinstance(pattern, AST.NamePattern) and else_branch is None:
            return AST.VarDecl(span=pattern.span, name=pattern.name, var_type=var_type, init_expr=init_expr)
        if init_expr is None:
            raise ParseError("A destructuring let requires an initializer", pattern.span)
        return AST.PatternLet(let_span, pattern, var_type, init_expr, else_branch)

    def parse_binding_pattern(self) -> AST.Pattern:
        return self.__parse_pattern_or()

    # ------------------------------------------------------------------
    # match-arm / pattern helpers
    # ------------------------------------------------------------------

    def __parse_match_arm(self) -> AST.MatchArm:
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Comma:
            self.__stream.advance()
        pattern = self.__parse_pattern()
        guard: AST.Expr | None = None
        token = self.__stream.peek()
        if isinstance(token, Tok.Keyword) and token.kind == Tok.KeywordKind.If:
            self.__stream.advance()
            guard = self.parse_expr()
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
        return AST.MatchArm(span=pattern.span, pattern=pattern, guard=guard, body=block)

    def __parse_pattern(self) -> AST.Pattern:
        first = self.__parse_pattern_or()
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Comma:
            if not isinstance(first, AST.LiteralPattern):
                raise ParseError("Comma-separated alternatives require literals", token.span)
            alternatives: list[AST.Pattern] = [first]
            while self.__at_pattern_punct(Tok.PunctuatorKind.Comma):
                self.__stream.advance()
                alternative = self.__parse_pattern_atom()
                if not isinstance(alternative, AST.LiteralPattern) or type(alternative.literal) is not type(first.literal):
                    raise ParseError("Comma-separated alternatives must use the same literal kind", alternative.span)
                alternatives.append(alternative)
            return AST.OrPattern(span=first.span + alternatives[-1].span, alternatives=alternatives)
        return first

    def __parse_pattern_or(self) -> AST.Pattern:
        first = self.__parse_pattern_at()
        alternatives = [first]
        while self.__at_pattern_punct(Tok.PunctuatorKind.Pipe):
            self.__stream.advance()
            alternatives.append(self.__parse_pattern_at())
        if len(alternatives) == 1:
            return first
        return AST.OrPattern(span=first.span + alternatives[-1].span, alternatives=alternatives)

    def __parse_pattern_at(self) -> AST.Pattern:
        token = self.__stream.peek()
        following = self.__stream.peek_nth(1)
        if isinstance(token, Tok.Identifier) and isinstance(following, Tok.Punctuator) and following.kind == Tok.PunctuatorKind.At:
            name = self.__stream.consume_identifier()
            self.__stream.advance()
            inner = self.__parse_pattern_at()
            return AST.BindPattern(span=name.span + inner.span, name=name, inner=inner)
        return self.__parse_pattern_atom()

    def __parse_pattern_atom(self) -> AST.Pattern:
        token = self.__stream.peek()
        if isinstance(token, Tok.Keyword) and token.kind == Tok.KeywordKind.Underscore:
            self.__stream.advance()
            return AST.WildcardPattern(span=token.span)
        if isinstance(token, Tok.Keyword) and token.kind in (Tok.KeywordKind.True_, Tok.KeywordKind.False_):
            self.__stream.advance()
            literal = Tok.BoolLiteral(raw=token.kind.value, span=token.span, value=token.kind == Tok.KeywordKind.True_)
            return AST.LiteralPattern(span=token.span, literal=literal)
        if isinstance(token, (Tok.IntLiteral, Tok.CharLiteral, Tok.StrLiteral)) or (
            isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Minus
        ):
            literal = self.__parse_pattern_literal()
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.DotDot:
                if not isinstance(literal, (Tok.IntLiteral, Tok.CharLiteral)):
                    raise ParseError("Range endpoints must be integer or character literals", next_token.span)
                self.__stream.advance()
                upper = self.__parse_pattern_literal()
                if not isinstance(upper, type(literal)):
                    raise ParseError("Range endpoints must have the same literal kind", upper.span)
                return AST.RangePattern(span=literal.span + upper.span, lower=literal, upper=upper)
            return AST.LiteralPattern(span=literal.span, literal=literal)
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.LBracket:
            self.__stream.advance()
            prefix: list[AST.Pattern] = []
            suffix: list[AST.Pattern] = []
            rest = False
            while not self.__at_pattern_punct(Tok.PunctuatorKind.RBracket):
                item = self.__stream.peek()
                if isinstance(item, Tok.Punctuator) and item.kind == Tok.PunctuatorKind.DotDot:
                    if rest:
                        raise ParseError("Only one '..' is allowed in a sequence pattern", item.span)
                    self.__stream.advance()
                    rest = True
                else:
                    (suffix if rest else prefix).append(self.__parse_pattern_or())
                item = self.__stream.peek()
                if isinstance(item, Tok.Punctuator) and item.kind == Tok.PunctuatorKind.Comma:
                    self.__stream.advance()
                else:
                    break
            self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)
            return AST.SequencePattern(span=token.span, prefix=prefix, suffix=suffix, rest=rest)
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.LParen:
            self.__stream.advance()
            if self.__at_pattern_punct(Tok.PunctuatorKind.RParen):
                self.__stream.advance()
                return AST.TuplePattern(span=token.span, elements=[])
            first = self.__parse_pattern_or()
            if self.__at_pattern_punct(Tok.PunctuatorKind.RParen):
                self.__stream.advance()
                return first
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Comma)
            elements = [first]
            while not self.__at_pattern_punct(Tok.PunctuatorKind.RParen):
                elements.append(self.__parse_pattern_or())
                if not self.__at_pattern_punct(Tok.PunctuatorKind.Comma):
                    break
                self.__stream.advance()
            self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
            return AST.TuplePattern(span=token.span, elements=elements)
        if isinstance(token, (Tok.Identifier, Tok.Keyword)):
            return self.__parse_named_pattern()
        raise ParseError(f"Unexpected token '{token}' in pattern", token.span)

    def __parse_pattern_literal(self) -> Tok.IntLiteral | Tok.CharLiteral | Tok.StrLiteral:
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Minus:
            return self.__parse_int_pattern_value()
        if isinstance(token, (Tok.IntLiteral, Tok.CharLiteral, Tok.StrLiteral)):
            self.__stream.advance()
            return token
        raise ParseError("Expected literal in pattern", token.span)

    def __parse_named_pattern(self) -> AST.Pattern:
        mark = self.__stream.mark()
        first_token = self.__stream.peek()
        try:
            qualifier = self.__type_parser.parse_type()
        except ParseError:
            self.__stream.reset(mark)
            qualifier = None
        next_token = self.__stream.peek()
        if qualifier is not None and isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Dot:
            self.__stream.advance()
            name = self.__stream.consume_identifier()
        elif isinstance(qualifier, ASTTy.InstanceType) and isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.LParen:
            assert isinstance(first_token, Tok.Identifier)
            name = AST.Identifier(span=first_token.span, name=first_token.name)
        else:
            self.__stream.reset(mark)
            qualifier = None
            name = self.__stream.consume_identifier()
        next_token = self.__stream.peek()
        if not isinstance(next_token, Tok.Punctuator) or next_token.kind != Tok.PunctuatorKind.LParen:
            if qualifier is not None:
                return AST.ConstructPattern(span=name.span, name=name, qualifier=qualifier, positional=None, named=None)
            return AST.NamePattern(span=name.span, name=name)
        self.__stream.advance()
        positional: list[AST.Pattern] = []
        named: list[AST.FieldPattern] | None = None
        rest = False
        while not self.__at_pattern_punct(Tok.PunctuatorKind.RParen):
            item = self.__stream.peek()
            following = self.__stream.peek_nth(1)
            if isinstance(item, Tok.Punctuator) and item.kind == Tok.PunctuatorKind.DotDot:
                if rest:
                    raise ParseError("Only one '..' is allowed in a field pattern", item.span)
                self.__stream.advance()
                rest = True
                if self.__at_pattern_punct(Tok.PunctuatorKind.Comma):
                    self.__stream.advance()
                if not self.__at_pattern_punct(Tok.PunctuatorKind.RParen):
                    raise ParseError("'..' must be the last field", item.span)
                break
            elif isinstance(item, (Tok.Identifier, Tok.Keyword)) and isinstance(following, Tok.Punctuator) and following.kind == Tok.PunctuatorKind.Equal:
                if positional:
                    raise ParseError("Cannot mix positional and named fields", item.span)
                if named is None:
                    named = []
                field = self.__stream.consume_identifier()
                self.__stream.advance()
                subpattern = self.__parse_pattern_or()
                named.append(AST.FieldPattern(span=field.span, name=field, pattern=subpattern))
            else:
                if named is not None or rest:
                    raise ParseError("Cannot mix positional and named fields", item.span)
                positional.append(self.__parse_pattern_or())
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Comma:
                self.__stream.advance()
            else:
                break
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
        return AST.ConstructPattern(span=name.span, name=name, qualifier=qualifier, positional=None if named is not None or rest else positional, named=named, rest=rest)

    def __at_pattern_punct(self, kind: Tok.PunctuatorKind) -> bool:
        token = self.__stream.peek()
        return isinstance(token, Tok.Punctuator) and token.kind == kind

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
