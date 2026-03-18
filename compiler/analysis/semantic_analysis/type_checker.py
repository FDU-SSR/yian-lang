"""
Type Check pass: check types and complete CGIR generation

- Perform type checking on each instruction
- Desugar certain instructions into CGIR instructions
- Generic instantiation
"""

from compiler.analysis.semantic_analysis.utils.analysis_pass import UnitPass
from compiler.analysis.semantic_analysis.utils.context import DefPoint, SemanticCtx
from compiler.config.defs import IRHandler, IRHandlerMap, StmtId, TypeId
from compiler.utils import IR, ty
from compiler.utils.errors import CompilerError, NameResolutionError, SemanticError, YianSyntaxError, YianTypeError
from compiler.utils.IR import cgir as cir
from compiler.utils.IR import gir as ir
from compiler.utils.ty import TypeSpace


class TypeChecker(UnitPass):
    """
    This pass actually hacks the standard process of `TypeAnalysisPass`.

    1. Instead of processing unit by unit, it processes function/method with `worklist algorithm`.
    2. Initially, only the `main` function is added to the worklist.
    3. When a function/method call is encountered, its definition point is resolved and added to the worklist if not already processed.
    4. This continues until all reachable functions/methods have been processed.
    """

    def __init__(self, ctx: SemanticCtx):
        super().__init__(ctx)

        self.__worklist: list[DefPoint] = []
        self.__processed_defs: set[DefPoint] = set()

        self.__intrinsic_handlers: dict[str, IRHandler[ir.CallStmt]] = {
            "panic": self.__builtin_panic,
            "sizeof": self.__builtin_sizeof,
            "typeof": self.__builtin_typeof,
            "bitcast": self.__builtin_bitcast,
            "byte_offset": self.__builtin_byte_offset,
            "memcpy": self.__builtin_memcpy,
            "read": self.__builtin_read,
            "write": self.__builtin_write,
            "open": self.__builtin_open,
            "close": self.__builtin_close,
        }

    @property
    def _code_block_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.VariableDeclStmt: self.__variable_decl,

            ir.IfStmt: self.__if_stmt,
            ir.ForStmt: self.__for_stmt,
            ir.ForInStmt: self.__forin_stmt,
            ir.LoopStmt: self.__loop_stmt,
            ir.SwitchStmt: self.__switch_stmt,
            ir.BlockStmt: self.__block,

            ir.ReturnStmt: self.__return_stmt,
            ir.CallStmt: self.__call_stmt,
            ir.AssignStmt: self.__assign_stmt,
            ir.BreakStmt: self.__break_stmt,
            ir.ContinueStmt: self.__continue_stmt,
            ir.AssertStmt: self.__assert_stmt,
            ir.DeleteStmt: self.__del_stmt,

            ir.NewObjectStmt: self.__new_object,
            ir.NewArrayStmt: self.__new_array,
        }

    @property
    def _top_level_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        return {
            ir.FunctionDeclStmt: self.__collect_main,
            ir.ImplementDeclStmt: lambda stmt: None,
            ir.ImportStmt: lambda stmt: None,
            ir.VariableDeclStmt: lambda stmt: None,
            ir.TraitDeclStmt: lambda stmt: None,
            ir.StructDeclStmt: lambda stmt: None,
            ir.EnumDeclStmt: lambda stmt: None,
            ir.TypeAliasDeclStmt: lambda stmt: None,
        }

    def _run_prelude(self) -> None:
        pass

    def _run_postlude(self) -> None:
        """
        Process the worklist until empty.
        """
        while self.__worklist:
            def_point = self.__worklist.pop(0)
            if def_point in self.__processed_defs:
                continue
            self.__processed_defs.add(def_point)

            self._ctx.set_def_point(def_point)
            self._ctx.set_unit_data_by_id(def_point.unit_id)
            stmt = self._ctx.gir_get(def_point.stmt_id)
            match stmt:
                case ir.FunctionDeclStmt():
                    self.__function_decl(stmt, def_point)
                case ir.MethodDeclStmt():
                    self.__method_decl(stmt, def_point)
                case _:
                    raise CompilerError(f"DefPoint {def_point} does not point to a function or method declaration")
            self._ctx.unset_unit_data()
            self._ctx.unset_def_point()

        self._ctx.record_processed_def_points(self.__processed_defs)

    def __process_block(self, block_id: StmtId, handlers: IRHandlerMap[ir.GIRStmt]) -> None:
        """
        Override to generate block CGIR if needed.
        """
        # emit block CGIR if not yet emitted
        if not self._ctx.cgir_contains(block_id):
            gir_block = self._ctx.gir_get(block_id).expect_block()
            self._ctx.cgir_emit(cir.Block.from_gir(gir_block))

        super()._process_block(block_id, handlers)

    def __emit_block_into(self, block_id: StmtId, parent_block_id: StmtId) -> None:
        """
        Emit GIR statements from a block directly into an existing CGIR block.
        """
        gir_block = self._ctx.gir_get(block_id).expect_block()
        for stmt_id in gir_block.body:
            gir_stmt = self._ctx.gir_get(stmt_id)

            if isinstance(gir_stmt, ir.BlockStmt):
                self.__emit_block_into(gir_stmt.stmt_id, parent_block_id)
                continue

            old_parent_id = gir_stmt.metadata.parent_stmt_id
            gir_stmt.metadata.parent_stmt_id = parent_block_id
            try:
                handler = self._code_block_handlers.get(type(gir_stmt))
                if handler is None:
                    raise CompilerError(f"No handler for statement type: {type(gir_stmt)}")
                handler(gir_stmt)
            finally:
                gir_stmt.metadata.parent_stmt_id = old_parent_id

    def _unit_prelude(self) -> None:
        pass

    def _unit_postlude(self) -> None:
        pass

    # ======== Handlers ==========

    def __collect_main(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.FunctionDeclStmt)

        if stmt.name != "main":
            return

        if len(stmt.type_parameters) > 0:
            raise SemanticError("The 'main' function cannot be generic")

        if self.__worklist:
            raise SemanticError("Multiple 'main' functions found")

        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
        func_symbol = self._ctx.symbol_get(symbol_id)
        assert isinstance(func_symbol, IR.Function)

        dp = DefPoint(
            unit_id=self._ctx.unit_id,
            stmt_id=stmt.stmt_id,
            procedure_name=stmt.name,
            type_id=func_symbol.type_id,
            root_block_id=stmt.body,
        )
        self.__worklist.append(self._ctx.register_def_point(dp))

    def __check_target(self, stmt: ir.GIRStmt, target: str, value: IR.TypedValue, lvalue: bool) -> IR.Variable:
        """
        Check if `value` can be assigned to the target symbol named `target` in `stmt`.

        - If the target symbol is compiler-generated and not yet defined, create it.
        - If the target symbol is compiler-generated and already defined, or user-defined, check type compatibility.
        """
        if self._ctx.symbol_is_inplace_defined(stmt.stmt_id, target):
            # case 1: need to create target symbol
            symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, target)
            var_symbol = IR.VariableSymbol(symbol_id, target, value.type_id, lvalue)
            self._ctx.symbol_register(var_symbol)
            var = IR.Variable(symbol_id, target, value.type_id, lvalue)
        else:
            # case 2: check target symbol
            var_symbol = self._ctx.symbol_lookup(stmt.stmt_id, target)
            assert isinstance(var_symbol, IR.VariableSymbol)
            if var_symbol.type_id is None:
                raise CompilerError(f"Variable symbol {target} at statement {stmt} has no type defined")
            self._ctx.ty_assignable_check(var_symbol.type_id, value)
            var = IR.Variable(var_symbol.symbol_id, var_symbol.name, var_symbol.type_id, var_symbol.lvalue)

        return var

    def __function_decl(self, stmt: ir.FunctionDeclStmt, def_point: DefPoint) -> None:
        self._ctx.enter_function_scope(def_point.type_id)

        # declare parameters
        func_type = self._ctx.ty_get(def_point.type_id).expect_function()
        params = func_type.parameters(self._ctx.ty_instantiate)
        for param in params:
            var_symbol = IR.VariableSymbol(param.stmt_id, param.name, param.type_id)
            self._ctx.symbol_register(var_symbol)

        # emit root block
        root_block = self._ctx.gir_get(stmt.body).expect_block()
        self._ctx.cgir_emit(cir.Block.from_gir(root_block))

        self.__process_block(stmt.body, self._code_block_handlers)

        self._ctx.exit_scope()

    def __method_decl(self, stmt: ir.MethodDeclStmt, def_point: DefPoint) -> None:
        impl_stmt_id = self._ctx.ty_query_impl(def_point.type_id)
        self._ctx.enter_impl_scope(impl_stmt_id)

        self._ctx.enter_method_scope(def_point.type_id)

        # declare parameters
        method_type = self._ctx.ty_get(def_point.type_id).expect_method()
        params = method_type.parameters(self._ctx.ty_instantiate)
        for param in params:
            var_symbol = IR.VariableSymbol(param.stmt_id, param.name, param.type_id)
            self._ctx.symbol_register(var_symbol)

        # emit root block
        root_block = self._ctx.gir_get(stmt.body).expect_block()
        self._ctx.cgir_emit(cir.Block.from_gir(root_block))

        self.__process_block(stmt.body, self._code_block_handlers)

        self._ctx.exit_scope()
        self._ctx.exit_scope()

    def __return_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.ReturnStmt)

        # check return type
        func_id = self._ctx.ty_current_procedure
        func_return_ty = self._ctx.ty_get(func_id).expect_callable().return_type(self._ctx.ty_instantiate)

        if stmt.value is None:
            # handle return void
            if func_return_ty != TypeSpace.void_id:
                raise YianTypeError.mismatch(func_return_ty, TypeSpace.void_id, self._ctx.ty_formatter)
            new_stmt = cir.Return.from_gir(stmt, None)
        else:
            # make sure return value is assignable to function return type
            return_value = self._ctx.parse_value(stmt.stmt_id, stmt.value)
            self._ctx.ty_assignable_check(func_return_ty, return_value)
            new_stmt = cir.Return.from_gir(stmt, return_value)

        self._ctx.cgir_emit(new_stmt)

    def __builtin_read(self, stmt: ir.CallStmt) -> None:
        """
        read(fd, array) -> str
        """
        if len(stmt.positional_arguments) != 2 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'read' takes exactly two positional arguments: read(fd, buffer)")

        # First argument: file descriptor (i32)
        fd_arg = stmt.positional_arguments[0]
        fd_value = self._ctx.parse_value(stmt.stmt_id, fd_arg)
        self._ctx.ty_assignable_check(TypeSpace.i32_id, fd_value)

        # Second argument: buffer (array variable)
        buf_arg = stmt.positional_arguments[1]
        buf_value = self._ctx.parse_value(stmt.stmt_id, buf_arg)

        if not isinstance(buf_value, IR.Variable):
            raise YianTypeError("The second argument of 'read' must be an array variable")

        self._ctx.ty_get(buf_value.type_id).expect_array()

        read_result = IR.Variable(-1, "", TypeSpace.str_id, False)  # dummy value to do type checking

        if stmt.target is None:
            raise SemanticError("read must have a target")

        target_var = self.__check_target(stmt, stmt.target, read_result, False)

        self._ctx.cgir_emit(cir.Read.from_gir(stmt, target_var, fd_value, buf_value))

    def __builtin_write(self, stmt: ir.CallStmt) -> None:
        """
        write(fd, str)
        """
        # 仅从函数名无法判断是系统调用 write 还是 trait Hasher 的 write 方法
        # 如果有 receiver，说明是方法调用（如 hasher.write(bytes)），转交给 receiver_call 处理
        if stmt.receiver is not None:
            self.__receiver_call(stmt)
            return

        if len(stmt.positional_arguments) != 2 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'write' takes exactly two positional arguments: write(fd, str)")

        # First argument: file descriptor (i32)
        fd_arg = stmt.positional_arguments[0]
        fd_value = self._ctx.parse_value(stmt.stmt_id, fd_arg)
        self._ctx.ty_assignable_check(TypeSpace.i32_id, fd_value)

        # Second argument: str value
        str_arg = stmt.positional_arguments[1]
        str_value = self._ctx.parse_value(stmt.stmt_id, str_arg)
        self._ctx.ty_assignable_check(TypeSpace.str_id, str_value)

        self._ctx.cgir_emit(cir.Write.from_gir(stmt, fd_value, str_value))

    def __builtin_open(self, stmt: ir.CallStmt) -> None:
        """
        open(str, i32) -> i32
        """
        if len(stmt.positional_arguments) != 2 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'open' takes exactly two positional arguments: open(path, flags)")

        path_arg = stmt.positional_arguments[0]
        path_value = self._ctx.parse_value(stmt.stmt_id, path_arg)
        self._ctx.ty_assignable_check(TypeSpace.str_id, path_value)

        flags_arg = stmt.positional_arguments[1]
        flags_value = self._ctx.parse_value(stmt.stmt_id, flags_arg)
        self._ctx.ty_assignable_check(TypeSpace.i32_id, flags_value)

        open_result = IR.Variable(-1, "", TypeSpace.i32_id, False)  # dummy value

        if stmt.target is None:
            raise SemanticError("open must have a target")

        target_var = self.__check_target(stmt, stmt.target, open_result, False)

        self._ctx.cgir_emit(cir.Open.from_gir(stmt, target_var, path_value, flags_value))

    def __builtin_close(self, stmt: ir.CallStmt) -> None:
        """
        close(i32)
        """
        if len(stmt.positional_arguments) != 1 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'close' takes exactly one positional argument: close(fd)")

        arg = stmt.positional_arguments[0]
        arg_value = self._ctx.parse_value(stmt.stmt_id, arg)

        self._ctx.ty_assignable_check(TypeSpace.i32_id, arg_value)

        self._ctx.cgir_emit(cir.Close.from_gir(stmt, arg_value))

    def __builtin_panic(self, stmt: ir.CallStmt) -> None:
        """
        panic(str)
        """
        if len(stmt.positional_arguments) != 1 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'panic' takes exactly only one positional argument")

        arg = stmt.positional_arguments[0]
        arg_value = self._ctx.parse_value(stmt.stmt_id, arg)

        self._ctx.ty_assignable_check(TypeSpace.str_id, arg_value)

        self._ctx.cgir_emit(cir.Panic.from_gir(stmt, arg_value))

    def __builtin_sizeof(self, stmt: ir.CallStmt) -> None:
        """
        sizeof(<type>) -> u64
        """
        if len(stmt.positional_arguments) != 1 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'sizeof' takes exactly only one positional argument")

        type_arg = stmt.positional_arguments[0]
        type_id = self._ctx.parse_type(stmt.stmt_id, type_arg)

        sizeof_value = IR.IntegerLiteral(0)  # dummy value to do type checking
        sizeof_value.type_id = TypeSpace.u64_id  # sizeof returns u64

        if stmt.target is None:
            raise SemanticError("sizeof must have a target")

        target_var = self.__check_target(stmt, stmt.target, sizeof_value, False)

        self._ctx.cgir_emit(cir.SizeOf.from_gir(stmt, target_var, type_id))

    def __builtin_typeof(self, stmt: ir.CallStmt) -> None:
        """
        typeof(value, <type>) -> bool
        """
        if len(stmt.positional_arguments) != 2 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'typeof' takes exactly two positional arguments")

        value_arg = stmt.positional_arguments[0]
        type_arg = stmt.positional_arguments[1]

        value = self._ctx.parse_value(stmt.stmt_id, value_arg)
        type_id = self._ctx.parse_type(stmt.stmt_id, type_arg)

        typeof_value = IR.BooleanLiteral(value.type_id == type_id)

        if stmt.target is None:
            raise SemanticError("typeof must have a target")

        target_var = self.__check_target(stmt, stmt.target, typeof_value, False)

        self._ctx.cgir_emit(cir.Assign(stmt.metadata, stmt.pos, target_var, typeof_value))

    def __builtin_bitcast(self, stmt: ir.CallStmt) -> None:
        """
        bitcast<target_type>(value) -> target_type
        """
        if len(stmt.positional_arguments) != 1 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'bitcast' takes exactly only one positional argument")

        if len(stmt.type_arguments) != 1:
            raise YianTypeError("The 'bitcast' takes exactly only one type argument")

        value_arg = stmt.positional_arguments[0]
        target_type_arg = stmt.type_arguments[0]

        value = self._ctx.parse_value(stmt.stmt_id, value_arg)
        target_type_id = self._ctx.parse_type(stmt.stmt_id, target_type_arg)

        # value must be a pointer or u64
        value_ty = self._ctx.ty_get(value.type_id)
        if not (isinstance(value_ty, ty.PointerType) or value.type_id == TypeSpace.u64_id):
            raise YianTypeError("The argument of 'bitcast' must be a pointer or u64")

        # target type must be a pointer
        self._ctx.ty_get(target_type_id).expect_pointer()

        bitcast_value = IR.Variable(-1, "", target_type_id, False)  # dummy value to do type checking

        if stmt.target is None:
            raise SemanticError("bitcast must have a target")

        target_var = self.__check_target(stmt, stmt.target, bitcast_value, False)

        self._ctx.cgir_emit(cir.BitCast.from_gir(stmt, target_var, value, target_type_id))

    def __builtin_byte_offset(self, stmt: ir.CallStmt) -> None:
        """
        byte_offset(ptr, offset) -> ptr

        offset is in bytes
        """
        if len(stmt.positional_arguments) != 2 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'byte_offset' takes exactly two positional arguments")

        ptr_arg = stmt.positional_arguments[0]
        offset_arg = stmt.positional_arguments[1]

        ptr_value = self._ctx.parse_value(stmt.stmt_id, ptr_arg)
        offset_value = self._ctx.parse_value(stmt.stmt_id, offset_arg)

        # ptr must be a pointer variable
        if not isinstance(ptr_value, IR.Variable):
            raise YianTypeError("The first argument of 'byte_offset' must be a pointer variable")
        self._ctx.ty_get(ptr_value.type_id).expect_pointer()

        # offset must be i64
        self._ctx.ty_assignable_check(TypeSpace.i64_id, offset_value)

        byte_offset_value = IR.Variable(-1, "", ptr_value.type_id, False)  # dummy value to do type checking

        if stmt.target is None:
            raise SemanticError("byte_offset must have a target")

        target_var = self.__check_target(stmt, stmt.target, byte_offset_value, False)

        self._ctx.cgir_emit(cir.ByteOffset.from_gir(stmt, target_var, ptr_value, offset_value))

    def __builtin_memcpy(self, stmt: ir.CallStmt) -> None:
        """
        memcpy(dest, src, size) -> void

        dest and src are pointers, size is in bytes
        """
        if len(stmt.positional_arguments) != 3 or len(stmt.named_arguments) != 0:
            raise YianTypeError("The 'memcpy' takes exactly three positional arguments")

        dest_arg = stmt.positional_arguments[0]
        src_arg = stmt.positional_arguments[1]
        size_arg = stmt.positional_arguments[2]

        dest_value = self._ctx.parse_value(stmt.stmt_id, dest_arg)
        src_value = self._ctx.parse_value(stmt.stmt_id, src_arg)
        size_value = self._ctx.parse_value(stmt.stmt_id, size_arg)

        # dest and src must be pointer variables
        if not isinstance(dest_value, IR.Variable) or not isinstance(src_value, IR.Variable):
            raise YianTypeError("The first two arguments of 'memcpy' must be pointer variables")
        self._ctx.ty_get(dest_value.type_id).expect_pointer()
        self._ctx.ty_get(src_value.type_id).expect_pointer()

        # size must be u64
        self._ctx.ty_assignable_check(TypeSpace.u64_id, size_value)

        # if stmt.target is not None:
        #     raise SemanticError("memcpy cannot have a target")

        self._ctx.cgir_emit(cir.MemCopy.from_gir(stmt, dest_value, src_value, size_value))

    def __call_stmt(self, stmt: ir.GIRStmt) -> None:
        """
        将 call 指令分为三类处理

        1. 内置函数
        2. 无 receiver 的函数
        3. 有 receiver 的函数
        """
        assert isinstance(stmt, ir.CallStmt)

        # 1. 内置函数
        if stmt.name in self.__intrinsic_handlers:
            return self.__intrinsic_handlers[stmt.name](stmt)

        # 2. 无 receiver 的函数
        if stmt.receiver is None:
            self.__call(stmt)
            return

        # 3. 有 receiver 的函数
        self.__receiver_call(stmt)

    def __call(self, stmt: ir.CallStmt) -> None:
        """
        处理无 receiver 的函数调用

        1. 函数调用
        2. 结构体构造
        3. 函数指针调用
        4. 基本数据类型转换
        """
        # case 1: invoke
        try:
            callee_value = self._ctx.parse_value(stmt.stmt_id, stmt.name)
            assert isinstance(callee_value, IR.Variable)
            self.__invoke(stmt, callee_value)
            return
        except (NameResolutionError, TypeError):
            pass

        # case 2: struct constructor / basic data type conversion
        try:
            ty_id = self._ctx.parse_type(stmt.stmt_id, stmt.name)
            ty_def = self._ctx.ty_get(ty_id)
            if isinstance(ty_def, ty.StructType):
                self.__struct_construct(stmt, ty_id)
                return
            if isinstance(ty_def, ty.BasicType):
                self.__basic_data_type_conversion(stmt, ty_id)
                return
        except NameResolutionError:
            pass

        # case 3: function call
        func_symbol = self._ctx.symbol_lookup(stmt.stmt_id, stmt.name)
        if not isinstance(func_symbol, IR.Function):
            raise YianTypeError(f"Symbol {stmt.name} is not a function")
        self.__function_call(stmt, func_symbol)

    def __receiver_call(self, stmt: ir.CallStmt) -> None:
        """
        将有 receiver 的函数调用分为两类

        1. receiver 是变量
        2. receiver 是类型
        """
        assert stmt.receiver is not None

        # case 1: receiver is variable
        try:
            receiver_value = self._ctx.parse_value(stmt.stmt_id, stmt.receiver)
            self.__method_call(stmt, receiver_value)
            return
        except NameResolutionError:
            pass

        # case 2: receiver is type
        receiver_ty_id = self._ctx.parse_type(stmt.stmt_id, stmt.receiver)
        receiver_ty_def = self._ctx.ty_get(receiver_ty_id)
        if isinstance(receiver_ty_def, ty.EnumType) and receiver_ty_def.enum_def.has_variant(stmt.name):
            self.__enum_variant_construct(stmt, receiver_ty_id)
            return
        else:
            self.__static_method_call(stmt, receiver_ty_id)

    def __method_call(self, stmt: ir.CallStmt, receiver_value: IR.TypedValue) -> None:
        # prepare method name
        method_name = stmt.name

        # prepare generic args
        generic_args = None
        if len(stmt.type_arguments) > 0:
            generic_args = [self._ctx.parse_type(stmt.stmt_id, arg) for arg in stmt.type_arguments]

        # prepare arguments
        args = [self._ctx.parse_value(stmt.stmt_id, arg) for arg in stmt.positional_arguments]
        if len(stmt.named_arguments) > 0:
            raise YianTypeError("Cannot use named arguments in method call")

        # lookup method
        lookup_result = self._ctx.ty_method_lookup(receiver_value, method_name, generic_args, args)

        # perform auto deref if needed (respect Deref trait overloads)
        for _ in range(lookup_result.deref_levels):
            op_result = self._ctx.ty_unary_op_check(IR.Operator.Star, receiver_value)

            if op_result.method_type is None:
                receiver_value = self._ctx.cgir_build_unary_op(
                    stmt,
                    IR.Operator.Star,
                    receiver_value,
                    op_result.result_type,
                ).target
                continue

            call_stmt = self._ctx.cgir_build_method_call(
                stmt,
                receiver_value,
                op_result.method_type,
                [],
                op_result.result_type,
            )

            self.__worklist.append(self._ctx.ty_resolve_def_point(op_result.method_type))

            assert call_stmt.target is not None
            # Overloaded deref lowers to *receiver.deref()
            derefed_id = self._ctx.ty_deref(call_stmt.target.type_id)
            receiver_value = self._ctx.cgir_build_unary_op(
                stmt,
                IR.Operator.Star,
                call_stmt.target,
                derefed_id,
            ).target

        # check return type
        if stmt.target is not None:
            method_ty_def = self._ctx.ty_get(lookup_result.method_id).expect_method()
            return_ty = method_ty_def.return_type(self._ctx.ty_instantiate)
            return_value = IR.Variable(-1, "", return_ty, False)  # dummy value to do type checking
            target_var = self.__check_target(stmt, stmt.target, return_value, False)
        else:
            target_var = None

        # emit method call
        self._ctx.cgir_emit(cir.MethodCall.from_gir(
            stmt,
            lookup_result.method_id,
            target_var,
            receiver_value,
            args,
        ))

        # add def point to worklist
        self.__worklist.append(self._ctx.ty_resolve_def_point(lookup_result.method_id))

    def __static_method_call(self, stmt: ir.CallStmt, receiver_ty_id: TypeId) -> None:
        # prepare method name
        method_name = stmt.name

        # prepare generic args
        generic_args = None
        if stmt.type_arguments is not None:
            generic_args = [self._ctx.parse_type(stmt.stmt_id, arg) for arg in stmt.type_arguments]

        # prepare arguments
        args = [self._ctx.parse_value(stmt.stmt_id, arg) for arg in stmt.positional_arguments]
        if len(stmt.named_arguments) > 0:
            raise YianTypeError("Cannot use named arguments in static method call")

        # lookup method
        lookup_result = self._ctx.ty_static_method_lookup(receiver_ty_id, method_name, generic_args, args)

        # check return type
        if stmt.target is not None:
            method_ty_def = self._ctx.ty_get(lookup_result.method_id).expect_method()
            return_ty = method_ty_def.return_type(self._ctx.ty_instantiate)
            return_value = IR.Variable(-1, "", return_ty, False)  # dummy value to do type checking
            target_var = self.__check_target(stmt, stmt.target, return_value, False)
        else:
            target_var = None

        # emit static method call
        self._ctx.cgir_emit(cir.StaticMethodCall.from_gir(
            stmt,
            lookup_result.method_id,
            target_var,
            args,
        ))

        # add def point to worklist
        self.__worklist.append(self._ctx.ty_resolve_def_point(lookup_result.method_id))

    def __function_call(self, stmt: ir.CallStmt, func_def: IR.Function) -> None:
        # func type
        func_ty = func_def.type_id

        # prepare generic args
        generic_args = None
        if len(stmt.type_arguments) > 0:
            generic_args = [self._ctx.parse_type(stmt.stmt_id, arg) for arg in stmt.type_arguments]

        # prepare arguments
        positional_args = [self._ctx.parse_value(stmt.stmt_id, arg) for arg in stmt.positional_arguments]
        named_args = {name: self._ctx.parse_value(stmt.stmt_id, arg) for name, arg in stmt.named_arguments.items()}

        # check func call
        func_ty, arg_values = self._ctx.ty_func_call_check(func_ty, generic_args, positional_args, named_args)

        # check return type
        if stmt.target is not None:
            func_ty_def = self._ctx.ty_get(func_ty).expect_function()
            return_value = IR.Variable(-1, "", func_ty_def.return_type(self._ctx.ty_instantiate), False)  # dummy value to do type checking
            target_var = self.__check_target(stmt, stmt.target, return_value, False)
        else:
            target_var = None

        # emit function call
        self._ctx.cgir_emit(cir.FuncCall.from_gir(
            stmt,
            func_ty,
            target_var,
            arg_values,
        ))

        # add def point to worklist
        self.__worklist.append(self._ctx.ty_resolve_def_point(func_ty))

    def __struct_construct(self, stmt: ir.CallStmt, struct_ty_id: TypeId) -> None:
        # handle generic instantiation
        if len(stmt.type_arguments) > 0:
            generic_args = [self._ctx.parse_type(stmt.stmt_id, arg) for arg in stmt.type_arguments]
            struct_ty_def = self._ctx.ty_get(struct_ty_id).expect_struct()
            generic_params = struct_ty_def.struct_def.generics
            struct_ty_id = self._ctx.ty_instantiate(struct_ty_id, dict(zip(generic_params, generic_args)))

        # prepare arguments
        positional_args = [self._ctx.parse_value(stmt.stmt_id, arg) for arg in stmt.positional_arguments]
        named_args = {name: self._ctx.parse_value(stmt.stmt_id, arg) for name, arg in stmt.named_arguments.items()}

        # struct construct
        struct_ty_id, field_values = self._ctx.ty_struct_construct(struct_ty_id, positional_args, named_args)

        self._ctx.ty_constructable_check(struct_ty_id)

        # check return type
        if stmt.target is None:
            raise SemanticError("Struct constructor must have a target")
        return_value = IR.Variable(-1, "", struct_ty_id, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, False)

        # emit struct construct
        self._ctx.cgir_emit(cir.StructConstruct.from_gir(
            stmt,
            struct_ty_id,
            target_var,
            field_values,
        ))

    def __invoke(self, stmt: ir.CallStmt, callable_value: IR.Variable) -> None:
        invokable_ty = callable_value.type_id

        # prepare arguments
        positional_args = [self._ctx.parse_value(stmt.stmt_id, arg) for arg in stmt.positional_arguments]
        if len(stmt.named_arguments) > 0:
            raise YianTypeError("Cannot use named arguments when invoking")

        # check invokable
        self._ctx.ty_invoke_check(invokable_ty, positional_args)

        # check return type
        if stmt.target is not None:
            invokable_ty_def = self._ctx.ty_get(invokable_ty).expect_function_pointer()
            return_ty = invokable_ty_def.return_type
            return_value = IR.Variable(-1, "", return_ty, False)  # dummy value to do type checking
            target_var = self.__check_target(stmt, stmt.target, return_value, False)
        else:
            target_var = None

        # emit invoke
        self._ctx.cgir_emit(cir.Invoke.from_gir(
            stmt,
            callable_value,
            target_var,
            positional_args,
        ))

    def __basic_data_type_conversion(self, stmt: ir.CallStmt, target_type: TypeId) -> None:
        # handle args
        if len(stmt.named_arguments) > 0:
            raise YianTypeError("Basic data type conversion cannot have named arguments")
        if len(stmt.positional_arguments) != 1:
            raise YianTypeError("Basic data type conversion must have exactly one argument")
        from_value = self._ctx.parse_value(stmt.stmt_id, stmt.positional_arguments[0])

        # check convertibility
        self._ctx.ty_basic_type_conversion_check(from_value, target_type)

        # check return type
        if stmt.target is None:
            raise SemanticError("Basic data type conversion must have a target")
        return_value = IR.Variable(-1, "", target_type, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, False)

        # emit conversion
        self._ctx.cgir_emit(cir.Cast.from_gir(
            stmt,
            target_var,
            from_value,
            target_type,
        ))

    def __enum_variant_construct(self, stmt: ir.CallStmt, enum_ty_id: TypeId) -> None:
        # find variant
        enum_type = self._ctx.ty_get(enum_ty_id).expect_enum()
        variant = enum_type.get_variant_by_name(stmt.name, self._ctx.ty_instantiate)
        if variant is None:
            raise YianTypeError.member_not_found(enum_ty_id, stmt.name, self._ctx.ty_formatter)

        # find payload type
        if variant.payload is None:
            raise YianTypeError(f"Variant {variant.name} has no payload, cannot be called as constructor")
        payload_type = self._ctx.ty_get(variant.payload).expect_struct()

        # prepare arguments
        positional_args = [self._ctx.parse_value(stmt.stmt_id, arg) for arg in stmt.positional_arguments]
        named_args = {name: self._ctx.parse_value(stmt.stmt_id, arg) for name, arg in stmt.named_arguments.items()}

        # check payload construct
        _, field_values = self._ctx.ty_struct_construct(payload_type.type_id, positional_args, named_args)

        # check return type
        if stmt.target is None:
            raise SemanticError("Enum variant constructor must have a target")
        return_value = IR.Variable(-1, "", enum_ty_id, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, False)

        # emit enum variant construct
        self._ctx.cgir_emit(cir.VariantConstruct.from_gir(
            stmt,
            enum_ty_id,
            variant,
            target_var,
            field_values,
        ))

    def __enum_variant(self, stmt: ir.AssignStmt, enum_ty_id: TypeId) -> None:
        if stmt.rhs is None:
            raise YianSyntaxError("Missing rhs in enum variant access")

        # find variant
        enum_type = self._ctx.ty_get(enum_ty_id).expect_enum()
        variant = enum_type.get_variant_by_name(stmt.rhs, self._ctx.ty_instantiate)
        if variant is None:
            raise YianTypeError.member_not_found(enum_ty_id, stmt.rhs, self._ctx.ty_formatter)

        # find payload type
        if variant.payload is not None:
            raise YianTypeError(f"Variant {variant.name} has a payload, cannot be accessed as field")

        # check return type
        if stmt.target is None:
            raise SemanticError("Enum variant constructor must have a target")
        return_value = IR.Variable(-1, "", enum_ty_id, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, False)

        # emit enum variant construct
        self._ctx.cgir_emit(cir.VariantConstruct.from_gir(
            stmt,
            enum_ty_id,
            variant,
            target_var,
            None,
        ))

    def __assign_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.AssignStmt)

        if stmt.operator == IR.Operator.Dot:
            self.__field_access(stmt)
            return

        if stmt.rhs is not None:
            self.__binary_op_assign(stmt)
            return

        if stmt.operator is not None:
            self.__unary_op_assign(stmt)
            return

        self.__assign(stmt)

    def __binary_op_assign(self, stmt: ir.AssignStmt) -> None:
        assert stmt.rhs is not None
        assert stmt.operator is not None

        # parse lhs and rhs
        lhs_value = self._ctx.parse_value(stmt.stmt_id, stmt.lhs)
        rhs_value = self._ctx.parse_value(stmt.stmt_id, stmt.rhs)

        # check binary operation
        op_result = self._ctx.ty_binary_op_check(stmt.operator, lhs_value, rhs_value)

        # insert oob check if needed
        if stmt.operator == IR.Operator.Index and op_result.method_type is None:
            self.__oob_check(stmt, lhs_value, rhs_value)

        call_stmt = None
        if op_result.method_type is not None:
            # call operator overload if needed
            call_stmt = self._ctx.cgir_build_method_call(stmt, lhs_value, op_result.method_type, [rhs_value], op_result.result_type)

            # add def point to worklist
            self.__worklist.append(self._ctx.ty_resolve_def_point(op_result.method_type))

            # add deref to overloaded Index operator
            if stmt.operator == IR.Operator.Index:
                assert call_stmt.target is not None
                derefed_id = self._ctx.ty_deref(call_stmt.target.type_id)
                op_result.result_type = derefed_id
                call_stmt = self._ctx.cgir_build_unary_op(stmt, IR.Operator.Star, call_stmt.target, derefed_id)
        elif stmt.operator == IR.Operator.Range:
            # range operator desugaring
            call_stmt = self._ctx.cgir_build_struct_construct(
                stmt,
                op_result.result_type,
                {"start": lhs_value, "end": rhs_value},
            )

        # check return type
        return_value = IR.Variable(-1, "", op_result.result_type, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, op_result.lvalue)

        # emit binary operation
        if call_stmt is None:
            self._ctx.cgir_emit(cir.BinaryOpAssign.from_gir(
                stmt,
                target_var,
                lhs_value,
                rhs_value,
            ))
        else:
            call_stmt.target = target_var

    def __unary_op_assign(self, stmt: ir.AssignStmt) -> None:
        assert stmt.operator is not None

        # parse operand
        operand_value = self._ctx.parse_value(stmt.stmt_id, stmt.lhs)

        # check unary operation
        op_result = self._ctx.ty_unary_op_check(stmt.operator, operand_value)

        # address-of function produces def point
        if stmt.operator == IR.Operator.Ampersand and isinstance(operand_value, IR.Variable):
            var_type = self._ctx.ty_get(operand_value.type_id)
            if isinstance(var_type, ty.FunctionType):
                self.__worklist.append(self._ctx.ty_resolve_def_point(var_type.type_id))

        # call operator overload if needed
        call_stmt = None
        if op_result.method_type is not None:
            call_stmt = self._ctx.cgir_build_method_call(stmt, operand_value, op_result.method_type, [], op_result.result_type)

            # add def point to worklist
            self.__worklist.append(self._ctx.ty_resolve_def_point(op_result.method_type))

            # add deref to overloaded Deref operator
            if stmt.operator == IR.Operator.Star:
                assert call_stmt.target is not None
                derefed_id = self._ctx.ty_deref(call_stmt.target.type_id)
                op_result.result_type = derefed_id
                call_stmt = self._ctx.cgir_build_unary_op(stmt, IR.Operator.Star, call_stmt.target, derefed_id)

        # check return type
        return_value = IR.Variable(-1, "", op_result.result_type, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, op_result.lvalue)

        # emit unary operation
        if call_stmt is None:
            self._ctx.cgir_emit(cir.UnaryOpAssign.from_gir(
                stmt,
                target_var,
                operand_value,
            ))
        else:
            call_stmt.target = target_var

    def __assign(self, stmt: ir.AssignStmt) -> None:
        # parse rhs
        value = self._ctx.parse_value(stmt.stmt_id, stmt.lhs)

        # check return type
        lvalue = isinstance(value, IR.Variable) and value.lvalue
        target_var = self.__check_target(stmt, stmt.target, value, lvalue)

        # emit assign
        self._ctx.cgir_emit(cir.Assign.from_gir(
            stmt,
            target_var,
            value,
        ))

    def __field_access(self, stmt: ir.AssignStmt) -> None:
        """
        分为两种情况:

        1. reciver 是变量, 此时必然是 struct, 访问其数据成员
        2. reciver 是类型名称, 此时必然为 enum, 访问其变体
        """
        try:
            lhs_ty = self._ctx.parse_type(stmt.stmt_id, stmt.lhs)
            self.__enum_variant(stmt, lhs_ty)
            return
        except NameResolutionError:
            pass

        receiver_value = self._ctx.parse_value(stmt.stmt_id, stmt.lhs)
        if not isinstance(receiver_value, IR.Variable):
            raise YianTypeError("Field access receiver must be a variable")
        self.__struct_field(stmt, receiver_value)

    def __struct_field(self, stmt: ir.AssignStmt, receiver_value: IR.Variable) -> None:
        """
        Access the field of a struct variable via field_name

        1. If receiver is a struct, access its field directly
        2. If receiver is a pointer, deref it and access its field
        """
        if stmt.rhs is None:
            raise YianSyntaxError("Missing rhs in field access")

        # prepare field name
        field_name = stmt.rhs

        # perform field lookup with auto deref
        deref_level, struct_type = self._ctx.ty_struct_field_access(receiver_value.type_id, field_name)

        # perform deref if needed
        for _ in range(deref_level):
            derefed_id = self._ctx.ty_deref(receiver_value.type_id)
            receiver_value = self._ctx.cgir_build_unary_op(stmt, IR.Operator.Star, receiver_value, derefed_id).target

        # check return type
        if stmt.target is None:
            raise SemanticError("Field access must have a target")
        struct_type_def = self._ctx.ty_get(struct_type).expect_struct()
        field = struct_type_def.get_field_by_name(field_name, self._ctx.ty_instantiate)
        if field is None:
            raise YianTypeError.member_not_found(struct_type, field_name, self._ctx.ty_formatter)
        field_value = IR.Variable(-1, "", field.type_id, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, field_value, True)

        # emit field access
        self._ctx.cgir_emit(cir.FieldAccess.from_gir(
            stmt,
            target_var,
            receiver_value,
            field,
        ))

    def __if_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.IfStmt)

        condition_value = self._ctx.parse_value(stmt.stmt_id, stmt.condition)

        # check condition type
        self._ctx.ty_assignable_check(TypeSpace.bool_id, condition_value)

        # process then body
        self.__process_block(stmt.then_body, self._code_block_handlers)

        if stmt.else_body is not None:
            self.__process_block(stmt.else_body, self._code_block_handlers)

        # emit if stmt
        self._ctx.cgir_emit(cir.If.from_gir(
            stmt,
            condition_value,
        ))

    def __for_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.ForStmt)

        if stmt.init_body is not None:
            self.__process_block(stmt.init_body, self._code_block_handlers)
        if stmt.condition_prebody is not None:
            self.__process_block(stmt.condition_prebody, self._code_block_handlers)

        condition_value = self._ctx.parse_value(stmt.stmt_id, stmt.condition)

        # check condition type
        self._ctx.ty_assignable_check(TypeSpace.bool_id, condition_value)

        self.__process_block(stmt.body, self._code_block_handlers)

        if stmt.update_body is not None:
            self.__process_block(stmt.update_body, self._code_block_handlers)

        # emit for stmt
        self._ctx.cgir_emit(cir.For.from_gir(
            stmt,
            condition_value,
        ))

    def __forin_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.ForInStmt)

        # parse iterable
        iterable_value = self._ctx.parse_value(stmt.stmt_id, stmt.iterable)

        iterable_type = self._ctx.ty_get(iterable_value.type_id)
        if isinstance(iterable_type, ty.ArrayType):
            self.__forin_array(stmt, iterable_value, iterable_type)
            return

        # resolve iterator type
        # implicit call to into_iter()
        lookup_result = self._ctx.ty_method_lookup(
            iterable_value, "into_iter", None, []
        )

        # perform auto deref if needed (respect Deref trait overloads)
        for _ in range(lookup_result.deref_levels):
            op_result = self._ctx.ty_unary_op_check(IR.Operator.Star, iterable_value)

            if op_result.method_type is None:
                iterable_value = self._ctx.cgir_build_unary_op(
                    stmt,
                    IR.Operator.Star,
                    iterable_value,
                    op_result.result_type,
                ).target
                continue

            deref_call_stmt = self._ctx.cgir_build_method_call(
                stmt,
                iterable_value,
                op_result.method_type,
                [],
                op_result.result_type,
            )

            self.__worklist.append(self._ctx.ty_resolve_def_point(op_result.method_type))

            assert deref_call_stmt.target is not None
            # Overloaded deref lowers to *receiver.deref()
            derefed_id = self._ctx.ty_deref(deref_call_stmt.target.type_id)
            iterable_value = self._ctx.cgir_build_unary_op(
                stmt,
                IR.Operator.Star,
                deref_call_stmt.target,
                derefed_id,
            ).target

        self.__worklist.append(self._ctx.ty_resolve_def_point(lookup_result.method_id))
        into_iter_ty = self._ctx.ty_get(lookup_result.method_id).expect_method()
        # call into_iter
        call_stmt = self._ctx.cgir_build_method_call(
            stmt, iterable_value, lookup_result.method_id, [], into_iter_ty.return_type(self._ctx.ty_instantiate), emit=True
        )
        assert call_stmt.target is not None
        it_var = call_stmt.target

        # resolve item type
        # implicit call to next()
        lookup_result = self._ctx.ty_method_lookup(
            it_var, "next", None, []
        )
        self.__worklist.append(self._ctx.ty_resolve_def_point(lookup_result.method_id))
        next_ty = self._ctx.ty_get(lookup_result.method_id).expect_method()
        option_ty = self._ctx.ty_get(next_ty.return_type(self._ctx.ty_instantiate)).expect_enum()

        # extract item type from Option<T>
        if option_ty.name != "Option":
            raise YianTypeError(f"Iterator::next() must return Option<T>, got '{self._ctx.ty_formatter(option_ty.type_id)}'")

        some_variant = option_ty.get_variant_by_name("Some", self._ctx.ty_instantiate)
        if some_variant is None or some_variant.payload is None:
            raise YianTypeError("Invalid Option type returned by iterator")

        payload_ty = some_variant.payload
        item_field = self._ctx.ty_get(payload_ty).expect_struct().get_field_by_name("val", self._ctx.ty_instantiate)
        assert item_field is not None
        item_ty = item_field.type_id

        # register iterator variable
        # The iterator variable is only available in the loop body
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.iterator)
        loop_var_symbol = IR.VariableSymbol(symbol_id, stmt.iterator, item_ty)
        self._ctx.symbol_register(loop_var_symbol)
        loop_var = IR.Variable(symbol_id, stmt.iterator, item_ty, False)

        # 1. Build next() call (inside loop)
        next_call_stmt = self._ctx.cgir_build_method_call(
            stmt, it_var, next_ty.type_id, [], option_ty.type_id, emit=False
        )
        assert next_call_stmt.target is not None
        next_val = next_call_stmt.target

        # 2. Build None case (break)
        break_stmt = self._ctx.cgir_build_break(stmt, emit=False)
        none_case = self._ctx.cgir_build_enum_case(stmt, ["None"], [break_stmt], emit=False)

        # 3. Build Some case
        symbol_id = self._ctx.symbol_register_def(stmt.stmt_id, f"payload_{stmt.stmt_id}")
        payload_var_symbol = IR.VariableSymbol(symbol_id, f"payload_{stmt.stmt_id}", payload_ty)
        self._ctx.symbol_register(payload_var_symbol)
        payload_var = IR.Variable(symbol_id, payload_var_symbol.name, payload_ty, False)

        assign_loop_var = self._ctx.cgir_build_field_access(stmt, payload_var, item_field, emit=False)
        assign_loop_var.target = loop_var

        some_case_stmts: list[cir.CheckedGIR] = [assign_loop_var]
        some_case = self._ctx.cgir_build_enum_payload_case(stmt, "Some", payload_var, some_case_stmts, emit=False)
        self.__emit_block_into(stmt.body, some_case.body)

        # 4. Build Match
        match_stmt = self._ctx.cgir_build_match(stmt, next_val, [some_case, none_case], emit=False)

        # 5. Build Loop
        loop_stmts = [next_call_stmt, match_stmt]
        self._ctx.cgir_build_loop(stmt, loop_stmts, emit=True)

    def __forin_array(self, stmt: ir.ForInStmt, iterable_value: IR.TypedValue, array_type: ty.ArrayType) -> None:
        """
        Lower for-in over native arrays into an explicit loop.
        """
        # register loop variable
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.iterator)
        loop_var_symbol = IR.VariableSymbol(symbol_id, stmt.iterator, array_type.element_type)
        self._ctx.symbol_register(loop_var_symbol)
        loop_var = IR.Variable(symbol_id, stmt.iterator, array_type.element_type, False)

        # synthesize index variable
        idx_name = f"__forin_idx_{stmt.stmt_id}"
        idx_symbol_id = self._ctx.symbol_register_def(stmt.stmt_id, idx_name)
        idx_symbol = IR.VariableSymbol(idx_symbol_id, idx_name, TypeSpace.u64_id, True)
        self._ctx.symbol_register(idx_symbol)
        idx_var = IR.Variable(idx_symbol_id, idx_name, TypeSpace.u64_id, True)

        zero = IR.IntegerLiteral(0)
        zero.type_id = TypeSpace.u64_id
        self._ctx.cgir_build_assign(stmt, zero, idx_var, emit=True)

        # if i >= len { break }
        arr_len = IR.IntegerLiteral(array_type.length)
        arr_len.type_id = TypeSpace.u64_id
        cmp_stmt = self._ctx.cgir_build_binary_op(stmt, IR.Operator.Ge, idx_var, arr_len, TypeSpace.bool_id, emit=False)
        break_stmt = self._ctx.cgir_build_break(stmt, emit=False)
        if_stmt = self._ctx.cgir_build_if(stmt, cmp_stmt.target, [break_stmt], None, emit=False)

        # elem = array[i]
        elem_stmt = self._ctx.cgir_build_binary_op(
            stmt,
            IR.Operator.Index,
            iterable_value,
            idx_var,
            array_type.element_type,
            emit=False,
        )
        assign_loop_var = self._ctx.cgir_build_assign(stmt, elem_stmt.target, loop_var, emit=False)

        loop_stmts = [cmp_stmt, if_stmt, elem_stmt, assign_loop_var]
        loop_stmt = self._ctx.cgir_build_loop(stmt, loop_stmts, emit=True)

        # emit original loop body into the loop block
        self.__emit_block_into(stmt.body, loop_stmt.body)

        # i = i + 1
        one = IR.IntegerLiteral(1)
        one.type_id = TypeSpace.u64_id
        add_stmt = self._ctx.cgir_build_binary_op(stmt, IR.Operator.Add, idx_var, one, TypeSpace.u64_id, emit=False)
        inc_stmt = self._ctx.cgir_build_assign(stmt, add_stmt.target, idx_var, emit=False)

        add_stmt.metadata.parent_stmt_id = loop_stmt.body
        inc_stmt.metadata.parent_stmt_id = loop_stmt.body
        self._ctx.cgir_emit(add_stmt)
        self._ctx.cgir_emit(inc_stmt)

    def __loop_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.LoopStmt)

        self.__process_block(stmt.body, self._code_block_handlers)

        # emit loop stmt
        self._ctx.cgir_emit(cir.Loop.from_gir(
            stmt,
        ))

    def __switch_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.SwitchStmt)

        # parse condition
        condition_value = self._ctx.parse_value(stmt.stmt_id, stmt.condition)
        condition_type = self._ctx.ty_get(condition_value.type_id)

        if isinstance(condition_type, ty.IntType):
            self.__int_switch(stmt, condition_value)
        elif isinstance(condition_type, ty.CharType):
            self.__char_switch(stmt, condition_value)
        elif isinstance(condition_type, ty.EnumType):
            self.__enum_switch(stmt, condition_value, condition_type)
        else:
            raise YianTypeError("Invalid switch condition type")

    def __int_switch(self, stmt: ir.SwitchStmt, condition_value: IR.TypedValue) -> None:
        case_values: set[int] = set()
        default_flag = False

        def case_handler(case_stmt: ir.GIRStmt) -> None:
            assert isinstance(case_stmt, ir.CaseStmt)

            # make sure no payload
            if case_stmt.payload is not None:
                raise YianTypeError("Int case cannot have payload")

            # process case values
            case_list: list[int] = []
            for value in case_stmt.values:
                type_def = self._ctx.parse_value(case_stmt.stmt_id, value)
                if not isinstance(type_def, IR.IntegerLiteral):
                    raise YianTypeError("Invalid case condition for int switch")
                if type_def.value in case_values:
                    raise SemanticError("Duplicate match condition")
                case_values.add(type_def.value)
                case_list.append(type_def.value)

            # process case body
            self.__process_block(case_stmt.body, self._code_block_handlers)

            # emit case
            self._ctx.cgir_emit(cir.IntCase.from_gir(
                case_stmt,
                case_list,
            ))

        def default_handler(default_stmt: ir.GIRStmt) -> None:
            assert isinstance(default_stmt, ir.DefaultStmt)

            # make sure only one default
            nonlocal default_flag
            if default_flag:
                raise SemanticError("Multiple default case")
            default_flag = True

            # process default body
            if default_stmt.body is not None:
                self.__process_block(default_stmt.body, self._code_block_handlers)

            # emit default
            self._ctx.cgir_emit(cir.DefaultCase.from_gir(
                default_stmt,
            ))

        self.__process_block(stmt.body, {
            ir.CaseStmt: case_handler,
            ir.DefaultStmt: default_handler,
        })

        # exhaustive check
        if not default_flag:
            raise SemanticError("Int switch is not exhaustive")

        # emit switch
        self._ctx.cgir_emit(cir.Match.from_gir(
            stmt,
            condition_value,
            default_flag,
        ))

    def __char_switch(self, stmt: ir.SwitchStmt, condition_value: IR.TypedValue) -> None:
        case_values: set[str] = set()
        default_flag = False

        def case_handler(case_stmt: ir.GIRStmt) -> None:
            assert isinstance(case_stmt, ir.CaseStmt)

            # make sure no payload
            if case_stmt.payload is not None:
                raise YianTypeError("Char case cannot have payload")

            # process case values
            case_list: list[str] = []
            for value in case_stmt.values:
                char_literal = self._ctx.parse_value(case_stmt.stmt_id, value)
                if not isinstance(char_literal, IR.CharLiteral):
                    raise YianTypeError("Invalid case condition for char switch")
                if char_literal.value in case_values:
                    raise SemanticError("Duplicate match condition")
                case_values.add(char_literal.value)
                case_list.append(char_literal.value)

            # process case body
            self.__process_block(case_stmt.body, self._code_block_handlers)

            # emit case
            self._ctx.cgir_emit(cir.CharCase.from_gir(
                case_stmt,
                case_list,
            ))

        def default_handler(default_stmt: ir.GIRStmt) -> None:
            assert isinstance(default_stmt, ir.DefaultStmt)

            # make sure only one default
            nonlocal default_flag
            if default_flag:
                raise SemanticError("Multiple default case")
            default_flag = True

            # process default body
            if default_stmt.body is not None:
                self.__process_block(default_stmt.body, self._code_block_handlers)

            # emit default
            self._ctx.cgir_emit(cir.DefaultCase.from_gir(
                default_stmt,
            ))

        self.__process_block(stmt.body, {
            ir.CaseStmt: case_handler,
            ir.DefaultStmt: default_handler,
        })

        # exhaustive check
        if not default_flag:
            raise SemanticError("Char switch is not exhaustive")

        # emit switch
        self._ctx.cgir_emit(cir.Match.from_gir(
            stmt,
            condition_value,
            default_flag,
        ))

    def __enum_switch(self, stmt: ir.SwitchStmt, condition_value: IR.TypedValue, condition_type: ty.EnumType) -> None:
        case_variants: set[str] = set()
        default_flag = False

        def case_handler(case_stmt: ir.GIRStmt) -> None:
            assert isinstance(case_stmt, ir.CaseStmt)

            if case_stmt.payload is not None:
                # process case with payload
                if len(case_stmt.values) != 1:
                    raise SemanticError("Multiple condition cannot have payload")

                # find variant
                variant_name = case_stmt.values[0]
                variant = condition_type.get_variant_by_name(variant_name, self._ctx.ty_instantiate)
                if variant is None:
                    raise NameResolutionError(f"Unknown enum variant: {variant_name}")
                if variant.payload is None:
                    raise YianTypeError(f"Variant {variant.name} has no payload")
                if variant.name in case_variants:
                    raise SemanticError("Duplicate match condition")
                case_variants.add(variant.name)

                # process payload symbol
                symbol_id = self._ctx.symbol_lookup_def(case_stmt.stmt_id, case_stmt.payload)
                payload_symbol = IR.VariableSymbol(symbol_id, case_stmt.payload, variant.payload)
                self._ctx.symbol_register(payload_symbol)

                assert payload_symbol.type_id is not None

                # process case body
                self.__process_block(case_stmt.body, self._code_block_handlers)

                # emit case
                self._ctx.cgir_emit(cir.EnumPayloadCase.from_gir(
                    case_stmt,
                    variant.name,
                    IR.Variable(payload_symbol.symbol_id, payload_symbol.name, payload_symbol.type_id, True),
                ))

            else:
                # process case without payload
                # find variants
                for variant_name in case_stmt.values:
                    variant = condition_type.get_variant_by_name(variant_name, self._ctx.ty_instantiate)
                    if variant is None:
                        raise NameResolutionError(f"Unknown enum variant: {variant_name}")
                    if variant.name in case_variants:
                        raise SemanticError("Duplicate match condition")
                    case_variants.add(variant.name)

                # process case body
                self.__process_block(case_stmt.body, self._code_block_handlers)

                # emit case
                self._ctx.cgir_emit(cir.EnumCase.from_gir(
                    case_stmt,
                    case_stmt.values,
                ))

        def default_handler(default_stmt: ir.GIRStmt) -> None:
            assert isinstance(default_stmt, ir.DefaultStmt)

            # make sure only one default
            nonlocal default_flag
            if default_flag:
                raise SemanticError("Multiple default case")
            default_flag = True

            # process default body
            if default_stmt.body is not None:
                self.__process_block(default_stmt.body, self._code_block_handlers)

            # emit default
            self._ctx.cgir_emit(cir.DefaultCase.from_gir(
                default_stmt,
            ))

        self.__process_block(stmt.body, {
            ir.CaseStmt: case_handler,
            ir.DefaultStmt: default_handler,
        })

        # exhaustive check
        if not default_flag:
            variant_num = condition_type.variant_count
            condition_num = len(case_variants)
            if variant_num != condition_num:
                raise SemanticError("Enum switch is not exhaustive")

        # emit switch
        self._ctx.cgir_emit(cir.Match.from_gir(
            stmt,
            condition_value,
            default_flag,
        ))

    def __block(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.BlockStmt)
        self.__process_block(stmt.stmt_id, self._code_block_handlers)

    def __assert_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.AssertStmt)

        # parse condition and message
        condition_value = self._ctx.parse_value(stmt.stmt_id, stmt.condition)
        message_value = self._ctx.parse_value(stmt.stmt_id, stmt.message)

        # check type
        self._ctx.ty_assignable_check(TypeSpace.bool_id, condition_value)
        self._ctx.ty_assignable_check(TypeSpace.str_id, message_value)

        # emit assert
        self._ctx.cgir_emit(cir.Assert.from_gir(stmt, condition_value, message_value))

    def __del_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.DeleteStmt)

        # parse target
        target_value = self._ctx.parse_value(stmt.stmt_id, stmt.target)

        # check delete
        self._ctx.ty_delete_check(target_value)

        # emit delete
        self._ctx.cgir_emit(cir.Delete.from_gir(stmt, target_value))

    def __new_object(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.NewObjectStmt)

        if stmt.data_type is not None:
            self.__new_object_from_type(stmt)
            return

        self.__new_object_from_init(stmt)

    def __new_object_from_type(self, stmt: ir.NewObjectStmt) -> None:
        assert stmt.data_type is not None
        assert stmt.init_value is None

        # parse data type
        type_id = self._ctx.parse_type(stmt.stmt_id, stmt.data_type)

        # check return type
        return_type = self._ctx.ty_alloc_pointer(type_id)
        return_value = IR.Variable(-1, "", return_type, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, False)

        # emit new object
        self._ctx.cgir_emit(cir.DynType.from_gir(stmt, target_var, type_id))

    def __new_object_from_init(self, stmt: ir.NewObjectStmt) -> None:
        assert stmt.data_type is None
        assert stmt.init_value is not None

        # parse init value
        init_value = self._ctx.parse_value(stmt.stmt_id, stmt.init_value)

        # check return type
        return_type = self._ctx.ty_alloc_pointer(init_value.type_id)
        return_value = IR.Variable(-1, "", return_type, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, False)

        # emit new object
        self._ctx.cgir_emit(cir.DynValue.from_gir(stmt, target_var, init_value))

    def __new_array(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.NewArrayStmt)

        # parse element type and length
        element_type_id = self._ctx.parse_type(stmt.stmt_id, stmt.data_type)
        length_value = self._ctx.parse_value(stmt.stmt_id, stmt.length)

        # check length type
        self._ctx.ty_assignable_check(TypeSpace.u64_id, length_value)

        # check return type
        return_type = self._ctx.ty_alloc_pointer(element_type_id)
        return_value = IR.Variable(-1, "", return_type, False)  # dummy value to do type checking
        target_var = self.__check_target(stmt, stmt.target, return_value, False)

        # emit new array
        self._ctx.cgir_emit(cir.DynArray.from_gir(stmt, target_var, element_type_id, length_value))

    def __variable_decl(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.VariableDeclStmt)

        # parse variable type
        var_type_id = self._ctx.parse_type(stmt.stmt_id, stmt.data_type)

        # register variable symbol
        symbol_id = self._ctx.symbol_lookup_def(stmt.stmt_id, stmt.name)
        var_symbol = IR.VariableSymbol(symbol_id, stmt.name, var_type_id)
        self._ctx.symbol_register(var_symbol)

        # create variable
        var = IR.Variable(stmt.stmt_id, stmt.name, var_type_id, True)

        # emit variable decl
        self._ctx.cgir_emit(cir.VarDecl.from_gir(stmt, var))

    def __break_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.BreakStmt)

        # emit break
        self._ctx.cgir_emit(cir.Break.from_gir(stmt))

    def __continue_stmt(self, stmt: ir.GIRStmt) -> None:
        assert isinstance(stmt, ir.ContinueStmt)

        # emit continue
        self._ctx.cgir_emit(cir.Continue.from_gir(stmt))

    def __oob_check(self, stmt: ir.GIRStmt, container: IR.TypedValue, index: IR.TypedValue) -> None:
        """
        Insert out-of-bounds check for array indexing

        - if `index` is literal, check at compile time
        - if `index` is variable, emit runtime check
        """
        assert isinstance(container, IR.Variable)

        index_ty = self._ctx.ty_get(index.type_id)
        if not isinstance(index_ty, ty.IntType):
            return  # index is not integer, skip oob check

        container_type = self._ctx.ty_get(container.type_id)
        match container_type:
            case ty.ArrayType():
                self.__array_oob_check(stmt, container, index, container_type)
            case ty.SliceType():
                self.__slice_oob_check(stmt, container, index, container_type)

    def __slice_oob_check(self, stmt: ir.GIRStmt, container: IR.Variable, index: IR.TypedValue, container_type: ty.SliceType) -> None:
        """
        Insert out-of-bounds check for slice indexing

        1. dynamic check for all cases
        2. use intrinsic `slice_len` instruction to get slice length
        """
        # dynamic check

        # if index >= slice_len(container) {
        #     panic("Slice '{slice_var_name}': index '{index_var_name}' out of bounds")
        # }

        # lookup len() method for slice
        lookup_result = self._ctx.ty_method_lookup(container, "len", None, [])
        if lookup_result.deref_levels > 0:
            raise CompilerError("Len method for slice cannot require dereference")

        # call len() method to get slice length
        self.__worklist.append(self._ctx.ty_resolve_def_point(lookup_result.method_id))
        len_call_stmt = self._ctx.cgir_build_method_call(stmt, container, lookup_result.method_id, [], TypeSpace.u64_id)
        assert len_call_stmt.target is not None
        slice_length = len_call_stmt.target

        # build comparison
        cmp_stmt = self._ctx.cgir_build_binary_op(stmt, IR.Operator.Ge, index, slice_length, TypeSpace.bool_id)

        # prepare panic message
        panic_message = f"Slice '{container.name}': index '{index}' out of bounds\n"
        panic_message_value = IR.StringLiteral(panic_message.encode('utf-8'))

        # build panic
        panic_stmt = self._ctx.cgir_build_panic(stmt, panic_message_value, emit=False)

        # build if
        self._ctx.cgir_build_if(stmt, cmp_stmt.target, [panic_stmt], None)

    def __array_oob_check(self, stmt: ir.GIRStmt, container: IR.Variable, index: IR.TypedValue, container_type: ty.ArrayType) -> None:
        """
        Insert out-of-bounds check for array indexing

        1. static check for literal index
        2. dynamic check for variable index
        """
        array_length = container_type.length

        # static check
        if isinstance(index, IR.LiteralValue):
            assert isinstance(index, IR.IntegerLiteral)
            if index.value < 0 or index.value >= array_length:
                raise SemanticError(f"Array '{container.name}': index '{index.value}' out of bounds")
            return

        # dynamic check

        # if index >= array_length {
        #     panic("Array '{array_var_name}': index '{index_var_name}' out of bounds")
        # }

        # prepare length value
        length_value = IR.IntegerLiteral(array_length, None, TypeSpace.u64_id)

        # build comparison
        cmp_stmt = self._ctx.cgir_build_binary_op(stmt, IR.Operator.Ge, index, length_value, TypeSpace.bool_id)

        # prepare panic message
        panic_message = f"Array '{container.name}': index '{index.name}' out of bounds\n"
        panic_message_value = IR.StringLiteral(panic_message.encode('utf-8'))

        # build panic
        panic_stmt = self._ctx.cgir_build_panic(stmt, panic_message_value, emit=False)

        # build if
        self._ctx.cgir_build_if(stmt, cmp_stmt.target, [panic_stmt], None)
