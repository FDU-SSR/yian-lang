"""
This package contains utilities for the Yian compiler.

Brief Overview:
- Intermediate Representation (IR): This subpackage contains definitions and utilities for handling the Intermediate Representation (IR) in the Yian compiler.
- Errors Handling (errors): This subpackage contains definitions and utilities for handling errors in the Yian compiler.
- Type (ty): This subpackage provides type definitions and utilities used throughout the compiler.
- Other Utilities (__init__.py): This module contains general utilities used in various parts of the compiler.
"""


from typing import Optional

from compiler.config.constants import YIAN_KEYWORDS

from lian.util import util
from lian.util.data_model import DataModel

from .ty import InstantiatedType, TypeSpace


def __mangle_identifier(identifier: str) -> str:
    """
    对标识符进行名称混淆。
    """
    return f"{len(identifier)}{identifier}"


def is_available(element) -> bool:
    if isinstance(element, str):
        element = element.strip()
    return util.is_available(element)


def is_empty(element) -> bool:
    if isinstance(element, str):
        element = element.strip()
    return util.is_empty(element)


def is_user_defined_name(name: str) -> bool:
    if name.startswith("%vv") or name.startswith("%mm"):
        return False
    return True


def is_compiler_generated_name(name: str) -> bool:
    if name.startswith("%vv") or name.startswith("%mm"):
        return True
    return False


def is_identifier(name: str) -> bool:
    """
    For any legal identifier in C language, it's also a legal identifier in Yian language. Except the single underscore "_".
    """
    if not name:
        return False
    if not (name[0].isalpha() or name[0] == "_"):
        return False
    for ch in name[1:]:
        if not (ch.isalnum() or ch == "_"):
            return False
    if name in YIAN_KEYWORDS:
        return False
    return True


def mangle_function(
    unit_name: str,                 # unit 名称
    receiver_id: Optional[int],     # 方法所属于的类型
    trait_id: Optional[int],        # 方法所属于的 trait
    function_name: str,             # 方法名称
    generic_type_ids: list[int],    # 方法的泛型参数
    type_space: TypeSpace,          # 工具类，用于获取类型信息
) -> str:
    """
    对函数进行名称混淆。
    """
    res = "_AN"

    # 对 unit 名称进行名称混淆
    res += __mangle_identifier(unit_name)

    # 对所属类型进行名称混淆
    if receiver_id is not None:
        res += f"_R_{mangle_type(unit_name, receiver_id, type_space)}"

    # 对所属 trait 进行名称混淆
    if trait_id is not None:
        res += f"_T_{mangle_type(unit_name, trait_id, type_space)}"

    # 对方法名称进行名称混淆
    res += f"_F_{__mangle_identifier(function_name)}"

    # 对泛型参数进行名称混淆
    if len(generic_type_ids) > 0:
        res += "_G_"
        for generic_type_id in generic_type_ids:
            res += mangle_type(unit_name, generic_type_id, type_space)

    return res


def mangle_type(
    unit_name: str,                # unit 名称
    type_id: int,                  # 类型 ID
    type_space: TypeSpace          # 工具类，用于获取类型信息
) -> str:
    """
    对类型进行名称混淆。
    """
    yian_type = type_space[type_id]

    # 对于非自定义类型, 无需进行名称混淆
    if not isinstance(yian_type, InstantiatedType):
        return type_space.get_name(type_id)

    res = "_AN"

    # 对 unit 名称进行名称混淆
    res += __mangle_identifier(unit_name)

    # 对类型名称进行名称混淆
    res += f"{__mangle_identifier(yian_type.name)}"

    # 对泛型参数进行名称混淆
    if len(yian_type.generic_args) > 0:
        res += "_G_"
        for generic_arg_id in yian_type.generic_args:
            res += mangle_type(unit_name, generic_arg_id, type_space)

    return res


def save_dict_list(dict_list: list, file_path: str):
    DataModel(dict_list).save(file_path)


# def get_unit_id_by_stmt_id(stmt_id: StmtId, unit_data_collection: dict[UnitId, "UnitData"]) -> UnitId:
#     """
#     Get the unit ID of a given statement.

#     Args:
#         stmt_id (StmtId): The ID of the statement.
#         unit_data_collection (dict[int, UnitData]): The collection of unit data.
#     """
#     target_unit_id = None
#     for unit_id, unit_data in unit_data_collection.items():
#         if stmt_id in unit_data:
#             target_unit_id = unit_id
#             break
#     if target_unit_id is None:
#         raise CompilerError("Cannot find the unit data for function body copying")
#     return target_unit_id
