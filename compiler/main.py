#!/usr/bin/env python3

import os

import config.config as config

from lian.main import Lian
from lian.args_parser import ArgsParser
from lian.lang.lang_analysis import LangAnalysis

from compiler.config.defs import UnitId

from compiler.utils.ty import MethodRegistry, TypeSpace
from compiler.utils.IR import DefPoint
from compiler.unit_data import UnitData

from compiler.frontend.yian_parser import YianParser

from compiler.analysis.semantic_analysis.utils.context import SemanticCtx
from compiler.analysis.semantic_analysis.utils.analysis_pass import DefPointPass, UnitPass
from compiler.analysis.semantic_analysis.symbol_id_alloc import SymbolIDAllocator
from compiler.analysis.semantic_analysis.symbol_collector import SymbolCollector
from compiler.analysis.semantic_analysis.export_collector import ExportCollector
from compiler.analysis.semantic_analysis.import_resolver import ImportResolver
from compiler.analysis.semantic_analysis.decl_scanner import DeclScanner
from compiler.analysis.semantic_analysis.impl_validator import ImplValidator
from compiler.analysis.semantic_analysis.type_checker import TypeChecker
from compiler.analysis.semantic_analysis.visibility_analyzer import VisibilityAnalyzer
from compiler.analysis.semantic_analysis.variable_analyzer import VariableAnalyzer

from compiler.backend.utils.context import LLVMCtx
from compiler.backend.translator import LowLevelIRTranslator


class CompilerDriver:
    def __init__(self, lian: Lian):
        self.lian = lian
        self.options = lian.options

        self.__unit_datas: dict[UnitId, UnitData] = {}
        self.__def_points: set[DefPoint] = set()

        # ====== initialization =====
        self.__init_compiler_workspace()

    def __init_compiler_workspace(self):
        """
        Create necessary directories in the compiler workspace.
        """
        options = self.options

        options.basic_dir = os.path.join(options.workspace, config.BASIC_DIR)

        intermediate_results_dir = os.path.join(options.workspace, config.INTERMEDIATE_RESULTS_DIR)
        options.intermediate_results_dir = intermediate_results_dir
        os.makedirs(intermediate_results_dir, exist_ok=True)

        results_dir = os.path.join(options.workspace, config.RESULTS_DIR)
        options.results_dir = results_dir
        os.makedirs(results_dir, exist_ok=True)

        generics_dir = os.path.join(options.workspace, config.RESULTS_DIR)
        options.out_dir = generics_dir
        os.makedirs(generics_dir, exist_ok=True)

        objects_dir = os.path.join(options.workspace, config.OBJECTS_DIR)
        options.objects_dir = objects_dir
        os.makedirs(objects_dir, exist_ok=True)

        bin_dir = os.path.join(options.workspace, config.BIN_DIR)
        options.bins_dir = bin_dir
        os.makedirs(bin_dir, exist_ok=True)

        log_dir = os.path.join(options.workspace, config.LOG_DIR)
        options.log_dir = log_dir
        os.makedirs(log_dir, exist_ok=True)

    def run(self):
        semantic_ctx = self.__semantic_analysis()
        llvm_ctx = semantic_ctx.into_llvm_ctx()
        self.__translate(llvm_ctx)
        return self

    def __dump_def_points(self, def_points: set[DefPoint]) -> None:
        if not self.options.debug:
            return

        for dp in def_points:
            unit_name = self.__unit_datas[dp.unit_id].unit_name

            cgir_export_path = os.path.join(
                self.options.log_dir,
                f"{unit_name}_DefPoint_{dp.procedure_name}_{dp.type_id}_CGIR.txt"
            )

            try:
                dp.export(cgir_export_path)
                print(f"Exported CGIR for DefPoint '{unit_name}::{dp.procedure_name}::{dp.type_id}' at {cgir_export_path}")
            except Exception as e:
                print(f"Failed to export CGIR for DefPoint '{unit_name}::{dp.procedure_name}::{dp.type_id}': {e}")

    def __dump_unit_datas(self, unit_datas: dict[UnitId, UnitData]) -> None:
        if not self.options.debug:
            return

        for unit_id, unit_data in unit_datas.items():
            unit_name = unit_data.unit_name

            cgir_export_path = os.path.join(
                self.options.log_dir,
                f"UnitData_{unit_data.unit_name}_GIR.txt"
            )

            try:
                unit_data.export(cgir_export_path)
                print(f"Exported GIR for UnitData '{unit_name}::{unit_id}' at {cgir_export_path}")
            except Exception as e:
                print(f"Failed to export GIR for UnitData '{unit_name}::{unit_id}': {e}")

    def __semantic_analysis(self) -> SemanticCtx:
        # 1. PASS 1: GIR conversion
        unit_infos = {
            unit_info.unit_id: unit_info
            for unit_info in self.lian.loader.get_all_unit_info()
        }
        self.__unit_datas = {
            int(unit_id): UnitData(self.lian, unit_info)  # type: ignore
            for unit_id, unit_info in unit_infos.items()
        }
        max_gir_id = max(
            max(
                stmt.stmt_id
                for stmt in self.lian.loader.get_unit_gir(unit_id)  # type: ignore
            ) for unit_id in unit_infos.keys()
        )

        type_space = TypeSpace()
        method_registry = MethodRegistry(type_space)
        semantic_ctx = SemanticCtx(
            type_space,
            method_registry,
            self.__unit_datas,
            self.__def_points,
            max_gir_id,
        )

        unit_passes: list[type[UnitPass]] = [
            SymbolIDAllocator,
            SymbolCollector,
            ExportCollector,
            ImportResolver,
            DeclScanner,
            ImplValidator,
            TypeChecker,
        ]

        def_point_passes: list[type[DefPointPass]] = [
            VisibilityAnalyzer,
            VariableAnalyzer,
        ]

        print("\n\n=== Starting Unit Passes ===\n")
        print(f"Total Passes to run: {len(unit_passes)}\n")
        print(f"Units to analyze: {self.__unit_datas}\n")

        if self.options.debug:
            self.__dump_unit_datas(self.__unit_datas)

        for pass_cls in unit_passes:
            print("=" * 10, f"Running {pass_cls.__name__}", "=" * 10)

            pass_instance = pass_cls(semantic_ctx)
            pass_instance.run(set(self.__unit_datas.values()))

        print("\n\n=== Starting DefPoint Analysis Passes ===\n")
        print(f"Total Passes to run: {len(def_point_passes)}\n")
        print(f"DefPoints to analyze: {self.__def_points}\n")

        if self.options.debug:
            self.__dump_def_points(self.__def_points)

        for pass_cls in def_point_passes:
            print("=" * 10, f"Running {pass_cls.__name__}", "=" * 10)

            pass_instance = pass_cls(semantic_ctx)
            pass_instance.run(self.__def_points)

        semantic_ctx.ty_finalize()

        return semantic_ctx

    def __translate(self, llvm_ctx: LLVMCtx) -> None:
        LowLevelIRTranslator(llvm_ctx).run(self.__def_points).export(
            os.path.join(
                self.options.objects_dir,
                config.LLVM_IR_OUTPUT_FILE_NAME
            )
        )


