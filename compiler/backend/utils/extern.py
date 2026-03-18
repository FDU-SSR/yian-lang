#!/usr/bin/env python3
from llvmlite import ir
from config.constants import SYSCALL_NUM


class ExternCall:
    def __init__(self):
        pass


class SyscallGenerator:
    def __init__(self, module, architecture='x86_64'):
        self.module = module
        self.architecture = architecture
        syscall_type = ir.FunctionType(
            ir.IntType(64),
            [
                ir.IntType(64)
            ],
            var_arg=True
        )
        syscall_fn = ir.Function(self.module, syscall_type, name="syscall")
        self.syscall_fn = syscall_fn

    def _cast_to_i64(self, val, builder):
        if isinstance(val.type, ir.PointerType):
            return builder.ptrtoint(val, ir.IntType(64))
        elif isinstance(val.type, ir.IntType):
            if val.type.width < 64:
                return builder.zext(val, ir.IntType(64))
            elif val.type.width > 64:
                return builder.trunc(val, ir.IntType(64))
        return val

    def run(self, callnum, args, builder):
        if self.architecture == 'x86_64':
            syscall_num = ir.Constant(ir.IntType(64), callnum)
            converted_args = [self._cast_to_i64(arg, builder) for arg in args]

            call_args = [syscall_num] + converted_args
            builder.call(self.syscall_fn, call_args)

    def open(self, pathname, flags, mode, builder):
        """
        打开或创建文件
        """
        if self.architecture == 'x86_64':
            return self.run(SYSCALL_NUM.SYS_OPEN, [pathname, flags, mode], builder)

    def close(self, fd, builder):
        if self.architecture == 'x86_64':
            return self.run(SYSCALL_NUM.SYS_CLOSE, [fd], builder)

    def read(self, fd, buf, count, builder):
        if self.architecture == 'x86_64':
            return self.run(SYSCALL_NUM.SYS_READ, [fd, buf, count], builder)

    def write(self, fd, buf, count, builder):
        if self.architecture == 'x86_64':
            return self.run(SYSCALL_NUM.SYS_WRITE, [fd, buf, count], builder)
