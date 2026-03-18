from dataclasses import dataclass

from compiler.config.defs import TypeId
from compiler.utils.ty import TypeSpace
from llvmlite import ir


@dataclass
class LLValue:
    """
    A class representing a low-level value in the compiler backend.
    """

    type_id: TypeId
    value: ir.Value

    @property
    def is_constant_true(self) -> bool:
        if not self.type_id == TypeSpace.bool_id:
            return False
        if not isinstance(self.value, ir.Constant):
            return False
        return self.value.constant == 1

    @property
    def is_constant_false(self) -> bool:
        if not self.type_id == TypeSpace.bool_id:
            return False
        if not isinstance(self.value, ir.Constant):
            return False
        return self.value.constant == 0


@dataclass
class LLFunction:
    """
    A class representing a low-level function in the compiler backend.
    """

    type_id: TypeId  # function/method/function pointer
    function: ir.Value