class CompilerArgsParser(ArgsParser):
    def init(self):
        # Create the top-level parser
        subparsers = self.main_parser.add_subparsers(dest='sub_command')
        # Create the parser for the "lang" command
        parser_compile = subparsers.add_parser('compile', help="Compile an YIAN project or individual files")
        parser_run = subparsers.add_parser('run', help='Run the YIAN executable')

        for parser in [parser_compile, parser_run]:
            parser.add_argument('in_path', nargs='+', type=str, help='the input')
            parser.add_argument('-w', "--workspace", default=config.DEFAULT_WORKSPACE_PATH, type=str, help='the workspace directory (default:lian_workspace)')
            parser.add_argument("-f", "--force", action="store_true", help="Enable the FORCE mode for rewriting the workspace directory")
            parser.add_argument("-d", "--debug", action="store_true", help="Enable the DEBUG mode")
            parser.add_argument("-c", "--cores", default=1, help="Configure the available CPU cores")
            parser.add_argument("--strict-parse-mode", action="store_false", help="Enable the strict way to parse code")
            parser.add_argument("--dep_path", action="append", default=[], help="Add deps path")
            parser.add_argument("--generate-binary", action="store_true", help="Run the backend to generate binary files")
        return self

    def set_yian_default_options(self):
        self.options.lang = config.LANG_NAME
        self.options.workspace = config.DEFAULT_WORKSPACE_PATH
        self.options.noextern = True
        return self


class Compiler:
    def run_frontend(self):
        lian = Lian()
        lian.add_lang(config.LANG_NAME, config.LANG_EXTENSION, config.LANG_SO_PATH, YianParser)
        lian.options = CompilerArgsParser().init().set_yian_default_options().parse_cmds()
        lian.set_workspace_dir(config.YIAN_WORKSPACE_DIR)
        lian.init_submodules()
        LangAnalysis(lian).run()

        return lian

    def run(self):
        # 1. run frontend and get results
        lian = self.run_frontend()

        # 2. print welcome message
        print(
            "\n\n\t" + "/" * 60 + "\n"
            "\t////" + " " * 20 + "Yian Compiler" + " " * 18 + " ////\n"
            "\t" + "/" * 60 + "\n"
        )
        # 3. run analysis and backend compiler worker
        CompilerDriver(lian).run()


def main():
    Compiler().run()


if __name__ == "__main__":
    main()
