"""
This module contains all possible statements' type definitions.

Statements defined here are basically a direct mapping from the data structure provided by `lian`.

NOTEs:

1. The validation work is done by `from_raw` method and `__init__` method, but their division is not very clear yet. We may need to refine it in the future.
2. Some validation logic may be missing, keep an eye on it.
"""


import ast
from abc import abstractmethod

from compiler import utils
from compiler.config.constants import YianAttribute
from compiler.config.defs import StmtId
from compiler.utils.errors import CompilerError

from .meta import SrcPosition, StmtMetadata
from .operator import Operator


class GIRStmt:
    """
    Base class for all statements.

    Attributes:
        stmt_metadata (StmtMetadata): Metadata for the statement.
        pos (SrcPosition): Source code position of the statement.
    """

    def __init__(self, metadata: StmtMetadata, pos: SrcPosition):
        self.metadata = metadata
        self.pos = pos

    @abstractmethod
    def __repr__(self) -> str:
        pass

    @classmethod
    @abstractmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "GIRStmt":
        """
        Creates a Stmt object from raw statement data. The working of this method is as follows:

        1. Conversion: Convert the raw statement data into the appropriate Stmt subclass based on the operation type.
        2. Validation: Validate the fields of the statement to ensure they conform to expected formats and constraints.
        """
        Mapper: dict[str, type[GIRStmt]] = {
            "import_stmt": ImportStmt,
            "type_alias_decl": TypeAliasDeclStmt,
            "struct_decl": StructDeclStmt,
            "enum_decl": EnumDeclStmt,
            "trait_decl": TraitDeclStmt,
            "implement_decl": ImplementDeclStmt,
            "method_header": MethodHeaderStmt,

            "variable_decl": VariableDeclStmt,

            "if_stmt": IfStmt,
            "for_stmt": ForStmt,
            "forin_stmt": ForInStmt,
            "loop_stmt": LoopStmt,
            "switch_stmt": SwitchStmt,
            "block": BlockStmt,
            "block_start": BlockStmt,

            "return_stmt": ReturnStmt,
            "call_stmt": CallStmt,
            "assign_stmt": AssignStmt,
            "break_stmt": BreakStmt,
            "continue_stmt": ContinueStmt,
            "assert_stmt": AssertStmt,
            "del_stmt": DeleteStmt,

            "new_object": NewObjectStmt,
            "new_array": NewArrayStmt,

            "parameter_decl": ParameterDeclStmt,
            "case_stmt": CaseStmt,
            "default_stmt": DefaultStmt,
            "variant_decl": VariantDeclStmt,
        }
        if stmt.operation in Mapper:
            stmt_class = Mapper[stmt.operation]
        elif stmt.operation == "method_decl":
            if metadata.parent_stmt_id == 0:  # method in global scope -> function
                stmt_class = FunctionDeclStmt
            else:
                stmt_class = MethodDeclStmt
        else:
            raise CompilerError(f"Unknown statement operation: {stmt.operation}")
        return stmt_class.from_raw(stmt, metadata, pos, block_info)

    @property
    def stmt_id(self) -> StmtId:
        """
        A quick access to the statement ID.
        """
        return self.metadata.stmt_id

    def expect_import(self) -> "ImportStmt":
        if not isinstance(self, ImportStmt):
            raise CompilerError(f"Expected ImportStmt, got {self}")
        return self

    def expect_variable_decl(self) -> "VariableDeclStmt":
        if not isinstance(self, VariableDeclStmt):
            raise CompilerError(f"Expected VariableDeclStmt, got {self}")
        return self

    def expect_assign(self) -> "AssignStmt":
        if not isinstance(self, AssignStmt):
            raise CompilerError(f"Expected AssignStmt, got {self}")
        return self

    def expect_return(self) -> "ReturnStmt":
        if not isinstance(self, ReturnStmt):
            raise CompilerError(f"Expected ReturnStmt, got {self}")
        return self

    def expect_struct_decl(self) -> "StructDeclStmt":
        if not isinstance(self, StructDeclStmt):
            raise CompilerError(f"Expected StructDeclStmt, got {self}")
        return self

    def expect_implement_decl(self) -> "ImplementDeclStmt":
        if not isinstance(self, ImplementDeclStmt):
            raise CompilerError(f"Expected ImplementDeclStmt, got {self}")
        return self

    def expect_parameter_decl(self) -> "ParameterDeclStmt":
        if not isinstance(self, ParameterDeclStmt):
            raise CompilerError(f"Expected ParameterDeclStmt, got {self}")
        return self

    def expect_call(self) -> "CallStmt":
        if not isinstance(self, CallStmt):
            raise CompilerError(f"Expected CallStmt, got {self}")
        return self

    def expect_if(self) -> "IfStmt":
        if not isinstance(self, IfStmt):
            raise CompilerError(f"Expected IfStmt, got {self}")
        return self

    def expect_for(self) -> "ForStmt":
        if not isinstance(self, ForStmt):
            raise CompilerError(f"Expected ForStmt, got {self}")
        return self

    def expect_forin(self) -> "ForInStmt":
        if not isinstance(self, ForInStmt):
            raise CompilerError(f"Expected ForInStmt, got {self}")
        return self

    def expect_switch(self) -> "SwitchStmt":
        if not isinstance(self, SwitchStmt):
            raise CompilerError(f"Expected SwitchStmt, got {self}")
        return self

    def expect_block(self) -> "BlockStmt":
        if not isinstance(self, BlockStmt):
            raise CompilerError(f"Expected BlockStmt, got {self}")
        return self

    def expect_break(self) -> "BreakStmt":
        if not isinstance(self, BreakStmt):
            raise CompilerError(f"Expected BreakStmt, got {self}")
        return self

    def expect_continue(self) -> "ContinueStmt":
        if not isinstance(self, ContinueStmt):
            raise CompilerError(f"Expected ContinueStmt, got {self}")
        return self

    def expect_assert(self) -> "AssertStmt":
        if not isinstance(self, AssertStmt):
            raise CompilerError(f"Expected AssertStmt, got {self}")
        return self

    def expect_delete(self) -> "DeleteStmt":
        if not isinstance(self, DeleteStmt):
            raise CompilerError(f"Expected DeleteStmt, got {self}")
        return self

    def expect_new_object(self) -> "NewObjectStmt":
        if not isinstance(self, NewObjectStmt):
            raise CompilerError(f"Expected NewObjectStmt, got {self}")
        return self

    def expect_new_array(self) -> "NewArrayStmt":
        if not isinstance(self, NewArrayStmt):
            raise CompilerError(f"Expected NewArrayStmt, got {self}")
        return self

    def expect_trait_decl(self) -> "TraitDeclStmt":
        if not isinstance(self, TraitDeclStmt):
            raise CompilerError(f"Expected TraitDeclStmt, got {self}")
        return self

    def expect_enum_decl(self) -> "EnumDeclStmt":
        if not isinstance(self, EnumDeclStmt):
            raise CompilerError(f"Expected EnumDeclStmt, got {self}")
        return self

    def expect_type_alias_decl(self) -> "TypeAliasDeclStmt":
        if not isinstance(self, TypeAliasDeclStmt):
            raise CompilerError(f"Expected TypeAliasDeclStmt, got {self}")
        return self

    def expect_method_header(self) -> "MethodHeaderStmt":
        if not isinstance(self, MethodHeaderStmt):
            raise CompilerError(f"Expected MethodHeaderStmt, got {self}")
        return self

    def expect_case(self) -> "CaseStmt":
        if not isinstance(self, CaseStmt):
            raise CompilerError(f"Expected CaseStmt, got {self}")
        return self

    def expect_default(self) -> "DefaultStmt":
        if not isinstance(self, DefaultStmt):
            raise CompilerError(f"Expected DefaultStmt, got {self}")
        return self

    def expect_variant_decl(self) -> "VariantDeclStmt":
        if not isinstance(self, VariantDeclStmt):
            raise CompilerError(f"Expected VariantDeclStmt, got {self}")
        return self

    def expect_function_decl(self) -> "FunctionDeclStmt":
        if not isinstance(self, FunctionDeclStmt):
            raise CompilerError(f"Expected FunctionDeclStmt, got {self}")
        return self

    def expect_method_decl(self) -> "MethodDeclStmt":
        if not isinstance(self, MethodDeclStmt):
            raise CompilerError(f"Expected MethodDeclStmt, got {self}")
        return self


