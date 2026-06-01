from __future__ import annotations

from compiler.frontend.lex import token as Tok
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.parser_expr import ExprParser
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import TokenStream


class StmtParser:
    def __init__(self, stream: TokenStream, expr_parser: ExprParser, type_parser: TypeParser):
        self.__stream = stream

        self.__expr_parser = expr_parser
        self.__type_parser = type_parser

    def parse_stmt(self) -> AST.Stmt:
        """Parses a statement from the token stream."""
        token = self.__stream.peek()
        match token:
            case Tok.Punctuator(kind=Tok.PunctuatorKind.LBrace):
                return self.__parse_block()
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
            case _:
                if self.__stream.var_decl_like():
                    return self.__parse_var_decl()
                return self.__parse_expr_stmt()

    def __parse_block(self) -> AST.Block:
        span = self.__stream.consume_punctuator(Tok.PunctuatorKind.LBrace).span
        stmts = self.__stream.consume_until(self.parse_stmt, {Tok.PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RBrace)
        return AST.Block(stmts=stmts, span=span)

    def __parse_return(self) -> AST.Return:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Return).span

        self.__stream.consume_spaces(endl_sensitive=True)
        token = self.__stream.peek()
        if isinstance(token, Tok.Punctuator) and token.kind in {Tok.PunctuatorKind.Endl, Tok.PunctuatorKind.EOF}:
            expr = None
        else:
            expr = self.__expr_parser.parse_expr()
        return AST.Return(span=span, expr=expr)

    def __parse_if(self) -> AST.If:
        span = self.__stream.consume_keyword(Tok.KeywordKind.If).span

        self.__stream.consume_spaces()
        condition = self.__expr_parser.parse_expr()

        self.__stream.consume_spaces()
        then_branch = self.__parse_block()

        elif_branches: list[tuple[AST.Expr, AST.Block]] = []
        while True:
            self.__stream.consume_spaces()
            next_token = self.__stream.peek()
            if isinstance(next_token, Tok.Keyword) and next_token.kind == Tok.KeywordKind.Elif:
                self.__stream.consume_keyword(Tok.KeywordKind.Elif)

                self.__stream.consume_spaces()
                elif_condition = self.__expr_parser.parse_expr()

                self.__stream.consume_spaces()
                elif_block = self.__parse_block()

                elif_branches.append((elif_condition, elif_block))
            else:
                break

        self.__stream.consume_spaces()
        else_branch: AST.Block | None = None
        next_token = self.__stream.peek()
        if isinstance(next_token, Tok.Keyword) and next_token.kind == Tok.KeywordKind.Else:
            self.__stream.consume_keyword(Tok.KeywordKind.Else)
            self.__stream.consume_spaces()
            else_branch = self.__parse_block()

        return AST.If(span=span, condition=condition, then_branch=then_branch, elif_branches=elif_branches, else_branch=else_branch)

    def __parse_for(self) -> AST.For:
        span = self.__stream.consume_keyword(Tok.KeywordKind.For).span

        self.__stream.consume_spaces()
        var_name = self.__stream.consume_identifier()

        self.__stream.consume_spaces()
        self.__stream.consume_keyword(Tok.KeywordKind.In)

        self.__stream.consume_spaces()
        iterable = self.__expr_parser.parse_expr()

        self.__stream.consume_spaces()
        body = self.__parse_block()

        return AST.For(span=span, var_name=var_name, iterable=iterable, body=body)

    def __parse_while(self) -> AST.While:
        span = self.__stream.consume_keyword(Tok.KeywordKind.While).span

        self.__stream.consume_spaces()
        condition = self.__expr_parser.parse_expr()

        self.__stream.consume_spaces()
        body = self.__parse_block()

        return AST.While(span=span, condition=condition, body=body)

    def __parse_loop(self) -> AST.Loop:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Loop).span

        self.__stream.consume_spaces()
        body = self.__parse_block()

        return AST.Loop(span=span, body=body)

    def __parse_match(self) -> AST.Match:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Match).span

        self.__stream.consume_spaces()
        expr = self.__expr_parser.parse_expr()

        self.__stream.consume_spaces()
        self.__stream.consume_punctuator(Tok.PunctuatorKind.LBrace)
        arms = self.__stream.consume_until(self.__parse_match_arm, {Tok.PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RBrace)

        return AST.Match(span=span, expr=expr, arms=arms)

    def __parse_break(self) -> AST.Break:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Break).span
        return AST.Break(span=span)

    def __parse_continue(self) -> AST.Continue:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Continue).span
        return AST.Continue(span=span)

    def __parse_assert(self) -> AST.Assert:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Assert).span

        self.__stream.consume_spaces()
        condition = self.__expr_parser.parse_expr()

        self.__stream.consume_spaces()
        next_token = self.__stream.peek()
        if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Colon:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Colon)

            self.__stream.consume_spaces()
            message = self.__expr_parser.parse_expr()
        else:
            message = None

        return AST.Assert(span=span, condition=condition, message=message)

    def __parse_delete(self) -> AST.Delete:
        span = self.__stream.consume_keyword(Tok.KeywordKind.Del).span

        self.__stream.consume_spaces()
        target = self.__expr_parser.parse_expr()

        return AST.Delete(span=span, target=target)

    def __parse_var_decl(self) -> AST.VarDecl:
        var_type = self.__type_parser.parse_type()

        self.__stream.consume_spaces()
        var_name = self.__stream.consume_identifier()

        self.__stream.consume_spaces()
        next_token = self.__stream.peek()
        if isinstance(next_token, Tok.Punctuator) and next_token.kind == Tok.PunctuatorKind.Equal:
            self.__stream.consume_punctuator(Tok.PunctuatorKind.Equal)

            self.__stream.consume_spaces()
            init_expr = self.__expr_parser.parse_expr()
        else:
            init_expr = None

        return AST.VarDecl(span=var_name.span, name=var_name, var_type=var_type, init_expr=init_expr)

    def __parse_expr_stmt(self) -> AST.Expr:
        return self.__expr_parser.parse_expr()

    def __parse_match_arm(self) -> tuple[AST.Pattern, AST.Block]:
        pattern = self.__parse_pattern()

        self.__stream.consume_spaces()
        block = self.__parse_block()

        return pattern, block

    def __parse_pattern(self) -> AST.Pattern:
        next_token = self.__stream.peek()
        match next_token:
            case Tok.IntLiteral():
                values = self.__stream.consume_separated(self.__parse_int_pattern_value, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.LBrace})
                return AST.IntPattern(span=values[0].span, values=values)
            case Tok.Punctuator(kind=Tok.PunctuatorKind.Minus):
                values = self.__stream.consume_separated(self.__parse_int_pattern_value, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.LBrace})
                return AST.IntPattern(span=values[0].span, values=values)
            case Tok.CharLiteral():
                values = self.__stream.consume_separated(self.__parse_char_pattern_value, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.LBrace})
                return AST.CharPattern(span=values[0].span, values=values)
            case Tok.StrLiteral():
                values = self.__stream.consume_separated(self.__parse_str_pattern_value, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.LBrace})
                return AST.StrPattern(span=values[0].span, values=values)
            case Tok.Identifier():
                next_next_token = self.__stream.peek_nth(1)
                if isinstance(next_next_token, Tok.Punctuator) and next_next_token.kind == Tok.PunctuatorKind.LParen:
                    return self.__parse_enum_payload_pattern()
                variants = self.__stream.consume_separated(self.__stream.consume_identifier, {Tok.PunctuatorKind.Pipe}, {Tok.PunctuatorKind.LBrace})
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
        fields = self.__stream.consume_separated(self.__stream.consume_identifier, {Tok.PunctuatorKind.Comma}, {Tok.PunctuatorKind.RParen})
        self.__stream.consume_punctuator(Tok.PunctuatorKind.RParen)

        return AST.PayloadPattern(span=variant_token.span, variant=variant_token, fields=fields)
