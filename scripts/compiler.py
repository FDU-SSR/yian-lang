#!/usr/bin/env python3
import argparse
import subprocess
import sys
from pathlib import Path

# 使用 argparse 解析参数
parser = argparse.ArgumentParser(description="Helper script to run Yian compiler.")
parser.add_argument("-p", "--preview", action="store_true", help="Enable display/preview (run dfview)")

# 解析已知参数 (-p), 其余参数保存在 compiler_args 中
args_namespace, compiler_args = parser.parse_known_args()

if len(sys.argv) == 1:
    parser.print_help()
    sys.exit(1)

# 计算目录
root_dir = Path(__file__).resolve().parent.parent

exe_path = root_dir / "compiler" / "main.py"
lib_path = root_dir / "lib"
output_path = root_dir / "tests" / "yian_workspace"

# 构造编译命令
cmd = ["python", str(exe_path)]
options = ["compile", "-f", "-d", "-w", str(output_path)]
full_cmd = cmd + options + compiler_args + [str(lib_path)]

result = subprocess.run(full_cmd)

if args_namespace.preview:
    # === 运行 dfview.py ===
    view_script = root_dir / "lian" / "scripts" / "dfview.py"
    view_output = root_dir / "tests" / "yian_workspace"
    cmd_view = [str(view_script), str(view_output)]
    print("=== view ===")
    subprocess.run(cmd_view, check=True)

if result.returncode != 0:
    print(f"[ERROR] {' '.join(full_cmd)}")
    sys.exit(result.returncode)
