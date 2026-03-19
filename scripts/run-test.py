#!/usr/bin/env python3

import subprocess
from pathlib import Path

# Get the root directory where the compiler.sh script is located
root_dir = Path(__file__).resolve().parent.parent

# scripts/compiler.py
compiler_path = root_dir / "scripts" / "compiler.py"
output_path = root_dir / "tests" / "yian_workspace"
tests_path = root_dir / "tests"
tests_result_path = root_dir / "tests" / "tests_results"
ll_path = root_dir / "tests" / "yian_workspace" / "objects" / "output.ll"

options = f"compile -f -w {output_path}"

relative_test_dirs = [
    "array",
    "call",
    "control_flow",
    "dyn",
    "error",
    "generics",
    "impl",
    "import",
    "literal",
    "op",
    "op_overload",
    "pointer",
    "private",
    "string",
    "trait",
    "tuple",
    "type",

    "lib/fs",
    "lib/option",
    "lib/result",
    "lib/slice",
    "lib/str",
    "lib/string",
    "lib/vec",
]

# Concatenate the full path
test_dirs: list[Path] = [tests_path / dir_path for dir_path in relative_test_dirs]


def run_compiler_test(target_path: Path) -> tuple[bool, str]:
    """
    Execute compilation test on the specified file
    """

    compile_cmd = f"{compiler_path} {target_path}"

    if target_path.parent.name == "op_overload":
        compile_cmd += f" {target_path.with_name('ops.an')}"
    if target_path.parent.name == "private":
        compile_cmd += f" {target_path.with_name('private.an')}"

    expect_error = target_path.is_file() and target_path.name.endswith('.err.an')
    try:
        print(f"Testing file: {target_path}")
        # Execute compilation command
        subprocess.run(compile_cmd, shell=True, check=True, text=True, capture_output=True)
        print("Compilation successful")
        if expect_error:
            return False, "Expected compilation error, but compilation succeeded"

        if ll_path.exists():
            # First, use opt to optimize the .ll file
            opt_cmd = f"opt -S -o {ll_path} -O3 {ll_path}"
            subprocess.run(opt_cmd, shell=True, check=True, text=True, capture_output=True)
            # Use lli to run the .ll file
            run_cmd = f"lli {ll_path}"
            run_result = subprocess.run(run_cmd, shell=True, text=True, capture_output=True)

            actual_returncode = run_result.returncode
            stdout_output = run_result.stdout.strip()

            # Find the corresponding .an.ans file
            relative_path = target_path.relative_to(tests_path)
            ans_file_path = tests_result_path / relative_path.parent / (relative_path.name + '.ans')
            if not ans_file_path.exists():
                if actual_returncode == 0:
                    print("Program returned 0, considered successful")
                    return True, "Success"
                else:
                    msg = f"No .ans file found, and the program return code is not 0: {actual_returncode}"
                    print(msg)
                    return False, msg

            expected_content = ans_file_path.read_text().strip()

            # Compare return value or standard output
            if str(actual_returncode) == expected_content or stdout_output == expected_content:
                print("Run result is as expected")
            else:
                print(f"Run result does not match expectation. Expected: {expected_content}, Actual return code: {actual_returncode}, Actual output: {stdout_output}")
                return False, f"Expected: {expected_content}, Actual return code: {actual_returncode}, Actual output: {stdout_output}"
        else:
            print("Generated .ll file not found")
            return False, "Generated .ll file not found"

        return True, "Success"
    except subprocess.CalledProcessError as e:
        if expect_error:
            print("Compilation error as expected")
            return True, "Success"
        print(f"Test failed: {e.stderr}")
        return False, e.stderr


def main():
    success_count = 0
    total_count = 0
    error_files_count = 0
    failed_files = []  # Used to record all failed files and their error messages

    for test_dir in test_dirs:
        for path in test_dir.iterdir():

            if path.name in {"ops.an", "private.an"}:
                continue

            total_count += 1
            if path.is_dir():
                success, error_msg = run_compiler_test(path)
                if success:
                    success_count += 1
                else:
                    failed_files.append((str(path), error_msg))
            elif path.is_file():
                if path.name.endswith(".err.an"):
                    error_files_count += 1
                success, error_msg = run_compiler_test(path)
                if success:
                    success_count += 1
                else:
                    failed_files.append((str(path), error_msg))

    print(f"\nTest finished. Total: {total_count}, Succeeded: {success_count}, Failed: {total_count - success_count} (including {error_files_count} expected compilation errors *.err.an)")

    if failed_files:
        print("\nThe following files failed to compile or their run results were not as expected:")
        for file_path, error_msg in failed_files:
            print(f"File: {file_path}")
            print(f"Error message: {error_msg}")
            print("-" * 50)


if __name__ == '__main__':
    main()
