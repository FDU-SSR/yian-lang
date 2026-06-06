from __future__ import annotations

from compiler.frontend.lex import token as Tok
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import TokenStream


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

            op_info = BinaryOperator.try_from_token(self.__stream)

            if op_info is None:
                break

            op, op_len = op_info
            if op.lbp < min_bp:
                break

            # consume the operator tokens
            for _ in range(op_len):
                self.__stream.advance()

            rhs = self.__parse_expr_bp(op.rbp)
            lhs = AST.Binary(span=lhs.span + rhs.span, op=op, left=lhs, right=rhs)

        return lhs

    def __parse_prefix(self) -> AST.Expr:
        """Parses the prefix part of an expression."""
        # try to parse a unary operator
        op_info = UnaryOperator.try_from_token(self.__stream)
        if op_info is not None:
            op, op_len = op_info
            # consume the operator tokens
            for _ in range(op_len):
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
            case Tok.Keyword(kind=Tok.KeywordKind.True_):
                self.__stream.consume_keyword(Tok.KeywordKind.True_)
                return AST.Literal(span=token.span, literal=Tok.BoolLiteral(raw="true", span=token.span, value=True))
            case Tok.Keyword(kind=Tok.KeywordKind.False_):
                self.__stream.consume_keyword(Tok.KeywordKind.False_)
                return AST.Literal(span=token.span, literal=Tok.BoolLiteral(raw="false", span=token.span, value=False))
            case Tok.Identifier() | Tok.Keyword():
                # Identifiers and (non-literal) keywords both represent names in
                # expression context (type names, function names, built-in calls).
                ident = self.__stream.consume_identifier()

                # handle generic arguments (e.g., Type<...>, bitcast<T*>, Array<T, 5>)
                next_token = self.__stream.peek()
                if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Less:
                    # Distinguish Type<T> from a < b (comparison)
                    if self.__looks_like_type_item():
                        self.__stream.consume_punctuator(Tok.PunctuatorKind.Less)
                        generics = self.__stream.consume_separated(self.__type_parser.parse_generic_arg, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.Greater})
                        self.__stream.consume_punctuator(Tok.PunctuatorKind.Greater)
                        return AST.TypeItem(span=ident.span, name=ident, generics=generics)

                return ident
            case Tok.IntLiteral() | Tok.FloatLiteral() | Tok.CharLiteral() | Tok.StrLiteral():
                self.__stream.advance()
                return AST.Literal(span=token.span, literal=token)
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

                    items += self.__stream.consume_separated(self.parse_expr, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
                    return AST.Tuple(span=token.span, elements=items)

                # parenthesized expression
                self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)
                return expr
            case Tok.Punctuator(kind=Tok.PunctuatorKind.LBracket):
                # array expression
                self.__stream.consume_punctuator(Tok.PunctuatorKind.LBracket)
                items = self.__stream.consume_separated(self.parse_expr, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RBracket})
                self.__stream.consume_punctuator(Tok.PunctuatorKind.RBracket)
                return AST.Array(span=token.span, elements=items)
            case _:
                raise ParseError(f"Unexpected token '{token}' while parsing expression", token.span)

    def __parse_postfix(self, expr: AST.Expr) -> AST.Expr:
        """Parses postfix expressions (e.g., function calls, field access)."""
        # absorb postfix as long as possible
        while True:
            token = self.__stream.peek()
            match token:
                case Tok.Punctuator(kind=Tok.PunctuatorKind.LParen):
                    # function call
                    self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                    args = self.__stream.consume_separated(self.parse_arg, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
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
                            args = self.__stream.consume_separated(self.parse_arg, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
                            self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

                            expr = AST.MethodCall(span=expr.span, receiver=expr, method_name=field_or_method_name, generics=[], args=args)
                        case Tok.Punctuator(kind=Tok.PunctuatorKind.Less):
                            # Could be a generic method call: expr.name<T>(args)
                            # Or a comparison: expr.name < T
                            # Look ahead: if the tokens after <...> are (, it's a method call
                            is_method_call = self.__looks_like_generic_method_call()
                            if is_method_call:
                                self.__stream.consume_punctuator(Tok.PunctuatorKind.Less)
                                generics = self.__stream.consume_separated(self.__type_parser.parse_type, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.Greater})
                                self.__stream.consume_punctuator(Tok.PunctuatorKind.Greater)

                                self.__stream.consume_punctuator(Tok.PunctuatorKind.LParen)
                                args = self.__stream.consume_separated(self.parse_arg, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
                                self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

                                expr = AST.MethodCall(span=expr.span, receiver=expr, method_name=field_or_method_name, generics=generics, args=args)
                            else:
                                # field access, < is a comparison operator
                                expr = AST.FieldAccess(span=expr.span, receiver=expr, field_name=field_or_method_name)
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

    def __looks_like_type_item(self) -> bool:
        """Look ahead to distinguish `Type<T>` from `a < b` (comparison).

        In expression context, TypeItem is used as receiver for method calls
        or field access: Type<T>.method() or Type<T>.Variant.
        Also handles Type<T>* (pointer) and Type<T>[] (slice/array).

        If `>` is followed by `.`, `(`, `*`, `[`, or `)`, `,`, `;`, `}`, `:`,
        `=`, `->`, EOF, or a keyword, it's a TypeItem. Otherwise, `<` is comparison.
        """
        saved = self.__stream.save()
        try:
            token = self.__stream.peek()
            if not (isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Less):
                return False
            self.__stream.advance()  # consume <
            depth = 1
            while depth > 0:
                tok = self.__stream.peek()
                if isinstance(tok, Tok.Punctuator):
                    if tok.kind == Tok.PunctuatorKind.Less:
                        depth += 1
                    elif tok.kind == Tok.PunctuatorKind.Greater:
                        depth -= 1
                    elif tok.kind in (Tok.PunctuatorKind.EOF, Tok.PunctuatorKind.LBrace,
                                       Tok.PunctuatorKind.RBrace, Tok.PunctuatorKind.Semicolon):
                        # Definitely not a TypeItem — these can't appear inside <>
                        return False
                self.__stream.advance()
            # After matching >, check what follows
            next_tok = self.__stream.peek()
            if isinstance(next_tok, Tok.Punctuator) and next_tok.kind in {
                Tok.PunctuatorKind.Dot, Tok.PunctuatorKind.LParen,
                Tok.PunctuatorKind.Star, Tok.PunctuatorKind.LBracket,
                Tok.PunctuatorKind.RParen, Tok.PunctuatorKind.Comma,
                Tok.PunctuatorKind.Semicolon, Tok.PunctuatorKind.RBrace,
                Tok.PunctuatorKind.Colon, Tok.PunctuatorKind.Equal,
                Tok.PunctuatorKind.Arrow, Tok.PunctuatorKind.FatArrow,
                Tok.PunctuatorKind.EqualEqual, Tok.PunctuatorKind.NotEqual,
                Tok.PunctuatorKind.AmpersandAmpersand, Tok.PunctuatorKind.PipePipe,
                Tok.PunctuatorKind.Plus, Tok.PunctuatorKind.Minus,
                Tok.PunctuatorKind.Greater, Tok.PunctuatorKind.RBracket,
                Tok.PunctuatorKind.EOF,
            }:
                return True
            if isinstance(next_tok, Tok.Keyword):
                return True
            # Followed by identifier or literal → comparison
            return False
        finally:
            self.__stream.restore(saved)

    def __looks_like_generic_method_call(self) -> bool:
        """Look ahead to distinguish `expr.name<T>(args)` from `expr.name < T`.

        After `<`, scan forward through balanced `<>` brackets. If the token
        right after the matching `>` is `(`, it's a generic method call.
        """
        saved = self.__stream.save()
        try:
            token = self.__stream.peek()
            if not (isinstance(token, Tok.Punctuator) and token.kind == Tok.PunctuatorKind.Less):
                return False
            self.__stream.advance()  # consume <
            depth = 1
            while depth > 0:
                tok = self.__stream.peek()
                if isinstance(tok, Tok.Punctuator):
                    if tok.kind == Tok.PunctuatorKind.Less:
                        depth += 1
                    elif tok.kind == Tok.PunctuatorKind.Greater:
                        depth -= 1
                    elif tok.kind in (Tok.PunctuatorKind.EOF, Tok.PunctuatorKind.LBrace,
                                       Tok.PunctuatorKind.RBrace, Tok.PunctuatorKind.Semicolon):
                        return False
                self.__stream.advance()
            # After matching >, check for (
            next_tok = self.__stream.peek()
            return isinstance(next_tok, Tok.Punctuator) and next_tok.kind == Tok.PunctuatorKind.LParen
        finally:
            self.__stream.restore(saved)

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
