from compiler.frontend.lex.token import Keyword, KeywordKind, Punctuator, PunctuatorKind, Token
from compiler.utils.IR.position import SrcSpan
from compiler.frontend.parse import ast as AST


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

    def consume(self, expected_token: Token) -> Token:
        token = self.peek()
        if token is None:
            raise ValueError(f"Expected '{expected_token}' but got end of token stream")
        if token != expected_token:
            raise ValueError(f"Expected '{expected_token}' but got '{token}'")
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

    def __concat_space(self) -> None:
        """Concatenates consecutive space tokens into a single token with the combined span."""
        concatenated_tokens = []
        current_space_token = None

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
            self.__stream.skip_spaces()
            match self.__stream.peek():
                case Keyword(KeywordKind.Import, _):
                    items.append(self.__parse_import())
                case Keyword(KeywordKind.From, _):
                    items.append(self.__parse_import())
                case Keyword(KeywordKind.Typedef, _):
                    items.append(self.__parse_alias())
                case Keyword(KeywordKind.Impl, _):
                    items.append(self.__parse_impl())
                case Keyword(KeywordKind.Pub, _):
                    # consume 'pub' and check the next token to determine item type
                    self.__stream.consume(Keyword.from_kind(KeywordKind.Pub))
                    self.__stream.skip_spaces()
                    match self.__stream.peek():
                        case Keyword(KeywordKind.Struct, _):
                            items.append(self.__parse_struct(is_pub=True))
                        case Keyword(KeywordKind.Enum, _):
                            items.append(self.__parse_enum(is_pub=True))
                        case Keyword(KeywordKind.Trait, _):
                            items.append(self.__parse_trait(is_pub=True))
                        case _:
                            items.append(self.__parse_func_or_var(is_pub=True))
                case Keyword(KeywordKind.Struct, _):
                    items.append(self.__parse_struct(is_pub=False))
                case Keyword(KeywordKind.Enum, _):
                    items.append(self.__parse_enum(is_pub=False))
                case Keyword(KeywordKind.Trait, _):
                    items.append(self.__parse_trait(is_pub=False))
                case _:
                    items.append(self.__parse_func_or_var(is_pub=False))

        return AST.Program(
            span=SrcSpan.combine_all([item.span for item in items]),
            items=items
        )
