from compiler.frontend.lex.token import Identifier, Keyword, KeywordKind, Punctuator, PunctuatorKind, Token
from compiler.utils.IR.position import SrcSpan
from compiler.frontend.parse import ast as AST
from compiler.utils.errors.yian_error import CompilerError


class ParseError(ValueError):
    def __init__(self, message: str, span: SrcSpan):
        super().__init__(message)
        self.span = span


class TokenStream:
    def __init__(self, tokens: list[Token]):
        self.__tokens = tokens
        self.__index = 0

        self.__concat_space()

    def at_end(self) -> bool:
        return self.__index >= len(self.__tokens)

    def peek(self) -> Token | None:
        if self.at_end():
            return None
        return self.__tokens[self.__index]

    def next(self) -> Token:
        if self.at_end():
            raise StopIteration("End of token stream reached")
        token = self.__tokens[self.__index]
        self.__index += 1
        return token

    def advance(self) -> None:
        if self.at_end():
            raise StopIteration("End of token stream reached")
        self.__index += 1

    def consume_keyword(self, expected_kind: KeywordKind) -> Keyword:
        token = self.peek()
        if token is None:
            raise ValueError(f"Expected keyword '{expected_kind}' but got end of token stream")
        if not isinstance(token, Keyword) or token.kind != expected_kind:
            raise ValueError(f"Expected keyword '{expected_kind}' but got '{token}'")
        self.advance()
        return token

    def consume_punctuator(self, expected_kind: PunctuatorKind) -> Punctuator:
        token = self.peek()
        if token is None:
            raise ValueError(f"Expected punctuator '{expected_kind}' but got end of token stream")
        if not isinstance(token, Punctuator) or token.kind != expected_kind:
            raise ValueError(f"Expected punctuator '{expected_kind}' but got '{token}'")
        self.advance()
        return token

    def skip_spaces(self) -> None:
        """Skips consecutive space tokens."""
        while not self.at_end():
            token = self.peek()
            if isinstance(token, Punctuator) and token.kind in {PunctuatorKind.Space, PunctuatorKind.Endl}:
                self.advance()
            else:
                break

    def consume_attrs(self) -> list[AST.Attr]:
        """Consumes attributes (e.g., #[attr]) and returns them as a list."""
        attrs: list[AST.Attr] = []
        while not self.at_end():
            self.skip_spaces()
            match self.peek():
                case Keyword(KeywordKind.Pub, span):
                    self.advance()
                    attrs.append(AST.Attr(span=span, kind=AST.AttrKind.Pub))
                case Keyword(KeywordKind.Static, span):
                    self.advance()
                    attrs.append(AST.Attr(span=span, kind=AST.AttrKind.Static))
                case _:
                    break
        return attrs

    def consume_generics(self) -> list[AST.Identifier]:
        """Consumes generic parameters enclosed in angle brackets and returns them as a list of identifiers."""
        generics: list[AST.Identifier] = []
        token = self.peek()
        if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Less:
            self.consume_punctuator(PunctuatorKind.Less)

            while True:
                self.skip_spaces()
                generics.append(self.expect_identifier())
                self.skip_spaces()

                token = self.peek()
                if isinstance(token, Punctuator) and token.kind == PunctuatorKind.Comma:
                    self.consume_punctuator(PunctuatorKind.Comma)
                else:
                    break

            self.consume_punctuator(PunctuatorKind.Greater)

        return generics

    def expect_identifier(self) -> AST.Identifier:
        """Consumes and returns the next token if it is an identifier/keyword, otherwise raises an error."""
        token = self.next()
        match token:
            case Keyword(kind, span):
                self.advance()
                return AST.Identifier(name=kind.value, span=span)
            case Identifier(name, span):
                self.advance()
                return AST.Identifier(name=name, span=span)
            case _:
                raise ValueError(f"Expected identifier or keyword but got '{token}'")

    def __concat_space(self) -> None:
        """Concatenates consecutive space tokens into a single token with the combined span."""
        concatenated_tokens: list[Token] = []
        current_space_token: Punctuator | None = None

        for token in self.__tokens:
            if not isinstance(token, Punctuator) or token.kind != PunctuatorKind.Space:
                if current_space_token is not None:
                    concatenated_tokens.append(current_space_token)
                    current_space_token = None
                concatenated_tokens.append(token)
            else:
                if current_space_token is None:
                    current_space_token = token
                else:
                    # Extend the span of the current space token to include the new one
                    current_space_token.span += token.span

        if current_space_token is not None:
            concatenated_tokens.append(current_space_token)

        self.__tokens = concatenated_tokens


