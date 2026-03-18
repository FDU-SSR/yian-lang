"""
Error reporting module for the compiler.

This module provides functionality to report errors encountered during
the compilation process, including detailed information about the source
code location where the error occurred.

NOTEs:

1. The error reporting currently prints directly to standard output and exits the program. This may be refined in the future to allow for better error handling strategies.
"""

import sys
import traceback
from pathlib import Path
from typing import TYPE_CHECKING, NoReturn

if TYPE_CHECKING:
    from compiler.utils.IR import cgir as cir
    from compiler.utils.IR import gir as ir


class ErrorReporter:
    def __init__(self, original_path: str):
        self.original_path = original_path

    def __print_reporter_info(self, error: Exception):
        print("Traceback (most recent call last):")
        for line in traceback.format_tb(error.__traceback__):
            print(line, end="")
        print()

    def set_path(self, path: str):
        self.original_path = path

    def report(self, stmt: "ir.GIRStmt | cir.CheckedGIR", error: Exception) -> NoReturn:
        """
        Report an error related to a specific statement in the source code.
        """
        print("-" * 20)
        self.__print_reporter_info(error)

        start_row = stmt.pos.start_row
        start_column = stmt.pos.start_col
        end_row = stmt.pos.end_row
        end_column = stmt.pos.end_col

        print(error)
        print(f"--> {self.original_path}:{start_row + 1}:{start_column}")
        print(f"    {stmt}\n")

        src_file = Path(self.original_path)
        lines = src_file.read_text(encoding='utf-8').splitlines()

        start_line = lines[start_row]
        print(f"{start_line}")
        if start_row == end_row:
            # 起始行和结束行相同，打印从 start_column 到 end_column 的 ^
            marker = " " * (start_column - 1) + "^" * (end_column - start_column + 1)
        else:
            # 起始行和结束行不同，打印从 start_column 到行尾的 ^
            marker = " " * (start_column - 1) + "^" * (len(start_line) - start_column + 1)
        print(f"{marker}")

        sys.exit(-1)

    def global_report(self, error: Exception) -> NoReturn:
        """
        Report a global error not tied to a specific statement.
        """
        print("-" * 20)
        self.__print_reporter_info(error)

        print(error)
        print(f"--> {self.original_path}\n")

        sys.exit(-1)
