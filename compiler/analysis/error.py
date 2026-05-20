from compiler.utils.IR.position import SrcSpan


class AnalysisError(ValueError):
    def __init__(self, message: str, span: SrcSpan) -> None:
        super().__init__(message)
        self.span = span
