from __future__ import annotations

from compiler.frontend.lex.token import Keyword, KeywordKind, Punctuator, PunctuatorKind, Token
from compiler.frontend.parse import ast as AST
from compiler.frontend.parse.error import ParseError
from compiler.frontend.parse.parser_expr import ExprParser
from compiler.frontend.parse.parser_stmt import StmtParser
from compiler.frontend.parse.parser_type import TypeParser
from compiler.frontend.parse.stream import TokenStream
from compiler.utils.errors.yian_error import CompilerError
from compiler.utils.IR.position import SrcSpan


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
            attrs = self.__stream.consume_attrs()  # Consume any attributes before the item
            self.__stream.consume_spaces()  # Skip any spaces after attributes
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
                case _:
                    items.append(self.__parse_func_def(attrs=attrs))
            self.__stream.consume_spaces()  # Skip any spaces between items

        return AST.Program(
            span=SrcSpan.combine_all([item.span for item in items]),
            items=items
        )

    def __parse_import(self) -> list[AST.Import]:
        match self.__stream.next():
            case Keyword(KeywordKind.Import, span):
                # Parse `import xxx.yyy as zzz`
                self.__stream.consume_spaces()

                # Parse the path
                paths = self.__stream.consume_separated(self.__stream.consume_identifier, {PunctuatorKind.Dot}, set())

                # pop last as target
                target = paths.pop()

                self.__stream.consume_spaces()

                match self.__stream.peek():
                    case Keyword(KeywordKind.As, _):
                        self.__stream.advance()
                        self.__stream.consume_spaces()
                        alias = self.__stream.consume_identifier()
                        return [AST.Import(span=span, paths=paths, target=target, alias=alias)]
                    case _:
                        return [AST.Import(span=span, paths=paths, target=target, alias=None)]

            case Keyword(KeywordKind.From, span):
                # Parse `from xxx.yyy import zzz as zzz_alias, www as www_alias`
                self.__stream.consume_spaces()

                # Parse the path
                paths = self.__stream.consume_separated(self.__stream.consume_identifier, {PunctuatorKind.Dot}, set())

                self.__stream.consume_spaces()
                self.__stream.consume_keyword(KeywordKind.Import)
                self.__stream.consume_spaces()

                def parse_target() -> tuple[AST.Identifier, AST.Identifier | None]:
                    target = self.__stream.consume_identifier()
                    self.__stream.consume_spaces()
                    match self.__stream.peek():
                        case Keyword(KeywordKind.As, _):
                            self.__stream.advance()
                            self.__stream.consume_spaces()
                            alias = self.__stream.consume_identifier()
                            return target, alias
                        case _:
                            return target, None

                targets_and_aliases = self.__stream.consume_separated(parse_target, {PunctuatorKind.Comma}, set())
                targets, aliases = zip(*targets_and_aliases)

                # each target corresponds to a separate import stmt
                return [AST.Import(span=span, paths=paths, target=target, alias=alias) for target, alias in zip(targets, aliases)]

            case _:
                raise CompilerError("Unreachable code")

    def __parse_alias(self, attrs: list[AST.Attr]) -> AST.Alias:
        self.__stream.consume_keyword(KeywordKind.Typedef)
        self.__stream.consume_spaces()
        name = self.__stream.consume_identifier()
        generics = self.__stream.consume_generics()
        self.__stream.consume_spaces()
        self.__stream.consume_punctuator(PunctuatorKind.Equal)
        self.__stream.consume_spaces()
        target = self.__parse_type()
        return AST.Alias(span=name.span, attrs=attrs, name=name, generics=generics, target=target)

    def __parse_impl(self) -> AST.Impl:
        span = self.__stream.consume_keyword(KeywordKind.Impl).span
        generics = self.__stream.consume_generics()

        self.__stream.consume_spaces()
        ty = self.__parse_type()
        self.__stream.consume_spaces()

        token = self.__stream.peek()
        if isinstance(token, Keyword) and token.kind == KeywordKind.For:
            trait = ty

            self.__stream.consume_keyword(KeywordKind.For)

            self.__stream.consume_spaces()
            target = self.__parse_type()
            self.__stream.consume_spaces()
        else:
            trait = None
            target = ty

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        items = self.__stream.consume_until(self.__parse_method_def, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.Impl(span=span, generics=generics, target=target, trait=trait, items=items)

    def __parse_struct(self, attrs: list[AST.Attr]) -> AST.StructDef:
        self.__stream.consume_keyword(KeywordKind.Struct)

        self.__stream.consume_spaces()
        name = self.__stream.consume_identifier()
        generics = self.__stream.consume_generics()
        self.__stream.consume_spaces()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        fields = self.__stream.consume_until(self.__parse_field_info, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.StructDef(span=name.span, attrs=attrs, name=name, generics=generics, fields=fields)

    def __parse_enum(self, attrs: list[AST.Attr]) -> AST.EnumDef:
        self.__stream.consume_keyword(KeywordKind.Enum)

        self.__stream.consume_spaces()
        name = self.__stream.consume_identifier()
        generics = self.__stream.consume_generics()
        self.__stream.consume_spaces()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        variants = self.__stream.consume_separated(self.__parse_variant_info, {PunctuatorKind.Comma, PunctuatorKind.Endl}, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.EnumDef(span=name.span, attrs=attrs, name=name, generics=generics, variants=variants)

    def __parse_trait(self, attrs: list[AST.Attr]) -> AST.TraitDef:
        self.__stream.consume_keyword(KeywordKind.Trait)

        self.__stream.consume_spaces()
        name = self.__stream.consume_identifier()
        generics = self.__stream.consume_generics()
        self.__stream.consume_spaces()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        items = self.__stream.consume_until(self.__parse_trait_item, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.TraitDef(span=name.span, attrs=attrs, name=name, generics=generics, items=items)

    def __parse_method_decl(self) -> AST.MethodDecl:
        attrs = self.__stream.consume_attrs()

        self.__stream.consume_spaces()
        name = self.__stream.consume_identifier()
        generics = self.__stream.consume_generics()
        self.__stream.consume_punctuator(PunctuatorKind.LParen)
        params = self.__stream.consume_separated(self.__parse_var_info, {PunctuatorKind.Comma}, {PunctuatorKind.RParen})
        self.__stream.consume_punctuator(PunctuatorKind.RParen)

        self.__stream.consume_spaces()
        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Arrow:
            self.__stream.consume_punctuator(PunctuatorKind.Arrow)
            self.__stream.consume_spaces()
            ret_type = self.__parse_type()
        else:
            ret_type = None

        return AST.MethodDecl(span=name.span, attrs=attrs, name=name, generics=generics, params=params, ret_type=ret_type)

    def __parse_method_def(self, decl: AST.MethodDecl | None = None) -> AST.MethodDef:
        if decl is None:
            decl = self.__parse_method_decl()

        self.__stream.consume_spaces()
        block_span = self.__stream.consume_punctuator(PunctuatorKind.LBrace).span
        body = self.__stream.consume_until(self.__parse_stmt, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        block = AST.Block(span=block_span, stmts=body)

        return AST.MethodDef(span=decl.span, decl=decl, body=block)

    def __parse_field_info(self) -> AST.FieldInfo:
        attrs = self.__stream.consume_attrs()

        self.__stream.consume_spaces()
        field_type = self.__parse_type()

        self.__stream.consume_spaces()
        name = self.__stream.consume_identifier()

        return AST.FieldInfo(span=name.span, attrs=attrs, field_type=field_type, name=name)

    def __parse_variant_info(self) -> AST.VariantInfo:
        name = self.__stream.consume_identifier()

        self.__stream.consume_spaces_inline()
        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.LBrace:
            self.__stream.consume_punctuator(PunctuatorKind.LBrace)
            fields = self.__stream.consume_separated(self.__parse_var_info, {PunctuatorKind.Comma, PunctuatorKind.Endl}, {PunctuatorKind.RBrace})
            self.__stream.consume_punctuator(PunctuatorKind.RBrace)
        else:
            fields: list[AST.VarInfo] = []

        return AST.VariantInfo(span=name.span, name=name, fields=fields)

    def __parse_var_info(self) -> AST.VarInfo:
        var_type = self.__parse_type()

        self.__stream.consume_spaces()
        name = self.__stream.consume_identifier()

        return AST.VarInfo(span=name.span, var_type=var_type, name=name)

    def __parse_trait_item(self) -> AST.TraitItem:
        decl = self.__parse_method_decl()

        self.__stream.consume_spaces()
        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.LBrace:
            return self.__parse_method_def(decl=decl)
        return decl

    def __parse_func_def(self, attrs: list[AST.Attr]) -> AST.FuncDef:
        name = self.__stream.consume_identifier()
        generics = self.__stream.consume_generics()

        self.__stream.consume_punctuator(PunctuatorKind.LParen)
        params = self.__stream.consume_separated(self.__parse_var_info, {PunctuatorKind.Comma}, {PunctuatorKind.RParen})
        self.__stream.consume_punctuator(PunctuatorKind.RParen)

        self.__stream.consume_spaces()
        token = self.__stream.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Arrow:
            self.__stream.consume_punctuator(PunctuatorKind.Arrow)
            self.__stream.consume_spaces()
            ret_type = self.__parse_type()
        else:
            ret_type = None

        self.__stream.consume_spaces()
        block_span = self.__stream.consume_punctuator(PunctuatorKind.LBrace).span
        body = self.__stream.consume_until(self.__parse_stmt, {PunctuatorKind.RBrace})
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        block = AST.Block(span=block_span, stmts=body)

        return AST.FuncDef(span=name.span, attrs=attrs, name=name, generics=generics, params=params, ret_type=ret_type, body=block)

    def __parse_stmt(self) -> AST.Stmt:
        return self.__stmt_parser.parse_stmt()

    def __parse_type(self) -> AST.ASTType:
        return self.__type_parser.parse_type()
