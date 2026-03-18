from llvmlite import ir
from llvmlite.binding import create_target_data


class LowLevelSetup:
    def __init__(self):
        self.__module_name = "yian_module"
        self.__data_layout = "e-m:e-i64:64-f80:128-n8:16:32:64-S128"
        self.__triple = "x86_64-pc-linux-gnu"

        self.target_data = create_target_data(self.__data_layout)

    def generate_module(self) -> ir.Module:
        module = ir.Module(name=self.__module_name)
        module.triple = self.__triple
        module.data_layout = self.__data_layout
        return module