class ImportStmt(GIRStmt):
    """
    Represents an import statement.

    Attributes:
        paths (list[str]): List of module path components.
        target (str): Target symbol being imported.
        alias (str | None): Optional alias for the imported symbol.
    """

    def __init__(self, stmt_metadata: StmtMetadata, pos: SrcPosition, paths: list[str], target: str, alias: str | None):
        super().__init__(stmt_metadata, pos)
        self.paths = paths
        self.target = target
        self.alias = alias

    def __repr__(self) -> str:
        return f"{self.stmt_id}:ImportStmt(module_path={'.'.join(self.paths + [self.target])}, alias={self.alias})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "ImportStmt":
        module_components = stmt.name.split(".")

        paths = module_components[:-1]
        target = module_components[-1]
        alias = stmt.alias if utils.is_available(stmt.alias) else None

        return cls(metadata, pos, paths, target, alias)


class FunctionDeclStmt(GIRStmt):
    """
    Represents a function declaration statement.

    Attributes:
        attributes (list[YianAttribute]): Function attributes.
        name (str): Name of the function.
        return_type (str): Return type of the function.
        type_parameters (list[str]): Type parameters of the function.
        parameters (StmtId | None): Block id of parameter block.
        body (StmtId): Block id of function body block.
    """
    LEGAL_ATTRS = {
        YianAttribute.Inline,
        YianAttribute.Public,
        YianAttribute.Intrinsic,
    }

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        attributes: list[YianAttribute],
        name: str,
        return_type: str,
        type_parameters: list[str],
        parameters: StmtId | None,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.attributes = attributes
        self.name = name
        self.return_type = return_type
        self.type_parameters = type_parameters
        self.parameters = parameters
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:FunctionDeclStmt(name={self.name}, body={self.body})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "FunctionDeclStmt":
        attribute_names = stmt.attrs.replace(" ", "").split(",") if utils.is_available(stmt.attrs) else []
        attributes = [YianAttribute.from_str(attr) for attr in attribute_names]
        name = stmt.name
        return_type = stmt.data_type if utils.is_available(stmt.data_type) else "void"
        type_parameters = stmt.type_parameters.replace(" ", "").split(",") if utils.is_available(stmt.type_parameters) else []
        parameters = stmt.parameters if utils.is_available(stmt.parameters) else None
        body = int(stmt.body)

        if any(attr not in cls.LEGAL_ATTRS for attr in attributes):
            raise CompilerError(f"Illegal attribute in function declaration: {[attr.name for attr in attributes]}")

        return cls(metadata, pos, attributes, name, return_type, type_parameters, parameters, body)


