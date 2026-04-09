from dataclasses import dataclass, field

from compiler.config.constants import IntrinsicType
from compiler.config.defs import SymbolId, TypeId
from compiler.utils.errors import CompilerError


@dataclass
class Variable:
    symbol_id: SymbolId
    name: str
    type_id: TypeId
    lvalue: bool = False  # For user-defined variables, this is always True. For temporals, it can be False.

    def __repr__(self) -> str:
        return self.name


@dataclass
class IntegerLiteral:
    value: int
    suffix: str | None = None
    _type_id: TypeId | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"{self.value}{self.suffix or ''}"

    @property
    def type_id(self) -> TypeId:
        if self._type_id is not None:
            return self._type_id

        from compiler.utils.ty import TypeSpace

        # Determine type based on suffix
        if self.suffix is not None:
            return TypeSpace.intrinsic_type(IntrinsicType.from_str(self.suffix))

        # Default to Int type
        return TypeSpace.i32_id

    @type_id.setter
    def type_id(self, value: TypeId) -> None:
        if self.suffix is not None:
            raise CompilerError("Cannot set type ID for integer literal with suffix.")
        if self._type_id is None:
            self._type_id = value
        elif self._type_id != value:
            raise CompilerError("Type ID has already been set.")

    def get_determined_type_id(self) -> TypeId | None:
        """Get the determined type ID from _type_id if available, otherwise return None."""
        if self._type_id is not None:
            return self._type_id

        from compiler.utils.ty import TypeSpace

        # Determine type based on suffix
        if self.suffix is not None:
            return TypeSpace.intrinsic_type(IntrinsicType.from_str(self.suffix))

        return None


@dataclass
class FloatLiteral:
    value: float
    suffix: str | None = None
    _type_id: TypeId | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"{self.value}{self.suffix or ''}"

    @property
    def type_id(self) -> TypeId:
        if self._type_id is not None:
            return self._type_id

        from compiler.utils.ty import TypeSpace

        # Determine type based on suffix
        if self.suffix is not None:
            return TypeSpace.intrinsic_type(IntrinsicType.from_str(self.suffix))

        # Default to Float type
        return TypeSpace.f64_id

    @type_id.setter
    def type_id(self, value: TypeId) -> None:
        if self.suffix is not None:
            raise CompilerError("Cannot set type ID for float literal with suffix.")
        if self._type_id is None:
            self._type_id = value
        elif self._type_id != value:
            raise CompilerError("Type ID has already been set to a different value.")

    def get_determined_type_id(self) -> TypeId | None:
        """Get the determined type ID from _type_id if available, otherwise return None."""
        if self._type_id is not None:
            return self._type_id

        from compiler.utils.ty import TypeSpace

        # Determine type based on suffix
        if self.suffix is not None:
            return TypeSpace.intrinsic_type(IntrinsicType.from_str(self.suffix))

        return None


@dataclass
class StringLiteral:
    value: bytes

    def __repr__(self) -> str:
        return f'"{self.value.decode("utf-8")}"'

    @property
    def type_id(self) -> TypeId:
        from compiler.utils.ty import TypeSpace

        return TypeSpace.intrinsic_type(IntrinsicType.Str)

    def get_determined_type_id(self) -> TypeId | None:
        """Get the determined type ID. Since StringLiteral has no _type_id field, return type_id."""
        return self.type_id


@dataclass
class BooleanLiteral:
    value: bool

    def __repr__(self) -> str:
        return "true" if self.value else "false"

    @property
    def type_id(self) -> TypeId:
        from compiler.utils.ty import TypeSpace

        return TypeSpace.intrinsic_type(IntrinsicType.Bool)

    def get_determined_type_id(self) -> TypeId | None:
        """Get the determined type ID. Since BooleanLiteral has no _type_id field, return type_id."""
        return self.type_id


@dataclass
class CharLiteral:
    value: str

    def __repr__(self) -> str:
        return f"'{self.value}'"

    @property
    def type_id(self) -> TypeId:
        from compiler.utils.ty import TypeSpace

        return TypeSpace.intrinsic_type(IntrinsicType.Char)

    def get_determined_type_id(self) -> TypeId | None:
        """Get the determined type ID. Since CharLiteral has no _type_id field, return type_id."""
        return self.type_id


@dataclass
class ArrayLiteral:
    elements: list['LiteralValue']
    _type_id: TypeId | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"[{', '.join(repr(e) for e in self.elements)}]"

    @property
    def type_id(self) -> TypeId:
        if self._type_id is None:
            raise CompilerError("Array literal type ID has not been set.")
        return self._type_id

    @type_id.setter
    def type_id(self, value: TypeId) -> None:
        if self._type_id is None:
            self._type_id = value
        elif self._type_id != value:
            raise CompilerError("Array literal type ID has already been set to a different value.")

    def get_determined_type_id(self) -> TypeId | None:
        """Get the determined type ID from _type_id if available, otherwise return None."""
        if self._type_id is not None:
            return self._type_id
        return None


@dataclass
class TupleLiteral:
    elements: list['LiteralValue']
    _type_id: TypeId | None = field(default=None, repr=False)

    def __repr__(self) -> str:
        return f"({', '.join(repr(e) for e in self.elements)})"

    @property
    def type_id(self) -> TypeId:
        if self._type_id is None:
            raise CompilerError("Tuple literal type ID has not been set.")
        return self._type_id

    @type_id.setter
    def type_id(self, value: TypeId) -> None:
        if self._type_id is None:
            self._type_id = value
        elif self._type_id != value:
            raise CompilerError("Tuple literal type ID has already been set to a different value.")

    def get_determined_type_id(self) -> TypeId | None:
        """Get the determined type ID from _type_id if available, otherwise return None."""
        return self._type_id


LiteralValue = IntegerLiteral | FloatLiteral | StringLiteral | BooleanLiteral | CharLiteral | ArrayLiteral | TupleLiteral


TypedValue = Variable | LiteralValue
