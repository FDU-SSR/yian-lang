from __future__ import annotations

from typing import TYPE_CHECKING

from compiler.analysis.ty import ty as Type

if TYPE_CHECKING:
    from compiler.analysis.ty.context import TypeCtx


class TypeFormatter:
    def __init__(self, ctx: TypeCtx):
        self.__ctx = ctx
        self.__name_cache: dict[int, str] = {}

    def get_name(self, type_id: int) -> str:
        if type_id in self.__name_cache:
            return self.__name_cache[type_id]

        ty = self.__ctx[type_id]
        match ty:
            case Type.VoidType():
                name = "void"
            case Type.NeverType():
                name = "!"
            case Type.BoolType():
                name = "bool"
            case Type.CharType():
                name = "char"
            case Type.StrType():
                name = "str"
            case Type.IntType(size=size, signed=signed):
                prefix = "i" if signed else "u"
                name = f"{prefix}{size * 8}"
            case Type.FloatType(size=size):
                name = f"f{size * 8}"
            case Type.IntLiteralType():
                name = "IntLiteralType"
            case Type.FloatLiteralType():
                name = "FloatLiteralType"
            case Type.PointerType(pointee_type=pointee_type):
                pointee_name = self.get_name(pointee_type)
                name = f"{pointee_name}*"
            case Type.SliceType(element_type=element_type):
                element_name = self.get_name(element_type)
                name = f"{element_name}[]"
            case Type.ConstGenericType(name=cg_name):
                name = cg_name
            case Type.LiteralValueType(value=value):
                name = str(value)
            case Type.ArrayType(element_type=element_type, length=length):
                element_name = self.get_name(element_type)
                length_name = self.get_name(length)
                name = f"{element_name}[{length_name}]"
            case Type.TupleType(element_types=element_types):
                element_names = [self.get_name(elem_id) for elem_id in element_types]
                name = f"({', '.join(element_names)})"
            case Type.FunctionPointerType(parameter_types=param_types, return_type=return_type):
                param_names = [self.get_name(param_id) for param_id in param_types]
                return_name = self.get_name(return_type)
                name = f"fn({', '.join(param_names)}) -> {return_name}"
            case Type.GenericType(name=name):
                pass
            case Type.StructType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.EnumType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.TraitType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.MethodType(custom_def=custom_def, generic_args=generic_args):
                receiver_name = self.get_name(custom_def.receiver_type)
                if len(generic_args) == 0:
                    name = f"{receiver_name}::{custom_def.name}"
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{receiver_name}::{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.FunctionType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.AliasType(custom_def=custom_def, generic_args=generic_args):
                if len(generic_args) == 0:
                    name = custom_def.name
                else:
                    generic_arg_names = [self.get_name(arg_id) for arg_id in generic_args]
                    name = f"{custom_def.name}<{', '.join(generic_arg_names)}>"
            case Type.ClosureType(parameters=params, return_type=ret, captured_vars=captures):
                param_names = [f"{p.name}: {self.get_name(p.type_id)}" for p in params]
                ret_name = self.get_name(ret)
                captured_names = [f"{cv.name}: {self.get_name(cv.type_id)}" for cv in captures]
                name = f"|{', '.join(captured_names)}| ({', '.join(param_names)}) -> {ret_name}"

        self.__name_cache[type_id] = name
        return name
