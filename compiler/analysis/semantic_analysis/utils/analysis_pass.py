"""
This module defines the structure for analysis passes used in type analysis.

- `UnitPass`: An abstract base class for creating analysis passes that operate on unit data.
"""

from abc import ABC, abstractmethod

from compiler.analysis.semantic_analysis.utils.context import SemanticCtx
from compiler.config.constants import ROOT_BLOCK_ID
from compiler.config.defs import IRHandlerMap, StmtId
from compiler.unit_data import UnitData
from compiler.utils.IR import cgir as cir
from compiler.utils.IR import gir as ir
from compiler.utils.IR.def_point import DefPoint


class UnitPass(ABC):
    def __init__(self, ctx: SemanticCtx):
        """
        Initializes the analysis pass with the given unit data and context.

        Normally, you don't need to override this method in subclasses since ctx maintains everything needed.
        """
        self._ctx = ctx

    @property
    @abstractmethod
    def _code_block_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        """
        The handler mapping for processing function/method body.
        """
        pass

    @property
    @abstractmethod
    def _top_level_handlers(self) -> IRHandlerMap[ir.GIRStmt]:
        """
        The handler mapping for processing top-level code blocks.
        """
        pass

    @abstractmethod
    def _run_prelude(self) -> None:
        """
        Operations to perform before running the main analysis pass.
        """
        pass

    @abstractmethod
    def _run_postlude(self) -> None:
        """
        Operations to perform after running the main analysis pass.
        """
        pass

    @abstractmethod
    def _unit_prelude(self) -> None:
        """
        Operations to perform before processing each unit.
        """
        pass

    @abstractmethod
    def _unit_postlude(self) -> None:
        """
        Operations to perform after processing each unit.
        """
        pass

    def _process_block(self, block_id: StmtId, handlers: IRHandlerMap[ir.GIRStmt]) -> None:
        self._ctx.process_gir_block(block_id, handlers)

    def run(self, unit_datas: set[UnitData]) -> None:
        """
        Main method to execute the analysis pass.

        DO NOT OVERRIDE THIS METHOD IN SUBCLASSES.
        """
        self._run_prelude()
        for unit_data in unit_datas:
            self._ctx.set_unit_data(unit_data)
            self._unit_prelude()
            self._process_block(ROOT_BLOCK_ID, self._top_level_handlers)
            self._unit_postlude()
            self._ctx.unset_unit_data()
        self._run_postlude()


class DefPointPass(ABC):
    def __init__(self, ctx: SemanticCtx):
        """
        Initializes the analysis pass with the given unit data and context.

        Normally, you don't need to override this method in subclasses since ctx maintains everything needed.
        """
        self._ctx = ctx

    @property
    @abstractmethod
    def _def_point_handlers(self) -> IRHandlerMap[cir.CheckedGIR]:
        """
        The handler mapping for processing CGIR statements at specific definition points.
        """
        pass

    @abstractmethod
    def _run_prelude(self) -> None:
        """
        Operations to perform before running the main analysis pass.
        """
        pass

    @abstractmethod
    def _run_postlude(self) -> None:
        """
        Operations to perform after running the main analysis pass.
        """
        pass

    @abstractmethod
    def _def_point_prelude(self) -> None:
        """
        Operations to perform before processing each definition point.
        """
        pass

    @abstractmethod
    def _def_point_postlude(self) -> None:
        """
        Operations to perform after processing each definition point.
        """
        pass

    def _process_block(self, block_id: StmtId) -> None:
        self._ctx.process_cgir_block(block_id, self._def_point_handlers)

    def run(self, def_points: set[DefPoint]) -> None:
        """
        Main method to execute the analysis pass.

        DO NOT OVERRIDE THIS METHOD IN SUBCLASSES.
        """
        self._run_prelude()
        for def_point in def_points:
            self._ctx.set_def_point(def_point)
            self._ctx.set_unit_data_by_id(def_point.unit_id)
            self._def_point_prelude()
            self._process_block(def_point.root_block_id)
            self._def_point_postlude()
            self._ctx.unset_unit_data()
            self._ctx.unset_def_point()
        self._run_postlude()
