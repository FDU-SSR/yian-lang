#include <llvm-c/Target.h>

int YIANLLVMInitializeNativeTarget(void) {
    if (LLVMInitializeNativeTarget() != 0) {
        return 1;
    }
    if (LLVMInitializeNativeAsmPrinter() != 0) {
        return 1;
    }
    return 0;
}