class VariableDeclStmt(GIRStmt):
    """
    Represents a variable declaration statement.

    Attributes:
        attributes (list[YianAttribute]): Variable attributes.
        name (str): Name of the variable.
        data_type (str): Data type of the variable.
    """
    LEGAL_ATTRS = {
        YianAttribute.Public,
    }

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        attributes: list[YianAttribute],
        name: str,
        data_type: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.attributes = attributes
        self.name = name
        self.data_type = data_type

    def __repr__(self) -> str:
        return f"{self.stmt_id}:VariableDeclStmt(name={self.name}, data_type={self.data_type})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "VariableDeclStmt":
        attributes_names = stmt.attrs.replace(" ", "").split(",") if utils.is_available(stmt.attrs) else []
        attributes = [YianAttribute.from_str(attr) for attr in attributes_names]
        name = stmt.name
        data_type = stmt.data_type

        if any(attr not in cls.LEGAL_ATTRS for attr in attributes):
            raise CompilerError(f"Illegal attribute in variable declaration: {[attr.name for attr in attributes]}")

        return cls(metadata, pos, attributes, name, data_type)


class AssignStmt(GIRStmt):
    """
    Represents an assignment statement.

    Attributes:
        target (str): Target variable name.
        operator (Operator | None): Operator used in the assignment.
        lhs (str): Left-hand side expression.
        rhs (str | None): Right-hand side expression.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        target: str,
        operator: Operator | None,
        lhs: str,
        rhs: str | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.target = target
        self.operator = operator
        self.lhs = lhs
        self.rhs = rhs

    def __repr__(self) -> str:
        return f"{self.stmt_id}:AssignStmt(target={self.target}, operator={self.operator}, lhs={self.lhs}, rhs={self.rhs})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "AssignStmt":
        target = stmt.target
        operator = Operator.from_str(stmt.operator) if utils.is_available(stmt.operator) else None
        lhs = stmt.operand
        rhs = stmt.operand2 if utils.is_available(stmt.operand2) else None
        return cls(metadata, pos, target, operator, lhs, rhs)


class ReturnStmt(GIRStmt):
    """
    Represents a return statement.

    Attributes:
        value (str | None): Return value expression.
    """

    def __init__(self, stmt_metadata: StmtMetadata, pos: SrcPosition, value: str | None):
        super().__init__(stmt_metadata, pos)
        self.value = value

    def __repr__(self) -> str:
        return f"{self.stmt_id}:ReturnStmt(value={self.value})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "ReturnStmt":
        value = stmt.name if utils.is_available(stmt.name) else None
        return cls(metadata, pos, value)


class StructDeclStmt(GIRStmt):
    """
    Represents a struct declaration statement.

    Attributes:
        attributes (list[YianAttribute]): Struct attributes.
        name (str): Name of the struct.
        type_parameters (list[str]): Type parameters of the struct.
        fields (StmtId | None): Field statements of the struct.
    """
    LEGAL_ATTRS = {
        YianAttribute.Public,
        YianAttribute.Dyn,
        YianAttribute.Intrinsic,
    }

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        attributes: list[YianAttribute],
        name: str,
        type_parameters: list[str],
        fields: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.attributes = attributes
        self.name = name
        self.type_parameters = type_parameters
        self.fields = fields

    def __repr__(self) -> str:
        return f"{self.stmt_id}:StructDeclStmt(name={self.name})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "StructDeclStmt":
        attributes_names = stmt.attrs.replace(" ", "").split(",") if utils.is_available(stmt.attrs) else []
        attributes = [YianAttribute.from_str(attr) for attr in attributes_names]
        name = stmt.name
        type_parameters = stmt.type_parameters.replace(" ", "").split(",") if utils.is_available(stmt.type_parameters) else []
        fields = int(stmt.fields) if utils.is_available(stmt.fields) else None

        if any(attr not in cls.LEGAL_ATTRS for attr in attributes):
            raise CompilerError(f"Illegal attribute in struct declaration: {[attr.name for attr in attributes]}")

        return cls(metadata, pos, attributes, name, type_parameters, fields)


class ImplementDeclStmt(GIRStmt):
    """
    Represents an implement declaration statement.

    Attributes:
        target_type (str): Target type being implemented.
        type_parameters (list[str]): Type parameters of the implementation.
        trait_type (str | None): Trait type being implemented, if any.
        methods (StmtId | None): Method statements of the implementation.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        type_parameters: list[str],
        target_type: str,
        trait_type: str | None,
        methods: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.target_type = target_type
        self.type_parameters = type_parameters
        self.trait_type = trait_type
        self.methods = methods

    def __repr__(self) -> str:
        return f"{self.stmt_id}:ImplementDeclStmt(target_type={self.target_type}, trait_type={self.trait_type})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "ImplementDeclStmt":
        target_type = stmt.struct_name
        type_parameters = stmt.type_parameters.replace(" ", "").split(",") if utils.is_available(stmt.type_parameters) else []
        trait_type = stmt.trait_name if utils.is_available(stmt.trait_name) else None
        methods = int(stmt.body) if utils.is_available(stmt.body) else None
        return cls(metadata, pos, type_parameters, target_type, trait_type, methods)


