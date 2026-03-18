from compiler.analysis.semantic_analysis.utils.analysis_pass import DefPointPass
from compiler.config.defs import IRHandlerMap
from compiler.utils.IR import cgir as cir


class VariableAnalyzer(DefPointPass):
    @property
    def _def_point_handlers(self) -> IRHandlerMap[cir.CheckedGIR]:
        return {
            cir.VarDecl: lambda stmt: None,

            cir.BinaryOpAssign: lambda stmt: None,
            cir.UnaryOpAssign: lambda stmt: None,
            cir.Assign: lambda stmt: None,
            cir.FieldAccess: lambda stmt: None,
            cir.Cast: lambda stmt: None,
            cir.FuncCall: lambda stmt: None,
            cir.MethodCall: lambda stmt: None,
            cir.StaticMethodCall: lambda stmt: None,
            cir.Invoke: lambda stmt: None,
            cir.StructConstruct: lambda stmt: None,
            cir.VariantConstruct: lambda stmt: None,

            cir.If: lambda stmt: None,
            cir.For: lambda stmt: None,
            cir.Loop: lambda stmt: None,
            cir.Match: lambda stmt: None,
            cir.Block: lambda stmt: None,

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
        }

    def _run_prelude(self) -> None:
        pass

    def _run_postlude(self) -> None:
        pass

    def _def_point_prelude(self) -> None:
        pass

    def _def_point_postlude(self) -> None:
        pass

    # ========= Handlers ==========
