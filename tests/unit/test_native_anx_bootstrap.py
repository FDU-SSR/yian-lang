"""Focused tests for fixed bootstrap recipes and toolchain orchestration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from _toolchain.bootstrap_anx import PACKAGE_ORDERS, PACKAGES, build_recipe, compiler_arguments
from _toolchain.identity import (
    IdentityError,
    component_source_files,
    component_source_sha256,
    file_sha256,
    resources_source_sha256,
    tree_sha256,
)
from check_toolchain_parity import validate_install


class NativeAnxBootstrapTests(unittest.TestCase):
    def test_bootstrap_recipes_use_fixed_package_closures(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native anx recipe 空格 ") as temporary:
            checkout = Path(temporary)
            package_names = dict.fromkeys(
                name for order in PACKAGE_ORDERS.values() for name in order
            )
            for name in package_names:
                relative_root = PACKAGES[name].root
                shutil.copytree(
                    ROOT / relative_root,
                    checkout / relative_root,
                    ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
                )
            yianc_recipe = build_recipe(checkout, root_package="yianc")
            anx_recipe = build_recipe(checkout, root_package="anx")
            fmt_recipe = build_recipe(checkout, root_package="fmt")

            self.assertEqual(PACKAGES["yianc"].root, "yianc")
            self.assertEqual(PACKAGES["anx"].root, "anx")
            self.assertEqual(PACKAGES["fmt"].root, "fmt")
            self.assertEqual(PACKAGES["fmt"].kind, "hybrid")
            self.assertEqual(PACKAGES["fmt"].entry, "src/main.an")
            self.assertFalse(PACKAGES["fmt"].ffi)
            self.assertEqual(
                anx_recipe.package_map["packages"]["yianc"]["sourceRoot"],
                str(checkout / "yianc/src"),
            )
            self.assertEqual(yianc_recipe.package_map["root"], "yianc")
            self.assertEqual(tuple(yianc_recipe.package_map["packages"]), PACKAGE_ORDERS["yianc"])
            self.assertEqual(anx_recipe.package_map["root"], "anx")
            self.assertEqual(tuple(anx_recipe.package_map["packages"]), PACKAGE_ORDERS["anx"])
            self.assertEqual(fmt_recipe.package_map["root"], "fmt")
            self.assertEqual(tuple(fmt_recipe.package_map["packages"]), PACKAGE_ORDERS["fmt"])
            self.assertEqual(PACKAGE_ORDERS["fmt"], ("fmt", "yianc", "json", "llvm", "ffi", "std"))
            self.assertEqual(fmt_recipe.package_map["packages"]["fmt"]["sourceRoot"], str(checkout / "fmt/src"))
            self.assertEqual(fmt_recipe.package_map["packages"]["fmt"]["dependencies"], ["yianc"])
            self.assertNotIn("nativeLLVM", fmt_recipe.package_map["packages"]["fmt"])
            self.assertNotIn("toml", fmt_recipe.package_map["packages"])
            self.assertNotIn("toml", yianc_recipe.package_map["packages"])
            self.assertEqual(
                yianc_recipe.package_map["packages"]["llvm"]["nativeLLVM"]["sources"],
                ["native/target.c"],
            )
            for recipe in (yianc_recipe, anx_recipe, fmt_recipe):
                self.assertTrue(all(path.is_file() for path in recipe.source_files))
                arguments = compiler_arguments(
                    recipe, Path("package-map.json"), Path("output")
                )
                self.assertIn("--raw-pointers", arguments)
                self.assertIn("-O2", arguments)
                self.assertEqual(
                    [value for value in arguments if value.endswith(".an")],
                    [str(path) for path in recipe.source_files],
                )

    def test_installer_uses_staged_native_anx(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native anx installer ") as temporary:
            workspace = Path(temporary)
            checkout = workspace / "checkout"
            (checkout / "scripts").mkdir(parents=True)
            shutil.copy2(ROOT / "scripts" / "bootstrap_toolchain.sh", checkout / "scripts" / "bootstrap_toolchain.sh")
            shutil.copytree(ROOT / "scripts" / "_toolchain", checkout / "scripts" / "_toolchain")
            package_names = dict.fromkeys(name for order in PACKAGE_ORDERS.values() for name in order)
            for name in package_names:
                relative = PACKAGES[name].root
                shutil.copytree(
                    ROOT / relative,
                    checkout / relative,
                    ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
                )
            for relative in ("runtime/src", "runtime/include", "python/yianc"):
                shutil.copytree(
                    ROOT / relative,
                    checkout / relative,
                    ignore=shutil.ignore_patterns("__pycache__"),
                )

            for package in ("yianc", "anx"):
                build = checkout / package / "build"
                build.mkdir(parents=True)
                (build / "app").write_text(f"stale {package} artifact\n", encoding="utf-8")
            old_yianc = checkout / "yianc/build/app"
            old_anx = checkout / "anx/build/app"

            fake_python = workspace / "selected python" / "python"
            fake_python.parent.mkdir()
            fake_tools = workspace / "fake LLVM tools"
            fake_tools.mkdir()
            for name, output in (
                ("llvm-config", "22.0.0\n"),
                ("clang", "clang version 22.0.0\n"),
                ("llvm-ar", "LLVM version 22.0.0\n"),
            ):
                executable = fake_tools / name
                executable.write_text(f"#!/bin/sh\nprintf '%s' {json.dumps(output)}\n", encoding="utf-8")
                executable.chmod(0o755)
            call_log = workspace / "calls.jsonl"
            fake_python.write_text(
                textwrap.dedent(
                    """\
    #!/usr/bin/env python3
    import json, os, pathlib, subprocess, sys
    args = sys.argv[1:]
    def record(event):
        with open(os.environ['CALL_LOG'], 'a', encoding='utf-8') as stream:
            stream.write(json.dumps(event) + '\\n')
    if args and args[0] == '-c':
        raise SystemExit(0)
    if args and args[0] == os.environ['CHECKOUT'] + '/scripts/_toolchain/cold.py':
        completed = subprocess.run([sys.executable, *args], check=False)
        raise SystemExit(completed.returncode)
    if len(args) >= 2 and args[:2] == ['-m', 'yianc.main']:
        compiler_args = args[2:]
        package_map = pathlib.Path(compiler_args[compiler_args.index('--packages') + 1])
        root_package = json.loads(package_map.read_text(encoding='utf-8'))['root']
        output = pathlib.Path(compiler_args[compiler_args.index('-o') + 1])
        record({
            'kind': 'python-yianc',
            'argv': compiler_args,
            'root': root_package,
            'yianc': os.environ.get('YIANC'),
            'pythonpath': os.environ.get('PYTHONPATH'),
        })
        builder = pathlib.Path(os.environ['FAKE_BUILDER'])
        output.write_bytes(builder.read_bytes())
        output.chmod(0o755)
        raise SystemExit(0)
    raise SystemExit(2)
    """
                ),
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            fake_builder = workspace / "fake builder"
            fake_builder.write_text(
                "#!/usr/bin/env bash\n"
                "set -u\n"
                "if [[ \"${1:-}\" == --help ]]; then printf 'usage: yianc\\n'; exit 0; fi\n"
                "args=(\"$@\")\n"
                "output=\"\"\n"
                "mapping=\"\"\n"
                "for ((index = 0; index < ${#args[@]}; index++)); do\n"
                "  if [[ \"${args[index]}\" == -o ]]; then output=\"${args[index + 1]}\"; fi\n"
                "  if [[ \"${args[index]}\" == --packages ]]; then mapping=\"${args[index + 1]}\"; fi\n"
                "done\n"
                "[[ -n \"$output\" && -n \"$mapping\" ]] || exit 2\n"
                "root=\"$(sed -n 's/^  \"root\": \"\\([^\"]*\\)\",*$/\\1/p' \"$mapping\" | head -1)\"\n"
                "yianc_value=null\n"
                "if [[ -n \"${YIANC:-}\" ]]; then yianc_value=\"\\\"${YIANC}\\\"\"; fi\n"
                "printf '{\"kind\":\"native-yianc\",\"binary\":\"%s\",\"root\":\"%s\",\"yianc\":%s,\"output\":\"%s\",\"packages\":\"%s\",\"options\":\"%s\"}\\n' \"$0\" \"$root\" \"$yianc_value\" \"$output\" \"$mapping\" \"$*\" >>\"$CALL_LOG\"\n"
                "if [[ \"${FAIL_PACKAGE:-}\" == \"$root\" ]]; then exit 17; fi\n"
                "cp \"$FAKE_FINAL\" \"$output\"\n"
                "chmod 0755 \"$output\"\n",
                encoding="utf-8",
            )
            fake_builder.chmod(0o755)
            fake_final = workspace / "fake final"
            fake_final.write_text(
                "#!/usr/bin/env bash\n"
                "case \"${1:-}\" in\n"
                "  --version) printf 'yian-fmt 0.1.0\\n'; exit 0 ;;\n"
                "  --help) printf 'Usage: yian-fmt [options] [PATH...]\\n'; exit 0 ;;\n"
                "  --stdin) cat >/dev/null; printf 'fn main() { let (a, b) = (1, 2); }\\n'; exit 0 ;;\n"
                "esac\n"
                "exit 0\n",
                encoding="utf-8",
            )
            fake_final.chmod(0o755)

            def run_install(prefix: Path, *, fail_package: str | None = None) -> subprocess.CompletedProcess[str]:
                environment = os.environ.copy()
                environment.update(
                    {
                        "CALL_LOG": str(call_log),
                        "CHECKOUT": str(checkout),
                        "FAIL_PACKAGE": fail_package or "",
                        "FAKE_BUILDER": str(fake_builder),
                        "FAKE_FINAL": str(fake_final),
                        "PATH": f"{fake_tools}{os.pathsep}{os.environ['PATH']}",
                    }
                )
                environment.pop("YIANC", None)
                return subprocess.run(
                    [
                        "bash",
                        str(checkout / "scripts" / "bootstrap_toolchain.sh"),
                        "--prefix",
                        str(prefix),
                        "--python",
                        str(fake_python),
                    ],
                    cwd=workspace,
                    env=environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            successful_prefix = workspace / "successful prefix"
            successful = run_install(successful_prefix)
            self.assertEqual(successful.returncode, 0, f"{successful.stdout}\n{successful.stderr}")
            installed_yianc = successful_prefix / "bin/yianc"
            installed_anx = successful_prefix / "bin/anx"
            installed_fmt = successful_prefix / "bin/yian-fmt"
            self.assertTrue(installed_yianc.is_file())
            self.assertTrue(installed_anx.is_file())
            self.assertTrue(installed_fmt.is_file())
            self.assertTrue(os.access(installed_fmt, os.X_OK))
            self.assertTrue((successful_prefix / "share/yian/lib/src").is_dir())
            self.assertEqual(old_yianc.read_text(encoding="utf-8"), "stale yianc artifact\n")
            self.assertEqual(old_anx.read_text(encoding="utf-8"), "stale anx artifact\n")

            events = [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [event["kind"] for event in events],
                ["python-yianc", "native-yianc", "native-yianc"],
            )
            python_event, anx_event, fmt_event = events
            self.assertEqual(python_event["root"], "yianc")
            self.assertEqual(anx_event["root"], "anx")
            self.assertEqual(fmt_event["root"], "fmt")
            for event in (python_event, anx_event, fmt_event):
                self.assertIsNone(event["yianc"])
            self.assertEqual(python_event["pythonpath"], str(checkout / "python"))
            self.assertEqual(anx_event["binary"], python_event["argv"][python_event["argv"].index("-o") + 1])
            self.assertEqual(fmt_event["binary"], python_event["argv"][python_event["argv"].index("-o") + 1])
            self.assertEqual(Path(anx_event["packages"]).name, "anx.json")
            self.assertEqual(Path(fmt_event["packages"]).name, "fmt.json")
            python_args = python_event["argv"]
            self.assertIn("--raw-pointers", python_args)
            self.assertIn("-O2", python_args)
            self.assertEqual(python_args[python_args.index("-t") + 1], "exe")
            python_sources = [Path(value) for value in python_args if value.endswith(".an")]
            self.assertTrue(python_sources)
            self.assertTrue(all(checkout in source.parents for source in python_sources))
            for event in (anx_event, fmt_event):
                self.assertIn("--raw-pointers", event["options"])
                self.assertIn("-O2", event["options"])
                self.assertIn("-t exe", event["options"])
                self.assertIn(".an", event["options"])
            yianc_output = Path(python_event["argv"][python_event["argv"].index("-o") + 1])
            anx_output = Path(anx_event["output"])
            fmt_output = Path(fmt_event["output"])
            self.assertEqual(yianc_output.name, "yianc")
            self.assertEqual(anx_output.name, "anx")
            self.assertEqual(fmt_output.name, "yian-fmt")
            self.assertEqual(yianc_output.parent.name, "bin")
            self.assertEqual(anx_output.parent.name, "bin")
            self.assertEqual(fmt_output.parent.name, "bin")

            build_info = json.loads((successful_prefix / "share/yian/.build-info").read_text(encoding="utf-8"))
            components = build_info["components"]
            self.assertEqual(components["yianc"]["builder"], "python-recovery")
            self.assertEqual(components["anx"]["builder"], "staged-yianc")
            self.assertEqual(components["yian-fmt"]["builder"], "staged-yianc")
            self.assertEqual(components["yianc"]["build_mode"], "raw")
            self.assertEqual(components["anx"]["build_mode"], "raw")
            self.assertEqual(components["yianc"]["optimization"], 2)
            self.assertEqual(components["anx"]["optimization"], 2)
            self.assertEqual(components["yian-fmt"]["artifact_sha256"], file_sha256(installed_fmt))
            self.assertEqual(
                components["yian-fmt"]["builder_inputs"]["yianc_sha256"],
                file_sha256(installed_yianc),
            )
            self.assertEqual(
                components["yianc"]["builder_inputs"]["python_sha256"],
                build_info["build_python"]["sha256"],
            )
            self.assertEqual(
                components["anx"]["builder_inputs"]["yianc_sha256"],
                file_sha256(installed_yianc),
            )
            self.assertNotIn("seed_anx_sha256", build_info)
            marker = (successful_prefix / "share/yian/.native-install").read_text(encoding="utf-8")
            self.assertIn("yianc_build_mode=raw\n", marker)
            self.assertIn("anx_build_mode=raw\n", marker)
            self.assertIn("fmt_build_mode=raw\n", marker)
            self.assertIn(f"yian-fmt={file_sha256(installed_fmt)}\n", marker)
            self.assertIn("pointer_mode=raw\n", marker)

            call_log.unlink()
            failing_prefix = workspace / "existing prefix"
            old_bin = failing_prefix / "bin"
            old_data = failing_prefix / "share/yian"
            old_bin.mkdir(parents=True)
            old_data.mkdir(parents=True)
            (old_data / "sentinel").write_text("old resources\n", encoding="utf-8")
            (old_bin / "yianc").write_text("old yianc\n", encoding="utf-8")
            (old_bin / "anx").write_text("old anx\n", encoding="utf-8")
            (old_bin / "yian-fmt").write_text("old yian-fmt\n", encoding="utf-8")
            (old_data / ".native-install").write_text(
                "yian-native-install-v1\n"
                f"yianc={file_sha256(old_bin / 'yianc')}\n"
                f"anx={file_sha256(old_bin / 'anx')}\n"
                f"yian-fmt={file_sha256(old_bin / 'yian-fmt')}\n",
                encoding="utf-8",
            )

            failed = run_install(failing_prefix, fail_package="anx")
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("build-anx-staged-yianc failed", failed.stderr)
            self.assertEqual((old_data / "sentinel").read_text(encoding="utf-8"), "old resources\n")
            self.assertEqual((old_bin / "yianc").read_text(encoding="utf-8"), "old yianc\n")
            self.assertEqual((old_bin / "anx").read_text(encoding="utf-8"), "old anx\n")
            self.assertEqual(old_yianc.read_text(encoding="utf-8"), "stale yianc artifact\n")
            self.assertEqual(old_anx.read_text(encoding="utf-8"), "stale anx artifact\n")
            failure_events = [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event["kind"] for event in failure_events], ["python-yianc", "native-yianc"])

            call_log.unlink()
            fmt_failed = run_install(failing_prefix, fail_package="fmt")
            self.assertNotEqual(fmt_failed.returncode, 0)
            self.assertIn("build-yian-fmt-staged-yianc failed", fmt_failed.stderr)
            self.assertEqual((old_data / "sentinel").read_text(encoding="utf-8"), "old resources\n")
            self.assertEqual((old_bin / "yianc").read_text(encoding="utf-8"), "old yianc\n")
            self.assertEqual((old_bin / "anx").read_text(encoding="utf-8"), "old anx\n")
            self.assertEqual((old_bin / "yian-fmt").read_text(encoding="utf-8"), "old yian-fmt\n")
            fmt_failure_events = [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [event["kind"] for event in fmt_failure_events],
                ["python-yianc", "native-yianc", "native-yianc"],
            )
            self.assertEqual([event["root"] for event in fmt_failure_events], ["yianc", "anx", "fmt"])

    def test_refresh_uses_staged_native_toolchain(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native refresh with spaces ") as temporary:
            workspace = Path(temporary)
            fake_bin = workspace / "fake tools"
            fake_bin.mkdir()
            calls = workspace / "calls.tsv"
            python_called = workspace / "python-called"
            prefix = workspace / "installed prefix"

            yianc_template = workspace / "yianc-template"
            yianc_template.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == --help ]]; then printf 'usage: yianc\\n'; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            anx_template = workspace / "anx-template"
            anx_template.write_text(
                "#!/usr/bin/env bash\n"
                "set -euo pipefail\n"
                "if [[ \"${1:-}\" == --help ]]; then printf 'Commands:\\n'; exit 0; fi\n"
                "[[ \"${1:-}\" == build && $# -ge 3 ]] || exit 2\n"
                "project=\"$2\"\n"
                "printf '%s\\t%s\\t%s\\t%s\\n' \"${0}\" \"${project##*/}\" \"${YIANC:-}\" \"$*\" >>\"$CALL_LOG\"\n"
                "mkdir -p \"$project/build\"\n"
                "case \"${project##*/}\" in\n"
                "  yianc) cp \"$FAKE_YIANC\" \"$project/build/app\" ;;\n"
                "  anx) cp \"$FAKE_ANX\" \"$project/build/app\" ;;\n"
                "  fmt) if [[ \"${FAIL_FMT:-0}\" == 1 ]]; then exit 17; fi; cp \"$FAKE_FMT\" \"$project/build/app\" ;;\n"
                "  lsp) cp \"$FAKE_LSP\" \"$project/build/app\" ;;\n"
                "  *) exit 3 ;;\n"
                "esac\n"
                "chmod +x \"$project/build/app\"\n",
                encoding="utf-8",
            )
            fmt_template = workspace / "fmt-template"
            fmt_template.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == --version ]]; then printf 'yian-fmt 0.7.0\\n'; exit 0; fi\n"
                "if [[ \"${1:-}\" == --help ]]; then printf 'Usage: yian-fmt [options] [PATH...]\\n'; exit 0; fi\n"
                "if [[ \"${1:-}\" == --stdin ]]; then cat >/dev/null; printf 'fn main() { let (a, b) = (1, 2); }\\n'; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            lsp_template = workspace / "lsp-template"
            lsp_template.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == --version ]]; then printf 'yian-lsp 0.7.0\\n'; exit 0; fi\n"
                "if [[ \"${1:-}\" == --stdio ]]; then cat >/dev/null; "
                "if [[ \"${FAIL_LSP_HANDSHAKE:-0}\" == 1 ]]; then printf '{}\\n'; else "
                "printf '%s\\n' '{\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"serverInfo\":{}}}' '{\"jsonrpc\":\"2.0\",\"id\":2,\"result\":null}'; fi; exit 0; fi\n"
                "exit 2\n",
                encoding="utf-8",
            )
            for executable in (yianc_template, anx_template, fmt_template, lsp_template):
                executable.chmod(0o755)

            seed_yianc = fake_bin / "yianc-seed"
            seed_anx = fake_bin / "anx-seed"
            shutil.copy2(yianc_template, seed_yianc)
            shutil.copy2(anx_template, seed_anx)
            for name, script in (("llvm-config", "printf '22.1.8\\n'"),
                                 ("clang", "printf 'clang version 22.1.8\\n'"),
                                 ("llvm-ar", "printf 'LLVM version 22.1.8\\n'")):
                tool = fake_bin / name
                tool.write_text(f"#!/usr/bin/env bash\n{script}\n", encoding="utf-8")
                tool.chmod(0o755)
            fake_od = fake_bin / "od"
            fake_od.write_text("#!/usr/bin/env bash\nprintf '7f454c46\\n'\n", encoding="utf-8")
            fake_od.chmod(0o755)
            for name in ("python", "python3", "python3.12"):
                sentinel = fake_bin / name
                sentinel.write_text(
                    f"#!/usr/bin/env bash\nprintf called >> {json.dumps(str(python_called))}\nexit 88\n",
                    encoding="utf-8",
                )
                sentinel.chmod(0o755)

            environment = os.environ.copy()
            environment.update(
                {
                    "PATH": f"{fake_bin}{os.pathsep}{environment['PATH']}",
                    "CALL_LOG": str(calls),
                    "FAKE_YIANC": str(yianc_template),
                    "FAKE_ANX": str(anx_template),
                    "FAKE_FMT": str(fmt_template),
                    "FAKE_LSP": str(lsp_template),
                    "YIAN_ROOT": "/invalid/inherited/root",
                    "YIAN_LIB": "/invalid/inherited/lib",
                    "PYTHONPATH": "/invalid/inherited/pythonpath",
                }
            )

            def run_refresh(*, fail_handshake: bool = False, fail_fmt: bool = False) -> subprocess.CompletedProcess[str]:
                run_environment = environment.copy()
                run_environment["FAIL_LSP_HANDSHAKE"] = "1" if fail_handshake else "0"
                run_environment["FAIL_FMT"] = "1" if fail_fmt else "0"
                return subprocess.run(
                    [
                        "bash",
                        str(ROOT / "scripts" / "refresh_toolchain.sh"),
                        "--anx",
                        str(seed_anx),
                        "--yianc",
                        str(seed_yianc),
                        "--prefix",
                        str(prefix),
                    ],
                    cwd=workspace,
                    env=run_environment,
                    capture_output=True,
                    text=True,
                    check=False,
                )

            old_apps = {
                project: (project / "build/app").read_bytes()
                for project in (ROOT / "yianc", ROOT / "anx", ROOT / "fmt", ROOT / "lsp")
                if (project / "build/app").is_file()
            }
            succeeded = run_refresh()
            self.assertEqual(succeeded.returncode, 0, f"{succeeded.stdout}\n{succeeded.stderr}")
            self.assertFalse(python_called.exists(), "warm refresh invoked a Python sentinel")
            events = [line.split("\t") for line in calls.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event[1] for event in events], ["yianc", "anx", "fmt", "lsp"])
            self.assertEqual([Path(event[0]).resolve() for event in events[:2]], [seed_anx.resolve()] * 2)
            self.assertEqual(Path(events[0][2]).resolve(), seed_yianc.resolve())
            staged_yianc = Path(events[1][2])
            staged_anx = Path(events[2][0])
            self.assertEqual(staged_yianc.name, "yianc")
            self.assertEqual(staged_yianc.parent.name, "bin")
            self.assertEqual(staged_anx.name, "anx")
            self.assertEqual(staged_anx.parent.name, "bin")
            self.assertNotEqual(staged_yianc.resolve(), seed_yianc.resolve())
            self.assertNotEqual(staged_anx.resolve(), seed_anx.resolve())
            for event in events[2:]:
                self.assertEqual(Path(event[0]).resolve(), staged_anx.resolve())
                self.assertEqual(Path(event[2]), staged_yianc)
            self.assertIn(str(ROOT / "yianc"), events[0][3])
            self.assertIn(str(ROOT / "anx"), events[1][3])
            self.assertIn(str(ROOT / "fmt"), events[2][3])
            self.assertIn(str(ROOT / "lsp"), events[3][3])
            for event in events:
                self.assertIn("--raw-pointers", event[3])
            for project in (ROOT / "yianc", ROOT / "anx", ROOT / "fmt", ROOT / "lsp"):
                if project in old_apps:
                    self.assertEqual((project / "build/app").read_bytes(), old_apps[project])
                else:
                    self.assertFalse((project / "build/app").exists())

            installed = prefix / "bin"
            build_info = json.loads((prefix / "share/yian/.build-info").read_text(encoding="utf-8"))
            self.assertEqual(build_info["components"]["yian-fmt"]["builder"], "staged-anx")
            self.assertEqual(
                build_info["components"]["yian-fmt"]["builder_inputs"]["yianc_sha256"],
                build_info["components"]["yianc"]["artifact_sha256"],
            )
            for name in ("yianc", "anx", "yian-fmt", "yian-lsp"):
                self.assertEqual(
                    build_info["components"][name]["artifact_sha256"],
                    file_sha256(installed / name),
                )
                self.assertEqual(build_info["components"][name]["build_mode"], "raw")
                self.assertEqual(build_info["components"][name]["optimization"], 2)
            self.assertEqual(build_info["components"]["yian-lsp"]["build_mode"], "raw")
            self.assertEqual(build_info["components"]["yian-lsp"]["optimization"], 2)
            self.assertEqual((prefix / "share/yian-lsp/.native-install").read_text(encoding="utf-8").splitlines()[0], "yian-lsp-install-v1")
            marker_text = (prefix / "share/yian/.native-install").read_text(encoding="utf-8")
            self.assertIn("fmt_build_mode=raw\n", marker_text)
            self.assertIn(f"yian-fmt={file_sha256(installed / 'yian-fmt')}\n", marker_text)
            previous_hashes = {name: file_sha256(installed / name) for name in ("yianc", "anx", "yian-fmt", "yian-lsp")}
            previous_marker = (prefix / "share/yian/.native-install").read_bytes()
            calls.write_text("", encoding="utf-8")
            failed_fmt = run_refresh(fail_fmt=True)
            self.assertNotEqual(failed_fmt.returncode, 0)
            self.assertIn("build-yian-fmt failed", failed_fmt.stderr)
            self.assertEqual(
                {name: file_sha256(installed / name) for name in previous_hashes},
                previous_hashes,
            )
            self.assertEqual((prefix / "share/yian/.native-install").read_bytes(), previous_marker)
            self.assertFalse(python_called.exists())
            calls.write_text("", encoding="utf-8")
            failed = run_refresh(fail_handshake=True)
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("initialize response has no serverInfo", failed.stderr)
            self.assertEqual(
                {name: file_sha256(installed / name) for name in previous_hashes},
                previous_hashes,
            )
            self.assertEqual((prefix / "share/yian/.native-install").read_bytes(), previous_marker)
            self.assertFalse(python_called.exists())

    def test_lsp_uninstall_allows_reinstall(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native lsp reinstall ") as temporary:
            root = Path(temporary)
            prefix = root / "installed prefix"
            bundle = root / "bundle"
            binaries = bundle / "bin"
            binaries.mkdir(parents=True)
            for name in ("yianc", "anx", "yian-fmt", "yian-lsp"):
                (binaries / name).write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
                (binaries / name).chmod(0o755)

            data = bundle / "share/yian"
            data.mkdir(parents=True)
            (data / "resource").write_text("shared resources\n", encoding="utf-8")
            resource_hash = tree_sha256(data)
            (data / ".build-info").write_text('{"format": 1}\n', encoding="utf-8")
            (data / ".native-install").write_text(
                "yian-native-install-v1\n"
                f"yianc={file_sha256(binaries / 'yianc')}\n"
                f"anx={file_sha256(binaries / 'anx')}\n"
                f"yian-fmt={file_sha256(binaries / 'yian-fmt')}\n"
                f"resources={resource_hash}\n"
                f"build_info_sha256={file_sha256(data / '.build-info')}\n"
                "yianc_build_mode=raw\nanx_build_mode=raw\nfmt_build_mode=raw\noptimization=2\npointer_mode=raw\n",
                encoding="utf-8",
            )
            lsp_data = bundle / "share/yian-lsp"
            lsp_data.mkdir()
            (lsp_data / ".build-info").write_text('{"format": 1}\n', encoding="utf-8")
            (lsp_data / ".native-install").write_text(
                "yian-lsp-install-v1\n"
                f"sha256={file_sha256(binaries / 'yian-lsp')}\n"
                f"build_info_sha256={file_sha256(lsp_data / '.build-info')}\n"
                "build_mode=raw\noptimization=2\n",
                encoding="utf-8",
            )

            def run_script(script: str, *arguments: str) -> None:
                process = subprocess.run(
                    ["bash", str(ROOT / "scripts" / script), "--prefix", str(prefix), *arguments],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(process.returncode, 0, f"{process.stdout}\n{process.stderr}")

            run_script("_toolchain/publish.sh", "--bundle", str(bundle))
            run_script("uninstall.sh", "--lsp-only")
            self.assertFalse((prefix / "bin/yian-lsp").exists())
            self.assertFalse((prefix / "share/yian-lsp").exists())
            for name in ("yianc", "anx", "yian-fmt"):
                self.assertEqual((prefix / "bin" / name).read_bytes(), (binaries / name).read_bytes())
            self.assertEqual((prefix / "share/yian/resource").read_text(), "shared resources\n")

            run_script("_toolchain/publish.sh", "--bundle", str(bundle))
            self.assertEqual(
                (prefix / "share/yian-lsp/.build-info").read_bytes(),
                (lsp_data / ".build-info").read_bytes(),
            )
            self.assertTrue((prefix / "bin/yian-lsp").is_file())
            run_script("uninstall.sh")
            self.assertFalse((prefix / "share/yian-lsp").exists())
            self.assertFalse((prefix / "share/yian").exists())
            for name in ("yianc", "anx", "yian-fmt", "yian-lsp"):
                self.assertFalse((prefix / "bin" / name).exists())

    def test_lsp_uninstall_preserves_unmatched_build_info(self) -> None:
        for kind in ("modified", "unrecorded", "symlink"):
            with self.subTest(kind=kind), tempfile.TemporaryDirectory(prefix="lsp metadata ") as temporary:
                prefix = Path(temporary) / "prefix"
                data = prefix / "share/yian-lsp"
                data.mkdir(parents=True)
                original = Path(temporary) / "original.json"
                original.write_text('{"format": 1}\n', encoding="utf-8")
                info = data / ".build-info"
                if kind == "symlink":
                    info.symlink_to(original)
                else:
                    info.write_text("modified metadata\n", encoding="utf-8")
                marker = data / ".native-install"
                marker_text = "yian-lsp-install-v1\n"
                if kind != "unrecorded":
                    marker_text += f"build_info_sha256={file_sha256(original)}\n"
                marker.write_text(marker_text, encoding="utf-8")
                previous_info = info.read_bytes()
                process = subprocess.run(
                    ["bash", str(ROOT / "scripts/uninstall.sh"), "--lsp-only", "--prefix", str(prefix)],
                    capture_output=True, text=True, check=False,
                )
                self.assertEqual(process.returncode, 1, f"{process.stdout}\n{process.stderr}")
                self.assertIn("Preserving modified or unmanaged language-server build-info", process.stderr)
                self.assertEqual(info.read_bytes(), previous_info)
                self.assertEqual(marker.read_text(), marker_text)
                self.assertEqual(info.is_symlink(), kind == "symlink")
                self.assertEqual(original.read_text(), '{"format": 1}\n')

    def test_formatter_uninstall_scopes_and_protection(self) -> None:
        with tempfile.TemporaryDirectory(prefix="formatter uninstall scopes ") as temporary:
            root = Path(temporary)

            def prepare(name: str, *, formatter: str = "recorded", lsp: bool = False) -> Path:
                prefix = root / name
                binaries = prefix / "bin"
                data = prefix / "share/yian"
                binaries.mkdir(parents=True)
                data.mkdir(parents=True)
                contents = {"yianc": "yianc\n", "anx": "anx\n", "yian-fmt": "formatter\n"}
                for command, text in contents.items():
                    (binaries / command).write_text(text, encoding="utf-8")
                    (binaries / command).chmod(0o755)
                (data / "resource").write_text("shared resources\n", encoding="utf-8")
                marker = [
                    "yian-native-install-v1",
                    f"yianc={file_sha256(binaries / 'yianc')}",
                    f"anx={file_sha256(binaries / 'anx')}",
                ]
                if formatter == "recorded":
                    marker.append(f"yian-fmt={file_sha256(binaries / 'yian-fmt')}")
                elif formatter == "modified":
                    marker.append(f"yian-fmt={file_sha256(binaries / 'yian-fmt')}")
                    (binaries / "yian-fmt").write_text("locally modified\n", encoding="utf-8")
                elif formatter == "symlink":
                    target = prefix / "external-formatter"
                    target.write_text("formatter\n", encoding="utf-8")
                    (binaries / "yian-fmt").unlink()
                    (binaries / "yian-fmt").symlink_to(target)
                    marker.append(f"yian-fmt={file_sha256(target)}")
                else:
                    self.assertEqual(formatter, "unrecorded")
                marker.extend(("optimization=2", "pointer_mode=raw"))
                (data / ".native-install").write_text("\n".join(marker) + "\n", encoding="utf-8")
                if lsp:
                    lsp_bin = binaries / "yian-lsp"
                    lsp_bin.write_text("lsp\n", encoding="utf-8")
                    lsp_bin.chmod(0o755)
                    lsp_data = prefix / "share/yian-lsp"
                    lsp_data.mkdir()
                    (lsp_data / ".native-install").write_text(
                        "yian-lsp-install-v1\n"
                        f"sha256={file_sha256(lsp_bin)}\n"
                        "build_mode=raw\noptimization=2\n",
                        encoding="utf-8",
                    )
                return prefix

            def run_uninstall(prefix: Path, *arguments: str) -> subprocess.CompletedProcess[str]:
                return subprocess.run(
                    ["bash", str(ROOT / "scripts/uninstall.sh"), "--prefix", str(prefix), *arguments],
                    capture_output=True, text=True, check=False,
                )

            managed = prepare("managed")
            removed = run_uninstall(managed)
            self.assertEqual(removed.returncode, 0, f"{removed.stdout}\n{removed.stderr}")
            for command in ("yianc", "anx", "yian-fmt"):
                self.assertFalse((managed / "bin" / command).exists())
            self.assertFalse((managed / "share/yian").exists())

            lsp_only = prepare("lsp only", lsp=True)
            kept = run_uninstall(lsp_only, "--lsp-only")
            self.assertEqual(kept.returncode, 0, f"{kept.stdout}\n{kept.stderr}")
            for command in ("yianc", "anx", "yian-fmt"):
                self.assertTrue((lsp_only / "bin" / command).is_file())
            self.assertTrue((lsp_only / "share/yian/resource").is_file())

            modified = prepare("modified formatter", formatter="modified")
            preserved = run_uninstall(modified)
            self.assertEqual(preserved.returncode, 0, f"{preserved.stdout}\n{preserved.stderr}")
            self.assertEqual((modified / "bin/yian-fmt").read_text(encoding="utf-8"), "locally modified\n")
            self.assertTrue((modified / "share/yian/resource").is_file())
            self.assertFalse((modified / "bin/yianc").exists())

            unrecorded = prepare("unrecorded formatter", formatter="unrecorded")
            protected = run_uninstall(unrecorded)
            self.assertEqual(protected.returncode, 0, f"{protected.stdout}\n{protected.stderr}")
            self.assertEqual((unrecorded / "bin/yian-fmt").read_text(encoding="utf-8"), "formatter\n")
            self.assertTrue((unrecorded / "share/yian/resource").is_file())

            symlinked = prepare("symlinked formatter", formatter="symlink")
            protected_link = run_uninstall(symlinked)
            self.assertEqual(protected_link.returncode, 0, f"{protected_link.stdout}\n{protected_link.stderr}")
            self.assertTrue((symlinked / "bin/yian-fmt").is_symlink())
            self.assertTrue((symlinked / "external-formatter").is_file())
            self.assertTrue((symlinked / "share/yian/resource").is_file())

            legacy = prepare("legacy prefix", formatter="unrecorded")
            (legacy / "bin/yian-fmt").unlink()
            legacy_result = run_uninstall(legacy)
            self.assertEqual(legacy_result.returncode, 0, f"{legacy_result.stdout}\n{legacy_result.stderr}")
            self.assertFalse((legacy / "bin/yianc").exists())
            self.assertFalse((legacy / "bin/anx").exists())
            self.assertFalse((legacy / "share/yian").exists())

    def test_formatter_identity_contract(self) -> None:
        python_digest = component_source_sha256(ROOT, "yian-fmt")
        shell = subprocess.run(
            [
                "bash",
                "-c",
                f'source "{ROOT / "scripts/_toolchain/identity.sh"}"; yian_component_sha256 "{ROOT}" yian-fmt',
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(shell.returncode, 0, shell.stderr)
        self.assertEqual(shell.stdout.strip(), python_digest, "Python and shell formatter identities must agree")
        sources = component_source_files(ROOT, "yian-fmt")
        for expected in (
            ROOT / "fmt/package.anx",
            ROOT / "fmt/src/main.an",
            ROOT / "yianc/package.anx",
            ROOT / "lib/src/core/string.an",
        ):
            self.assertIn(expected.resolve(), sources)
        self.assertNotIn((ROOT / "anx/package.anx").resolve(), sources)

        with tempfile.TemporaryDirectory(prefix="formatter identity fixture ") as temporary:
            prefix = Path(temporary) / "prefix"
            binaries = prefix / "bin"
            data = prefix / "share/yian"
            binaries.mkdir(parents=True)
            for command in ("yianc", "anx", "yian-fmt"):
                shutil.copy2(Path("/bin/true").resolve(), binaries / command)
                (binaries / command).chmod(0o755)
            for relative in ("lib/src", "runtime/src", "runtime/include"):
                (data / relative).mkdir(parents=True)
                (data / relative / "resource").write_text(relative + "\n", encoding="utf-8")
            resource_hash = tree_sha256(data, excluded_names=frozenset((".native-install", ".build-info")))
            components = {
                name: {
                    "source_sha256": component_source_sha256(ROOT, name),
                    "artifact_sha256": file_sha256(binaries / name),
                    "build_mode": "raw",
                    "optimization": 2,
                }
                for name in ("yianc", "anx", "yian-fmt")
            }
            (data / ".build-info").write_text(
                json.dumps(
                    {
                        "format": 1,
                        "components": components,
                        "resources": {
                            "source_sha256": resources_source_sha256(ROOT),
                            "artifact_sha256": resource_hash,
                        },
                    }
                ),
                encoding="utf-8",
            )
            marker = (
                "yian-native-install-v1\n"
                + "".join(f"{name}={file_sha256(binaries / name)}\n" for name in ("yianc", "anx", "yian-fmt"))
                + f"resources={resource_hash}\n"
                + f"build_info_sha256={file_sha256(data / '.build-info')}\n"
                + "yianc_build_mode=raw\nanx_build_mode=raw\nfmt_build_mode=raw\noptimization=2\npointer_mode=raw\n"
            )
            (data / ".native-install").write_text(marker, encoding="utf-8")
            identity = validate_install(prefix)
            self.assertEqual(identity["formatter"], str(binaries.resolve() / "yian-fmt"))
            self.assertEqual(identity["formatter_sha256"], file_sha256(binaries / "yian-fmt"))
            self.assertEqual(identity["formatter_source_sha256"], component_source_sha256(ROOT, "yian-fmt"))

            (binaries / "yian-fmt").write_text("tampered\n", encoding="utf-8")
            with self.assertRaisesRegex(IdentityError, "yian-fmt"):
                validate_install(prefix)
            shutil.copy2(Path("/bin/true").resolve(), binaries / "yian-fmt")
            (data / ".native-install").write_text(
                marker.replace(
                    f"yian-fmt={file_sha256(binaries / 'yian-fmt')}\n",
                    "",
                ),
                encoding="utf-8",
            )
            with self.assertRaisesRegex(IdentityError, "yian-fmt"):
                validate_install(prefix)

    def test_publish_restores_previous_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native install rollback ") as temporary:
            root = Path(temporary)
            prefix = root / "installed prefix"
            bundle = root / "stage/bundle"
            staged_bin = bundle / "bin"
            staged_data = bundle / "share/yian"
            staged_bin.mkdir(parents=True)
            for name in ("yianc", "anx", "yian-fmt"):
                (staged_bin / name).write_text(f"new {name}\n", encoding="utf-8")
                (staged_bin / name).chmod(0o755)
            (staged_data / "lib/src").mkdir(parents=True)
            (staged_data / "runtime/src").mkdir(parents=True)
            (staged_data / "runtime/include").mkdir(parents=True)
            (staged_data / "lib/src/core.an").write_text("fn main() {}\n", encoding="utf-8")
            component_info = {
                name: {"artifact_sha256": file_sha256(staged_bin / name)}
                for name in ("yianc", "anx", "yian-fmt")
            }
            resource_hash = tree_sha256(staged_data)
            (staged_data / ".build-info").write_text(
                json.dumps(
                    {
                        "format": 1,
                        "components": component_info,
                        "resources": {"artifact_sha256": resource_hash},
                    }
                ),
                encoding="utf-8",
            )
            (staged_data / ".native-install").write_text(
                "yian-native-install-v1\n"
                f"yianc={file_sha256(staged_bin / 'yianc')}\n"
                f"anx={file_sha256(staged_bin / 'anx')}\n"
                f"yian-fmt={file_sha256(staged_bin / 'yian-fmt')}\n"
                f"resources={resource_hash}\n"
                "yianc_build_mode=raw\n"
                "anx_build_mode=raw\n"
                "fmt_build_mode=raw\n"
                "optimization=2\n"
                "pointer_mode=raw\n",
                encoding="utf-8",
            )

            fake_bin = root / "fake bin"
            fake_bin.mkdir()
            fail_once = root / "fail-once"
            fake_mv = fake_bin / "mv"
            fake_mv.write_text(
                "#!/usr/bin/env bash\n"
                "target_path=\"${@:3:1}\"\n"
                f"if [[ \"$target_path\" == *'/bin/yian-fmt' && ! -e {json.dumps(str(fail_once))} ]]; then\n"
                f"  touch {json.dumps(str(fail_once))}\n"
                "  exit 71\n"
                "fi\n"
                "exec /usr/bin/mv \"$@\"\n",
                encoding="utf-8",
            )
            fake_mv.chmod(0o755)

            old_bin = prefix / "bin"
            old_data = prefix / "share/yian"
            old_bin.mkdir(parents=True)
            old_data.mkdir(parents=True)
            (old_bin / "yianc").write_text("old yianc\n", encoding="utf-8")
            (old_bin / "anx").write_text("old anx\n", encoding="utf-8")
            (old_bin / "yian-fmt").write_text("old yian-fmt\n", encoding="utf-8")
            (old_data / ".native-install").write_text(
                "yian-native-install-v1\n"
                f"yianc={file_sha256(old_bin / 'yianc')}\n"
                f"anx={file_sha256(old_bin / 'anx')}\n"
                f"yian-fmt={file_sha256(old_bin / 'yian-fmt')}\n",
                encoding="utf-8",
            )
            (old_data / "sentinel").write_text("old resources\n", encoding="utf-8")

            environment = os.environ.copy()
            environment["PATH"] = f"{fake_bin}{os.pathsep}{environment['PATH']}"
            process = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "_toolchain" / "publish.sh"),
                    "--prefix",
                    str(prefix),
                    "--bundle",
                    str(bundle),
                ],
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(process.returncode, 0, f"{process.stdout}\n{process.stderr}")
            self.assertIn("could not publish staged install target", process.stderr)
            self.assertTrue(fail_once.is_file())
            self.assertEqual((old_bin / "yianc").read_text(encoding="utf-8"), "old yianc\n")
            self.assertEqual((old_bin / "anx").read_text(encoding="utf-8"), "old anx\n")
            self.assertEqual((old_bin / "yian-fmt").read_text(encoding="utf-8"), "old yian-fmt\n")
            self.assertEqual((old_data / "sentinel").read_text(encoding="utf-8"), "old resources\n")
            self.assertFalse(any(path.name.startswith(".yian-publish-") for path in prefix.iterdir()))

            unmanaged_prefix = root / "unmanaged formatter"
            (unmanaged_prefix / "bin").mkdir(parents=True)
            (unmanaged_prefix / "bin" / "yian-fmt").write_text("unmanaged formatter\n", encoding="utf-8")
            unmanaged = subprocess.run(
                [
                    "bash",
                    str(ROOT / "scripts" / "_toolchain" / "publish.sh"),
                    "--prefix",
                    str(unmanaged_prefix),
                    "--bundle",
                    str(bundle),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertNotEqual(unmanaged.returncode, 0)
            self.assertIn("unmanaged command", unmanaged.stderr)
            self.assertEqual(
                (unmanaged_prefix / "bin" / "yian-fmt").read_text(encoding="utf-8"),
                "unmanaged formatter\n",
            )

            source_root = root / "source checkout"
            package = source_root / "yianc"
            dependency = source_root / "bootstrap/json"
            for package_root, name, dependency_text in (
                (package, "yianc", 'json = { path = "../bootstrap/json" }\n'),
                (dependency, "json", ""),
            ):
                (package_root / "src").mkdir(parents=True)
                (package_root / "package.anx").write_text(
                    f"[package]\nname = \"{name}\"\nkind = \"lib\"\n\n[dependencies]\n{dependency_text}",
                    encoding="utf-8",
                )
                (package_root / "src/main.an").write_text("fn main() {}\n", encoding="utf-8")
            (source_root / "lib/src").mkdir(parents=True)
            (source_root / "lib/src/std.an").write_text("fn std_value() {}\n", encoding="utf-8")
            (source_root / "runtime/src").mkdir(parents=True)
            (source_root / "runtime/include").mkdir(parents=True)
            initial = component_source_sha256(source_root, "yianc")
            (package / "README.md").write_text("documentation-only change\n", encoding="utf-8")
            self.assertEqual(component_source_sha256(source_root, "yianc"), initial)
            (package / "src/main.an").write_text("fn changed() {}\n", encoding="utf-8")
            self.assertNotEqual(component_source_sha256(source_root, "yianc"), initial)


if __name__ == "__main__":
    unittest.main()
