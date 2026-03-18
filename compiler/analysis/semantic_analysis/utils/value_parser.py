from compiler.analysis.semantic_analysis.utils import constant_parse as ConstantParser
from compiler.analysis.semantic_analysis.utils.scope_manager import ScopeManager
from compiler.config.constants import SELF_SYMBOL_ID, VAR_FIRST_CHAR
from compiler.config.defs import StmtId
from compiler.unit_data import UnitData
from compiler.utils import IR
from compiler.utils.errors import CompilerError, NameResolutionError, SemanticError
from compiler.utils.IR import DefPoint
from compiler.utils.ty import TypeSpace


class ValueParser:
    def __init__(self, scope_manager: ScopeManager, type_space: TypeSpace):
        self.__scope_manager = scope_manager
        self.__space = type_space

    def parse_value(self, stmt_id: StmtId, name: str, unit_data: UnitData, def_point: DefPoint | None) -> IR.TypedValue:
        """
        Parse a typed value by name under the given statement context.

        1. If the name is a literal, parse it as a constant literal.
        2. If the name is "self", return the self variable.
        3. Otherwise, look up the symbol table for the variable symbol.
        """
        # if it's a literal, use `ConstantParser` to parse it
        if self.__is_literal(name):
            literal_value, _ = ConstantParser.parse_constant(name)
            return literal_value

        # if it's "self", fetch the self type and return the self variable
        if name == "self":
            return IR.Variable(SELF_SYMBOL_ID, name, self.__space.alloc_pointer(self.__scope_manager.Self_type))

        # otherwise, look up the symbol table
        symbol = unit_data.symbol_lookup(stmt_id, name)
        match symbol:
            case IR.VariableSymbol():
                if def_point is not None:
                    symbol = def_point.get_symbol(symbol.symbol_id)
                    assert isinstance(symbol, IR.VariableSymbol)
                if symbol.type_id is None:
                    raise SemanticError(f"Variable symbol {name} at the statement {stmt_id} has no type defined")
                return IR.Variable(symbol.symbol_id, symbol.name, symbol.type_id, symbol.lvalue)
            case IR.Function():
                return IR.Variable(symbol.symbol_id, symbol.name, symbol.type_id)
            case _:
                raise NameResolutionError(f"Symbol {name} at the statement {stmt_id} is not a variable symbol")

    def __is_literal(self, name: str) -> bool:
        if len(name) == 0:
            raise CompilerError("cannot tell if empty str is literal")
        if name == "true" or name == "false":
            return True
        if name.startswith("b'") and name.endswith("'"):
            return True
        if name.startswith('%'):
            return False
        if name[0] in VAR_FIRST_CHAR:
            return False
        return True