class MethodDeclStmt(GIRStmt):
    """
    Represents a method declaration statement.

    Attributes:
        attributes (list[YianAttribute]): Method attributes.
        name (str): Name of the method.
        return_type (str): Return type of the method.
        type_parameters (list[str]): Type parameters of the method.
        parameters (StmtId | None): Parameter statements of the method.
        body (StmtId): Body statements of the method.
    """
    LEGAL_ATTRS = {
        YianAttribute.Inline,
        YianAttribute.Public,
        YianAttribute.Static,
        YianAttribute.Intrinsic,
    }

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        attributes: list[YianAttribute],
        name: str,
        return_type: str,
        type_parameters: list[str],
        parameters: StmtId | None,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.attributes = attributes
        self.name = name
        self.return_type = return_type
        self.type_parameters = type_parameters
        self.parameters = parameters
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:MethodDeclStmt(name={self.name})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "MethodDeclStmt":
        attributes_names = stmt.attrs.replace(" ", "").split(",") if utils.is_available(stmt.attrs) else []
        attributes = [YianAttribute.from_str(attr) for attr in attributes_names]
        name = stmt.name
        return_type = stmt.data_type if utils.is_available(stmt.data_type) else "void"
        type_parameters = stmt.type_parameters.replace(" ", "").split(",") if utils.is_available(stmt.type_parameters) else []
        parameters = int(stmt.parameters) if utils.is_available(stmt.parameters) else None
        body = int(stmt.body)

        if any(attr not in cls.LEGAL_ATTRS for attr in attributes):
            raise CompilerError(f"Illegal attribute in method declaration: {[attr.name for attr in attributes]}")

        return cls(metadata, pos, attributes, name, return_type, type_parameters, parameters, body)


