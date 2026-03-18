from compiler.analysis.semantic_analysis.utils.scope_manager import ScopeManager
from compiler.config.constants import IntrinsicType
from compiler.config.defs import StmtId, TypeId, UnitId
from compiler.unit_data import UnitData
from compiler.utils import IR
from compiler.utils.errors import CompilerError, NameResolutionError, YianSyntaxError
from compiler.utils.ty import TypeSpace


class TypeParser:
    LEGAL_TYPE_CHARS = set("abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_%")

    def __init__(self, type_space: TypeSpace, scope_manager: ScopeManager, unit_datas: dict[UnitId, UnitData]):
        self.__space = type_space
        self.__scope_manager = scope_manager
        self.__unit_datas = unit_datas

    def parse_type(self, stmt_id: StmtId, type_str: str, generic_name_to_id: dict[str, TypeId], unit_data: UnitData) -> tuple[TypeId, str]:
        """
        Parse type string to type id and return remaining suffix.

        Raise error if the type string is invalid.

        Args:
            stmt_id (StmtId): The statement ID where the type string is located.
            type_str (str): The type string to parse.
            generic_name_to_id (dict[str, TypeId]): A mapping from generic type names to their IDs.
        """
        type_str = type_str.strip()

        # 处理元组类型: (Type1, Type2, ...)
        if type_str.startswith("("):
            result, suffix = self.__parse_tuple(stmt_id, type_str, generic_name_to_id, unit_data)
        else:
            basic_type_str, suffix = self.__split_type_name(type_str)

            if basic_type_str == "Self":
                # If matched Self, return the corresponding type id based on current scope
                result = self.__scope_manager.Self_type
            elif basic_type_str == "fn":
                # If matched `fn`, parse as function pointer type
                # `fn<...>` will be consumed, the remaining suffix will be returned
                result, suffix = self.__parse_function_pointer(stmt_id, suffix, generic_name_to_id, unit_data)
            elif IntrinsicType.is_of(basic_type_str):
                # If matched basic type, return the corresponding type id
                basic_type = IntrinsicType.from_str(basic_type_str)
                result = TypeSpace.intrinsic_type(basic_type)
            elif basic_type_str in generic_name_to_id:
                # If matched generic type name, return the corresponding type id
                result = generic_name_to_id[basic_type_str]
            else:
                symbol_def = unit_data.symbol_lookup(stmt_id, basic_type_str)
                if isinstance(symbol_def, IR.TypeAlias):
                    if symbol_def.type_id is None:
                        raise NameResolutionError(f"Type name '{basic_type_str}' is not resolved yet")
                    result = symbol_def.type_id
                elif not isinstance(symbol_def, IR.CustomType):
                    raise NameResolutionError(f"Type name '{basic_type_str}' does not refer to a custom type")
                if symbol_def.type_id is None:
                    raise NameResolutionError(f"Type name '{basic_type_str}' is not resolved yet")
                result = symbol_def.type_id

        # 继续处理剩余的后缀（指针、数组、泛型等）
        while suffix != "":
            # case 1: pointer types
            if suffix.startswith(("*", "@", "$")):
                result, suffix = self.__parse_pointer(result, suffix, unit_data)
            # case 2: array types
            elif suffix.startswith("["):
                result, suffix = self.__parse_array_or_slice(result, suffix, unit_data)
            # case 3: generic parameter list
            elif suffix.startswith("<"):
                result, suffix = self.__parse_instance(stmt_id, result, suffix, generic_name_to_id, unit_data)
            # other cases: break
            else:
                break
        return result, suffix.strip()

    @staticmethod
    def extract_symbol_names(type_str: str) -> set[str]:
        """
        Extract symbol names from a type string. For example, for `Option<Result<T, E>>`, it will return `{"Option", "Result", "T", "E"}`.
        """
        type_str = type_str.strip()
        symbols: set[str] = set()
        token: list[str] = []

        def flush_token() -> None:
            if not token:
                return
            name = "".join(token)
            token.clear()
            # Filter out pure numbers like array lengths
            if name[0].isalpha() or name[0] in {"_", "%"}:
                symbols.add(name)

        for c in type_str:
            if c in TypeParser.LEGAL_TYPE_CHARS:
                token.append(c)
            else:
                flush_token()
        flush_token()
        return symbols

    def __parse_instance(self, stmt_id: StmtId, base_type_id: TypeId, suffix: str, generic_name_to_id: dict[str, TypeId], unit_data: UnitData) -> tuple[TypeId, str]:
        """
        Parse instantiated type from suffix starting with `<...>`

        Returns a tuple of (type_id, remaining_suffix)

        Args:
            stmt_id (StmtId): The statement ID where the type string is located.
            base_type_id (TypeId): The base type id to instantiate.
            suffix (str): The suffix string starting with `<...>`
            generic_name_to_id (dict[str, TypeId]): A mapping from generic type names to their IDs.
        """
        # find the matching '>'
        end_index = self.__bracket_matcher(suffix, "<")

        # split the content inside '<' and '>'
        type_content, remaining_suffix = suffix[1:end_index - 1].strip(), suffix[end_index:].strip()

        # parse template parameter types
        template_type_ids: list[TypeId] = []
        while len(type_content) > 0:
            template_type_id, type_content = self.parse_type(stmt_id, type_content, generic_name_to_id, unit_data)
            template_type_ids.append(template_type_id)
            type_content = type_content.strip()
            if type_content.startswith(","):
                type_content = type_content[1:].strip()
            else:
                break

        instantiated_type_id = self.__space.alloc_instantiated(base_type_id, template_type_ids)

        return instantiated_type_id, remaining_suffix

    def __parse_pointer(self, base_type_id: TypeId, suffix: str, unit_data: UnitData) -> tuple[TypeId, str]:
        """
        Parse pointer type from suffix starting with `*`/`@`/`$`

        Returns a tuple of (type_id, remaining_suffix)

        Args:
            base_type_id (TypeId): The base type id to point to.
            suffix (str): The suffix string starting with `*`/`@`/`$`
        """
        ptr_sig = suffix[0]
        remaining_suffix = suffix[1:].strip()

        if unit_data.is_lib_module:
            pointer_type_id = self.__space.alloc_pointer(base_type_id)
        else:
            if ptr_sig == "*":
                pointer_type_id = self.__space.alloc_single_ptr(base_type_id)
            elif ptr_sig == "@":
                pointer_type_id = self.__space.alloc_slice(base_type_id)
            elif ptr_sig == "$":
                pointer_type_id = self.__space.alloc_full_ptr(base_type_id)
            else:
                raise YianSyntaxError(f"Invalid pointer signature: {ptr_sig}")

        return pointer_type_id, remaining_suffix

    def __parse_array_or_slice(self, base_type_id: TypeId, suffix: str, unit_data: UnitData) -> tuple[TypeId, str]:
        """
        Parse array or slice type from suffix starting with `[...]`

        Returns a tuple of (type_id, remaining_suffix)

        Args:
            base_type_id (TypeId): The base type id of the array elements.
            suffix (str): The suffix string starting with `[...]`
        """
        end_index = suffix.find("]")
        if end_index == -1:
            raise YianSyntaxError(f"Missing ']' in array type: {suffix}")

        lengths_str = suffix[1:end_index]
        if lengths_str == "":
            # [] indicates a slice type
            array_type_id = self.__space.alloc_slice(base_type_id)
        else:
            # fixed-size array type
            lengths = [int(x.strip()) for x in lengths_str.split(",")]
            lengths = lengths[::-1]
            array_type_id = base_type_id
            for length in lengths:
                array_type_id = self.__space.alloc_array(array_type_id, length)

        remaining_suffix = suffix[end_index + 1:].strip()

        return array_type_id, remaining_suffix

    def __parse_function_pointer(self, stmt_id: StmtId, suffix: str, generic_name_to_id: dict[str, TypeId], unit_data: UnitData) -> tuple[TypeId, str]:
        """
        Parse function pointer type from suffix starting with `<...>`

        Returns a tuple of (type_id, remaining_suffix)

        Args:
            suffix (str): The suffix string starting with `<...>`
            generic_name_to_id (dict[str, TypeId]): A mapping from generic type names to their IDs.
        """
        # find the matching '>'
        end_index = self.__bracket_matcher(suffix, "<")

        # split the content inside '<' and '>'
        type_content, remaining_suffix = suffix[1:end_index - 1].strip(), suffix[end_index:].strip()

        # consume the return type
        return_id, type_content = self.parse_type(stmt_id, type_content, generic_name_to_id, unit_data)

        # remove the "(" and ")" around parameter list
        if not (type_content.startswith("(") and type_content.endswith(")")):
            raise YianSyntaxError(f"Invalid function pointer type signature: {type_content}")
        param_types_str = type_content[1:-1].strip()

        # parse parameter types
        param_type_ids = []
        while len(param_types_str) > 0:
            param_type_id, param_types_str = self.parse_type(stmt_id, param_types_str, generic_name_to_id, unit_data)
            param_type_ids.append(param_type_id)
            param_types_str = param_types_str.strip()
            if param_types_str.startswith(","):
                param_types_str = param_types_str[1:].strip()
            else:
                break

        # register function pointer type
        func_type_id = self.__space.alloc_function_pointer(param_type_ids, return_id)

        return func_type_id, remaining_suffix

    def __parse_tuple(self, stmt_id: StmtId, type_str: str, generic_name_to_id: dict[str, TypeId], unit_data: UnitData) -> tuple[TypeId, str]:
        """
        Parse tuple type from type string starting with `(...)`

        Returns a tuple of (type_id, remaining_suffix)

        Args:
            type_str (str): The type string starting with `(...)`
            generic_name_to_id (dict[str, TypeId]): A mapping from generic type names to their IDs.
        """
        # find the matching ')'
        end_index = self.__bracket_matcher(type_str, "(")

        # split the content inside '(' and ')'
        tuple_content, remaining_suffix = type_str[1:end_index - 1].strip(), type_str[end_index:].strip()

        # parse tuple element types
        element_type_ids: list[TypeId] = []
        while len(tuple_content) > 0:
            element_type_id, tuple_content = self.parse_type(stmt_id, tuple_content, generic_name_to_id, unit_data)
            element_type_ids.append(element_type_id)
            tuple_content = tuple_content.strip()
            if tuple_content.startswith(","):
                tuple_content = tuple_content[1:].strip()
            else:
                break

        tuple_type_id = self.__space.alloc_tuple(element_type_ids)
        return tuple_type_id, remaining_suffix

    def __split_type_name(self, type_name: str) -> tuple[str, str]:
        """
        从 str 的开头截取表示类型名称的部分，即所有合法的字母和数字以及下划线

        剩余的部分是类型的修饰符，例如指针、数组、泛型参数列表等

        :param type_name: 类型名称字符串
        :return: 一个元组，第一个元素是类型名称，第二个元素是剩余的部分
        """
        i = 0
        for c in type_name:
            if c not in TypeParser.LEGAL_TYPE_CHARS:
                break
            i += 1
        return type_name[:i], type_name[i:].strip()

    def __bracket_matcher(self, type_str: str, starter: str) -> int:
        """
        Given a string starting with `<`/`(`/`[`, find the position of the matching closing bracket.

        Args:
            stmt: The statement being processed.
            type_str (str): The string starting with `<`/`(`/`[`.
            starter (str): The opening bracket character.
        """
        if starter not in "<([":
            raise CompilerError(f"Invalid starter for bracket matcher: {starter}")
        if not type_str.startswith(starter):
            raise CompilerError(f"Type string must start with {starter}: {type_str}")

        ENDER_MAP = {
            "<": ">",
            "(": ")",
            "[": "]",
        }

        ender = ENDER_MAP[starter]

        stack = 1
        for i, c in enumerate(type_str[1:], start=1):
            if c == starter:
                stack += 1
            elif c == ender:
                stack -= 1
                if stack == 0:
                    return i + 1
        raise CompilerError(f"Unmatched '{starter}' in template type: {type_str}")
