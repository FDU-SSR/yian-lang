#!/usr/bin/env python3
import sys
import subprocess
from pathlib import Path


# 没有传入参数时提示
if len(sys.argv) == 1:
    print(f"{sys.argv[0]} <path>")
    sys.exit(1)

args = [arg for arg in sys.argv[1:] if arg != '-l']

display_enabled = '-p' in args
args = [arg for arg in args if arg != '-p']

optimize_args = ['-O0', '-O1', '-O2', '-O3', '-Os', '-o0', '-o1', '-o2', '-o3', '-os']
clang_args = []
clang_args += [arg for arg in args if arg in optimize_args]
args = [arg for arg in args if arg not in optimize_args]

# 计算 ROOT_DIR（dirname(dirname(realpath($0)))）
root_dir = Path(__file__).resolve().parent.parent
llir_path = root_dir / "tests" / "yian_workspace" / "objects"

# === 运行编译命令（直接调用 scripts/compiler.py）===
cmd = ["python", str(root_dir / "scripts" / "compiler.py")]
full_cmd = cmd + args
result = subprocess.run(full_cmd)

if display_enabled:
    # === 运行 dfview.py ===
    view_script = root_dir / "lian" / "scripts" / "dfview.py"
    view_output = root_dir / "tests" / "yian_workspace"
    cmd_view = [str(view_script), str(view_output)]
    print("=== view ===")
    subprocess.run(cmd_view, check=True)

if result.returncode != 0:
    print(f"[ERROR] {' '.join(full_cmd)}")
    sys.exit(result.returncode)

ll_files = list(llir_path.glob("*.ll"))
if len(ll_files) > 1:  # 至少两个文件才考虑链接
    test_name = ll_files[0].stem.split('.')[0]
    output_file = llir_path / "out.ll"
    cmd_link = ["llvm-link", "-S", "-o", str(output_file)] + [str(f) for f in ll_files]
    print("=== linking ===")
    subprocess.run(cmd_link, check=True)
    print(f"linked {len(ll_files)} files to {output_file}")
    # 删除所有 .yy.ll 文件
    for file in ll_files:
        file.unlink()
else:
    output_file = ll_files[0]

clang_output_file = root_dir / "tests" / "yian_workspace" / "bin" / "out"
full_cmd = ["clang", output_file] + clang_args + ["-o", clang_output_file, "-lm"]
print("=== compiling ===")
subprocess.run(full_cmd, check=True)
print(f"compiled all files to {clang_output_file}")