class ParameterDeclStmt(GIRStmt):
    """
    Represents a parameter declaration statement.

    Attributes:
        name (str): Name of the parameter.
        data_type (str): Data type of the parameter.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        name: str,
        data_type: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.name = name
        self.data_type = data_type

    def __repr__(self) -> str:
        return f"{self.stmt_id}:ParameterDeclStmt(name={self.name}, data_type={self.data_type})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "ParameterDeclStmt":
        name = stmt.name
        data_type = stmt.data_type
        return cls(metadata, pos, name, data_type)


class CallStmt(GIRStmt):
    """
    Represents a function or method call statement.

    Attributes:
        name (str): Name of the callee.
        type_arguments (list[str]): Type arguments for the call.
        target (str | None): Target object for the call, if any.
        positional_arguments (list[str]): Positional arguments for the call.
        named_arguments (dict[str, str]): Named arguments for the call.
        receiver (str | None): Receiver object for method calls, if any.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        name: str,
        type_arguments: list[str],
        target: str | None,
        positional_arguments: list[str],
        named_arguments: dict[str, str],
        receiver: str | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.name = name
        self.type_arguments = type_arguments
        self.target = target
        self.positional_arguments = positional_arguments
        self.named_arguments = named_arguments
        self.receiver = receiver

    def __repr__(self) -> str:
        return f"{self.stmt_id}:CallStmt(name={self.name}, type_arguments={self.type_arguments}, target={self.target}, positional_arguments={self.positional_arguments}, named_arguments={self.named_arguments}, receiver={self.receiver})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "CallStmt":
        name = stmt.name
        if utils.is_available(stmt.type_parameters):
            s = stmt.type_parameters
            type_arguments = []
            stack_depth = 0
            last_index = 0
            for index in range(len(s) + 1):
                ch = s[index] if index < len(s) else ','
                if ch == '<' or ch == '[' or ch == '(' or ch == '{':
                    stack_depth += 1
                elif ch == '>' or ch == ']' or ch == ')' or ch == '}':
                    stack_depth -= 1
                elif ch == ',' and stack_depth == 0:
                    type_arguments.append(s[last_index:index].strip())
                    last_index = index + 1
        else:
            type_arguments = []
        target = stmt.target if utils.is_available(stmt.target) else None
        positional_arguments = ast.literal_eval(stmt.positional_args) if utils.is_available(stmt.positional_args) else []
        named_arguments = ast.literal_eval(stmt.named_args) if utils.is_available(stmt.named_args) else {}
        receiver = stmt.receiver if utils.is_available(stmt.receiver) else None
        return cls(metadata, pos, name, type_arguments, target, positional_arguments, named_arguments, receiver)


class IfStmt(GIRStmt):
    """
    Represents an if statement.

    Attributes:
        condition (str): Condition expression of the if statement.
        then_body (StmtId): Body statements for the 'then' branch.
        else_body (StmtId | None): Body statements for the 'else' branch, if any.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        condition: str,
        then_body: StmtId,
        else_body: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.condition = condition
        self.then_body = then_body
        self.else_body = else_body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:IfStmt(condition={self.condition})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "IfStmt":
        condition = stmt.condition
        then_body = int(stmt.then_body)
        else_body = int(stmt.else_body) if utils.is_available(stmt.else_body) else None
        return cls(metadata, pos, condition, then_body, else_body)


class ForStmt(GIRStmt):
    """
    Represents a for loop statement.

    Attributes:
        init_body (StmtId | None): Initialization statements of the for loop.
        condition_prebody (StmtId | None): Pre-condition statements of the for loop.
        condition (str): Condition expression of the for loop.
        body (StmtId): Body statements of the for loop.
        update_body (StmtId | None): Update statements of the for loop.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        init_body: StmtId | None,
        condition_prebody: StmtId | None,
        condition: str,
        body: StmtId,
        update_body: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.init_body = init_body
        self.condition_prebody = condition_prebody
        self.condition = condition
        self.body = body
        self.update_body = update_body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:ForStmt(condition={self.condition})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "ForStmt":
        init_body = int(stmt.init_body) if utils.is_available(stmt.init_body) else None
        condition_prebody = int(stmt.condition_prebody) if utils.is_available(stmt.condition_prebody) else None
        condition = stmt.condition
        body = int(stmt.body)
        update_body = int(stmt.update_body) if utils.is_available(stmt.update_body) else None
        return cls(metadata, pos, init_body, condition_prebody, condition, body, update_body)


