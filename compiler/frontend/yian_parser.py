#!/usr/bin/env python3

from compiler.config.constants import YIAN_ATTRS
from tree_sitter import Node

from lian.config import config
from lian.lang import common_parser
from lian.util import util


class YianParser(common_parser.Parser):
    condition_variable_id = 0
    def init(self):
        self.field_read_target_to_pos = {}
        self.LITERAL_MAP = {
            "int_literal": self.regular_number_literal,
            "float_literal": self.regular_number_literal,
            "decimal_floating_point_literal": self.regular_number_literal,
            "bool_literal": self.regular_literal,
            "char_literal": self.character_literal,
            "string_literal": self.string_literal,
            "string_fragment": self.string_fragment,
            "string_fragment_with_brace": self.string_fragment,
            "string_interpolation": self.string_interpolation,
            "bytes_literal": self.bytes_literal,
        }

        self.EXPRESSION_HANDLER_MAP = {
            "constant": self.constant,
            "parenthesized_expression": self.parenthesized_expression,
            "not_expression": self.not_expression,
            "boolean_expression": self.boolean_expression,
            "binary_expression": self.binary_expression,
            "unary_expression": self.unary_expression,
            "field_access": self.field,
            "array_access": self.array,
            "mem_access": self.mem,
            "addr_of": self.addr_of,
            "call_expression": self.call_expression,
            "array_creation_expression": self.new_array,
            "formal_parameter": self.formal_parameter,
            "array_initializer": self.array_initializer,
            "tuple_initializer": self.tuple_initializer,
            "dyn_expression": self.dyn_expression,
            "function_expression": self.method_declaration,
            "switch_expression": self.switch_expression,
            "if_expression": self.if_expression,
        }

        self.DECLARATION_HANDLER_MAP = {
            "struct_declaration": self.struct_declaration,
            "union_declaration": self.union_declaration,
            "enum_declaration": self.enum_declaration,
            "trait_declaration": self.trait_declaration,
            "implement_declaration": self.implement_declaration,
            "local_variable_declaration": self.variable_declaration,
            "method_declaration": self.method_declaration,
            "ffi_declaration": self.ffi_declaration,
            "ffi_item": self.method_declaration,
            "declaration": self.declaration,
        }

        self.STATEMENT_HANDLER_MAP = {
            "assignment_statement": self.assignment_statement,
            "ok_statement": self.ok_statement,
            "while_statement": self.while_statement,
            "do_while_statement": self.do_while_statement,
            "loop_statement": self.loop_statement,
            "for_statement": self.for_statement,
            "for_in_statement": self.for_in_statement,
            "break_statement": self.break_statement,
            "continue_statement": self.continue_statement,
            "standalone_block": self.block_statement,
            "return_statement": self.return_statement,
            "with_statement": self.with_statement,
            "import_statement": self.import_statement,
            "from_import_statement": self.from_import_stmt,
            "assert_statement": self.assert_statement,
            "yield_statement": self.yield_statement,
            "type_definition": self.type_definition,
            "del_statement": self.del_statement,
        }

    def condition_variable(self):
        self.condition_variable_id += 1
        return f"condition_variable_{self.condition_variable_id}"

    def end_parse(self, node: Node, statements):
        pass

    def validate_attrs(self, node: Node, attr_list):
        if len(attr_list) == 0:
            return
        for attr in attr_list:
            if len(attr) == 0:
                continue
            # print("attr:", attr)
            # print(YIAN_ATTRS)
            if attr not in YIAN_ATTRS:
                raise Exception("Unknown attribute: %s" % attr)

    def adjust_attrs(self, node: Node, attrs):
        if isinstance(attrs, str):
            attrs = attrs.split(", ")

        final_attrs = []
        for attr in attrs:
            attr = attr.strip()
            if attr:
                final_attrs.append(attr)

        self.validate_attrs(node, final_attrs)

        for i in range(len(final_attrs)):
            if YIAN_ATTRS.PUBLIC == final_attrs[i]:
                final_attrs[i] = YIAN_ATTRS.PUBLIC

        return ",".join(final_attrs)

    def is_comment(self, node: Node):
        return node.type in ["line_comment", "block_comment", "comment"]

    def is_identifier(self, node: Node):
        return node.type == "identifier"

    def string_literal(self, node: Node, statements: list, replacement):
        last_assign_result = ""
        start_end_tag = ["string_start", "string_end"]
        children_type = []
        for child in node.named_children:
            children_type.append(child.type)
        if "string_interpolation" in children_type:
            for index in range(len(node.named_children)):
                cur_node = node.named_children[index]
                if cur_node.type in start_end_tag:
                    continue
                tmp_var = self.tmp_variable()
                shadow_oprand = self.parse(cur_node, statements)
                if index == 0:
                    last_assign_result = shadow_oprand
                    continue
                if shadow_oprand:
                    self.append_stmts(statements, node, {"assign_stmt": {"target": tmp_var, "operator": "+", "operand": last_assign_result, "operand2": shadow_oprand}})
                    last_assign_result = tmp_var
            return tmp_var
        # shadow_oprand = ""
        # for child in node.named_children:
        #     if child.type in start_end_tag:
        #         continue
        #     tmp_var = self.tmp_variable()
        #     shadow_oprand += self.parse(child, statements)
        #     # self.append_stmts(statements, node, {"assign_stmt": {"target": tmp_var, "operand": shadow_oprand}}
        return self.read_node_text(node)

    def string_fragment(self, node: Node, statements, replacement):
        replacement = []
        for child in node.named_children:
            self.parse(child, statements, replacement)

        ret = self.read_node_text(node)
        if replacement:
            for r in replacement:
                (expr, value) = r
                ret = ret.replace(self.read_node_text(expr), value)

        ret = self.handle_hex_string(ret)
        return self.escape_string(ret)

    def string_interpolation(self, node: Node, statements, replacement):
        expr = node.named_children[0]
        shadow_expr = self.parse(expr, statements)
        replacement.append((expr, shadow_expr))
        return shadow_expr

    def regular_number_literal(self, node: Node, statements, replacement):
        value = self.read_node_text(node)
        value = self.common_eval(value)
        return str(value)

    def hex_float_literal(self, node: Node, statements, replacement):
        value = self.read_node_text(node)
        try:
            value = float.fromhex(value)
        except:
            pass
        return str(value)

    def regular_literal(self, node: Node, statements, replacement):
        return self.read_node_text(node)

    def character_literal(self, node: Node, statements, replacement):
        value = self.read_node_text(node)
        return "%s" % value

    def this_literal(self, node: Node, statements, replacement):
        return self.global_this()

    def super_literal(self, node: Node, statements, replacement):
        return self.global_super()

    def is_constant_literal(self, node: Node):
        return node.type in self.LITERAL_MAP
    
    def bytes_literal(self, node: Node, statements, replacement):
        value = self.read_node_text(node)
        return value

    CLASS_TYPE_MAP = {
        "class_declaration": "class",
        "interface_declaration": "interface",
        "record_declaration": "record",
    }

    def parse_import_list(self, node: Node, statements, import_list, prefix=""):
        def get_name(name):
            if prefix:
                return prefix + "." + name
            else:
                return name

        for child in import_list.named_children:
            if child.type == "dotted_name":
                name = self.read_node_text(child)
                self.append_stmts(statements, node, {
                    "import_stmt": {"attrs": "private", "name": get_name(name)}
                })
            else:
                name = self.read_node_text(self.find_child_by_field(child, "name"))
                alias = self.read_node_text(self.find_child_by_field(child, "alias"))
                self.append_stmts(statements, node, {
                    "import_stmt": {
                        "attrs": "private", "name": get_name(name), "alias": alias
                    }
                })

    def import_statement(self, node: Node, statements):
        import_list = node.named_children[0]
        self.parse_import_list(node, statements, import_list)

    def from_import_stmt(self, node: Node, statements):
        # self.print_tree(node)
        source_node = self.find_child_by_field(node, "module")
        import_list_list = self.find_children_by_type(node, "import_list")
        source = self.read_node_text(source_node)

        for import_list in import_list_list:
            self.parse_import_list(node, statements, import_list, prefix=source)

    def assert_statement(self, node: Node, statements):
        condition = self.find_child_by_field(node, "condition")
        shadow_condition = self.parse(condition, statements)

        message = self.find_child_by_field(node, "message")
        shadow_message = self.read_node_text(message)

        self.append_stmts(statements, node, {
            "assert_stmt": {"condition": shadow_condition, "message": shadow_message}
        })

    def yield_statement(self, node: Node, statements):
        expression_node = self.find_child_by_field(node, "expression")
        shadow_expr = ""
        if expression_node:
            shadow_expr = self.parse(expression_node, statements)
            self.append_stmts(statements, node, {"yield_stmt": {"name": shadow_expr}})
        else:
            self.syntax_error(node, "Expression in yield must not be empty")

    def del_statement(self, node: Node, statements):
        target_node = self.find_child_by_field(node, "target")
        target_name = self.parse(target_node, statements)
        self.append_stmts(statements, node, {"del_stmt": {"name": target_name}})

    # def read_type_node_with_stars(self, node: Node):
    #     type_node = self.find_child_by_field(node, "type")
    #     level_node = self.find_child_by_field(node, "level")
    #     pointer_stars = self.find_children_by_field(node, "pointer_stars")
    #     return self.read_node_text(type_node) + " " + self.read_node_text(level_node) + len(pointer_stars) * '*'
    def read_type_node_with_stars(self, node: Node):
        type_node = self.find_child_by_field(node, "type")
        pointer_stars = self.find_children_by_field(node, "pointer_stars")
        result = ""
        for pointer_star in pointer_stars:
            node_text = self.read_node_text(pointer_star)
            result += node_text if node_text != 'full@' else '$'
        return self.read_node_text(type_node) + result

    def type_definition(self, node: Node, statements):
        alias_node = self.find_child_by_field(node, "alias")
        type_name = self.read_type_node_with_stars(node)

        # 查找 < 和 > 的位置
        left_angle_index = type_name.find('<')
        right_angle_index = type_name.rfind('>')

        base_type = ""
        generics = ""
        base_type = type_name
        # if left_angle_index != -1 and right_angle_index != -1 and left_angle_index < right_angle_index:
        #     # 提取普通名字和泛型部分
        #     base_type = type_name[:left_angle_index]
        #     generics = type_name[left_angle_index:right_angle_index + 1]
        # else:
        #     base_type = type_name
        #     generics = ""

        alias_name = self.read_node_text(alias_node)

        statements.append(self.add_col_row_info(node, {
            "type_alias_decl": {
                "data_type": base_type,
                "name": alias_name,
                # "type_parameters": generics  # 添加泛型信息
            }
        }))

    def ffi_declaration(self, node: Node, statements):
        yian_node = {}
        yian_node["name"] = self.read_node_text(self.find_child_by_field(node, "name"))
        body_node = self.find_child_by_field(node, "body")
        new_body = []
        if body_node:
            method_nodes = self.find_children_by_type(body_node, "ffi_item")
            for method_node in method_nodes:
                self.parse(method_node, new_body)

        yian_node["body"] = new_body
        self.append_stmts(statements, node, {"ffi_decl": yian_node})

    def method_declaration(self, node: Node, statements):
        # node = self.find_child_by_type(node, "method_signature")
        modifiers_node = self.find_children_by_type(node, "modifiers")
        modifiers_list = []
        if len(modifiers_node) > 0:
            modifiers_list = self.read_node_text(modifiers_node[0]).split()

        # modifiers_list = []
        # for each_modifier in modifiers_node:
        #     modifiers_list.append(self.read_node_text(each_modifier))
        modifiers = self.adjust_attrs(node, modifiers_list)

        type_parameters_node = self.find_child_by_field(node, "type_parameters")
        type_parameters = self.read_node_text(type_parameters_node)[1:-1]

        mytype = self.read_type_node_with_stars(node)

        name_node = self.find_child_by_field(node, "name")
        if not name_node:    # function_expression与arrow_function
            name = self.tmp_method()  # 为匿名函数起一个临时名字
        else:
            name = self.read_node_text(name_node)

        parameters_block = []
        parameter_node = self.find_child_by_field(node, "parameters")
        if parameter_node and parameter_node.named_child_count > 0:
            # need to deal with parameters
            for p in parameter_node.named_children:
                if self.is_comment(p):
                    continue

                current_parameter_stmts = []
                self.parse(p, current_parameter_stmts)
                parameters_block.extend(current_parameter_stmts)

        body_node = self.find_child_by_field(node, "body")
        if body_node and body_node.type == "block":
            new_body = []
            self.parse(body_node, new_body)

            self.append_stmts(statements, node, {
                "method_decl": {
                    "attrs": modifiers, "data_type": mytype, "name": name, "type_parameters": type_parameters,
                    "parameters": parameters_block, "body": new_body
                }
            })
        else:
            self.append_stmts(statements, node, {
                "method_header": {
                    "attrs": modifiers, "data_type": mytype, "name": name, "type_parameters": type_parameters,
                    "parameters": parameters_block
                }
            })

        return name

    def implement_declaration(self, node: Node, statements):
        """
        解析 impl 声明并转换为中间表示。

        :param node: impl 声明的语法树节点
        :param statements: 用于存储解析结果的语句列表
        """
        yian_node = {}

        # 解析 trait 信息
        trait_info = self.find_child_by_type(node, "trait_in_impl")
        if trait_info:
            # trait_name_node = self.find_child_by_field(trait_info, "name")
            # trait_name = self.read_node_text(trait_name_node) if trait_name_node else ""
            # trait_type_params_node = self.find_child_by_field(trait_info, "type_parameters")
            # trait_type_params = self.read_node_text(trait_type_params_node)[1:-1] if trait_type_params_node else ""
            trait_name = ""
            for child in trait_info.named_children:
                trait_name += self.read_node_text(child)
            yian_node["trait_name"] = trait_name
        else:
            yian_node["trait_name"] = ""
            yian_node["trait_type_parameters"] = ""

        impl_type_params_node = self.find_child_by_field(node, "type_parameters")
        impl_type_params = self.read_node_text(impl_type_params_node)[1:-1] if impl_type_params_node else ""
        yian_node["type_parameters"] = impl_type_params
        # 解析结构体信息
        # struct_info = self.find_child_by_field(node, "type_in_impl")
        # struct_name_node = self.find_child_by_field(struct_info, "name")
        # struct_name = self.read_node_text(struct_name_node) if struct_name_node else ""
        # struct_type_params_node = self.find_child_by_field(struct_info, "type_parameters")
        # struct_type_params = self.read_node_text(struct_type_params_node) if struct_type_params_node else ""
        # yian_node["struct_name"] = struct_name + struct_type_params
        type_info = self.read_type_node_with_stars(node)
        yian_node["struct_name"] = type_info

        # 解析 where 子句
        where_clause_node = self.find_child_by_field(node, "where_clause")
        if where_clause_node:
            type_constraints = []
            constraint_nodes = self.find_children_by_type(where_clause_node, "type_constraint")
            for constraint_node in constraint_nodes:
                type_node = self.find_child_by_field(constraint_node, "type")
                bound_node = self.find_child_by_field(constraint_node, "bound")
                type_str = self.read_node_text(type_node) if type_node else ""
                bound_str = self.read_node_text(bound_node) if bound_node else ""
                type_constraints.append({
                    "type": type_str,
                    "bound": bound_str
                })
            yian_node["where_clause"] = type_constraints
        else:
            yian_node["where_clause"] = []

        # 解析实现体
        body_node = self.find_child_by_field(node, "body")
        new_body = []
        if body_node:
            method_nodes = self.find_children_by_type(body_node, "method_declaration")
            for method_node in method_nodes:
                self.parse(method_node, new_body)
                method_content = None
                for method_content_key in new_body[-1]:
                    method_content = new_body[-1][method_content_key]
                    break
                if not method_content:
                    continue
                if "static" not in method_content["attrs"]:
                    method_content["parameters"].insert(
                        0,
                        self.add_col_row_info(
                            node,
                            {'parameter_decl': {'attrs': [], 'data_type': type_info, 'name': 'self'}}
                        )
                    )
                if trait_info:
                    attrs = method_content["attrs"]
                    if attrs == "":
                        method_content["attrs"] = YIAN_ATTRS.PUBLIC
                    elif YIAN_ATTRS.PUBLIC not in attrs:
                            method_content["attrs"] = YIAN_ATTRS.PUBLIC + "," + attrs
                # all_type_params = [
                #     struct_type_params,
                #     method_content.get('type_parameters', '')
                # ]
                # method['type_parameters'] = ",".join([p for p in all_type_params if p])
                # method['impl'] = struct_name
                # method['trait'] = yian_node["trait_name"]

        yian_node["body"] = new_body

        self.append_stmts(statements, node, {"implement_decl": yian_node})

    def package_declaration(self, node: Node, statements):
        name = self.read_node_text(node.named_children[0])
        if name:
            self.append_stmts(statements, node, {"package_stmt": {"name": name}})

    def parse_field(self, node: Node, statements):
        myobject = self.find_child_by_field(node, "object")
        shadow_object = self.parse(myobject, statements)
        type_arguments = self.find_child_by_field(node, "type_arguments")
        if type_arguments:
            shadow_type_arguments = self.read_node_text(type_arguments)
            shadow_object += shadow_type_arguments
        
        # to deal with super
        # remaining_content = self.read_node_text(node).replace(self.read_node_text(myobject) + ".", "").split(".")[:-1]
        # if remaining_content:
        #     for child in remaining_content:
        #         tmp_var = self.tmp_variable()
        #         self.append_stmts(statements, node, {"field_read": {"target": tmp_var, "receiver_object": shadow_object, "field": child}})
        #         shadow_object = tmp_var

        field = self.find_child_by_field(node, "field")
        shadow_field = self.read_node_text(field)
        return (shadow_object, shadow_field)

    def parse_array(self, node: Node, statements):
        array = self.find_child_by_field(node, "array")
        shadow_array = self.parse(array, statements)
        # index = self.find_child_by_field(node, "index")
        dimensions_expr = self.find_child_by_type(node, "dimensions_expr")
        index = dimensions_expr.named_children
        shadow_index = []
        for i in index:
            shadow_index.append(self.parse(i, statements))
        # shadow_index = self.parse(index, statements)
        if len(shadow_index) > 1:
            tmp_var = self.tmp_variable()
            for i in range(len(shadow_index) - 1):
                tmp_var = self.tmp_variable()
                self.append_stmts(statements, node, {
                    "assign_stmt": {"target": tmp_var, "operator": '[]',
                                    "operand": shadow_array, "operand2": shadow_index[i]}
                })
                shadow_array = tmp_var
        return (shadow_array, shadow_index[-1])

    def parse_mem(self, node: Node, statements):
        shadow_operator = self.find_child_by_field(node, "operator")
        shadow_operator = self.read_node_text(shadow_operator)
        tmp_address = self.find_child_by_field(node, "argument")
        shadow_address = self.parse(tmp_address, statements)
        return (shadow_operator, shadow_address)

    def parse_addr(self, node: Node, statements):
        shadow_operator = self.find_child_by_field(node, "operator")
        shadow_operator = self.read_node_text(shadow_operator)
        tmp_address = self.find_child_by_field(node, "argument")
        shadow_address = ""
        if tmp_address.type == "array_access":
            shadow_array, shadow_index = self.parse_array(tmp_address, statements)
            # array_node = self.find_child_by_field(tmp_address, "array")
            # array_name = self.read_node_text(array_node)
            # index_node = self.find_child_by_type(tmp_address, "dimensions_expr")
            # index = index_node.named_children[0]
            # index = self.parse(index, statements)
            # shadow_address = {"array": array_name, "index": index}
            shadow_address = {"array": shadow_array, "index": shadow_index}
            shadow_operator = "array_access"
        elif tmp_address.type == "field_access":
            shadow_object, shadow_field = self.parse_field(tmp_address, statements)
            # receiver_node = self.find_child_by_field(tmp_address, "object")
            # receiver_name = self.read_node_text(receiver_node)
            # field_node = self.find_child_by_field(tmp_address, "field")
            # field_name = self.read_node_text(field_node)
            shadow_address = {"receiver": shadow_object, "field": shadow_field}
            shadow_operator = "field_access"
        else:
            shadow_address = self.parse(tmp_address, statements)
        return (shadow_operator, shadow_address)

    def array(self, node: Node, statements):
        tmp_var = self.tmp_variable()
        shadow_array, shadow_index = self.parse_array(node, statements)
        self.append_stmts(statements, node, {
            "assign_stmt": {"target": tmp_var, "operator": "[]",
                            "operand": shadow_array, "operand2": shadow_index}
        })
        return tmp_var

    def field(self, node: Node, statements):
        tmp_var = self.tmp_variable()
        shadow_object, shadow_field = self.parse_field(node, statements)
        self.append_stmts(statements, node, {
            "assign_stmt": {
                "target": tmp_var, "operator": '.',
                "operand": shadow_object, "operand2": shadow_field
            }
        })
        self.field_read_target_to_pos[tmp_var] = len(statements) - 1

        return tmp_var

    def mem(self, node: Node, statements):
        tmp_var = self.tmp_variable()
        shadow_operator, shadow_address = self.parse_mem(node, statements)
        self.append_stmts(statements, node, {"assign_stmt": {"target": tmp_var, "operator": '*',
                                                             "operand": shadow_address}})
        return tmp_var

    def addr_of(self, node: Node, statements):
        # self.print_tree(node)
        tmp_var = self.tmp_variable()
        shadow_operator, shadow_address = self.parse_addr(node, statements)

        if shadow_operator == "field_access":
            tmp_var2 = self.tmp_variable()
            self.append_stmts(statements, node,
                              {"assign_stmt": {
                                  "target": tmp_var2, "operator": ".",
                                  "operand": shadow_address["receiver"],
                                  "operand2": shadow_address["field"]
                              }})
            self.append_stmts(statements, node,
                              {"assign_stmt": {
                                  "target": tmp_var, "operator": "&",
                                  "operand": tmp_var2
                              }})
        elif shadow_operator == "array_access":
            tmp_var2 = self.tmp_variable()
            self.append_stmts(statements, node,
                              {"assign_stmt": {
                                  "target": tmp_var2, "operator": "[]",
                                  "operand": shadow_address["array"],
                                  "operand2": shadow_address["index"]
                              }})
            self.append_stmts(statements, node,
                              {"assign_stmt": {
                                  "target": tmp_var, "operator": "&",
                                  "operand": tmp_var2
                              }})
        else:
            self.append_stmts(statements, node, {"assign_stmt": {"target": tmp_var, "operator": "&", "operand": shadow_address}})
        return tmp_var

    def assignment_statement(self, node: Node, statements):
        left = self.find_child_by_field(node, "left")
        right = self.find_child_by_field(node, "right")

        tmp_var = self.tmp_variable()
        tmp_var2 = self.tmp_variable()
        shadow_right = ""
        shadow_left = ""
        right_is_switch_if = False

        operator = self.find_child_by_field(node, "operator")
        shadow_operator = self.read_node_text(operator).replace("=", "")

        if left.type not in ["field_access", "array_access", "mem_access"]:
            shadow_left = self.read_node_text(left)
        else:
            shadow_left = tmp_var

        shadow_right = shadow_left
        if right.type == "switch_expression":
            right_is_switch_if = True
            self.switch_expression(right, statements, shadow_right)
        elif right.type == "if_expression":
            right_is_switch_if = True
            self.if_expression(right, statements, shadow_right)
        else:
            shadow_right = self.parse(right, statements)

        if left.type == "field_access":
            shadow_object, field = self.parse_field(left, statements)
            if not shadow_operator:
                tmp_var3 = self.tmp_variable()
                self.append_stmts(statements, node, {
                    "assign_stmt": {
                        "target": tmp_var3, "operator": '.',
                        "operand": shadow_object, "operand2": field
                    }
                })
                self.append_stmts(statements, node, {
                    "assign_stmt": {
                        "target": tmp_var3, "operand": shadow_right
                    }
                })
                return shadow_right

            self.append_stmts(statements, node, {
                "assign_stmt": {
                    "target": tmp_var, "operator": '.',
                    "operand": shadow_object, "operand2": field,
                }
            })

            self.append_stmts(statements, node, {
                "assign_stmt": {
                    "target": tmp_var, "operator": shadow_operator,
                    "operand": tmp_var, "operand2": shadow_right
                }
            })

            return tmp_var2

        if left.type == "array_access":
            shadow_array, shadow_index = self.parse_array(left, statements)

            if not shadow_operator:
                tmp_var3 = self.tmp_variable()
                self.append_stmts(statements, node, {
                    "assign_stmt": {
                        "target": tmp_var3, "operator": "[]",
                        "operand": shadow_array, "operand2": shadow_index
                    }
                })
                self.append_stmts(statements, node, {
                    "assign_stmt": {
                        "target": tmp_var3, "operand": shadow_right
                    }
                })
                return shadow_right

            self.append_stmts(statements, node, {
                "assign_stmt": {
                    "target": tmp_var, "operator": "[]",
                    "operand": shadow_array, "operand2": shadow_index
                }
            })

            self.append_stmts(statements, node, {
                "assign_stmt": {
                    "target": tmp_var, "operator": shadow_operator,
                    "operand": tmp_var, "operand2": shadow_right
                }
            })

            return tmp_var2

        if left.type == "mem_access":
            shadow_op, shadow_address = self.parse_mem(left, statements)
            if not shadow_operator:
                tmp_var3 = self.tmp_variable()
                self.append_stmts(statements, node, {
                    "assign_stmt": {"target": tmp_var3, "operator": '*', "operand": shadow_address}
                })
                self.append_stmts(statements, node, {
                    "assign_stmt": {"target": tmp_var3, "operand": shadow_right}
                })
                return shadow_right

            self.append_stmts(statements, node, {
                "assign_stmt": {
                    "target": tmp_var, "operator": '*', "operand": shadow_address
                }
            })

            self.append_stmts(statements, node, {
                "assign_stmt": {
                    "target": tmp_var2, "operator": shadow_operator,
                    "operand": tmp_var, "operand2": shadow_right
                }
            })
            self.append_stmts(statements, node, {
                "assign_stmt": {
                    "target": tmp_var, "operand": tmp_var2
                }
            })

            return tmp_var2

        shadow_left = self.read_node_text(left)
        # self.append_stmts(statements, node, {"variable_decl": {"name": shadow_left}})
        if not shadow_right:
            self.syntax_error(node, "right operand is empty")

        if right_is_switch_if:
            return shadow_left

        if not shadow_operator:
            self.append_stmts(statements, node, {
                "assign_stmt": {"target": shadow_left, "operand": shadow_right}
            })
        else:
            self.append_stmts(statements, node, {
                "assign_stmt": {
                    "target": shadow_left, "operator": shadow_operator,
                    "operand": shadow_left, "operand2": shadow_right
                }
            })
        return shadow_left

    def evaluate_literal_binary_expression(self, root: Node, statements):
        node_list = [root]
        nodes_to_be_computed = []
        binary_expr_value_map = {}

        if not root:
            return

        # determine if it is a real literal_binary_expression
        while (len(node_list) > 0):
            node = node_list.pop()
            if not node:
                return

            if node.id in binary_expr_value_map:
                # This node cannot be evaluated
                if binary_expr_value_map.get(node.id) is None:
                    return
                continue

            if not self.is_constant_literal(node) and node.type != "binary_expression":
                return

            # literal
            if self.is_constant_literal(node):
                continue

            operator = self.find_child_by_field(node, "operator")
            left = self.find_child_by_field(node, "left")
            right = self.find_child_by_field(node, "right")

            node_list.append(left)
            node_list.append(right)

            if self.is_constant_literal(left) and self.is_constant_literal(right):
                shadow_operator = self.read_node_text(operator)
                shadow_left = self.parse(left, statements)
                shadow_right = self.parse(right, statements)
                content = shadow_left + shadow_operator + shadow_right
                value = self.common_eval(content)
                if value is None:
                    binary_expr_value_map[node.id] = None
                    binary_expr_value_map[root.id] = None
                    return

                if self.is_string(shadow_left):
                    value = self.escape_string(value)

                binary_expr_value_map[node.id] = value
                nodes_to_be_computed.append(node)

        # conduct evaluation from bottom to top
        while len(nodes_to_be_computed) > 0:
            node = nodes_to_be_computed.pop(0)
            if node == root:
                return binary_expr_value_map[root.id]

            parent = node.parent
            if not parent or parent.type != "binary_expression":
                return

            nodes_to_be_computed.append(parent)

            if parent.id in binary_expr_value_map:
                continue

            left = self.find_child_by_field(parent, "left")
            right = self.find_child_by_field(parent, "right")

            if not left or not right:
                return

            shadow_left = None
            shadow_right = None

            if left.id in binary_expr_value_map:
                shadow_left = binary_expr_value_map.get(left.id)
            elif self.is_constant_literal(left):
                shadow_left = self.parse(left, statements)
            else:
                return

            if right.id in binary_expr_value_map:
                shadow_right = binary_expr_value_map.get(right.id)
            elif self.is_constant_literal(right):
                shadow_right = self.parse(right, statements)
            else:
                return

            eval_content = ""
            try:
                eval_content = str(shadow_left) + str(shadow_operator) + str(shadow_right)
            except:
                return
            value = self.common_eval(eval_content)
            if value is None:
                return

            if self.is_string(shadow_left):
                value = self.escape_string(value)

            if isinstance(value, str):
                if len(value) > config.STRING_MAX_LEN:
                    return value[:-1] + '..."'
            binary_expr_value_map[parent.id] = value
        return binary_expr_value_map.get(root.id)

    def not_expression(self, node: Node, statements: list):
        arg = self.find_child_by_field(node, "argument")
        shadow_arg = self.parse(arg, statements)
        tmp_var = self.tmp_variable()
        self.append_stmts(statements, node, {"assign_stmt": {"target": tmp_var, "operator": "not", "operand": shadow_arg}})
        return tmp_var

    def boolean_expression(self, node: Node, statements: list):
        left = node.named_children[0]
        right = node.named_children[-1]
        operator = self.find_child_by_field(node, "operator")

        shadow_operator = self.read_node_text(operator)

        if shadow_operator == "and":
            shadow_left = self.parse(left, statements)
            condition_variable = self.condition_variable()

            self.append_stmts(statements, node, {
                "variable_decl": {"data_type": "bool", "name": condition_variable}
            })

            then_body = []
            shadow_right = self.parse(right, then_body)
            self.append_stmts(then_body, node, {
                "assign_stmt": {"target": condition_variable, "operand": shadow_right}
            })

            else_body = []
            self.append_stmts(else_body, node, {
                "assign_stmt": {"target": condition_variable, "operand": "false"}
            })

            self.append_stmts(statements, node, {
                "if_stmt": {
                    "condition": shadow_left,
                    "then_body": then_body,
                    "else_body": else_body
                }
            })
            return condition_variable

        if shadow_operator == "or":
            shadow_left = self.parse(left, statements)
            condition_variable = self.condition_variable()

            self.append_stmts(statements, node, {
                "variable_decl": {"data_type": "bool", "name": condition_variable}
            })

            then_body = []
            self.append_stmts(then_body, node, {
                "assign_stmt": {"target": condition_variable, "operand": "true"}
            })

            else_body = []
            shadow_right = self.parse(right, else_body)
            self.append_stmts(else_body, node, {
                "assign_stmt": {"target": condition_variable, "operand": shadow_right}
            })

            self.append_stmts(statements, node, {
                "if_stmt": {
                    "condition": shadow_left,
                    "then_body": then_body,
                    "else_body": else_body
                }
            })
            return condition_variable

        shadow_left = self.parse(left, statements)
        shadow_right = self.parse(right, statements)

        tmp_var = self.tmp_variable()
        self.append_stmts(statements, node, {
            "assign_stmt": {
                "target": tmp_var,
                "operator": shadow_operator,
                "operand": shadow_left,
                "operand2": shadow_right
            }
        })
        return tmp_var

    def binary_expression(self, node: Node, statements):
        evaluated_value = self.evaluate_literal_binary_expression(node, statements)
        if evaluated_value is not None:
            if evaluated_value == "False" or evaluated_value == "True":
                evaluated_value = evaluated_value.lower()
            return evaluated_value

        left = self.find_child_by_field(node, "left")
        right = self.find_child_by_field(node, "right")
        operator = self.find_child_by_field(node, "operator")

        shadow_operator = self.read_node_text(operator)

        if shadow_operator == "and":
            shadow_left = self.parse(left, statements)
            tmp_var = self.tmp_variable()

            then_body = []
            shadow_right = self.parse(right, then_body)
            self.append_stmts(then_body, node, {
                "assign_stmt": {"target": tmp_var, "operand": shadow_right}
            })

            else_body = []
            self.append_stmts(else_body, node, {
                "assign_stmt": {"target": tmp_var, "operand": "false"}
            })

            self.append_stmts(statements, node, {
                "if_stmt": {
                    "condition": shadow_left,
                    "then_body": then_body,
                    "else_body": else_body
                }
            })
            return tmp_var

        if shadow_operator == "or":
            shadow_left = self.parse(left, statements)
            tmp_var = self.tmp_variable()

            then_body = []
            self.append_stmts(then_body, node, {
                "assign_stmt": {"target": tmp_var, "operand": "true"}
            })

            else_body = []
            shadow_right = self.parse(right, else_body)
            self.append_stmts(else_body, node, {
                "assign_stmt": {"target": tmp_var, "operand": shadow_right}
            })

            self.append_stmts(statements, node, {
                "if_stmt": {
                    "condition": shadow_left,
                    "then_body": then_body,
                    "else_body": else_body
                }
            })
            return tmp_var

        shadow_left = self.parse(left, statements)
        shadow_right = self.parse(right, statements)

        tmp_var = self.tmp_variable()
        self.append_stmts(statements, node, {
            "assign_stmt": {
                "target": tmp_var,
                "operator": shadow_operator,
                "operand": shadow_left,
                "operand2": shadow_right
            }
        })

        return tmp_var

    def unary_expression(self, node: Node, statements):
        operand = self.find_child_by_field(node, "operand")
        shadow_operand = self.parse(operand, statements)
        operator = self.find_child_by_field(node, "operator")
        shadow_operator = self.read_node_text(operator)

        tmp_var = self.tmp_variable()
        self.append_stmts(statements, node, {
            "assign_stmt": {
                "target": tmp_var, "operator": shadow_operator, "operand": shadow_operand
            }
        })
        return tmp_var

    def constant(self, node: Node, statements):
        return self.parse(node.children[0], statements)

    def parenthesized_expression(self, node: Node, statements):
        if len(node.named_children) > 0:
            expr = node.named_children[0]
            expr_name = self.parse(expr, statements)
            return expr_name

    """
    # need break
    switch (day) {
        case MONDAY:
        case FRIDAY:
        case SUNDAY:
            numLetters = 6;
            break;

    # no break
    numLetters = switch (day) {
            case MONDAY, FRIDAY, SUNDAY -> 6;
    """

    def switch_expression(self, node: Node, statements, parent_var=None):
        if node.type == "expression_statement":
            node = node.named_children[0]

        # self.print_tree(node)
        switch_block = self.find_child_by_field(node, "body")

        condition = self.find_child_by_field(node, "condition")
        shadow_condition = self.parse(condition, statements)

        switch_stmt_list = []

        self.append_stmts(statements, node, {"switch_stmt": {"condition": shadow_condition, "body": switch_stmt_list}})

        for child in switch_block.named_children:
            if self.is_comment(child):
                continue
            # default情况
            block_node = self.find_child_by_type(child, "block")
            new_body = []

            if self.read_node_text(child.children[0]) in ["_", "default"]:
                if child.named_child_count <= 1:
                    continue

                for index, stmt in enumerate(block_node.named_children):
                    return_var = self.parse(stmt, new_body)
                    if index == len(block_node.named_children) - 1 and (not self.is_statement(stmt)) and parent_var:
                        new_body.append({"assign_stmt": {"operand": return_var, "target": parent_var}})

                switch_stmt_list.append(self.add_col_row_info(child, {"default_stmt": {"body": new_body}}))

            else:

                label = child.named_children[0]
                shadow_condition = []
                target = None
                for case_condition in label.named_children:

                    if self.is_comment(case_condition):
                        continue
                    condition_value = self.parse(case_condition, [])

                    if self.is_identifier(case_condition) or case_condition.type == "type_identifier":
                        condition_value = self.read_node_text(case_condition)
                    elif case_condition.type == "enum_case":
                        condition_node = self.find_child_by_field(case_condition, "condition")
                        condition_value = self.read_node_text(condition_node)
                        target_node = self.find_child_by_field(case_condition, "target")
                        target = self.read_node_text(target_node)
                    if case_condition.type == "int_literal":
                        condition_value = int(condition_value)
                    shadow_condition.append(condition_value)

                for index, stmt in enumerate(block_node.named_children):
                    if stmt.type == "expression_statement":
                        return_var = self.parse(stmt.named_children[0], new_body)
                    else:
                        return_var = self.parse(stmt, new_body)
                    if index == len(block_node.named_children) - 1 and (not self.is_statement(stmt)) and parent_var:
                        new_body.append({"assign_stmt": {"operand": return_var, "target": parent_var}})

                if target:
                    switch_stmt_list.append(self.add_col_row_info(
                        child,
                        {"case_stmt": {"condition": shadow_condition, "body": new_body, "name": target}}
                    ))
                else:
                    switch_stmt_list.append(self.add_col_row_info(
                        child,
                        {"case_stmt": {"condition": shadow_condition, "body": new_body, }}
                    ))
        return parent_var

    def call_expression(self, node: Node, statements):
        name = self.find_child_by_field(node, "function_name")
        shadow_name = self.parse(name, statements)

        type_text = ""
        type_arguments = self.find_child_by_field(node, "type_arguments")
        if type_arguments:
            type_text = self.read_node_text(type_arguments)[1:-1]
        args = self.find_child_by_type(node, "argument_list")
        return self.handle_argument_list(node, args, statements, shadow_name, type_text)

    def handle_argument_list(self, node: Node, args, statements, shadow_name, type_text):
        positional_args = []
        named_args = {}
        previous_stmt = {}
        if args.named_child_count > 0:
            positional_arg_count = 0
            named_arg_count = -1
            for index, child in enumerate(args.named_children):
                if self.is_comment(child):
                    continue

                if child.type == "named_arg":
                    if named_arg_count == -1:
                        named_arg_count = index

                    name_node = self.find_child_by_field(child, "name")
                    value_node = self.find_child_by_field(child, "value")
                    param_name = self.read_node_text(name_node)
                    shadow_value = self.parse(value_node, statements)
                    named_args[param_name] = shadow_value
                else:
                    positional_arg_count = index
                    shadow_variable = self.parse(child, statements)
                    if shadow_variable:
                        positional_args.append(shadow_variable)

            if named_arg_count != -1:
                if named_arg_count < positional_arg_count:
                    self.syntax_error(
                        args,
                        f"named_arg({named_arg_count}) should appear after all positional arguments ({positional_arg_count})"
                    )

        tmp_return = self.tmp_variable()
        if named_args:
            named_args = str(named_args)
        else:
            named_args = None

        # print("positional_args", positional_args, "named_args", named_args)

        # if self.is_field_read_call(shadow_name):
        #     field_read_pos = self.field_read_target_to_pos[shadow_name]
        #     previous_stmt = statements[field_read_pos]
        #     receiver = previous_stmt["assign_stmt"]["operand"]
        #     field = previous_stmt["assign_stmt"]["operand2"]
        #     del statements[field_read_pos]
        #     self.field_read_pos = None
        receiver = self.find_child_by_field(node, "receiver")
        receiver = self.parse(receiver, statements) if receiver else None
        method_name = self.find_child_by_field(node, "function_name")
        shadow_method_name = self.read_node_text(method_name)

        if receiver:
            shadow_receiver = self.read_node_text(receiver) if isinstance(receiver, Node) else receiver
            pointer_stars = self.find_child_by_field(node, "pointer_stars")
            if pointer_stars is not None:
                for pointer_star in pointer_stars:
                    node_text = self.read_node_text(pointer_star)
                    shadow_receiver += node_text if node_text != 'full@' else '$'
            receiver_type_arguments = self.find_child_by_field(node, "receiver_type_arguments")
            if receiver_type_arguments:
                shadow_receiver += self.read_node_text(receiver_type_arguments)
            self.append_stmts(statements, args,
                              {"call_stmt": {
                                  "receiver": shadow_receiver,
                                  "target": tmp_return,
                                  "name": shadow_method_name,
                                  "type_parameters": type_text,
                                  "positional_args": positional_args,
                                  "named_args": named_args
                              }})

        else:
            self.append_stmts(statements, args,
                              {"call_stmt": {
                                  "target": tmp_return,
                                  "name": shadow_name,
                                  "type_parameters": type_text,
                                  "positional_args": positional_args,
                                  "named_args": named_args
                              }})

        return tmp_return

    def is_field_read_call(self, shadow_name):
        if shadow_name in self.field_read_target_to_pos:
            return True
        # if "field_read" not in previous_stmt:
        #     return False
        # if previous_stmt["field_read"]["target"] == shadow_name:
        #     return True
        return False

    def new_array(self, node: Node, statements):
        shadow_type = self.read_type_node_with_stars(node)

        tmp_var = self.tmp_variable()
        self.append_stmts(statements, node, {"new_array": {"type": shadow_type, "target": tmp_var}})

        value = self.find_child_by_field(node, "value")
        if value and value.named_child_count > 0:
            index = 0
            for child in value.named_children:
                if self.is_comment(child):
                    continue

                shadow_child = self.parse(child, statements)
                self.syntax_error(node, "new_array is called here")
                self.append_stmts(statements, node, {
                    "array_write": {"array": tmp_var, "index": str(index), "source": shadow_child}
                })
                index += 1

        return tmp_var

    def annotation(self, node: Node, statements):
        return self.read_node_text(node)

    def formal_parameter(self, node: Node, statements):
        child = self.find_child_by_type(node, "modifiers")
        modifiers = self.read_node_text(child).split()

        shadow_type = self.read_type_node_with_stars(node)

        # if "[]" in shadow_type:
        #     modifiers.append("array")

        name = self.find_child_by_field(node, "name")
        shadow_name = self.read_node_text(name)
        star_count = 0
        for char in shadow_name:
            if char == '*':
                star_count += 1
            else:
                break
        # 创建新字符串
        shadow_type = shadow_type + ('*' * star_count)  # 将星号添加到shadow_type的结尾
        shadow_name = shadow_name[star_count:].replace(" ", "")           # 移除name开头的星号
        modifiers = self.adjust_attrs(node, modifiers)
        if util.is_empty(shadow_type):
            self.syntax_error(node, "parameter data type can not be empty")
        self.append_stmts(statements, node, {
            "parameter_decl": {"attrs": modifiers, "data_type": shadow_type, "name": shadow_name}
        })

    def array_initializer(self, node: Node, statements):
        parent = node.parent
        # self.print_tree(parent)
        name_node = self.find_child_by_field(parent, "name")
        name = self.read_node_text(name_node)
        # if name == '':
        #     self.syntax_error(node, "array_initializer must have a name")

        condition = False
        value = self.read_node_text(node)
        for child in node.named_children:
            if child.type != "constant":
                condition = True
                break

        if condition:
            if node.named_child_count > 0:
                self.parse_array_initializer(node, name, statements)
            return None
        return value

    def parse_array_initializer(self, node: Node, name, statements):
        index = 0
        condition = False
        for item in node.named_children:
            if item.type != "constant":
                condition = True
                break
        if not condition:
            value = self.read_node_text(node)
            self.append_stmts(statements, node, {
                "assign_stmt": {"target": name, "operand": value}
            })
            return

        for item in node.named_children:
            if self.is_comment(item):
                continue
            if item.type == "array_initializer":
                tmp_var = self.tmp_variable()
                self.append_stmts(statements, item, {
                    "assign_stmt": {"target": tmp_var, "operator": "[]",
                                    "operand": name, "operand2": str(index)}
                })
                self.parse_array_initializer(item, tmp_var, statements)
            else:
                source = self.parse(item, statements)
                tmp_var = self.tmp_variable()
                self.append_stmts(statements, item, {
                    "assign_stmt": {"target": tmp_var, "operator": "[]",
                                    "operand": name, "operand2": str(index)}
                })
                self.append_stmts(statements, item, {
                    "assign_stmt": {"target": tmp_var, "operand": source}
                })
            index += 1

    def parse_tuple_initializer(self, node: Node, name, statements):
        index = 0
        condition = False
        for item in node.named_children:
            if item.type != "constant":
                condition = True
                break
        if not condition:
            value = self.read_node_text(node)
            self.append_stmts(statements, node, {
                "assign_stmt": {"target": name, "operand": value}
            })
            return

        for item in node.named_children:
            if self.is_comment(item):
                continue
            elif item.type == "tuple_initializer":
                tmp_var = self.tmp_variable()
                self.append_stmts(statements, item, {
                    "assign_stmt": {"target": tmp_var, "operator": "[]",
                                    "operand": name, "operand2": str(index)}
                })
                self.parse_tuple_initializer(item, tmp_var, statements)
            else:
                source = self.parse(item, statements)
                tmp_var = self.tmp_variable()
                self.append_stmts(statements, item, {
                    "assign_stmt": {"target": tmp_var, "operator": "[]",
                                    "operand": name, "operand2": str(index)}
                })
                self.append_stmts(statements, item, {
                    "assign_stmt": {"target": tmp_var, "operand": source}
                })
            index += 1

    def tuple_initializer(self, node: Node, statements):
        parent = node.parent
        # self.print_tree(parent)
        name_node = self.find_child_by_field(parent, "name")
        if not name_node:
            name_node = self.find_child_by_field(parent, "left")
        name = self.read_node_text(name_node)
        # if name == '':
        #     self.syntax_error(node, "tuple_initializer must have a name")

        condition = False
        value = self.read_node_text(node)
        for child in node.named_children:
            if child.type != "constant":
                condition = True
                break

        if condition:
            if node.named_child_count > 0:
                self.parse_tuple_initializer(node, name, statements)
            return None
        return value

    def dyn_expression(self, node: Node, statements):
        """
        解析 dyn 表达式并转换为指定格式的 new object 中间表示。

        :param node: dyn 表达式的语法树节点
        :param statements: 用于存储解析结果的语句列表
        :return: 解析后的 new object 中间表示
        """
        dyn_statements = []
        new_obj_info = {}
        # 获取临时变量作为 target
        tmp_var = self.tmp_variable()
        new_obj_info["target"] = tmp_var
        is_array = False

        # 获取 dyn 关键字后的第一个子节点
        type_node = self.find_child_by_field(node, "type")
        shadow_type = self.read_type_node_with_stars(node)
        new_obj_info["data_type"] = shadow_type
        if type_node:
            args = self.find_child_by_type(node, "argument_list")
            if args:
                value = self.handle_argument_list(node, args, dyn_statements, shadow_type, "")
                new_obj_info["init_value"] = value
                new_obj_info["data_type"] = ""
        else:
            expr_node = self.find_child_by_field(node, "expr")
            if expr_node:
                shadow_expr = self.parse(expr_node, dyn_statements)
                new_obj_info["init_value"] = shadow_expr
                new_obj_info["data_type"] = self.read_type_node_with_stars(expr_node)
            else:
                constant_node = self.find_child_by_field(node, "constant")
                if constant_node:

                    new_obj_info["init_value"] = self.parse(constant_node, dyn_statements)
                else:
                    array_node = self.find_child_by_field(node, "array_type")
                    is_array = True
                    if array_node:
                        new_obj_info["data_type"] = self.read_node_text(array_node)
                        new_obj_info["length"] = self.read_type_node_with_stars(array_node)
                        size_node = self.find_child_by_field(node, "array_size")
                        index = size_node.named_children[0]
                        shadow_index = self.parse(index, statements)
                        new_obj_info["length"] += shadow_index
                    else:
                        array_init_node = self.find_child_by_field(node, "array_constant")
                        if array_init_node:
                            new_obj_info["init_value"] = self.parse(array_init_node, dyn_statements)
        # print(new_obj_info)
        # for each_stmt in dyn_statements:
        #     for tmp_op in each_stmt:
        #         each_stmt[tmp_op]["invalid_llir"] = True
        statements.extend(dyn_statements)
        if is_array:
            self.append_stmts(statements, node, {"new_array": new_obj_info})
        else:
            self.append_stmts(statements, node, {"new_object": new_obj_info})

        return tmp_var

    def check_if_blocks_end_with_expr(self, node_list):
        for node in node_list:
            # print(node_list)
            if util.is_empty(node):
                return False
            if len(node.named_children) == 0:
                return False
            if node.named_children[-1].type not in (
                "constant",
                "identifier",
                "expression_statement",
                "assignment_statement"
            ):
                return False
        return True

    def process_block_and_add_assignment(self, node: Node, statements, parent_name):
        pass

    def check_express_statement_internal_type(self, stmt, target_type):
        if stmt:
            if stmt.type == target_type:
                return True
            child = stmt.named_children[0]
            if child and child.type == target_type:
                return True

        return False

    def if_expression(self, node: Node, statements, parent_name=None):
        if node.type == "expression_statement":
            node = node.named_children[0]
        true_block = self.find_child_by_field(node, "consequence")
        false_block_list = self.find_children_by_field(node, "alternative")

        is_parent_name_available = util.is_available(parent_name)

        all_blocks = [node]
        all_children_blocks = [true_block]
        if false_block_list:
            for each_part in false_block_list:
                child_block = self.find_child_by_field(each_part, "consequence")
                all_children_blocks.append(child_block)
                all_blocks.append(each_part)

        # print("++all_children_blocks:", node, all_children_blocks, all_blocks)

        if is_parent_name_available:
            if not self.check_if_blocks_end_with_expr(all_children_blocks):
                self.syntax_error(node, "if expression must end with expressions")

        for each_node in all_blocks:
            condition_part = self.find_child_by_field(each_node, "condition")
            block_body = self.find_child_by_field(each_node, "consequence")

            else_branch_flag = False
            if each_node.type in ("if_expression", "elif_clause"):
                if util.is_empty(condition_part):
                    self.syntax_error(each_node, "condition cannot be empty")

                then_body = []
                else_body = []
                condition_name = self.parse(condition_part, statements)
                self.append_stmts(statements, each_node, {
                    "if_stmt": {"condition": condition_name, "then_body": then_body, "else_body": else_body}
                })
            else:
                else_branch_flag = True
                then_body = statements

            size = len(block_body.named_children)
            for index in range(size):
                stmt = block_body.named_children[index]
                if index < size - 1:
                    self.parse(stmt, then_body)
                else:
                    if not is_parent_name_available:
                        self.parse(stmt, then_body)
                    else:

                        # print(stmt.type, stmt, then_body, is_parent_name_available)
                        if self.check_express_statement_internal_type(stmt, "if_expression"):
                            self.if_expression(stmt, then_body, parent_name)
                        elif self.check_express_statement_internal_type(stmt, "switch_expression"):
                            self.switch_expression(stmt, then_body, parent_name)
                        else:
                            result = self.parse(stmt, then_body)
                            self.append_stmts(then_body, stmt, {
                                "assign_stmt": {"target": parent_name, "operand": result}
                            })

            if else_branch_flag:
                break

            statements = else_body

        return parent_name

    def ok_statement(self, node: Node, statements):
        condition_part = self.find_child_by_field(node, "condition")
        true_part = self.find_child_by_field(node, "consequence")
        false_part = self.find_child_by_field(node, "alternative")
        true_body = []
        shadow_condition = self.parse(condition_part, statements)
        self.parse(true_part, true_body)
        if false_part:
            false_body = []
            # self.sync_tmp_variable(statements, false_body)
            self.parse(false_part, false_body)
            self.append_stmts(statements,
                              node, {"ok_stmt": {
                                  "condition": shadow_condition, "then_body": true_body, "else_body": false_body}}
                              )
        else:
            self.append_stmts(statements,
                              node, {"ok_stmt":
                                     {"condition": shadow_condition, "then_body": true_body}}
                              )

    def while_statement(self, node: Node, statements):
        condition = self.find_child_by_field(node, "condition")
        body = self.find_child_by_field(node, "body")

        condition_init = []
        shadow_condition = self.parse(condition, condition_init)

        do_body = []
        self.parse(body, do_body)

        self.append_stmts(statements, node, {
            "for_stmt": {
                "condition_prebody": condition_init,
                "condition": shadow_condition,
                "body": do_body,
            }
        })

    def do_while_statement(self, node: Node, statements):
        condition = self.find_child_by_field(node, "condition")
        body = self.find_child_by_field(node, "body")

        condition_init = []
        shadow_condition = self.parse(condition, condition_init)

        do_body = []
        self.parse(body, do_body)

        statements.extend(do_body)
        self.append_stmts(statements, node, {
            "for_stmt": {
                "condition_prebody": condition_init,
                "condition": shadow_condition,
                "body": do_body,
            }
        })

    def loop_statement(self, node: Node, statements):
        body = self.find_child_by_field(node, "body")

        do_body = []
        self.parse(body, do_body)

        self.append_stmts(statements, node, {
            "loop_stmt": {
                "body": do_body,
            }
        })

    def for_statement(self, node: Node, statements):
        for_spec = self.find_child_by_type(node, "for_spec")
        init_children = self.find_children_by_field(for_spec, "init")
        step_children = self.find_children_by_field(for_spec, "update")
        condition = self.find_child_by_field(for_spec, "condition")

        init_body = []
        condition_init = []
        step_body = []

        shadow_condition = self.parse(condition, condition_init)
        for init_child in init_children:
            self.parse(init_child, init_body)
        for step_child in step_children:
            self.parse(step_child, step_body)

        for_body = []

        block = self.find_child_by_field(node, "body")
        self.parse(block, for_body)
        self.append_stmts(statements, node, {
            "for_stmt": {
                "init_body": init_body,
                "condition": shadow_condition,
                "condition_prebody": condition_init,
                "update_body": step_body,
                "body": for_body
            }
        })

    def for_in_statement(self, node: Node, statements):
        # init_body = []
        # index = self.find_child_by_field(node, "index")
        # value = self.find_child_by_field(node, "value")
        # shadow_index = self.read_node_text(index)
        # shadow_value = self.read_node_text(value)

        # if shadow_value:
        # self.append_stmts(init_body, node, {
        #     "variable_decl": {
        #         "index": shadow_index,
        #     }}
        # )
        #     self.append_stmts(init_body, node, {
        #         "variable_decl": {
        #             "value": shadow_index,
        #         }}
        #     )
        # else:
        #     self.append_stmts(init_body, node, {
        #         "variable_decl": {
        #             "name": shadow_index,
        #         }}
        #     )
        value = self.find_child_by_field(node, "value")
        shadow_value = self.read_node_text(value)

        # self.append_stmts(statements, node, {
        #     "variable_decl": {
        #         "name": shadow_value,
        #     }}
        # )

        receiver = self.find_child_by_field(node, "receiver")
        shadow_receiver = self.parse(receiver, statements)

        for_body = []
        body = self.find_child_by_field(node, "body")
        self.parse(body, for_body)

        self.append_stmts(statements, node, {
            "forin_stmt": {
                "value": shadow_value,
                "receiver": shadow_receiver,
                "body": for_body
            }}
        )
        # if shadow_value:
        #     self.append_stmts(statements, node, {
        #         "forin_stmt": {
        #             "index": shadow_index,
        #             "value": shadow_value,
        #             "init_body": init_body,
        #             "receiver": shadow_receiver,
        #             "body": for_body
        #         }}
        #     )
        # else:
        #     self.append_stmts(statements, node, {
        #         "forin_stmt": {
        #             "name": shadow_index,
        #             "init_body": init_body,
        #             "receiver": shadow_receiver,
        #             "body": for_body
        #         }}
        #     )

    def break_statement(self, node: Node, statements):
        self.append_stmts(statements, node, {"break_stmt": {"name": ""}})

    def continue_statement(self, node: Node, statements):
        self.append_stmts(statements, node, {"continue_stmt": {"name": ""}})

    def block_statement(self, node: Node, statements):
        new_body = []
        for stmt in node.children:
            self.parse(stmt, new_body)
        self.append_stmts(statements, node, {"block": {"body": new_body}})

    def return_statement(self, node: Node, statements):
        shadow_name = ""
        if node.named_child_count > 0:
            name = node.named_children[0]
            if name.type == "if_expression":
                shadow_name = self.tmp_variable()
                self.if_expression(name, statements, shadow_name)
            elif name.type == "switch_expression":
                shadow_name = self.tmp_variable()
                self.switch_expression(name, statements, shadow_name)
            else:
                shadow_name = self.parse(name, statements)

        self.append_stmts(statements, node, {"return_stmt": {"name": shadow_name}})
        return shadow_name

    def with_statement(self, node: Node, statements):
        field_node = self.find_child_by_field(node, "receiver")
        body_node = self.find_child_by_field(node, "body")
        receiver = self.parse(field_node, statements)

        body = []
        for stmt in body_node:
            self.parse(stmt, body)

        self.append_stmts(statements, node, {
            "with_stmt": {
                "receiver": receiver,
                "body": body
            }
        })

    def variable_declaration(self, node: Node, statements):
        # self.print_tree(node)
        child = self.find_child_by_type(node, "modifiers")
        modifiers = self.read_node_text(child).split()

        shadow_type = self.read_type_node_with_stars(node)
        # print("shadow_type", shadow_type)

        name = ""
        declarators = self.find_children_by_field(node, "declarator")
        for child in declarators:
            name_node = self.find_child_by_field(child, "name")
            if not name_node:
                continue

            if name_node.type == "identifier" or name_node.type == "field_identifier":
                name = self.read_node_text(name_node)
            elif name_node.type == "mem_access":
                name = self.read_node_text(name_node)
                name = name.replace(' ', '')
                leading_asterisks = ""
                for char in name:
                    if char == '*':
                        leading_asterisks += char
                    else:
                        break
                shadow_type += leading_asterisks
                name = name[len(leading_asterisks):]
                # name = self.read_node_text(name_node.named_children[0])
            else:
                self.syntax_error(name_node, "variable name must not be empty")

            modifiers = self.adjust_attrs(node, modifiers)
            self.append_stmts(statements, node, {
                "variable_decl": {"attrs": modifiers, "data_type": shadow_type, "name": name}
            })

            value = self.find_child_by_field(child, "value")
            if value:
                if value.type == "switch_expression":
                    self.switch_expression(value, statements, name)
                elif value.type == "if_expression":
                    self.if_expression(value, statements, name)
                else:
                    shadow_value = self.parse(value, statements)
                    if shadow_value:
                        self.append_stmts(statements, node, {
                            "assign_stmt": {"target": name, "operand": shadow_value}
                        })

        return name

    def constant_declaration(self, node: Node, statements):
        name_node = self.find_child_by_field(node, "name")
        name = self.read_node_text(name_node)
        value = self.find_child_by_field(node, "value")
        shadow_value = self.parse(value, statements)
        self.append_stmts(statements, node, {
            "constant_decl": {"name": name, "value": shadow_value}
        })

    def struct_declaration(self, node: Node, statements):
        yian_node = {}
        modifiers_node = self.find_child_by_type(node, "modifiers")
        shadow_modifiers = self.read_node_text(modifiers_node)

        yian_node["attrs"] = self.adjust_attrs(node, shadow_modifiers)
        if self.find_child_by_field(node, "heap_flag"):
            if len(yian_node["attrs"]) > 0:
                yian_node["attrs"] += ",dyn"
            else:
                yian_node["attrs"] = "dyn"

        name_node = self.find_child_by_field(node, "name")
        shadow_name = self.read_node_text(name_node)

        yian_node["name"] = shadow_name
        yian_node["fields"] = []
        child = self.find_child_by_field(node, "type_parameters")
        if child:
            type_parameters = self.read_node_text(child)[1:-1]
            yian_node["type_parameters"] = type_parameters
        else:
            yian_node["type_parameters"] = []

        fields_nodes = self.find_child_by_type(node, "struct_body")

        for field_node in fields_nodes.named_children:
            if field_node.type != "field_declaration":
                continue
            field_modifiers = self.find_child_by_type(field_node, "modifiers")
            shadow_modifiers = self.read_node_text(field_modifiers)
            shadow_type = self.read_type_node_with_stars(field_node)
            if util.is_empty(shadow_type):
                self.syntax_error(field_node, "struct field data type can not be empty")
            field_name = self.find_child_by_field(field_node, "name")
            while field_name:
                if field_name.type == "identifier" or field_name.type == "field_identifier":
                    field_name = self.read_node_text(field_name)
                    break
                elif field_name.type == "mem_access":
                    shadow_type += '*'
                    field_name = field_name.named_children[0]
            # shadow_name = self.read_node_text(field_name)
            shadow_modifiers = self.adjust_attrs(field_node, shadow_modifiers)
            yian_node["fields"].append(self.add_col_row_info(node, {
                "variable_decl": {"attrs": shadow_modifiers, "data_type": shadow_type, "name": field_name}
            }))
        self.append_stmts(statements, node, {"struct_decl": yian_node})

    def tuple_struct(self, node: Node, statements):
        modifiers_node = self.find_child_by_type(node, "modifiers")
        shadow_modifiers = self.read_node_text(modifiers_node)
        yian_node = {}
        name_node = self.find_child_by_field(node, "name")
        shadow_name = self.read_node_text(name_node)
        yian_node["name"] = shadow_name
        yian_node["attrs"] = [shadow_modifiers]
        if self.find_child_by_field(node, "heap_flag"):
            yian_node["attrs"].append("dyn")
        yian_node["fields"] = []
        fields_nodes = node.named_children[1:]
        for field_node in fields_nodes:
            # self.parse(field_node, yian_node)
            tmp_var = self.tmp_variable()
            field_data_type = self.read_node_text(field_node)
            if util.is_empty(field_data_type):
                self.syntax_error(field_node, "tuple struct field data type can not be empty")
            yian_node["fields"].append(self.add_col_row_info(
                node,
                {"variable_decl": {'attrs': YIAN_ATTRS.PUBLIC, "data_type": field_data_type, "name": tmp_var}}
            ))
        self.append_stmts(statements, node, {"tuple_decl": yian_node})

    def union_declaration(self, node: Node, statements):
        yian_node = {}
        name_node = self.find_child_by_field(node, "name")
        shadow_name = self.read_node_text(name_node)
        yian_node["attrs"] = []
        if self.find_child_by_field(node, "heap_flag"):
            yian_node["attrs"].append("dyn")
        child = self.find_child_by_field(node, "type_parameters")
        type_parameters = self.read_node_text(child)[1:-1]
        yian_node["type_parameters"] = type_parameters
        yian_node["name"] = shadow_name
        yian_node["fields"] = []
        fields_nodes = self.find_child_by_type(node, "struct_body")

        for field_node in fields_nodes.named_children:
            field_modifiers = self.find_child_by_type(field_node, "modifiers")
            shadow_modifiers = self.read_node_text(field_modifiers)
            shadow_type = self.read_type_node_with_stars(field_node)
            field_name = self.find_child_by_field(field_node, "name")
            if field_name == None:
                continue
            while field_name:
                if field_name.type == "identifier" or field_name.type == "field_identifier":
                    field_name = self.read_node_text(field_name)
                    break
                elif field_name.type == "mem_access":
                    shadow_type += '*'
                    field_name = field_name.named_children[0]
            # shadow_name = self.read_node_text(field_name)
            shadow_modifiers = self.adjust_attrs(node, shadow_modifiers)
            yian_node["fields"].append(self.add_col_row_info(node, {"variable_decl": {"attrs": shadow_modifiers, "data_type": shadow_type, "name": field_name}}))
        self.append_stmts(statements, node, {"union_decl": yian_node})

    def trait_declaration(self, node: Node, statements):
        yian_node = {}
        modifiers_node = self.find_child_by_type(node, "modifiers")
        shadow_modifiers = self.read_node_text(modifiers_node)
        yian_node["attrs"] = self.adjust_attrs(node, shadow_modifiers)

        yian_node["body"] = []
        name_node = self.find_child_by_field(node, "name")
        yian_node["name"] = self.read_node_text(name_node)
        child = self.find_child_by_field(node, "type_parameters")
        type_parameters = self.read_node_text(child)[1:-1]
        yian_node["type_parameters"] = type_parameters
        for stmt in node.named_children:
            if stmt.type == "method_declaration":
                self.parse(stmt, yian_node["body"])

        # set attrs to public
        for method in yian_node["body"]:
            if "method_header" in method:
                method["method_header"]["attrs"] = YIAN_ATTRS.PUBLIC
            elif "method_decl" in method:
                method["method_decl"]["attrs"] = YIAN_ATTRS.PUBLIC

        self.append_stmts(statements, node, {"trait_decl": yian_node})

    def enum_declaration(self, node: Node, statements):
        """
        解析枚举声明并转换为中间表示。

        :param node: 枚举声明的语法树节点
        :param statements: 用于存储解析结果的语句列表
        """
        # 获取枚举名称
        modifiers_node = self.find_child_by_type(node, "modifiers")
        modifiers = self.read_node_text(modifiers_node)
        modifiers = self.adjust_attrs(node, modifiers)

        name_node = self.find_child_by_field(node, "name")
        enum_name = self.read_node_text(name_node)

        # 获取类型参数
        type_params_node = self.find_child_by_field(node, "type_parameters")
        type_parameters = self.read_node_text(type_params_node)[1:-1] if type_params_node else ""

        variants = []
        # 查找枚举变体节点
        variant_nodes = self.find_children_by_type(node, "enum_variant")
        for variant_node in variant_nodes:
            variant_node = variant_node.named_children[0]
            variant_type = variant_node.type
            variant = None
            if variant_type == "simple_enum_variant":
                variant = self._parse_simple_enum_variant(variant_node)
            elif variant_type == "enum_variant_with_value":
                variant = self._parse_enum_variant_with_value(variant_node)
            elif variant_type == "enum_variant_struct":
                variant = self._parse_enum_variant_struct(variant_node)

            if variant:
                variants.append(variant)

        enum_ir = {
            "enum_decl": {
                "attrs": modifiers,
                "name": enum_name,
                "type_parameters": type_parameters,
                "variants": variants
            }
        }

        self.append_stmts(statements, node, enum_ir)

    def _parse_simple_enum_variant(self, node: Node):
        if len(self.read_node_text(node)) == 0:
            return None
        """解析简单枚举变体。"""
        name = self.read_node_text(node)

        return self.add_col_row_info(node, {
            "variant_decl":
            {"variant_type": "simple",
             "name": name}
        })

    def _parse_enum_variant_with_value(self, node: Node):
        """解析带值的枚举变体。"""
        name_node = self.find_child_by_field(node, "name")
        value_node = self.find_child_by_field(node, "value")
        name = self.read_node_text(name_node)
        value = self.parse(value_node, [])

        return self.add_col_row_info(node, {
            "variant_decl":
            {"variant_type": "with_value",
             "name": name,
             "value": value}
        })

    def _parse_enum_variant_struct(self, node: Node):
        """解析结构体形式的枚举变体。"""
        name_node = self.find_child_by_field(node, "name")
        name = self.read_node_text(name_node)

        fields = []
        field_nodes = self.find_children_by_type(node, "enum_field")
        for each_field in field_nodes:
            field_name = self.read_node_text(each_field)
            count = field_name.count('*')
            type_node = self.find_child_by_field(each_field, "type")
            field_name_node = self.find_child_by_field(each_field, "name")
            field_type = self.read_node_text(type_node)
            field_name = self.read_node_text(field_name_node)
            field_type += '*' * count
            self.append_stmts(fields, each_field, {
                "variable_decl":
                {"data_type": field_type,
                 "name": field_name}
            })

        return self.add_col_row_info(node, {
            "variant_decl":
            {"variant_type": "struct",
             "name": name,
             "fields": fields}
        })

    def obtain_literal_handler(self, node: Node):
        return self.LITERAL_MAP.get(node.type, None)

    def check_expression_handler(self, node: Node):
        return self.EXPRESSION_HANDLER_MAP.get(node.type, None)

    def check_declaration_handler(self, node: Node):
        return self.DECLARATION_HANDLER_MAP.get(node.type, None)

    def check_statement_handler(self, node: Node):
        return self.STATEMENT_HANDLER_MAP.get(node.type, None)

    def is_literal(self, node: Node):
        return self.obtain_literal_handler(node) is not None

    def is_expression(self, node: Node):
        return self.check_expression_handler(node) is not None

    def is_statement(self, node: Node):
        return self.check_statement_handler(node) is not None

    def is_declaration(self, node: Node):
        return self.check_declaration_handler(node) is not None

    def literal(self, node: Node, statements, replacement):
        handler = self.obtain_literal_handler(node)
        return handler(node, statements, replacement)

    def expression(self, node: Node, statements):
        handler = self.check_expression_handler(node)
        return handler(node, statements)

    def declaration(self, node: Node, statements):
        handler = self.check_declaration_handler(node)
        return handler(node, statements)

    def statement(self, node: Node, statements):
        # self.print_tree(node)
        # print(node.type)
        handler = self.check_statement_handler(node)
        return handler(node, statements)

    def dot_call_expression(self, node: Node, statements, parent_var):

        # SomeClass.super.<ArgType>genericMethod()
        name = self.find_child_by_field(node, "function_name")

        tmp_var = self.tmp_variable()
        self.syntax_error(node, "dot_call_expression is called here")
        self.append_stmts(statements, node, {
            "field_read": {
                "target": tmp_var, "receiver_object": parent_var, "field": self.read_node_text(name),
            }
        })

        # print("dot_call_expr", node)
        args = self.find_child_by_type(node, "argument_list")
        # print("dot_call_expr", args)
        return self.handle_argument_list(node, args, statements, tmp_var, "")

    def parse(self, node: Node, statements=[], replacement=[]):
        """
        主解析入口：
        处理流程：
        1. 调试输出AST树（配置开启时）
        2. 空节点直接返回
        3. 过滤注释节点
        4. 根据节点类型分发处理：
           - 标识符
           - 字面量
           - 声明语句
           - 控制语句
           - 表达式
        5. 递归处理子节点
        """
        if self.options.debug and self.options.print_stmts and not self.printed_flag:
            # self.print_tree(node)
            self.printed_flag = True

        if not node:
            return ""

        if self.is_comment(node):
            return

        if self.is_identifier(node):
            return self.read_node_text(node)

        if self.is_literal(node):
            result = self.literal(node, statements, replacement)
            if result is None:
                return self.read_node_text(node)
            return result

        if self.is_declaration(node):
            return self.declaration(node, statements)

        if self.is_statement(node):
            return self.statement(node, statements)

        if self.is_expression(node):
            return self.expression(node, statements)

        last_node_result = None
        node_children = node.named_children
        size = len(node_children)

        for i in range(size):
            current_node = node_children[i]

            if (
                current_node.type == "expression_statement"
                and len(current_node.named_children) == 1
                and current_node.named_children[0].type == "dot_call_expression"
            ):
                last_node_result = self.dot_call_expression(current_node.named_children[0], statements, last_node_result)
            else:
                last_node_result = self.parse(current_node, statements, replacement)

        return last_node_result