class Parser:
    def __init__(self, tokens: list[Token]):
        self.__stream = TokenStream(tokens)

    def parse(self) -> AST.Program:
        """
        Parses the token stream into an AST.
        """
        items: list[AST.ProgramItem] = []

        while not self.__stream.at_end():
            attrs = self.__stream.consume_attrs()  # Consume any attributes before the item
            self.__stream.skip_spaces()  # Skip any spaces after attributes
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
                    if attrs:
                        raise ParseError("Attributes are not allowed on type aliases", attrs[0].span)
                    items.append(self.__parse_alias())
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
                    items.append(self.__parse_func_or_var(attrs=attrs))

        return AST.Program(
            span=SrcSpan.combine_all([item.span for item in items]),
            items=items
        )

    def __parse_import(self) -> list[AST.Import]:
        paths: list[AST.Identifier] = []

        match self.__stream.next():
            case Keyword(KeywordKind.Import, span):
                # Parse `import xxx.yyy as zzz`
                self.__stream.skip_spaces()

                # Parse the path
                while True:
                    paths.append(self.__stream.expect_identifier())
                    match self.__stream.peek():
                        case Punctuator(PunctuatorKind.Dot, _):
                            self.__stream.advance()
                        case _:
                            break

                # pop last as target
                target = paths.pop()

                self.__stream.skip_spaces()

                match self.__stream.peek():
                    case Keyword(KeywordKind.As, _):
                        self.__stream.advance()
                        self.__stream.skip_spaces()
                        alias = self.__stream.expect_identifier()
                        return [AST.Import(span=span, paths=paths, target=target, alias=alias)]
                    case _:
                        return [AST.Import(span=span, paths=paths, target=target, alias=None)]

            case Keyword(KeywordKind.From, span):
                # Parse `from xxx.yyy import zzz as zzz_alias, www as www_alias`
                self.__stream.skip_spaces()

                # Parse the path
                while True:
                    paths.append(self.__stream.expect_identifier())
                    match self.__stream.peek():
                        case Punctuator(PunctuatorKind.Dot, _):
                            self.__stream.advance()
                        case _:
                            break

                self.__stream.skip_spaces()
                self.__stream.consume_keyword(KeywordKind.Import)
                self.__stream.skip_spaces()

                targets: list[AST.Identifier] = []
                aliases: list[AST.Identifier | None] = []

                while True:
                    targets.append(self.__stream.expect_identifier())
                    self.__stream.skip_spaces()
                    match self.__stream.peek():
                        case Keyword(KeywordKind.As, _):
                            self.__stream.advance()
                            self.__stream.skip_spaces()
                            aliases.append(self.__stream.expect_identifier())
                        case _:
                            aliases.append(None)

                    self.__stream.skip_spaces()
                    match self.__stream.peek():
                        case Punctuator(PunctuatorKind.Comma, _):
                            self.__stream.advance()
                            self.__stream.skip_spaces()
                        case _:
                            break

                # each target corresponds to a separate import stmt
                return [AST.Import(span=span, paths=paths, target=target, alias=alias) for target, alias in zip(targets, aliases)]

            case _:
                raise CompilerError("Unreachable code")

    def __parse_alias(self) -> AST.Alias:
        self.__stream.consume_keyword(KeywordKind.Typedef)
        self.__stream.skip_spaces()
        name = self.__stream.expect_identifier()
        generics = self.__stream.consume_generics()
        self.__stream.skip_spaces()
        self.__stream.consume_punctuator(PunctuatorKind.Equal)
        self.__stream.skip_spaces()
        target = self.__parse_type()
        return AST.Alias(span=name.span, name=name, generics=generics, target=target)

    def __parse_impl(self) -> AST.Impl:
        span = self.__stream.consume_keyword(KeywordKind.Impl).span
        generics = self.__stream.consume_generics()

        self.__stream.skip_spaces()
        ty = self.__parse_type()
        self.__stream.skip_spaces()

        token = self.__stream.peek()
        if isinstance(token, Keyword) and token.kind == KeywordKind.For:
            trait = ty

            self.__stream.consume_keyword(KeywordKind.For)

            self.__stream.skip_spaces()
            target = self.__parse_type()
            self.__stream.skip_spaces()
        else:
            trait = None
            target = ty

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        items: list[AST.MethodDef] = []
        while True:
            self.__stream.skip_spaces()
            token = self.__stream.peek()
            if isinstance(token, Punctuator) and token.kind == PunctuatorKind.RBrace:
                break
            items.append(self.__parse_method_def())
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.Impl(span=span, generics=generics, target=target, trait=trait, items=items)

    def __parse_struct(self, attrs: list[AST.Attr]) -> AST.StructDef:
        self.__stream.consume_keyword(KeywordKind.Struct)

        self.__stream.skip_spaces()
        name = self.__stream.expect_identifier()
        generics = self.__stream.consume_generics()
        self.__stream.skip_spaces()

        self.__stream.consume_punctuator(PunctuatorKind.LBrace)
        fields: list[AST.FieldInfo] = []
        while True:
            self.__stream.skip_spaces()
            token = self.__stream.peek()
            if isinstance(token, Punctuator) and token.kind == PunctuatorKind.RBrace:
                break
            fields.append(self.__parse_field_info())
        self.__stream.consume_punctuator(PunctuatorKind.RBrace)

        return AST.StructDef(span=name.span, attrs=attrs, name=name, generics=generics, fields=fields)

    def __parse_enum(self, attrs: list[AST.Attr]) -> AST.EnumDef:
        raise NotImplementedError("Enum parsing not implemented yet")

    def __parse_trait(self, attrs: list[AST.Attr]) -> AST.TraitDef:
        raise NotImplementedError("Trait parsing not implemented yet")

    def __parse_func_or_var(self, attrs: list[AST.Attr]) -> AST.VarDecl | AST.FuncDef:
        raise NotImplementedError("Function and variable parsing not implemented yet")

    def __parse_method_def(self) -> AST.MethodDef:
        raise NotImplementedError("Method parsing not implemented yet")

    def __parse_field_info(self) -> AST.FieldInfo:
        raise NotImplementedError("Field parsing not implemented yet")

    def __parse_type(self) -> AST.ASTType:
        raise NotImplementedError("Type parsing not implemented yet")