class ForInStmt(GIRStmt):
    """
    Represents a for-in loop statement.

    Attributes:
        iterator (str): Iterator variable name.
        iterable (str): Iterable expression.
        body (StmtId): Body statements of the for-in loop.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        iterator: str,
        iterable: str,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.iterator = iterator
        self.iterable = iterable
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:ForInStmt(iterator={self.iterator}, iterable={self.iterable})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "ForInStmt":
        iterator = stmt.value
        iterable = stmt.receiver
        body = int(stmt.body)
        return cls(metadata, pos, iterator, iterable, body)


class LoopStmt(GIRStmt):
    """
    Represents a loop statement.

    Attributes:
        body (StmtId): Body statements of the loop.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:LoopStmt"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "LoopStmt":
        body = int(stmt.body)
        return cls(metadata, pos, body)


class SwitchStmt(GIRStmt):
    """
    Represents a switch statement.

    Attributes:
        condition (str): Condition expression of the switch statement.
        body (StmtId): Body statements of the switch statement.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        condition: str,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.condition = condition
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:SwitchStmt(condition={self.condition})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "SwitchStmt":
        condition = stmt.condition
        body = int(stmt.body)
        return cls(metadata, pos, condition, body)


class BlockStmt(GIRStmt):
    """
    Represents a block statement.

    Attributes:
        body (list[StmtId]): Body statements of the block.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        body: list[StmtId],
    ):
        super().__init__(stmt_metadata, pos)
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:BlockStmt(body={self.body})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "BlockStmt":
        body = block_info[stmt.stmt_id]
        return cls(metadata, pos, body)


class BreakStmt(GIRStmt):
    """
    Represents a break statement.
    """

    def __init__(self, stmt_metadata: StmtMetadata, pos: SrcPosition):
        super().__init__(stmt_metadata, pos)

    def __repr__(self) -> str:
        return f"{self.stmt_id}:BreakStmt"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "BreakStmt":
        return cls(metadata, pos)


class ContinueStmt(GIRStmt):
    """
    Represents a continue statement.
    """

    def __init__(self, stmt_metadata: StmtMetadata, pos: SrcPosition):
        super().__init__(stmt_metadata, pos)

    def __repr__(self) -> str:
        return f"{self.stmt_id}:ContinueStmt"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "ContinueStmt":
        return cls(metadata, pos)


class AssertStmt(GIRStmt):
    """
    Represents an assert statement.

    Attributes:
        condition (str): Condition expression of the assert statement.
        message (str): Message for the assert statement.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        condition: str,
        message: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.condition = condition
        self.message = message

    def __repr__(self) -> str:
        return f"{self.stmt_id}:AssertStmt(condition={self.condition}, message={self.message})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "AssertStmt":
        condition = stmt.condition
        message = stmt.message if utils.is_available(stmt.message) else f"\"Assertion failed at {pos}\\n\""
        return cls(metadata, pos, condition, message)


class DeleteStmt(GIRStmt):
    """
    Represents a delete statement.

    Attributes:
        target (str): Target variable or resource to be deleted.
    """

    def __init__(self, stmt_metadata: StmtMetadata, pos: SrcPosition, target: str):
        super().__init__(stmt_metadata, pos)
        self.target = target

    def __repr__(self) -> str:
        return f"{self.stmt_id}:DeleteStmt(target={self.target})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "DeleteStmt":
        target = stmt.name
        return cls(metadata, pos, target)


class NewObjectStmt(GIRStmt):
    """
    Represents a new object creation statement.

    Attributes:
        data_type (str | None): Type ID of the object to be created.
        init_value (str | None): Initial value for the new object.
        target (str): Target variable to hold the new object.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        data_type: str | None,
        init_value: str | None,
        target: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.data_type = data_type
        self.init_value = init_value
        self.target = target

    def __repr__(self) -> str:
        if self.data_type is not None:
            return f"{self.stmt_id}:NewObjectStmt(data_type={self.data_type}, target={self.target})"
        else:
            return f"{self.stmt_id}:NewObjectStmt(target={self.target}, init_value={self.init_value})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "NewObjectStmt":
        data_type = stmt.data_type if utils.is_available(stmt.data_type) else None
        init_value = stmt.init_value if utils.is_available(stmt.init_value) else None
        target = stmt.target

        if data_type is not None and init_value is not None:
            raise CompilerError("NewObjectStmt cannot have both data_type and init_value set.")

        return cls(metadata, pos, data_type, init_value, target)


