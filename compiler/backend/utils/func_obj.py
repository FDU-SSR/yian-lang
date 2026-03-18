from compiler.backend.utils.ll_type import LowLevelTypeManager
from compiler.config.defs import TypeId, UnitId
from compiler.unit_data import UnitData
from compiler.utils import mangle_function, ty
from compiler.utils.errors import CompilerError, SemanticError
from compiler.utils.ty import MethodRegistry, TypeSpace
from llvmlite import ir


class FuncObjManager:
    def __init__(
        self,
        module: ir.Module,
        ll_type: LowLevelTypeManager,
        type_space: TypeSpace,
        method_registry: MethodRegistry,
        unit_datas: dict[UnitId, UnitData],
    ):
        self.__module = module
        self.__ll_type = ll_type
        self.__space = type_space
        self.__method_registry = method_registry
        self.__unit_datas = unit_datas

        self.__objs: dict[TypeId, ir.Function] = {}

    def get_func_name(self, callable_type_id: TypeId) -> str:
        ty_def = self.__space[callable_type_id]
        trait_id = self.__method_registry.query_trait(callable_type_id)

        # function
        if isinstance(ty_def, ty.FunctionType):
            if ty_def.name == "main":
                return "main"
            unit_name = self.__unit_datas[ty_def.function_def.unit_id].unit_name
            mangled_name = mangle_function(
                unit_name=unit_name,
                receiver_id=None,
                trait_id=trait_id,
                function_name=ty_def.name,
                generic_type_ids=ty_def.generic_args,
                type_space=self.__space,
            )
            return mangled_name

        # method
        if isinstance(ty_def, ty.MethodType):
            unit_name = self.__unit_datas[ty_def.method_def.unit_id].unit_name
            receiver_id = ty_def.receiver_type(self.__space.instantiate)
            mangled_name = mangle_function(
                unit_name=unit_name,
                receiver_id=receiver_id,
                trait_id=trait_id,
                function_name=ty_def.name,
                generic_type_ids=ty_def.generic_args,
                type_space=self.__space,
            )
            return mangled_name

        raise CompilerError(f"TypeId {callable_type_id} is not a callable type: {ty_def}")

    def get_func_obj(self, func_type_id: TypeId) -> ir.Function:
        """
        get the `ir.Function` object of the function according to func_type_id
        """
        if func_type_id not in self.__objs:
            raise CompilerError(f"Function object {func_type_id} not allocated.")
        return self.__objs[func_type_id]

    def alloc_func_obj(self, func_type_id: TypeId) -> ir.Function:
        """
        1. create `ir.Function` object according to func_type_id
        2. store the function object in `self.__objs`
        """
        if func_type_id in self.__objs:
            raise CompilerError("Function object already allocated.")

        func_ll_type = self.__ll_type.get_ll_type(func_type_id)
        func_name = self.get_func_name(func_type_id)
        func_obj = ir.Function(self.__module, func_ll_type, name=func_name)
        self.__objs[func_type_id] = func_obj
        return func_obj

    def alloc_method_obj(self, method_type_id: TypeId) -> ir.Function:
        """
        1. create `ir.Function` object according to method_type_id
        2. store the function object in `self.__objs`
        """
        if method_type_id in self.__objs:
            raise CompilerError("Method object already allocated.")

        method_ll_type = self.__ll_type.get_ll_type(method_type_id)
        method_name = self.get_func_name(method_type_id)
        method_obj = ir.Function(self.__module, method_ll_type, name=method_name)
        self.__objs[method_type_id] = method_obj
        return method_obj

    def finalize_functions(self):
        """
        finalize all functions/methods in the module by inserting `ret void` to those without explicit return
        """
        for type_id, func_obj in self.__objs.items():
            func_ty = self.__space[type_id].expect_callable()

            for block in func_obj.blocks:
                assert isinstance(block, ir.Block)
                if not block.is_terminated:
                    if func_ty.return_type(self.__space.instantiate) == TypeSpace.void_id:
                        ir.IRBuilder(block).ret_void()
                    else:
                        raise SemanticError(
                            f"Function/method '{func_obj.name}' missing return statement."
                        )
