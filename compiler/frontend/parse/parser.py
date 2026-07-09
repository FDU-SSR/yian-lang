from __future__ import annotations

from compiler.error import CompilerError
from compiler.frontend.lex.position import SrcSpan
from compiler.frontend.lex.token import (Keyword, KeywordKind, Punctuator,
                                         PunctuatorKind, Token)
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.parser_expr import ExprParser
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import (EMPTY_SET, SEMI_OR_COMMA,
                                            SEP_COMMA, SEP_DOT, TERM_RANGLE,
                                            TERM_RBRACE, TERM_RPAREN,
                                            TERM_SEMICOLON, TokenStream)
from compiler.utils.log import CompilerLog


def ch_parse(): return CompilerLog.get("parse")


class Parser:
    def __init__(self, tokens: list[Token]):
        self.__stream = TokenStream(tokens)

        self.__type_parser = TypeParser(self.__stream)
        self.__expr_parser = ExprParser(self.__stream, self.__type_parser)

    def parse(self) -> AST.Program:
        """
        Parses the token stream into an AST.
        """
        items: list[AST.ProgramItem] = []

        while not self.__stream.at_end():
            annots = self.__stream.consume_annots()
            attrs = self.__stream.consume_attrs()
            match self.__stream.peek():
                case Keyword(KeywordKind.Import, _):
                    if annots:
                        raise ParseError("Annotations are not allowed on import statements", annots[0].span)
                    if attrs:
                        raise ParseError("Attributes are not allowed on import statements", attrs[0].span)
                    items.extend(self.__parse_import())
                case Keyword(KeywordKind.From, _):
                    if annots:
                        raise ParseError("Annotations are not allowed on import statements", annots[0].span)
                    if attrs:
                        raise ParseError("Attributes are not allowed on import statements", attrs[0].span)
                    items.extend(self.__parse_import())
                case Keyword(KeywordKind.Typedef, _):
                    items.append(self.__parse_alias(annots=annots, attrs=attrs))
                case Keyword(KeywordKind.Impl, _):
                    if annots:
                        raise ParseError("Annotations are not allowed on impl blocks", annots[0].span)
                    if attrs:
                        raise ParseError("Attributes are not allowed on impl blocks", attrs[0].span)
                    items.append(self.__parse_impl(annots=annots))
                case Keyword(KeywordKind.Struct, _):
                    items.append(self.__parse_struct(annots=annots, attrs=attrs))
                case Keyword(KeywordKind.Enum, _):
                    items.append(self.__parse_enum(annots=annots, attrs=attrs))
                case Keyword(KeywordKind.Trait, _):
                    items.append(self.__parse_trait(annots=annots, attrs=attrs))
                case Keyword(KeywordKind.Fn, _):
                    items.append(self.__parse_func_def(annots=annots, attrs=attrs))
                case _:
                    raise ParseError(f"Expected a declaration keyword (fn, struct, enum, trait, impl, typedef, import, from) but got '{self.__stream.peek()}'", self.__stream.peek().span)
            if items:
                ch_parse().trace(lambda: f"parsed {type(items[-1]).__name__}")

        return AST.Program(
            span=SrcSpan.combine_all([item.span for item in items]),
            items=items
        )

    def __parse_import(self) -> list[AST.Import]:
        result: list[AST.Import] = []
        match self.__stream.next():
            case Keyword(KeywordKind.Import, span):
                # Parse `import xxx.yyy as zzz;`
                paths = self.__stream.consume_separated(self.__stream.consume_identifier, SEP_DOT, EMPTY_SET)
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
                if isinstance(token, Punctuator) and token.kind in SEMI_OR_COMMA:
                    self.__stream.advance()
                return result

            case Keyword(KeywordKind.From, span):
                # Parse `from xxx.yyy import zzz as zzz_alias, www as www_alias;`
                paths = self.__stream.consume_separated(self.__stream.consume_identifier, SEP_DOT, EMPTY_SET)

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

                targets_and_aliases = self.__stream.consume_separated(parse_target, SEP_COMMA, TERM_SEMICOLON)
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
            SEP_COMMA,
            TERM_RANGLE,
        )
        self.__stream.consume_punctuator(PunctuatorKind.RAngle)
        return params

    def __parse_alias(self, annots: list[AST.Annot], attrs: list[AST.Attr]) -> AST.Alias:
        self.__stream.consume_keyword(KeywordKind.Typedef)
        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()
        self.__stream.consume_punctuator(PunctuatorKind.Equal)
        target = self.__parse_type()
        self.__stream.consume_punctuator(PunctuatorKind.Semicolon)
        return AST.Alias(span=name.span, annots=annots, attrs=attrs, name=name, generics=generics, target=target)

    def __parse_impl(self, annots: list[AST.Annot]) -> AST.Impl:
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

        # optional if-clause: `if T: Clone, U: Debug`
        conditions: list[tuple[AST.Identifier, list[AST.ASTType]]] = []
        token = self.__stream.peek()
        if isinstance(token, Keyword) and token.kind == KeywordKind.If:
            self.__stream.consume_keyword(KeywordKind.If)
            conditions = self.__parse_impl_conditions()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        items = self.__stream.consume_until(self.__parse_method_def, TERM_RBRACE)
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.Impl(span=span, annots=annots, generics=generics, target=target, trait=trait, items=items, conditions=conditions)

    def __parse_impl_conditions(self) -> list[tuple[AST.Identifier, list[AST.ASTType]]]:
        conditions: list[tuple[AST.Identifier, list[AST.ASTType]]] = []
        while True:
            ident = self.__stream.consume_identifier()
            self.__stream.consume_punctuator(PunctuatorKind.Colon)
            traits: list[AST.ASTType] = [self.__parse_type()]
            token = self.__stream.peek()
            while isinstance(token, Punctuator) and token.kind == PunctuatorKind.Plus:
                self.__stream.consume_punctuator(PunctuatorKind.Plus)
                traits.append(self.__parse_type())
                token = self.__stream.peek()
            conditions.append((ident, traits))
            token = self.__stream.peek()
            if not (isinstance(token, Punctuator) and token.kind == PunctuatorKind.Comma):
                break
            self.__stream.consume_punctuator(PunctuatorKind.Comma)
        return conditions

    def __parse_struct(self, annots: list[AST.Annot], attrs: list[AST.Attr]) -> AST.StructDef:
        self.__stream.consume_keyword(KeywordKind.Struct)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        fields = self.__stream.consume_separated(self.__parse_field_info, SEP_COMMA, TERM_RBRACE)
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.StructDef(span=name.span, annots=annots, attrs=attrs, name=name, generics=generics, fields=fields)

    def __parse_enum(self, annots: list[AST.Annot], attrs: list[AST.Attr]) -> AST.EnumDef:
        self.__stream.consume_keyword(KeywordKind.Enum)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        variants = self.__stream.consume_separated(self.__parse_variant_info, SEP_COMMA, TERM_RBRACE)
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.EnumDef(span=name.span, annots=annots, attrs=attrs, name=name, generics=generics, variants=variants)

    def __parse_trait(self, annots: list[AST.Annot], attrs: list[AST.Attr]) -> AST.TraitDef:
        self.__stream.consume_keyword(KeywordKind.Trait)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        items = self.__stream.consume_until(self.__parse_trait_item, TERM_RBRACE)
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.TraitDef(span=name.span, annots=annots, attrs=attrs, name=name, generics=generics, items=items)

    def __parse_method_decl(self) -> AST.MethodDecl:
        attrs = self.__stream.consume_attrs()

        self.__stream.consume_keyword(KeywordKind.Fn)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()
        self.__stream.consume_punctuator(PunctuatorKind.LParen)
        params = self.__stream.consume_separated(self.__parse_var_info, SEP_COMMA, TERM_RPAREN)
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

        body = self.__expr_parser.parse_block()

        return AST.MethodDef(span=decl.span, decl=decl, body=body)

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
            fields = self.__stream.consume_separated(self.__parse_var_info, SEP_COMMA, TERM_RBRACE)
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

    def __parse_func_def(self, annots: list[AST.Annot], attrs: list[AST.Attr]) -> AST.FuncDef:
        self.__stream.consume_keyword(KeywordKind.Fn)

        name = self.__stream.consume_identifier()
        generics = self.__parse_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LParen)
        params = self.__stream.consume_separated(self.__parse_var_info, SEP_COMMA, TERM_RPAREN)
        self.__stream.consume_punctuator(PunctuatorKind.RParen)

        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Arrow:
            self.__stream.consume_punctuator(PunctuatorKind.Arrow)
            ret_type = self.__parse_type()
        else:
            ret_type = None

        body = self.__expr_parser.parse_block()

        return AST.FuncDef(span=name.span, annots=annots, attrs=attrs, name=name, generics=generics, params=params, ret_type=ret_type, body=body)

    def __parse_type(self) -> AST.ASTType:
        return self.__type_parser.parse_type()
