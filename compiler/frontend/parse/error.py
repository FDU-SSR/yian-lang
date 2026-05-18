from compiler.utils.IR.position import SrcSpan


class ParseError(ValueError):
    def __init__(self, message: str, span: SrcSpan):
        super().__init__(message)
        self.span = span
