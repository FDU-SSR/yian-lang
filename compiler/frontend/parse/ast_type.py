class ASTType:
    """
    Simple type representation used in the AST.
    """


class IntType(ASTType):
    def __init__(self, signed: bool, width: int):
        self.signed = signed
        self.width = width  # in bytes

    def __repr__(self) -> str:
        sign_str = "i" if self.signed else "u"
        return f"{sign_str}{self.width * 8}"


class FloatType(ASTType):
    def __init__(self, width: int):
        self.width = width  # in bytes

    def __repr__(self) -> str:
        return f"f{self.width * 8}"


class BoolType(ASTType):
    def __repr__(self) -> str:
        return "bool"


class StrType(ASTType):
    def __repr__(self) -> str:
        return "str"


class CharType(ASTType):
    def __repr__(self) -> str:
        return "char"


class VoidType(ASTType):
    def __repr__(self) -> str:
        return "void"


class NamedType(ASTType):
    def __init__(self, name: str):
        self.name = name

    def __repr__(self) -> str:
        return self.name


class ArrayType(ASTType):
    def __init__(self, element_type: ASTType, size: int):
        self.element_type = element_type
        self.size = size

    def __repr__(self) -> str:
        return f"{self.element_type}[{self.size}]"


class TupleType(ASTType):
    def __init__(self, element_types: list[ASTType]):
        self.element_types = element_types

    def __repr__(self) -> str:
        return f"({', '.join(repr(t) for t in self.element_types)})"


class PointerType(ASTType):
    def __init__(self, pointee_type: ASTType):
        self.pointee_type = pointee_type

    def __repr__(self) -> str:
        return f"*{self.pointee_type}"


class SliceType(ASTType):
    def __init__(self, element_type: ASTType):
        self.element_type = element_type

    def __repr__(self) -> str:
        return f"{self.element_type}[]"


class InstantiatedType(ASTType):
    def __init__(self, name: str, generic_args: list[ASTType]):
        self.name = name
        self.generic_args = generic_args

    def __repr__(self) -> str:
        if len(self.generic_args) == 0:
            return self.name
        else:
            return f"{self.name}<{', '.join(repr(arg) for arg in self.generic_args)}>"


class FunctionType(ASTType):
    def __init__(self, param_types: list[ASTType], return_type: ASTType):
        self.param_types = param_types
        self.return_type = return_type

    def __repr__(self) -> str:
        param_str = ", ".join(repr(t) for t in self.param_types)
        return f"fn({param_str}) -> {self.return_type}"