class NewArrayStmt(GIRStmt):
    """
    Represents a new array creation statement.

    Attributes:
        data_type (str): Element type of the array.
        length (str): Length expression of the array.
        target (str): Target variable to hold the new array.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        data_type: str,
        length: str,
        target: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.data_type = data_type
        self.length = length
        self.target = target

    def __repr__(self) -> str:
        return f"{self.stmt_id}:NewArrayStmt(data_type={self.data_type}, length={self.length}, target={self.target})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "NewArrayStmt":
        data_type = stmt.data_type
        length = stmt.length
        target = stmt.target
        return cls(metadata, pos, data_type, length, target)


class TraitDeclStmt(GIRStmt):
    """
    Represents a trait declaration statement.

    Attributes:
        attributes (list[YianAttribute]): Trait attributes.
        name (str): Name of the trait.
        type_parameters (list[str]): Type parameters of the trait.
        methods (StmtId | None): Method statements of the trait.
    """
    LEGAL_ATTRS = {
        YianAttribute.Public,
    }

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        attributes: list[YianAttribute],
        name: str,
        type_parameters: list[str],
        methods: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.attributes = attributes
        self.name = name
        self.type_parameters = type_parameters
        self.methods = methods

    def __repr__(self) -> str:
        return f"{self.stmt_id}:TraitDeclStmt(name={self.name})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "TraitDeclStmt":
        attribute_names = stmt.attrs.replace(" ", "").split(",") if utils.is_available(stmt.attrs) else []
        attributes = [YianAttribute.from_str(attr) for attr in attribute_names]
        name = stmt.name
        type_parameters = stmt.type_parameters.replace(" ", "").split(",") if utils.is_available(stmt.type_parameters) else []
        methods = int(stmt.body) if utils.is_available(stmt.body) else None

        if any(attr not in cls.LEGAL_ATTRS for attr in attributes):
            raise CompilerError(f"Illegal attribute in trait declaration: {[attr.name for attr in attributes]}")

        return cls(metadata, pos, attributes, name, type_parameters, methods)


class EnumDeclStmt(GIRStmt):
    """
    Represents an enum declaration statement.

    Attributes:
        attributes (list[YianAttribute]): Enum attributes.
        name (str): Name of the enum.
        type_parameters (list[str]): Type parameters of the enum.
        variants (StmtId): Variant statements of the enum.
    """
    LEGAL_ATTRS = {
        YianAttribute.Public,
        YianAttribute.Dyn,
        YianAttribute.Intrinsic,
    }

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        attributes: list[YianAttribute],
        name: str,
        type_parameters: list[str],
        variants: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.attributes = attributes
        self.name = name
        self.type_parameters = type_parameters
        self.variants = variants

    def __repr__(self) -> str:
        return f"{self.stmt_id}:EnumDeclStmt(name={self.name})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "EnumDeclStmt":
        attribute_names = stmt.attrs.replace(" ", "").split(",") if utils.is_available(stmt.attrs) else []
        attributes = [YianAttribute.from_str(attr) for attr in attribute_names]
        name = stmt.name
        type_parameters = stmt.type_parameters.replace(" ", "").split(",") if utils.is_available(stmt.type_parameters) else []
        variants = int(stmt.variants)

        if any(attr not in cls.LEGAL_ATTRS for attr in attributes):
            raise CompilerError(f"Illegal attribute in enum declaration: {[attr.name for attr in attributes]}")

        return cls(metadata, pos, attributes, name, type_parameters, variants)


class TypeAliasDeclStmt(GIRStmt):
    """
    Represents a type alias declaration statement.

    Attributes:
        attributes (list[YianAttribute]): Type alias attributes.
        name (str): Name of the type alias.
        aliased_type (str): The type being aliased.
    """
    LEGAL_ATTRS = {
        YianAttribute.Public,
    }

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        attributes: list[YianAttribute],
        name: str,
        aliased_type: str,
    ):
        super().__init__(stmt_metadata, pos)
        self.attributes = attributes
        self.name = name
        self.aliased_type = aliased_type

    def __repr__(self) -> str:
        return f"{self.stmt_id}:TypeAliasDeclStmt(name={self.name}, aliased_type={self.aliased_type})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "TypeAliasDeclStmt":
        attribute_names = stmt.attrs.replace(" ", "").split(",") if utils.is_available(stmt.attrs) else []
        attributes = [YianAttribute.from_str(attr) for attr in attribute_names]
        name = stmt.name
        aliased_type = stmt.data_type

        if any(attr not in cls.LEGAL_ATTRS for attr in attributes):
            raise CompilerError(f"Illegal attribute in type alias declaration: {[attr.name for attr in attributes]}")

        return cls(metadata, pos, attributes, name, aliased_type)


class MethodHeaderStmt(GIRStmt):
    """
    Represents a method header statement.

    Attributes:
        attributes (list[YianAttribute]): Method attributes.
        name (str): Name of the method.
        return_type (str): Return type of the method.
        type_parameters (list[str]): Type parameters of the method.
        parameters (StmtId | None): Parameter statements of the method.
    """
    LEGAL_ATTRS = {
        YianAttribute.Inline,
        YianAttribute.Public,
        YianAttribute.Static,
        YianAttribute.Intrinsic,
    }

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        attributes: list[YianAttribute],
        name: str,
        return_type: str,
        type_parameters: list[str],
        parameters: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.attributes = attributes
        self.name = name
        self.return_type = return_type
        self.type_parameters = type_parameters
        self.parameters = parameters

    def __repr__(self) -> str:
        return f"{self.stmt_id}:MethodHeaderStmt(name={self.name})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "MethodHeaderStmt":
        attribute_names = stmt.attrs.replace(" ", "").split(",") if utils.is_available(stmt.attrs) else []
        attributes = [YianAttribute.from_str(attr) for attr in attribute_names]
        name = stmt.name
        return_type = stmt.data_type if utils.is_available(stmt.data_type) else "void"
        type_parameters = stmt.type_parameters.replace(" ", "").split(",") if utils.is_available(stmt.type_parameters) else []
        parameters = int(stmt.parameters) if utils.is_available(stmt.parameters) else None

        if any(attr not in cls.LEGAL_ATTRS for attr in attributes):
            raise CompilerError(f"Illegal attribute in method header declaration: {[attr.name for attr in attributes]}")

        return cls(metadata, pos, attributes, name, return_type, type_parameters, parameters)


class CaseStmt(GIRStmt):
    """
    Represents a case statement in a switch.

    Attributes:
        values (list[str]): The value for the case.
        payload (str | None): The payload variable for the case, if any.
        body (StmtId): Body statements of the case.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        values: list[str],
        payload: str | None,
        body: StmtId,
    ):
        super().__init__(stmt_metadata, pos)
        self.values = values
        self.payload = payload
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:CaseStmt(values={self.values}, payload={self.payload})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "CaseStmt":
        values = [value for value in ast.literal_eval(stmt.condition)]
        payload = stmt.name if utils.is_available(stmt.name) else None
        body = int(stmt.body)
        return cls(metadata, pos, values, payload, body)


