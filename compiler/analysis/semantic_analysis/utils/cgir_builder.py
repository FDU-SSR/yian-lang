from copy import deepcopy
from typing import Callable

from compiler.config.defs import StmtId, SymbolId, TypeId
from compiler.utils import IR, ty
from compiler.utils.IR import Operator, StmtMetadata
from compiler.utils.IR import cgir as cir
from compiler.utils.IR import gir as ir
from compiler.utils.ty import TypeSpace


class CGIRBuilder:
    def __init__(
        self,
        space: TypeSpace,
        max_stmt_id: int,
        symbol_gen: Callable[[StmtId, str], SymbolId],
        symbol_register: Callable[[IR.VariableSymbol], None],
        cgir_emit: Callable[[cir.CheckedGIR], None],
    ) -> None:
        self.__space = space
        self.__next_stmt_id = max_stmt_id + 1
        self.__next_var = 0
        self.__symbol_gen = symbol_gen
        self.__symbol_register = symbol_register
        self.__cgir_emit = cgir_emit

    def new_stmt_id(self) -> int:
        stmt_id = self.__next_stmt_id
        self.__next_stmt_id += 1
        return stmt_id

    def new_temp_var(self) -> str:
        var_name = f"%mm{self.__next_var}"
        self.__next_var += 1
        return var_name

    def __new_meta(self, stmt: ir.GIRStmt) -> StmtMetadata:
        meta = deepcopy(stmt.metadata)
        meta.stmt_id = self.new_stmt_id()
        return meta

    def build_binary_op(self, stmt: ir.GIRStmt, op: Operator, lhs: IR.TypedValue, rhs: IR.TypedValue, target_type: TypeId, emit: bool) -> cir.BinaryOpAssign:
        """
        Build a CheckedGIR binary operation assignment statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            op (Operator): The binary operator.
            lhs (IR.TypedValue): The left-hand side operand.
            rhs (IR.TypedValue): The right-hand side operand.
            target_type (TypeId): The type of the target variable.
        """
        # Create a new statement metadata with a unique stmt ID
        meta = self.__new_meta(stmt)

        # [] and . produce lvalues
        lvalue = op in {Operator.Index, Operator.Dot}

        # Create target variable symbol
        symbol_id = self.__symbol_gen(meta.stmt_id, self.new_temp_var())
        target_symbol = IR.VariableSymbol(symbol_id, self.new_temp_var(), target_type, lvalue)
        self.__symbol_register(target_symbol)

        # Create target variable typed value
        target_var = IR.Variable(symbol_id, target_symbol.name, target_type, lvalue)

        # Build the CheckedGIR statement
        checked_gir_stmt = cir.BinaryOpAssign(meta, stmt.pos, target_var, op, lhs, rhs)

        # emit the statement
        if emit:
            self.__cgir_emit(checked_gir_stmt)

        return checked_gir_stmt

    def build_unary_op(self, stmt: ir.GIRStmt, op: Operator, operand: IR.TypedValue, target_type: TypeId, emit: bool) -> cir.UnaryOpAssign:
        """
        Build a CheckedGIR unary operation assignment statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            op (Operator): The unary operator.
            operand (IR.TypedValue): The operand.
            target_type (TypeId): The type of the target variable.
        """
        # Create a new statement metadata with a unique stmt ID
        meta = self.__new_meta(stmt)

        # * produces lvalues
        lvalue = op == Operator.Star

        # Create target variable symbol
        symbol_id = self.__symbol_gen(meta.stmt_id, self.new_temp_var())
        target_symbol = IR.VariableSymbol(symbol_id, self.new_temp_var(), target_type, lvalue)
        self.__symbol_register(target_symbol)

        # Create target variable typed value
        target_var = IR.Variable(symbol_id, target_symbol.name, target_type, lvalue)

        # Build the CheckedGIR statement
        checked_gir_stmt = cir.UnaryOpAssign(meta, stmt.pos, target_var, op, operand)

        # emit the statement
        if emit:
            self.__cgir_emit(checked_gir_stmt)

        return checked_gir_stmt

    def build_field_access(self, stmt: ir.GIRStmt, receiver: IR.TypedValue, field: ty.StructField, emit: bool) -> cir.FieldAccess:
        """
        Build a CGIR FieldAccess statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            receiver (IR.TypedValue): The receiver of the field access.
            field (ty.StructField): The field being accessed.
            target_type (TypeId): The type of the target variable.
        """
        assert isinstance(receiver, IR.Variable)

        # Create a new statement metadata with a unique stmt ID
        meta = self.__new_meta(stmt)

        # Create target variable symbol
        symbol_id = self.__symbol_gen(meta.stmt_id, self.new_temp_var())
        target_symbol = IR.VariableSymbol(symbol_id, self.new_temp_var(), field.type_id, lvalue=False)
        self.__symbol_register(target_symbol)

        # Create target variable typed value
        target_var = IR.Variable(symbol_id, target_symbol.name, field.type_id, lvalue=False)

        # Build the FieldAccess statement
        checked_gir_stmt = cir.FieldAccess(meta, stmt.pos, target_var, receiver, field)

        # emit the statement
        if emit:
            self.__cgir_emit(checked_gir_stmt)

        return checked_gir_stmt

    def build_assign(self, stmt: ir.GIRStmt, value: IR.TypedValue, target_var: IR.Variable, emit: bool) -> cir.Assign:
        """
        Build a CGIR Assign statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            value (IR.TypedValue): The value to assign.
            target_var (IR.Variable): The target variable.
        """
        # Create a new statement metadata with a unique stmt ID
        meta = self.__new_meta(stmt)

        # Build the Assign statement
        checked_gir_stmt = cir.Assign(meta, stmt.pos, target_var, value)

        # emit the statement
        if emit:
            self.__cgir_emit(checked_gir_stmt)

        return checked_gir_stmt

    def build_method_call(self, stmt: ir.GIRStmt, receiver: IR.TypedValue, method: TypeId, args: list[IR.TypedValue], target_type: TypeId | None, emit: bool) -> cir.MethodCall:
        """
        Build a CGIR MethodCall statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            receiver (IR.TypedValue): The receiver of the method call.
            method (TypeId): The method type ID.
            args (list[IR.TypedValue]): The list of argument typed values.
            target_type (TypeId | None): The type of the target variable, or None if no target.
        """
        # Create a new statement metadata with a unique stmt ID
        meta = self.__new_meta(stmt)

        # Create target variable symbol if needed
        target_var = None
        if target_type is not None:
            symbol_id = self.__symbol_gen(meta.stmt_id, self.new_temp_var())
            target_symbol = IR.VariableSymbol(symbol_id, self.new_temp_var(), target_type, lvalue=False)
            self.__symbol_register(target_symbol)

            # Create target variable typed value
            target_var = IR.Variable(symbol_id, target_symbol.name, target_type, lvalue=False)

        # Build the MethodCall statement
        checked_gir_stmt = cir.MethodCall(meta, stmt.pos, method, self.__space[method].expect_method().method_def.name, target_var, receiver, args)

        # emit the statement
        if emit:
            self.__cgir_emit(checked_gir_stmt)

        return checked_gir_stmt

    def build_struct_construct(self, stmt: ir.GIRStmt, field_values: dict[str, IR.TypedValue], struct_type: TypeId, emit: bool) -> cir.StructConstruct:
        """
        Build a CGIR StructConstruct statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            field_values (dict[str, IR.TypedValue]): A mapping of field names to their corresponding typed values.
            struct_type (TypeId): The struct type ID.
        """
        # Create a new statement metadata with a unique stmt ID
        meta = self.__new_meta(stmt)

        # Create target variable symbol
        symbol_id = self.__symbol_gen(meta.stmt_id, self.new_temp_var())
        target_symbol = IR.VariableSymbol(symbol_id, self.new_temp_var(), struct_type, lvalue=False)
        self.__symbol_register(target_symbol)

        # Create target variable typed value
        target_var = IR.Variable(symbol_id, target_symbol.name, struct_type, lvalue=False)

        # Build the StructConstruct statement
        checked_gir_stmt = cir.StructConstruct(meta, stmt.pos, struct_type, self.__space.get_name(struct_type), target_var, field_values)

        # emit the statement
        if emit:
            self.__cgir_emit(checked_gir_stmt)

        return checked_gir_stmt

    def build_if(self, stmt: ir.GIRStmt, condition: IR.TypedValue, then_stmts: list[cir.CheckedGIR], else_stmts: list[cir.CheckedGIR] | None, emit: bool) -> cir.If:
        """
        Build a CGIR If statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            condition (IR.TypedValue): The condition for the If statement.
            then_stmts (list[cir.CheckedGIR]): The list of statement IDs for the 'then' branch.
            else_stmts (list[cir.CheckedGIR] | None): The list of statement IDs for the 'else' branch, or None if not present.
        """
        # Create a new statement
        meta = self.__new_meta(stmt)

        # Create then block
        then_block_id = self.__build_block(stmt, meta.stmt_id, then_stmts).stmt_id

        # Create else block if present
        else_block_id = None
        if else_stmts is not None:
            else_block_id = self.__build_block(stmt, meta.stmt_id, else_stmts).stmt_id

        # Build the If statement
        checked_gir_stmt = cir.If(meta, stmt.pos, condition, then_block_id, else_block_id)

        # emit the statement
        if emit:
            self.__cgir_emit(checked_gir_stmt)

        return checked_gir_stmt

    def build_panic(self, stmt: ir.GIRStmt, message: IR.TypedValue, emit: bool) -> cir.Panic:
        """
        Build a CGIR Panic statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            message (IR.TypedValue): The panic message.
        """
        meta = self.__new_meta(stmt)

        panic_stmt = cir.Panic(meta, stmt.pos, message)

        # emit the statement
        if emit:
            self.__cgir_emit(panic_stmt)

        return panic_stmt

    def build_loop(self, stmt: ir.GIRStmt, body_stmts: list[cir.CheckedGIR], emit: bool) -> cir.Loop:
        """
        Build a CGIR Loop statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            body_stmts (list[cir.CheckedGIR]): The list of statement IDs in the loop body.
        """
        meta = self.__new_meta(stmt)

        # Create loop body block
        body_block_id = self.__build_block(stmt, meta.stmt_id, body_stmts).stmt_id

        loop_stmt = cir.Loop(meta, stmt.pos, body_block_id)

        # emit the statement
        if emit:
            self.__cgir_emit(loop_stmt)

        return loop_stmt

    def build_match(self, stmt: ir.GIRStmt, match_value: IR.TypedValue, cases: list[cir.CheckedGIR], emit: bool) -> cir.Match:
        """
        Build a CGIR Match statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            match_value (IR.TypedValue): The value being matched.
            cases (list[cir.CheckedGIR]): The list of cases.
        """
        meta = self.__new_meta(stmt)

        # Create Match body block containing cases
        body_block = self.__build_block(stmt, meta.stmt_id, cases)

        # Build the Match statement
        has_default_case = any(isinstance(c, cir.DefaultCase) for c in cases)
        match_stmt = cir.Match(meta, stmt.pos, match_value, has_default_case, body_block.stmt_id)

        # emit the statement
        if emit:
            self.__cgir_emit(match_stmt)

        return match_stmt

    def build_break(self, stmt: ir.GIRStmt, emit: bool) -> cir.Break:
        meta = self.__new_meta(stmt)
        break_stmt = cir.Break(meta, stmt.pos)
        if emit:
            self.__cgir_emit(break_stmt)
        return break_stmt

    def build_enum_payload_case(self, stmt: ir.GIRStmt, case_value: str, payload: IR.Variable, body_stmts: list[cir.CheckedGIR], emit: bool) -> cir.EnumPayloadCase:
        meta = self.__new_meta(stmt)

        body_block = self.__build_block(stmt, meta.stmt_id, body_stmts)

        case_stmt = cir.EnumPayloadCase(meta, stmt.pos, case_value, payload, body_block.stmt_id)

        if emit:
            self.__cgir_emit(case_stmt)

        return case_stmt

    def build_enum_case(self, stmt: ir.GIRStmt, case_values: list[str], body_stmts: list[cir.CheckedGIR], emit: bool) -> cir.EnumCase:
        meta = self.__new_meta(stmt)

        body_block = self.__build_block(stmt, meta.stmt_id, body_stmts)

        case_stmt = cir.EnumCase(meta, stmt.pos, case_values, body_block.stmt_id)

        if emit:
            self.__cgir_emit(case_stmt)

        return case_stmt

    def __build_block(self, stmt: ir.GIRStmt, parent_stmt_id: StmtId, stmts: list[cir.CheckedGIR]) -> cir.Block:
        """
        Build a CGIR Block statement.

        Args:
            stmt (ir.GIRStmt): The original GIR statement to copy metadata from.
            parent_stmt_id (StmtId): The statement ID of the parent statement.
            stmts (list[cir.CheckedGIR]): The list of statement IDs in the block.
        """
        meta = self.__new_meta(stmt)
        meta.parent_stmt_id = parent_stmt_id

        block = cir.Block(meta, stmt.pos, [])

        self.__cgir_emit(block)

        for s in stmts:
            s.metadata.parent_stmt_id = meta.stmt_id
            self.__cgir_emit(s)

        return block
