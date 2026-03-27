import platform
import sys

from llvmlite import ir
from llvmlite.binding import create_target_data


class LowLevelSetup:
    def __init__(self):
        self.__module_name = "yian_module"
        self.__triple, self.__data_layout = self.__get_target_config()

        self.target_data = create_target_data(self.__data_layout)

    def __get_target_config(self) -> tuple[str, str]:
        arch = platform.machine().lower()

        if sys.platform == "darwin":
            if arch in {"arm64", "aarch64"}:
                return (
                    "arm64-apple-darwin",
                    "e-m:o-i64:64-i128:128-n32:64-S128",
                )

            return (
                "x86_64-apple-darwin",
                "e-m:o-i64:64-f80:128-n8:16:32:64-S128",
            )

        return (
            "x86_64-pc-linux-gnu",
            "e-m:e-i64:64-f80:128-n8:16:32:64-S128",
        )

    def generate_module(self) -> ir.Module:
        module = ir.Module(name=self.__module_name)
        module.triple = self.__triple
        module.data_layout = self.__data_layout
        return module
