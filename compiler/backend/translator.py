from compiler.backend.utils.context import LLVMCtx
from compiler.backend.utils.ll_value import LLFunction, LLValue
from compiler.config.defs import IRHandlerMap, TypeId
from compiler.utils import ty
from compiler.utils.errors import CompilerError
from compiler.utils.IR import DefPoint, Operator, Variable, VariableSymbol
from compiler.utils.IR import cgir as cir
from compiler.utils.ty import TypeSpace


class LowLevelIRTranslator:
    def __init__(self, ctx: LLVMCtx):
        self.__ctx = ctx

        self.__code_block_handlers: IRHandlerMap[cir.CheckedGIR] = {
            cir.VarDecl: lambda stmt: None,

            cir.BinaryOpAssign: self.__binary_op_assign,
            cir.UnaryOpAssign: self.__unary_op_assign,
            cir.Assign: self.__assign,
            cir.FieldAccess: self.__field_access,
            cir.Cast: self.__cast,
            cir.FuncCall: self.__func_call,
            cir.MethodCall: self.__method_call,
            cir.StaticMethodCall: self.__static_method_call,
            cir.Invoke: self.__invoke,
            cir.StructConstruct: self.__struct_construct,
            cir.VariantConstruct: self.__variant_construct,

            cir.If: self.__if,
            cir.For: self.__for,
            cir.Loop: self.__loop,
            cir.Match: self.__match,
            cir.Block: self.__block,

            cir.Break: self.__break,
            cir.Continue: self.__continue,
            cir.Return: self.__return,

            cir.Delete: self.__delete,
            cir.DynType: self.__dyn_type,
            cir.DynValue: self.__dyn_value,
            cir.DynArray: self.__dyn_array,

            cir.Assert: self.__assert,
            cir.Read: self.__read,
            cir.Write: self.__write_fd,
            cir.Open: self.__open,
            cir.Close: self.__close,
            cir.Panic: self.__panic,
            cir.SizeOf: self.__size_of,
            cir.BitCast: self.__bitcast,
            cir.ByteOffset: self.__byte_offset,
            cir.MemCopy: self.__mem_copy,
        }

    def run(self, def_points: set[DefPoint]):
        # alloc all function/method objects first
        for def_point in def_points:
            self.__register_def_point(def_point)

        # translate all def points
        for def_point in def_points:
            self.__translate_def_point(def_point)

        # insert ret void to functions/methods without explicit return
        self.__ctx.ir_finalize_functions()
        return self

    def export(self, path: str) -> None:
        self.__ctx.ir_export(path)

    def __register_def_point(self, def_point: DefPoint) -> None:
        def_ty = self.__ctx.ty_get(def_point.type_id)
        match def_ty:
            case ty.FunctionType():
                self.__ctx.ir_alloc_func_obj(def_point, def_ty)
            case ty.MethodType():
                self.__ctx.ir_alloc_method_obj(def_point, def_ty)
            case _:
                raise CompilerError(f"Unsupported def point type for registration: {def_ty}")

    def __translate_def_point(self, def_point: DefPoint) -> None:
        self.__ctx.set_def_info(def_point)
        def_ty = self.__ctx.ty_get(def_point.type_id)
        match def_ty:
            case ty.FunctionType():
                self.__translate_function(def_point, def_ty)
            case ty.MethodType():
                self.__translate_method(def_point, def_ty)
            case _:
                raise CompilerError(f"Unsupported def point type for translation: {def_ty}")

    def __translate_function(self, def_point: DefPoint, func_ty: ty.FunctionType) -> None:
        self.__alloc_locals(def_point)
        self.__ctx.process_block(def_point.root_block_id, self.__code_block_handlers)

    def __translate_method(self, def_point: DefPoint, method_ty: ty.MethodType) -> None:
        self.__alloc_locals(def_point)
        self.__ctx.process_block(def_point.root_block_id, self.__code_block_handlers)

    def __alloc_locals(self, def_point: DefPoint) -> None:
        """
        Allocate stack space for all local variables and temporaries found in the DefPoint's symbol table.
        """
        for symbol in def_point.symbol_table.values():
            if isinstance(symbol, VariableSymbol):
                assert symbol.type_id is not None
                if symbol.type_id == TypeSpace.void_id:
                    continue
                var = cir.Variable(
                    symbol_id=symbol.symbol_id,
                    name=symbol.name,
                    type_id=symbol.type_id,
                    lvalue=symbol.lvalue
                )
                self.__ctx.ir_alloc_var(var)

    # ====== Code Block Handlers ======

    def __binary_op_assign(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.BinaryOpAssign)

        if stmt.operator.is_membership:
            self.__membership_op_assign(stmt)
            return

        if stmt.operator == Operator.Index:
            self.__index_op_assign(stmt)
            return

        # handle other trivial binary ops
        # get lhs and rhs values
        lhs_value = self.__ctx.ir_value(stmt.lhs)
        rhs_value = self.__ctx.ir_value(stmt.rhs)

        # perform the operation and get the result
        res_value = self.__ctx.ir_binary_op(stmt.operator, lhs_value, rhs_value)

        # store the result to target
        self.__ctx.ir_store(stmt.target, res_value)

    def __membership_op_assign(self, stmt: cir.BinaryOpAssign) -> None:
        raise NotImplementedError()

    def __index_op_assign(self, stmt: cir.BinaryOpAssign) -> None:
        # lhs of [] must be a variable
        assert isinstance(stmt.lhs, Variable)

        rhs_ty = self.__ctx.ty_get(stmt.rhs.type_id)

        if isinstance(rhs_ty, ty.IntType):
            # integer index access
            # get address of the element
            element_addr = self.__ctx.ir_index_access(stmt.lhs, stmt.rhs)

            # store the element address(lvalue) to target
            self.__ctx.ir_lvalue_store(stmt.target, element_addr)
        else:
            # range index access
            # get value of the slice
            slice_value = self.__ctx.ir_range_index_access(stmt.lhs, stmt.rhs)

            # store the slice value to target
            self.__ctx.ir_store(stmt.target, slice_value)

    def __unary_op_assign(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.UnaryOpAssign)

        if stmt.operator == Operator.Ampersand:
            self.__address_of_op_assign(stmt)
            return

        if stmt.operator == Operator.Star:
            self.__dereference_op_assign(stmt)
            return

        # handle other trivial unary ops
        # get operand value
        operand_value = self.__ctx.ir_value(stmt.operand)

        # perform the operation and get the result
        res_value = self.__ctx.ir_unary_op(stmt.operator, operand_value)

        # store the result to target
        self.__ctx.ir_store(stmt.target, res_value)

    def __address_of_op_assign(self, stmt: cir.UnaryOpAssign) -> None:
        assert isinstance(stmt.operand, Variable)

        # get address of the variable
        var_addr = self.__ctx.ir_address(stmt.operand)

        # store the address to target
        self.__ctx.ir_store(stmt.target, var_addr)

    def __dereference_op_assign(self, stmt: cir.UnaryOpAssign) -> None:
        assert isinstance(stmt.operand, Variable)

        # get address of the variable
        var_addr = self.__ctx.ir_value(stmt.operand)

        # store the value(lvalue) at the address to target
        self.__ctx.ir_lvalue_store(stmt.target, var_addr)

    def __assign(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Assign)

        value = self.__ctx.ir_value(stmt.value)
        casted_value = self.__ctx.ir_assign_cast(stmt.target.type_id, value)
        self.__ctx.ir_store(stmt.target, casted_value)

    def __field_access(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.FieldAccess)

        # get field address
        field_addr = self.__ctx.ir_field_access(stmt.receiver, stmt.field)

        # store the field address(lvalue) to target
        self.__ctx.ir_lvalue_store(stmt.target, field_addr)

    def __cast(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Cast)

        # get value to be casted
        value = self.__ctx.ir_value(stmt.value)

        # perform cast
        casted_value = self.__ctx.ir_cast(value, stmt.to_type)

        # store the casted value to target
        self.__ctx.ir_store(stmt.target, casted_value)

    def __func_call(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.FuncCall)

        func_type = self.__ctx.ty_get(stmt.func).expect_function()
        return_type = self.__ctx.ty_get(func_type.return_type(self.__ctx.ty_instantiate))

        # determine if the function(in llvm) returns a value
        has_ret = not isinstance(return_type, (ty.VoidType, ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType))

        # determine if the function uses sret
        uses_sret = isinstance(return_type, (ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType)) and stmt.target is not None

        arg_values: list[LLValue] = []
        # handle sret
        if uses_sret:
            assert stmt.target is not None
            sret_addr = self.__ctx.ir_address(stmt.target)
            arg_values.append(sret_addr)

        # handle other arguments
        param_types = func_type.parameter_types(self.__ctx.ty_instantiate)
        for arg, param_type in zip(stmt.arguments, param_types):
            arg_value = self.__ctx.ir_value(arg)
            arg_value = self.__ctx.ir_assign_cast(param_type, arg_value)
            arg_values.append(arg_value)

        # handle func obj
        func_obj = self.__ctx.ir_func_obj(stmt.func)

        # perform the function call
        if has_ret and stmt.target is not None and not uses_sret:
            res_value = self.__ctx.ir_func_call(func_obj, arg_values)
            assert res_value is not None
            self.__ctx.ir_store(stmt.target, res_value)
        else:
            self.__ctx.ir_func_call(func_obj, arg_values)

    def __method_call(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.MethodCall)

        method_type = self.__ctx.ty_get(stmt.method).expect_method()
        return_type = self.__ctx.ty_get(method_type.return_type(self.__ctx.ty_instantiate))

        # determine if the method(in llvm) returns a value
        has_ret = not isinstance(return_type, (ty.VoidType, ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType))

        # determine if the method uses sret
        uses_sret = isinstance(return_type, (ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType)) and stmt.target is not None

        arg_values: list[LLValue] = []
        # handle sret
        if uses_sret:
            assert stmt.target is not None
            sret_addr = self.__ctx.ir_address(stmt.target)
            arg_values.append(sret_addr)

        # handle self parameter
        assert isinstance(stmt.receiver, Variable)  # TODO: temporal solution
        receiver_addr = self.__ctx.ir_address(stmt.receiver)
        arg_values.append(receiver_addr)

        # handle other arguments
        param_types = method_type.parameter_types(self.__ctx.ty_instantiate)
        for arg, param_type in zip(stmt.arguments, param_types):
            arg_value = self.__ctx.ir_value(arg)
            arg_value = self.__ctx.ir_assign_cast(param_type, arg_value)
            arg_values.append(arg_value)

        # handle method obj
        method_obj = self.__ctx.ir_func_obj(stmt.method)

        # perform the method call
        if has_ret and stmt.target is not None:
            res_value = self.__ctx.ir_func_call(method_obj, arg_values)
            assert res_value is not None
            self.__ctx.ir_store(stmt.target, res_value)
        else:
            self.__ctx.ir_func_call(method_obj, arg_values)

    def __static_method_call(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.StaticMethodCall)

        method_type = self.__ctx.ty_get(stmt.method).expect_method()
        return_type = self.__ctx.ty_get(method_type.return_type(self.__ctx.ty_instantiate))

        # determine if the method(in llvm) returns a value
        has_ret = not isinstance(return_type, (ty.VoidType, ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType))

        # determine if the method uses sret
        uses_sret = isinstance(return_type, (ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType)) and stmt.target is not None

        arg_values: list[LLValue] = []
        # handle sret
        if uses_sret:
            assert stmt.target is not None
            sret_addr = self.__ctx.ir_address(stmt.target)
            arg_values.append(sret_addr)

        # handle other arguments
        param_types = method_type.parameter_types(self.__ctx.ty_instantiate)
        for arg, param_type in zip(stmt.arguments, param_types):
            arg_value = self.__ctx.ir_value(arg)
            arg_value = self.__ctx.ir_assign_cast(param_type, arg_value)
            arg_values.append(arg_value)

        # handle method obj
        method_obj = self.__ctx.ir_func_obj(stmt.method)

        # perform the method call
        if has_ret and stmt.target is not None:
            res_value = self.__ctx.ir_func_call(method_obj, arg_values)
            assert res_value is not None
            self.__ctx.ir_store(stmt.target, res_value)
        else:
            self.__ctx.ir_func_call(method_obj, arg_values)

    def __invoke(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Invoke)

        function_pointer_type = self.__ctx.ty_get(stmt.invoked.type_id).expect_function_pointer()
        return_type = self.__ctx.ty_get(function_pointer_type.return_type)

        # determine if the function pointer(in llvm) returns a value
        has_ret = not isinstance(return_type, (ty.VoidType, ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType))

        # determine if the function pointer uses sret
        uses_sret = isinstance(return_type, (ty.StructType, ty.ArrayType, ty.EnumType, ty.TupleType)) and stmt.target is not None

        arg_values: list[LLValue] = []
        # handle sret
        if uses_sret:
            assert stmt.target is not None
            sret_addr = self.__ctx.ir_address(stmt.target)
            arg_values.append(sret_addr)

        # handle other arguments
        param_types = function_pointer_type.parameter_types
        for arg, param_type in zip(stmt.arguments, param_types):
            arg_value = self.__ctx.ir_value(arg)
            arg_value = self.__ctx.ir_assign_cast(param_type, arg_value)
            arg_values.append(arg_value)

        # handle function pointer value
        func_ptr_value = self.__ctx.ir_value(stmt.invoked)
        func = LLFunction(func_ptr_value.type_id, func_ptr_value.value)

        # perform the function pointer invoke
        if has_ret and stmt.target is not None:
            res_value = self.__ctx.ir_func_call(func, arg_values)
            assert res_value is not None
            self.__ctx.ir_store(stmt.target, res_value)
        else:
            self.__ctx.ir_func_call(func, arg_values)

    def __get_field_type_at_path(self, struct_type_id: TypeId, field_path: list[int]) -> TypeId:
        """
        Get the type ID of the field at the given path in a struct type.
        """
        current_type_id = struct_type_id
        for field_index in field_path:
            current_type = self.__ctx.ty_get(current_type_id).expect_struct()
            fields = sorted(current_type.struct_def.fields.values(), key=lambda f: f.index)
            if field_index >= len(fields):
                raise CompilerError(f"Invalid field index {field_index} in struct {current_type.name}")
            field = fields[field_index]
            generic_args = current_type.generic_args
            substs = dict(zip(current_type.struct_def.generics, generic_args))
            current_type_id = self.__ctx.ty_instantiate(field.type_id, substs)
        return current_type_id

    def __struct_construct(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.StructConstruct)

        struct_type = self.__ctx.ty_get(stmt.struct_type).expect_struct()

        field_values: dict[str, LLValue] = {}
        for field_name, field_value in stmt.field_values.items():
            field = struct_type.get_field_by_name(field_name, self.__ctx.ty_instantiate)
            assert field is not None
            value = self.__ctx.ir_value(field_value)
            casted_value = self.__ctx.ir_assign_cast(field.type_id, value)
            field_values[field_name] = casted_value

        target_addr = self.__ctx.ir_address(stmt.target)
        self.__ctx.ir_struct_construct(struct_type, field_values, target_addr)

    def __variant_construct(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.VariantConstruct)

        enum_type = self.__ctx.ty_get(stmt.enum_type).expect_enum()

        if self.__ctx.null_pointer_optimizable(stmt.enum_type):
            opt_type_id = enum_type.generic_args[0]
            opt_type = self.__ctx.ty_get(opt_type_id)
            target_addr = self.__ctx.ir_address(stmt.target)

            if isinstance(opt_type, ty.PointerType):
                if stmt.variant.name == "None":
                    null_ptr = self.__ctx.ir_niche(opt_type_id)
                    self.__ctx.ir_store_addr(target_addr, null_ptr)
                elif stmt.variant.name == "Some":
                    if stmt.field_values is not None and len(stmt.field_values) > 0:
                        payload_value_name = list(stmt.field_values.keys())[0]
                        payload_value = self.__ctx.ir_value(stmt.field_values[payload_value_name])
                        casted_value = self.__ctx.ir_assign_cast(opt_type_id, payload_value)
                        self.__ctx.ir_store_addr(target_addr, casted_value)
                    else:
                        raise CompilerError("Some variant must have a payload value")
                return
            elif isinstance(opt_type, ty.StructType):
                if stmt.variant.name == "None":
                    field_path = self.__ctx.get_npo_field_path(opt_type_id)
                    nullable_field_type_id = self.__get_field_type_at_path(opt_type_id, field_path)
                    null_value = self.__ctx.ir_niche(nullable_field_type_id)
                    self.__ctx.ir_store_at_field_path(opt_type_id, target_addr, field_path, null_value)
                elif stmt.variant.name == "Some":
                    if stmt.field_values is not None:
                        if "val" not in stmt.field_values:
                            raise CompilerError("Some variant payload must have 'val' field")

                        val_typed_value = stmt.field_values["val"]
                        val_value = self.__ctx.ir_value(val_typed_value)
                        val_value = self.__ctx.ir_assign_cast(opt_type_id, val_value)

                        self.__ctx.ir_store_addr(target_addr, val_value)
                    else:
                        raise CompilerError("Some variant must have field values for struct payload")
                return

        target_addr = self.__ctx.ir_address(stmt.target)

        # set discriminant
        discriminant_addr = self.__ctx.ir_enum_discriminant_addr(enum_type, target_addr)
        discriminant_value = self.__ctx.ir_constant(TypeSpace.i32_id, stmt.variant.discriminant)
        self.__ctx.ir_store_addr(discriminant_addr, discriminant_value)

        # set payload if exists
        if stmt.field_values is not None:
            assert stmt.variant.payload is not None
            payload_type = self.__ctx.ty_get(stmt.variant.payload).expect_struct()

            field_values: dict[str, LLValue] = {}
            for field_name, field_value in stmt.field_values.items():
                field = payload_type.get_field_by_name(field_name, self.__ctx.ty_instantiate)
                assert field is not None
                value = self.__ctx.ir_value(field_value)
                casted_value = self.__ctx.ir_assign_cast(field.type_id, value)
                field_values[field_name] = casted_value

            payload_addr = self.__ctx.ir_enum_payload_address(enum_type, target_addr, stmt.variant)
            self.__ctx.ir_struct_construct(payload_type, field_values, payload_addr)

    def __if(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.If)

        cond_value = self.__ctx.ir_value(stmt.condition)

        self.__ctx.ir_enter_block()

        # TODO: optimize branching for constant conditions
        if stmt.else_body is None:
            then_bb = self.__ctx.ir_generate_bb()
            end_bb = self.__ctx.ir_end_bb()
            self.__ctx.ir_cbranch(cond_value, then_bb, end_bb)

            self.__ctx.ir_set_current_bb(then_bb)
            self.__ctx.process_block(stmt.then_body, self.__code_block_handlers)
            self.__ctx.ir_branch_end_bb()

        else:
            then_bb = self.__ctx.ir_generate_bb()
            else_bb = self.__ctx.ir_generate_bb()
            self.__ctx.ir_cbranch(cond_value, then_bb, else_bb)

            self.__ctx.ir_set_current_bb(then_bb)
            self.__ctx.process_block(stmt.then_body, self.__code_block_handlers)
            self.__ctx.ir_branch_end_bb()

            self.__ctx.ir_set_current_bb(else_bb)
            self.__ctx.process_block(stmt.else_body, self.__code_block_handlers)
            self.__ctx.ir_branch_end_bb()

        self.__ctx.ir_exit_ctx()

    def __for(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.For)

        start_bb = self.__ctx.ir_generate_bb()
        loop_bb = self.__ctx.ir_generate_bb()
        update_bb = self.__ctx.ir_generate_bb()

        self.__ctx.ir_enter_loop(update_bb)

        if stmt.init_body is not None:
            self.__ctx.process_block(stmt.init_body, self.__code_block_handlers)
        self.__ctx.ir_branch(start_bb)

        self.__ctx.ir_set_current_bb(start_bb)
        if stmt.condition_prebody is not None:
            self.__ctx.process_block(stmt.condition_prebody, self.__code_block_handlers)
        cond_value = self.__ctx.ir_value(stmt.condition)
        # Optimized branching for constant conditions
        if cond_value.is_constant_true:
            self.__ctx.ir_branch(loop_bb)
        elif cond_value.is_constant_false:
            self.__ctx.ir_branch_end_bb()
        else:
            end_bb = self.__ctx.ir_end_bb()
            self.__ctx.ir_cbranch(cond_value, loop_bb, end_bb)

        self.__ctx.ir_set_current_bb(loop_bb)
        self.__ctx.process_block(stmt.body, self.__code_block_handlers)
        self.__ctx.ir_continue()

        self.__ctx.ir_set_current_bb(update_bb)
        if stmt.update_body is not None:
            self.__ctx.process_block(stmt.update_body, self.__code_block_handlers)
        self.__ctx.ir_branch(start_bb)

        self.__ctx.ir_exit_ctx()

    def __loop(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Loop)

        start_bb = self.__ctx.ir_generate_bb()
        self.__ctx.ir_enter_loop(start_bb)

        self.__ctx.ir_branch(start_bb)

        self.__ctx.ir_set_current_bb(start_bb)
        self.__ctx.process_block(stmt.body, self.__code_block_handlers)
        self.__ctx.ir_continue()

        self.__ctx.ir_exit_ctx()

    def __match(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Match)

        match_type = self.__ctx.ty_get(stmt.match_value.type_id)

        match match_type:
            case ty.IntType():
                self.__match_int(stmt)
            case ty.CharType():
                self.__match_char(stmt)
            case ty.StrType():
                self.__match_str(stmt)
            case ty.EnumType():
                self.__match_enum(stmt)
            case _:
                raise CompilerError(f"Unsupported match type for translation: {match_type}")

    def __match_int(self, stmt: cir.Match) -> None:
        match_value = self.__ctx.ir_value(stmt.match_value)
        self.__ctx.ir_enter_switch(match_value)

        def handle_case(case_stmt: cir.CheckedGIR) -> None:
            assert isinstance(case_stmt, cir.IntCase)

            case_bb = self.__ctx.ir_generate_bb()

            for case_value in case_stmt.case_values:
                case_const = self.__ctx.ir_constant(match_value.type_id, case_value)
                self.__ctx.ir_add_case(case_const, case_bb)

            self.__ctx.ir_set_current_bb(case_bb)
            self.__ctx.process_block(case_stmt.body, self.__code_block_handlers)
            self.__ctx.ir_branch_end_bb()

        handlers: IRHandlerMap[cir.CheckedGIR] = {
            cir.IntCase: handle_case,
            cir.DefaultCase: self.__default,
        }

        self.__ctx.process_block(stmt.body, handlers)

        if not stmt.has_default:
            self.__ctx.ir_set_default_bb()
            self.__ctx.ir_unreachable()

        self.__ctx.ir_exit_ctx()

    def __default(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.DefaultCase)

        self.__ctx.ir_set_default_bb()
        if stmt.body is not None:
            self.__ctx.process_block(stmt.body, self.__code_block_handlers)
        self.__ctx.ir_branch_end_bb()

    def __match_char(self, stmt: cir.Match) -> None:
        match_value = self.__ctx.ir_value(stmt.match_value)
        self.__ctx.ir_enter_switch(match_value)

        def handle_case(case_stmt: cir.CheckedGIR) -> None:
            assert isinstance(case_stmt, cir.CharCase)

            case_bb = self.__ctx.ir_generate_bb()

            for case_value in case_stmt.case_values:
                case_const = self.__ctx.ir_constant(match_value.type_id, case_value)
                self.__ctx.ir_add_case(case_const, case_bb)

            self.__ctx.ir_set_current_bb(case_bb)
            self.__ctx.process_block(case_stmt.body, self.__code_block_handlers)
            self.__ctx.ir_branch_end_bb()

        handlers: IRHandlerMap[cir.CheckedGIR] = {
            cir.CharCase: handle_case,
            cir.DefaultCase: self.__default,
        }

        self.__ctx.process_block(stmt.body, handlers)

        if not stmt.has_default:
            self.__ctx.ir_set_default_bb()
            self.__ctx.ir_unreachable()

        self.__ctx.ir_exit_ctx()

    def __match_str(self, stmt: cir.Match) -> None:
        # TODO: implement string match
        raise NotImplementedError()

    def __match_enum(self, stmt: cir.Match) -> None:
        assert isinstance(stmt.match_value, Variable)

        if self.__ctx.null_pointer_optimizable(stmt.match_value.type_id):
            option_type = self.__ctx.ty_get(stmt.match_value.type_id).expect_enum()
            opt_type_id = option_type.generic_args[0]
            opt_type = self.__ctx.ty_get(opt_type_id)
            match_value_addr = self.__ctx.ir_address(stmt.match_value)

            # Get the nullable value (pointer or struct's nullable field)
            nullable_value = self.__ctx.ir_enum_discriminant_addr(option_type, match_value_addr)

            if isinstance(opt_type, ty.PointerType):
                null_ptr = self.__ctx.ir_niche(opt_type_id)
            elif isinstance(opt_type, ty.StructType):
                field_path = self.__ctx.get_npo_field_path(opt_type_id)
                nullable_field_type_id = self.__get_field_type_at_path(opt_type_id, field_path)
                null_ptr = self.__ctx.ir_niche(nullable_field_type_id)
            else:
                raise CompilerError(f"Unexpected optimizable type: {opt_type}")

            is_none = self.__ctx.ir_binary_op(Operator.Eq, nullable_value, null_ptr)

            none_bb = self.__ctx.ir_generate_bb()
            some_bb = self.__ctx.ir_generate_bb()

            self.__ctx.ir_enter_block()
            self.__ctx.ir_cbranch(is_none, none_bb, some_bb)

            def handle_none_case(case_stmt: cir.CheckedGIR) -> None:
                """Handle None case in optimized Option match."""
                assert isinstance(case_stmt, cir.EnumCase)

                handles_none = False
                for case_variant_name in case_stmt.case_values:
                    variant = option_type.get_variant_by_name(case_variant_name, self.__ctx.ty_instantiate)
                    if variant is not None and variant.name == "None":
                        handles_none = True
                        break

                if handles_none:
                    self.__ctx.process_block(case_stmt.body, self.__code_block_handlers)
                    self.__ctx.ir_branch_end_bb()

            def handle_none_payload_case(case_stmt: cir.CheckedGIR) -> None:
                """Handle None payload case in optimized Option match."""
                assert isinstance(case_stmt, cir.EnumPayloadCase)
                # None variant doesn't have payload, so this shouldn't match
                pass

            def handle_some_case(case_stmt: cir.CheckedGIR) -> None:
                """Handle Some case in optimized Option match."""
                assert isinstance(case_stmt, cir.EnumCase)

                handles_some = False
                for case_variant_name in case_stmt.case_values:
                    variant = option_type.get_variant_by_name(case_variant_name, self.__ctx.ty_instantiate)
                    if variant is not None and variant.name == "Some":
                        handles_some = True
                        break

                if handles_some:
                    self.__ctx.process_block(case_stmt.body, self.__code_block_handlers)
                    self.__ctx.ir_branch_end_bb()

            def handle_some_payload_case(case_stmt: cir.CheckedGIR) -> None:
                """Handle Some payload case in optimized Option match."""
                assert isinstance(case_stmt, cir.EnumPayloadCase)

                variant = option_type.get_variant_by_name(case_stmt.case_value, self.__ctx.ty_instantiate)
                if variant is not None and variant.name == "Some":
                    self.__ctx.ir_alloc_var(case_stmt.payload)
                    if isinstance(opt_type, ty.StructType):
                        struct_ptr_type_id = self.__ctx.ty_alloc_pointer(opt_type_id)
                        struct_addr = LLValue(struct_ptr_type_id, match_value_addr.value)
                        struct_value = self.__ctx.ir_load_addr(struct_addr)
                        self.__ctx.ir_store(case_stmt.payload, struct_value)
                    else:
                        self.__ctx.ir_store(case_stmt.payload, nullable_value)

                    self.__ctx.process_block(case_stmt.body, self.__code_block_handlers)
                    self.__ctx.ir_branch_end_bb()

            def handle_default_case(case_stmt: cir.CheckedGIR) -> None:
                """Handle Default case in optimized Option match."""
                assert isinstance(case_stmt, cir.DefaultCase)
                if case_stmt.body is not None:
                    self.__ctx.process_block(case_stmt.body, self.__code_block_handlers)
                self.__ctx.ir_branch_end_bb()

            # Handle None branch
            self.__ctx.ir_set_current_bb(none_bb)
            none_handlers: IRHandlerMap[cir.CheckedGIR] = {
                cir.EnumCase: handle_none_case,
                cir.EnumPayloadCase: handle_none_payload_case,
                cir.DefaultCase: handle_default_case,
            }
            self.__ctx.process_block(stmt.body, none_handlers)
            self.__ctx.ir_branch_end_bb()

            # Handle Some branch
            self.__ctx.ir_set_current_bb(some_bb)
            some_handlers: IRHandlerMap[cir.CheckedGIR] = {
                cir.EnumCase: handle_some_case,
                cir.EnumPayloadCase: handle_some_payload_case,
                cir.DefaultCase: handle_default_case,
            }
            self.__ctx.process_block(stmt.body, some_handlers)
            self.__ctx.ir_branch_end_bb()

            self.__ctx.ir_exit_ctx()
            return

        enum_type = self.__ctx.ty_get(stmt.match_value.type_id).expect_enum()

        match_value_addr = self.__ctx.ir_address(stmt.match_value)
        discriminant_addr = self.__ctx.ir_enum_discriminant_addr(enum_type, match_value_addr)
        discriminant_value = self.__ctx.ir_load_addr(discriminant_addr)

        self.__ctx.ir_enter_switch(discriminant_value)

        def handle_case(case_stmt: cir.CheckedGIR) -> None:
            assert isinstance(case_stmt, cir.EnumCase)

            case_bb = self.__ctx.ir_generate_bb()

            for case_variant_name in case_stmt.case_values:
                variant = enum_type.get_variant_by_name(case_variant_name, self.__ctx.ty_instantiate)
                assert variant is not None
                case_const = self.__ctx.ir_constant(TypeSpace.i32_id, variant.discriminant)
                self.__ctx.ir_add_case(case_const, case_bb)

            self.__ctx.ir_set_current_bb(case_bb)
            self.__ctx.process_block(case_stmt.body, self.__code_block_handlers)
            self.__ctx.ir_branch_end_bb()

        def handle_payload_case(case_stmt: cir.CheckedGIR) -> None:
            assert isinstance(case_stmt, cir.EnumPayloadCase)

            case_bb = self.__ctx.ir_generate_bb()

            variant = enum_type.get_variant_by_name(case_stmt.case_value, self.__ctx.ty_instantiate)
            assert variant is not None
            case_const = self.__ctx.ir_constant(TypeSpace.i32_id, variant.discriminant)
            self.__ctx.ir_add_case(case_const, case_bb)

            self.__ctx.ir_set_current_bb(case_bb)

            self.__ctx.ir_alloc_var(case_stmt.payload)
            payload_addr = self.__ctx.ir_enum_payload_address(enum_type, match_value_addr, variant)
            payload_value = self.__ctx.ir_load_addr(payload_addr)
            self.__ctx.ir_store(case_stmt.payload, payload_value)

            self.__ctx.process_block(case_stmt.body, self.__code_block_handlers)
            self.__ctx.ir_branch_end_bb()

        handlers: IRHandlerMap[cir.CheckedGIR] = {
            cir.EnumCase: handle_case,
            cir.EnumPayloadCase: handle_payload_case,
            cir.DefaultCase: self.__default,
        }

        self.__ctx.process_block(stmt.body, handlers)

        if not stmt.has_default:
            self.__ctx.ir_set_default_bb()
            self.__ctx.ir_unreachable()

        self.__ctx.ir_exit_ctx()

    def __block(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Block)

        self.__ctx.process_block(stmt.stmt_id, self.__code_block_handlers)

    def __break(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Break)

        self.__ctx.ir_break()

    def __continue(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Continue)

        self.__ctx.ir_continue()

    def __return(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Return)

        if stmt.value is None:
            self.__ctx.ir_ret_void()
        else:
            ret_value = self.__ctx.ir_value(stmt.value)
            self.__ctx.ir_ret(ret_value)

    def __delete(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Delete)

        deleted_value = self.__ctx.ir_value(stmt.target)
        self.__ctx.ir_delete(deleted_value)

    def __dyn_type(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.DynType)

        type_size = self.__ctx.ty_size(stmt.data_type)
        size_const = self.__ctx.ir_constant(TypeSpace.u64_id, type_size)
        alloced_addr = self.__ctx.ir_heap_alloc(size_const, stmt.data_type)

        self.__ctx.ir_store(stmt.target, alloced_addr)

    def __dyn_value(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.DynValue)

        value = self.__ctx.ir_value(stmt.value)

        type_size = self.__ctx.ty_size(stmt.value.type_id)
        size_const = self.__ctx.ir_constant(TypeSpace.u64_id, type_size)
        alloced_addr = self.__ctx.ir_heap_alloc(size_const, stmt.value.type_id)

        self.__ctx.ir_store_addr(alloced_addr, value)

        self.__ctx.ir_store(stmt.target, alloced_addr)

    def __dyn_array(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.DynArray)

        element_size = self.__ctx.ty_size(stmt.element_type)
        element_size_const = self.__ctx.ir_constant(TypeSpace.u64_id, element_size)
        length_value = self.__ctx.ir_value(stmt.length)
        total_size = self.__ctx.ir_binary_op(Operator.Star, element_size_const, length_value)
        alloced_addr = self.__ctx.ir_heap_alloc(total_size, stmt.element_type)

        self.__ctx.ir_store(stmt.target, alloced_addr)

    def __assert(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Assert)

        cond_value = self.__ctx.ir_value(stmt.condition)

        success_bb = self.__ctx.ir_generate_bb()
        fail_bb = self.__ctx.ir_generate_bb()

        self.__ctx.ir_cbranch(cond_value, success_bb, fail_bb)

        self.__ctx.ir_set_current_bb(fail_bb)
        msg_value = self.__ctx.ir_value(stmt.message)
        self.__ctx.ir_print(msg_value)
        self.__ctx.ir_panic()
        self.__ctx.ir_unreachable()

        self.__ctx.ir_set_current_bb(success_bb)

    def __read(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Read)

        fd = self.__ctx.ir_value(stmt.fd)
        buf_addr = self.__ctx.ir_address(stmt.buffer)
        result = self.__ctx.ir_read(fd, buf_addr, stmt.buffer.type_id)
        self.__ctx.ir_store(stmt.target, result)

    def __write_fd(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Write)

        fd = self.__ctx.ir_value(stmt.fd)
        value = self.__ctx.ir_value(stmt.value)
        self.__ctx.ir_write(fd, value)

    def __open(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Open)

        path = self.__ctx.ir_value(stmt.path)
        flags = self.__ctx.ir_value(stmt.flags)
        result = self.__ctx.ir_open(path, flags)
        self.__ctx.ir_store(stmt.target, result)

    def __close(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Close)

        fd = self.__ctx.ir_value(stmt.fd)
        self.__ctx.ir_close(fd)

    def __panic(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Panic)

        # print a default panic message
        msg_value = self.__ctx.ir_value(stmt.message)
        self.__ctx.ir_print(msg_value)

        self.__ctx.ir_panic()
        self.__ctx.ir_unreachable()

    def __size_of(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.SizeOf)

        size = self.__ctx.ty_size(stmt.data_type)
        size_const = self.__ctx.ir_constant(TypeSpace.u64_id, size)

        self.__ctx.ir_store(stmt.target, size_const)

    def __bitcast(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.BitCast)

        value = self.__ctx.ir_value(stmt.value)

        casted_value = self.__ctx.ir_bitcast(value, stmt.to_type)

        self.__ctx.ir_store(stmt.target, casted_value)

    def __byte_offset(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.ByteOffset)

        ptr_value = self.__ctx.ir_value(stmt.base)
        offset_value = self.__ctx.ir_value(stmt.offset)

        byte_offset_value = self.__ctx.ir_byte_offset(ptr_value, offset_value)

        self.__ctx.ir_store(stmt.target, byte_offset_value)

    def __mem_copy(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.MemCopy)

        dest_addr = self.__ctx.ir_value(stmt.target)
        src_addr = self.__ctx.ir_value(stmt.source)
        size_value = self.__ctx.ir_value(stmt.size)

        self.__ctx.ir_mem_copy(dest_addr, src_addr, size_value)
