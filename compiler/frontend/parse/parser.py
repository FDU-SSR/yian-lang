from __future__ import annotations

from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.lex.token import (Keyword, KeywordKind, Punctuator,
                                         PunctuatorKind, Token)
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.parser_expr import ExprParser
from compiler.frontend.parse.parser_stmt import StmtParser
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import TokenStream


class Parser:
    def __init__(self, tokens: list[Token]):
        self.__stream = TokenStream(tokens)

        self.__type_parser = TypeParser(self.__stream)
        self.__expr_parser = ExprParser(self.__stream, self.__type_parser)
        self.__stmt_parser = StmtParser(self.__stream, self.__expr_parser, self.__type_parser)

    def parse(self) -> AST.Program:
        """
        Parses the token stream into an AST.
        """
        items: list[AST.ProgramItem] = []

        while not self.__stream.at_end():
            attrs = self.__stream.consume_attrs()
            match self.__stream.peek():
                case Keyword(KeywordKind.Import, _):
                    if attrs:
                        raise ParseError("Attributes are not allowed on import statements", attrs[0].span)
                    items.extend(self.__parse_import())
                case Keyword(KeywordKind.From, _):
                    if attrs:
                        raise ParseError("Attributes are not allowed on import statements", attrs[0].span)
                    items.extend(self.__parse_import())
                case Keyword(KeywordKind.Typedef, _):
                    items.append(self.__parse_alias(attrs=attrs))
                case Keyword(KeywordKind.Impl, _):
                    if attrs:
                        raise ParseError("Attributes are not allowed on impl blocks", attrs[0].span)
                    items.append(self.__parse_impl())
                case Keyword(KeywordKind.Struct, _):
                    items.append(self.__parse_struct(attrs=attrs))
                case Keyword(KeywordKind.Enum, _):
                    items.append(self.__parse_enum(attrs=attrs))
                case Keyword(KeywordKind.Trait, _):
                    items.append(self.__parse_trait(attrs=attrs))
                case Keyword(KeywordKind.Fn, _):
                    items.append(self.__parse_func_def(attrs=attrs))
                case _:
                    raise ParseError(f"Expected a declaration keyword (fn, struct, enum, trait, impl, typedef, import, from) but got '{self.__stream.peek()}'", self.__stream.peek().span)

        return AST.Program(
            span=SrcSpan.combine_all([item.span for item in items]),
            items=items
        )

    def __parse_import(self) -> list[AST.Import]:
        result: list[AST.Import] = []
        match self.__stream.next():
            case Keyword(KeywordKind.Import, span):
                # Parse `import xxx.yyy as zzz;`
                paths = self.__stream.consume_separated(self.__stream.consume_identifier, {PunctuatorKind.Dot}, set())
                target = paths.pop()
                match self.__stream.peek():
                    case Keyword(KeywordKind.As, _):
                        self.__stream.advance()
                        alias = self.__stream.consume_identifier()
                        result = [AST.Import(span=span, paths=paths, target=target, alias=alias)]
                    case _:
                        result = [AST.Import(span=span, paths=paths, target=target, alias=None)]
                # consume any trailing semicolons/commas between imports
                token = self.__stream.peek()
                if isinstance(token, Punctuator) and token.kind in {PunctuatorKind.Semicolon, PunctuatorKind.Comma}:
                    self.__stream.advance()
                return result

            case Keyword(KeywordKind.From, span):
                # Parse `from xxx.yyy import zzz as zzz_alias, www as www_alias;`
                paths = self.__stream.consume_separated(self.__stream.consume_identifier, {PunctuatorKind.Dot}, set())

                self.__stream.consume_keyword(KeywordKind.Import)

                def parse_target() -> tuple[AST.Identifier, AST.Identifier | None]:
                    target = self.__stream.consume_identifier()
                    match self.__stream.peek():
                        case Keyword(KeywordKind.As, _):
                            self.__stream.advance()
                            alias = self.__stream.consume_identifier()
                            return target, alias
                        case _:
                            return target, None

                targets_and_aliases = self.__stream.consume_separated(parse_target, {PunctuatorKind.Comma}, {PunctuatorKind.Semicolon})
                targets, aliases = zip(*targets_and_aliases)

                # each target corresponds to a separate import stmt
                result = [AST.Import(span=span, paths=paths, target=target, alias=alias) for target, alias in zip(targets, aliases)]
                # consume trailing semicolon
                token = self.__stream.peek()
                if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Semicolon:
                    self.__stream.advance()
                return result

            case _:
                raise CompilerError("Unreachable code")

    def __parse_generics(self) -> list[AST.GenericParam]:
        """Parse generic parameters enclosed in angle brackets.

        Handles both type generic parameters (<T>) and const generic parameters
        (<const N: u64>). Returns a unified list of GenericParam.
        """
        params: list[AST.GenericParam] = []
        token = self.__stream.peek()
        if not (isinstance(token, Punctuator) and token.kind == PunctuatorKind.LAngle):
            return params

        self.__stream.consume_punctuator(PunctuatorKind.LAngle)

        def parse_param() -> AST.GenericParam:
            token = self.__stream.peek()
            # const generic: <const N: u64>
            if isinstance(token, Keyword) and token.kind == KeywordKind.Const:
                const_span = self.__stream.consume_keyword(KeywordKind.Const).span
                name = self.__stream.consume_identifier()
                self.__stream.consume_punctuator(PunctuatorKind.Colon)
                value_type = self.__type_parser.parse_type()
                return AST.ConstGenericParam(
                    span=const_span + value_type.span,
                    name=name,
                    value_type=value_type,
                )
            # type generic: <T>
            name = self.__stream.consume_identifier()
            return AST.TypeGenericParam(span=name.span, name=name)

        params = self.__stream.consume_separated(
            parse_param,
            {PunctuatorKind.Comma},
            {PunctuatorKind.RAngle},
        )
        self.__stream.consume_punctuator(PunctuatorKind.RAngle)
        return params

    def __parse_alias(self, attrs: list[AST.Attr]) -> AST.Alias:
        self.__stream.consume_keyword(KeywordKind.Typedef)
        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()
        self.__stream.consume_punctuator(PunctuatorKind.Equal)
        target = self.__parse_type()
        return AST.Alias(span=name.span, attrs=attrs, name=name, generics=generics, target=target)

    def __parse_impl(self) -> AST.Impl:
        span = self.__stream.consume_keyword(KeywordKind.Impl).span
        generics = self.__parse_generics()

        ty = self.__parse_type()

        token = self.__stream.peek()
        if isinstance(token, Keyword) and token.kind == KeywordKind.For:
            trait = ty

            self.__stream.consume_keyword(KeywordKind.For)

            target = self.__parse_type()
        else:
            trait = None
            target = ty

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        items = self.__stream.consume_until(self.__parse_method_def, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.Impl(span=span, generics=generics, target=target, trait=trait, items=items)

    def __parse_struct(self, attrs: list[AST.Attr]) -> AST.StructDef:
        self.__stream.consume_keyword(KeywordKind.Struct)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        fields = self.__stream.consume_separated(self.__parse_field_info, {PunctuatorKind.Comma}, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.StructDef(span=name.span, attrs=attrs, name=name, generics=generics, fields=fields)

    def __parse_enum(self, attrs: list[AST.Attr]) -> AST.EnumDef:
        self.__stream.consume_keyword(KeywordKind.Enum)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        variants = self.__stream.consume_separated(self.__parse_variant_info, {PunctuatorKind.Comma}, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.EnumDef(span=name.span, attrs=attrs, name=name, generics=generics, variants=variants)

    def __parse_trait(self, attrs: list[AST.Attr]) -> AST.TraitDef:
        self.__stream.consume_keyword(KeywordKind.Trait)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        items = self.__stream.consume_until(self.__parse_trait_item, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.TraitDef(span=name.span, attrs=attrs, name=name, generics=generics, items=items)

    def __parse_method_decl(self) -> AST.MethodDecl:
        attrs = self.__stream.consume_attrs()

        self.__stream.consume_keyword(KeywordKind.Fn)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()
        self.__stream.consume_punctuator(PunctuatorKind.LParen)
        params = self.__stream.consume_separated(self.__parse_var_info, {PunctuatorKind.Comma}, {PunctuatorKind.RParen})
        self.__stream.consume_punctuator(PunctuatorKind.RParen)

        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Arrow:
            self.__stream.consume_punctuator(PunctuatorKind.Arrow)
            ret_type = self.__parse_type()
        else:
            ret_type = None

        return AST.MethodDecl(span=name.span, attrs=attrs, name=name, generics=generics, params=params, ret_type=ret_type)

    def __parse_method_def(self, decl: AST.MethodDecl | None = None) -> AST.MethodDef:
        if decl is None:
            decl = self.__parse_method_decl()

        block_span = self.__stream.consume_punctuator(PunctuatorKind.LBrace).span
        body = self.__stream.consume_until(self.__parse_stmt, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        block = AST.Block(span=block_span, stmts=body)

        return AST.MethodDef(span=decl.span, decl=decl, body=block)

    def __parse_field_info(self) -> AST.FieldInfo:
        attrs = self.__stream.consume_attrs()

        name = self.__stream.consume_identifier()

        self.__stream.consume_punctuator(PunctuatorKind.Colon)

        field_type = self.__parse_type()

        return AST.FieldInfo(span=name.span, attrs=attrs, name=name, field_type=field_type)

    def __parse_variant_info(self) -> AST.VariantInfo:
        name = self.__stream.consume_identifier()

        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.LBrace:
            self.__stream.consume_punctuator(PunctuatorKind.LBrace)
            fields = self.__stream.consume_separated(self.__parse_var_info, {PunctuatorKind.Comma}, {PunctuatorKind.RBrace})
            self.__stream.consume_punctuator(PunctuatorKind.RBrace)
        else:
            fields: list[AST.VarInfo] = []

        return AST.VariantInfo(span=name.span, name=name, fields=fields)

    def __parse_var_info(self) -> AST.VarInfo:
        name = self.__stream.consume_identifier()

        self.__stream.consume_punctuator(PunctuatorKind.Colon)

        var_type = self.__parse_type()

        return AST.VarInfo(span=name.span, name=name, var_type=var_type)

    def __parse_trait_item(self) -> AST.TraitItem:
        decl = self.__parse_method_decl()

        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.LBrace:
            return self.__parse_method_def(decl=decl)
        # method declaration without body ends with semicolon
        self.__stream.consume_punctuator(PunctuatorKind.Semicolon)
        return decl

    def __parse_func_def(self, attrs: list[AST.Attr]) -> AST.FuncDef:
        self.__stream.consume_keyword(KeywordKind.Fn)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LParen)
        params = self.__stream.consume_separated(self.__parse_var_info, {PunctuatorKind.Comma}, {PunctuatorKind.RParen})
        self.__stream.consume_punctuator(PunctuatorKind.RParen)

        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Arrow:
            self.__stream.consume_punctuator(PunctuatorKind.Arrow)
            ret_type = self.__parse_type()
        else:
            ret_type = None

        block_span = self.__stream.consume_punctuator(PunctuatorKind.LBrace).span
        body = self.__stream.consume_until(self.__parse_stmt, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        block = AST.Block(span=block_span, stmts=body)

        return AST.FuncDef(span=name.span, attrs=attrs, name=name, generics=generics, params=params, ret_type=ret_type, body=block)

    def __parse_stmt(self) -> AST.Stmt:
        return self.__stmt_parser.parse_stmt()

    def __parse_type(self) -> AST.ASTType:
        return self.__type_parser.parse_type()
