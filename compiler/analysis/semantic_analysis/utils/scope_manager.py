from dataclasses import dataclass

from compiler.config.defs import StmtId, TypeId
from compiler.utils import ty
from compiler.utils.errors import CompilerError, SemanticError
from compiler.utils.ty import MethodRegistry, TypeSpace


@dataclass
class GlobalScope:
    pass


@dataclass
class CustomTypeDeclScope:
    custom_type: TypeId


@dataclass
class TraitDeclScope:
    trait_type: TypeId


@dataclass
class ImplDeclScope:
    impl: StmtId


@dataclass
class MethodDeclScope:
    method_type: TypeId


@dataclass
class FunctionDeclScope:
    function_type: TypeId


DeclScope = CustomTypeDeclScope | ImplDeclScope | MethodDeclScope | TraitDeclScope | GlobalScope | FunctionDeclScope


class ScopeManager:
    """
    This class manages the scopes during type analysis.

    It maintains a stack of scopes and provides methods to push and pop scopes, as well as to query information about the current scope.
    """

    def __init__(self, type_space: TypeSpace, method_registry: MethodRegistry):
        self.__type_space = type_space
        self.__method_registry = method_registry

        self.__scope_stack: list[DeclScope] = [GlobalScope()]

    @property
    def __last_scope(self) -> DeclScope:
        return self.__scope_stack[-1]

    def push(self, scope: DeclScope):
        match scope:
            case GlobalScope():
                raise CompilerError("Cannot push global scope")
            case CustomTypeDeclScope():
                if isinstance(self.__last_scope, GlobalScope):
                    self.__scope_stack.append(scope)
                else:
                    raise SemanticError("Cannot declare custom type in non-global scope")
            case TraitDeclScope():
                if isinstance(self.__last_scope, GlobalScope):
                    self.__scope_stack.append(scope)
                else:
                    raise SemanticError("Cannot declare trait in non-global scope")
            case ImplDeclScope():
                if isinstance(self.__last_scope, GlobalScope):
                    self.__scope_stack.append(scope)
                else:
                    raise SemanticError("Cannot declare impl in non-global scope")
            case MethodDeclScope():
                if isinstance(self.__last_scope, (TraitDeclScope, ImplDeclScope)):
                    self.__scope_stack.append(scope)
                else:
                    raise SemanticError("Cannot declare method in non-trait/impl scope")
            case FunctionDeclScope():
                if isinstance(self.__last_scope, GlobalScope):
                    self.__scope_stack.append(scope)
                else:
                    raise SemanticError("Cannot declare function in non-global scope")

    def pop(self):
        if isinstance(self.__last_scope, GlobalScope):
            raise SemanticError("Cannot pop global scope")
        self.__scope_stack.pop()

    @property
    def generic_dict(self) -> dict[str, TypeId]:
        generic_dict: dict[str, TypeId] = {}
        match self.__last_scope:
            case CustomTypeDeclScope(custom_type=custom_type):
                custom_ty = self.__type_space[custom_type]
                if isinstance(custom_ty, ty.StructType):
                    for generic, generic_arg in zip(custom_ty.struct_def.generics, custom_ty.generic_args):
                        gen = self.__type_space[generic].expect_generic()
                        generic_dict[gen.name] = generic_arg
                elif isinstance(custom_ty, ty.EnumType):
                    for generic, generic_arg in zip(custom_ty.enum_def.generics, custom_ty.generic_args):
                        gen = self.__type_space[generic].expect_generic()
                        generic_dict[gen.name] = generic_arg
                else:
                    raise CompilerError("Unreachable")
            case TraitDeclScope(trait_type=trait_type):
                trait_ty = self.__type_space[trait_type].expect_trait()
                for generic, generic_arg in zip(trait_ty.trait_def.generics, trait_ty.generic_args):
                    gen = self.__type_space[generic].expect_generic()
                    generic_dict[gen.name] = generic_arg
            case ImplDeclScope(impl=impl):
                impl = self.__method_registry[impl]
                for generic in impl.generics:
                    gen = self.__type_space[generic].expect_generic()
                    generic_dict[gen.name] = generic
            case MethodDeclScope(method_type=method_type):
                method_ty = self.__type_space[method_type].expect_method()
                for generic, generic_arg in zip(method_ty.method_def.generics, method_ty.generic_args):
                    gen = self.__type_space[generic].expect_generic()
                    generic_dict[gen.name] = generic_arg
            case FunctionDeclScope(function_type=function_type):
                function_ty = self.__type_space[function_type].expect_function()
                for generic, generic_arg in zip(function_ty.function_def.generics, function_ty.generic_args):
                    gen = self.__type_space[generic].expect_generic()
                    generic_dict[gen.name] = generic_arg
            case GlobalScope():
                pass
        return generic_dict

    @property
    def Self_type(self) -> TypeId:
        for scope in reversed(self.__scope_stack):
            if isinstance(scope, MethodDeclScope):
                method_ty = self.__type_space[scope.method_type].expect_method()
                return method_ty.receiver_type(self.__type_space.instantiate)
            if isinstance(scope, TraitDeclScope):
                return scope.trait_type
            # if isinstance(scope, ImplDeclScope):
            #     return self.__method_registry[scope.impl].target
        raise SemanticError("Not in trait/impl scope")

    @property
    def trait_type(self) -> TypeId:
        for scope in reversed(self.__scope_stack):
            if isinstance(scope, TraitDeclScope):
                return scope.trait_type
        raise SemanticError("Not in trait scope")

    @property
    def procedure_type(self) -> TypeId:
        """
        Get the type ID of the current procedure (function or method) in scope.
        """
        if isinstance(self.__last_scope, FunctionDeclScope):
            return self.__last_scope.function_type
        if isinstance(self.__last_scope, MethodDeclScope):
            return self.__last_scope.method_type
        raise SemanticError("Not in procedure scope")

    @property
    def is_global_scope(self) -> bool:
        return isinstance(self.__last_scope, GlobalScope)

    def is_in_impl_of(self, target_type: TypeId) -> bool:
        """
        Check whether current scope is inside an impl whose target type matches `target_type`.
        """
        for scope in reversed(self.__scope_stack):
            if isinstance(scope, ImplDeclScope):
                impl = self.__method_registry[scope.impl]
                if impl.target == target_type:
                    return True
                return ty.is_same_template(impl.target, target_type, self.__type_space)
            if isinstance(scope, GlobalScope):
                break
        return False