class DefaultStmt(GIRStmt):
    """
    Represents a default statement in a switch.

    Attributes:
        body (StmtId | None): Body statements of the default case.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        body: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.body = body

    def __repr__(self) -> str:
        return f"{self.stmt_id}:DefaultStmt"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "DefaultStmt":
        body = int(stmt.body) if utils.is_available(stmt.body) else None
        return cls(metadata, pos, body)


class VariantDeclStmt(GIRStmt):
    """
    Represents a variant declaration statement in an enum.

    Attributes:
        name (str): Name of the variant.
        payload (StmtId | None): Payload statements of the variant, if any.
    """

    def __init__(
        self,
        stmt_metadata: StmtMetadata,
        pos: SrcPosition,
        name: str,
        payload: StmtId | None,
    ):
        super().__init__(stmt_metadata, pos)
        self.name = name
        self.payload = payload

    def __repr__(self) -> str:
        return f"VariantDeclStmt(name={self.name}, payload={self.payload is not None})"

    @classmethod
    def from_raw(cls, stmt, metadata: StmtMetadata, pos: SrcPosition, block_info: dict[StmtId, list[StmtId]]) -> "VariantDeclStmt":
        name = stmt.name
        payload = int(stmt.fields) if utils.is_available(stmt.fields) else None
        return VariantDeclStmt(metadata, pos, name, payload)
