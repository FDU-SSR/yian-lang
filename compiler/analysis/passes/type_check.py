from compiler.analysis.ty.context import TypeCtx
from compiler.analysis.unit.def_point import DefPoint
from compiler.analysis.unit.unit_data import UnitData


class TypeCheck:
    def __init__(self, units: dict[int, UnitData], type_ctx: TypeCtx):
        self.__units = units
        self.__type_ctx = type_ctx

        self.__worklist: list[DefPoint] = []
        self.__def_points: dict[int, DefPoint] = {}  # type_id -> DefPoint

    def run(self) -> None:
        self.__find_main()

        processed_def: set[int] = set()
        while self.__worklist:
            def_point = self.__worklist.pop()
            if def_point.type_id in processed_def:
                continue
            processed_def.add(def_point.type_id)

            self.__type_check_def(def_point)

    def __find_main(self) -> DefPoint:
        raise NotImplementedError("TypeCheck.__find_main is not implemented yet")

    def __type_check_def(self, def_point: DefPoint) -> None:
        raise NotImplementedError("TypeCheck.__type_check_def is not implemented yet")
