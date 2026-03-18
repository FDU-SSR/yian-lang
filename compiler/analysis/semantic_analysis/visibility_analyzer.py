from compiler.analysis.semantic_analysis.utils.analysis_pass import DefPointPass
from compiler.config.constants import AccessMode
from compiler.config.defs import IRHandlerMap
from compiler.utils import ty
from compiler.utils.errors import SemanticError
from compiler.utils.IR import cgir as cir


class VisibilityAnalyzer(DefPointPass):
    def __init__(self, ctx):
        super().__init__(ctx)

    @property
    def _def_point_handlers(self) -> IRHandlerMap[cir.CheckedGIR]:
        return {
            cir.VarDecl: lambda stmt: None,

            cir.BinaryOpAssign: lambda stmt: None,
            cir.UnaryOpAssign: lambda stmt: None,
            cir.Assign: lambda stmt: None,
            cir.FieldAccess: self.__field_access,
            cir.Cast: lambda stmt: None,
            cir.FuncCall: self.__func_call,
            cir.MethodCall: self.__method_call,
            cir.StaticMethodCall: self.__static_method_call,
            cir.Invoke: lambda stmt: None,
            cir.StructConstruct: self.__struct_construct,
            cir.VariantConstruct: self.__variant_construct,

            cir.If: self.__if_stmt,
            cir.For: self.__for_stmt,
            cir.Loop: self.__loop_stmt,
            cir.Match: self.__match_stmt,
            cir.Block: self.__block,

            cir.Break: lambda stmt: None,
            cir.Continue: lambda stmt: None,
            cir.Return: lambda stmt: None,

            cir.Delete: lambda stmt: None,
            cir.DynType: lambda stmt: None,
            cir.DynValue: lambda stmt: None,
            cir.DynArray: lambda stmt: None,

            cir.Assert: lambda stmt: None,
            cir.Read: lambda stmt: None,
            cir.Write: lambda stmt: None,
            cir.Open: lambda stmt: None,
            cir.Close: lambda stmt: None,
            cir.Panic: lambda stmt: None,
            cir.SizeOf: lambda stmt: None,
            cir.BitCast: lambda stmt: None,
            cir.ByteOffset: lambda stmt: None,
            cir.MemCopy: lambda stmt: None,

            cir.IntCase: self.__case_stmt,
            cir.CharCase: self.__case_stmt,
            cir.EnumCase: self.__case_stmt,
            cir.EnumPayloadCase: self.__case_stmt,
            cir.DefaultCase: self.__default_case,
        }

    def _run_prelude(self) -> None:
        pass

    def _run_postlude(self) -> None:
        pass

    def _def_point_prelude(self) -> None:
        pass

    def _def_point_postlude(self) -> None:
        pass

    # ========== Handlers ==========
    def __block(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Block)
        self._process_block(stmt.stmt_id)

    def __if_stmt(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.If)
        self._process_block(stmt.then_body)
        if stmt.else_body is not None:
            self._process_block(stmt.else_body)

    def __for_stmt(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.For)
        if stmt.init_body is not None:
            self._process_block(stmt.init_body)
        if stmt.condition_prebody is not None:
            self._process_block(stmt.condition_prebody)
        self._process_block(stmt.body)
        if stmt.update_body is not None:
            self._process_block(stmt.update_body)

    def __loop_stmt(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Loop)
        self._process_block(stmt.body)

    def __match_stmt(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.Match)
        self._process_block(stmt.body)

    def __case_stmt(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, (cir.IntCase, cir.CharCase, cir.EnumCase, cir.EnumPayloadCase))
        self._process_block(stmt.body)

    def __default_case(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.DefaultCase)
        if stmt.body is not None:
            self._process_block(stmt.body)

    # ---- visibility checks ----

    def __check_custom_def_visible(self, defn: ty.CustomDef, kind: str) -> None:
        if defn.is_public:
            return
        if defn.unit_id == self._ctx.unit_id:
            return
        raise SemanticError(
            f"{kind} '{defn.name}' is private to its defining unit (unit_id={defn.unit_id}) and "
            f"cannot be used from unit_id={self._ctx.unit_id}"
        )

    def __check_struct_def_visible(self, struct_def: ty.StructDef) -> None:
        self.__check_custom_def_visible(struct_def, "Struct")
        for field in struct_def.fields.values():
            if field.access_mode == AccessMode.Private and struct_def.unit_id != self._ctx.unit_id:
                raise SemanticError(
                    f"Struct '{struct_def.name}' has private field '{field.name}' and cannot be used from unit_id={self._ctx.unit_id}"
                )

    def __check_enum_def_visible(self, enum_def: ty.EnumDef) -> None:
        self.__check_custom_def_visible(enum_def, "Enum")

    def __check_method_visible(self, method_ty_id: int) -> None:
        method_ty = self._ctx.ty_get(method_ty_id).expect_method()
        self.__check_custom_def_visible(method_ty.method_def, "Method")

    def __check_function_visible(self, func_ty_id: int) -> None:
        func_ty = self._ctx.ty_get(func_ty_id).expect_function()
        self.__check_custom_def_visible(func_ty.function_def, "Function")

    def __func_call(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.FuncCall)
        self.__check_function_visible(stmt.func)

    def __method_call(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.MethodCall)
        self.__check_method_visible(stmt.method)

    def __static_method_call(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.StaticMethodCall)
        self.__check_method_visible(stmt.method)

    def __struct_construct(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.StructConstruct)
        ty_def = self._ctx.ty_get(stmt.struct_type).expect_struct()
        self.__check_struct_def_visible(ty_def.struct_def)

    def __variant_construct(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.VariantConstruct)
        enum_ty = self._ctx.ty_get(stmt.enum_type).expect_enum()
        self.__check_enum_def_visible(enum_ty.enum_def)

    def __field_access(self, stmt: cir.CheckedGIR) -> None:
        assert isinstance(stmt, cir.FieldAccess)

        receiver_ty_id = stmt.receiver.type_id
        receiver_ty = self._ctx.ty_get(receiver_ty_id)
        while isinstance(receiver_ty, ty.PointerType):
            receiver_ty_id = receiver_ty.pointee_type
            receiver_ty = self._ctx.ty_get(receiver_ty_id)
        struct_ty = receiver_ty.expect_struct()

        if stmt.field.access_mode != AccessMode.Private:
            return

        if struct_ty.struct_def.unit_id == self._ctx.unit_id:
            return

        raise SemanticError(
            f"Field '{stmt.field.name}' of struct '{self._ctx.ty_formatter(stmt.receiver.type_id)}' is private and "
            f"cannot be accessed from unit {self._ctx.unit_name}.an unless inside an impl for that struct"
        )
