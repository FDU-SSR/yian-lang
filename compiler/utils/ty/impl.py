from dataclasses import dataclass, field

from compiler.config.constants import NO_TYPE
from compiler.config.defs import StmtId, TypeId
from compiler.utils.errors import CompilerError


@dataclass(eq=False)
class Impl:
    stmt_id: StmtId
    target: TypeId = NO_TYPE
    trait: TypeId | None = None
    generics: list[TypeId] = field(default_factory=list)
    methods: dict[str, TypeId] = field(default_factory=dict)

    def for_name(self, name: str) -> TypeId:
        """
        Find a method by name within this impl block.

        Args:
            name (str): The name of the method to find.
            type_space (TypeSpace): The type space for resolving type IDs.
        """
        if name not in self.methods:
            raise CompilerError(f"Method {name} not found in impl block at {self.stmt_id}")
        return self.methods[name]

    def exists_method(self, name: str) -> bool:
        """
        Check if a method with the given name exists in this impl block.

        Args:
            name (str): The name of the method to check.
            type_space (TypeSpace): The type space for resolving type IDs.
        """
        return name in self.methods

    def add_method(self, name: str, method_id: TypeId):
        """
        Add a method to this impl block.

        Args:
            method_id (TypeId): The type ID of the method to add.
        """
        self.methods[name] = method_id
