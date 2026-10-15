"""Focused tests for the native anx seed recipe and installer orchestration."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))

from _toolchain.bootstrap_anx import PACKAGE_ORDER, PACKAGES, build_recipe
from _toolchain.identity import component_source_sha256, file_sha256, tree_sha256
from yianc.analysis.package_map import PackageMap


class NativeAnxBootstrapTests(unittest.TestCase):
    def test_bootstrap_recipe_matches_native_package_map(self) -> None:
        prefix_value = os.environ.get("YIAN_INSTALL_PREFIX")
        self.assertTrue(prefix_value, "YIAN_INSTALL_PREFIX must select a current native toolchain")
        native_anx = Path(prefix_value) / "bin" / "anx"
        self.assertTrue(native_anx.is_file(), native_anx)

        with tempfile.TemporaryDirectory(prefix="native anx recipe 空格 ") as temporary:
            checkout = Path(temporary)
            for name in PACKAGE_ORDER:
                relative_root = PACKAGES[name].root
                shutil.copytree(
                    ROOT / relative_root,
                    checkout / relative_root,
                    ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
                )
            recipe = build_recipe(checkout)

            capture_path = checkout / "captured compiler.json"
            capture_launcher = checkout / "capture compiler"
            capture_launcher.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, sys\n"
                "with open(os.environ['CAPTURE_RECORD'], 'w', encoding='utf-8') as stream:\n"
                "    json.dump({'argv': sys.argv[1:], 'cwd': os.getcwd()}, stream)\n"
                "\n",
                encoding="utf-8",
            )
            capture_launcher.chmod(0o755)

            environment = os.environ.copy()
            environment["CAPTURE_RECORD"] = str(capture_path)
            environment["YIAN_ROOT"] = str(checkout)
            environment["YIAN_LIB"] = str(checkout / "lib" / "src")
            environment["YIANC"] = str(capture_launcher)
            process = subprocess.run(
                [str(native_anx), "check", str(checkout / "bootstrap" / "anx")],
                cwd=checkout,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(process.returncode, 0, f"{process.stdout}\n{process.stderr}")

            package_map_path = checkout / "bootstrap" / "anx" / "build" / "pkg.json"
            native_map = json.loads(package_map_path.read_text(encoding="utf-8"))
            self.assertEqual(native_map, recipe.package_map)
            parsed = PackageMap.from_document(native_map, source="native anx bootstrap probe")
            self.assertEqual(parsed.root, "anx")
            llvm = parsed.packages["llvm"].native_llvm
            self.assertIsNotNone(llvm)
            self.assertEqual(
                llvm.sources,
                ((checkout / "bootstrap" / "llvm" / "native" / "target.c").resolve(),),
            )

            captured = json.loads(capture_path.read_text(encoding="utf-8"))
            args = captured["argv"]
            self.assertEqual(Path(captured["cwd"]), checkout / "bootstrap" / "anx")
            self.assertEqual(args[:4], ["--packages", str(package_map_path), "-t", "none"])
            compiler_inputs = [value for value in args[4:] if value.endswith(".an")]
            self.assertEqual(compiler_inputs, [str(path) for path in recipe.source_files])
            self.assertEqual(len(compiler_inputs), len(recipe.source_files))
            self.assertTrue(all(Path(path).is_file() for path in compiler_inputs))

    def test_installer_uses_staged_native_anx(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native anx installer ") as temporary:
            workspace = Path(temporary)
            checkout = workspace / "checkout"
            (checkout / "scripts").mkdir(parents=True)
            shutil.copy2(
                ROOT / "scripts" / "bootstrap_toolchain.sh",
                checkout / "scripts" / "bootstrap_toolchain.sh",
            )
            shutil.copytree(ROOT / "scripts" / "_toolchain", checkout / "scripts" / "_toolchain")
            for package in ("yianc", "anx", "json", "llvm", "ffi", "toml"):
                shutil.copytree(
                    ROOT / "bootstrap" / package,
                    checkout / "bootstrap" / package,
                    ignore=shutil.ignore_patterns("build", "__pycache__", ".pytest_cache"),
                )
            for relative in (
                "lib/src",
                "runtime/src",
                "runtime/include",
                "python/yianc",
            ):
                if relative == "python/yianc":
                    shutil.copytree(ROOT / relative, checkout / relative, ignore=shutil.ignore_patterns("__pycache__"))
                else:
                    shutil.copytree(ROOT / relative, checkout / relative)
            (checkout / "bootstrap/yianc/build").mkdir(parents=True)
            (checkout / "bootstrap/anx/build").mkdir(parents=True)

            old_yianc = checkout / "bootstrap/yianc/build/app"
            old_anx = checkout / "bootstrap/anx/build/app"
            old_yianc.write_text("stale yianc artifact\n", encoding="utf-8")
            old_anx.write_text("stale anx artifact\n", encoding="utf-8")

            python_scripts = workspace / "python scripts"
            python_scripts.mkdir()
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
            seed_template = workspace / "seed anx template"
            call_log = workspace / "calls.jsonl"

            seed_template.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, subprocess, sys\n"
                "args = sys.argv[1:]\n"
                "event = {'kind': 'anx', 'argv': args, 'yianc': os.environ.get('YIANC'), "
                "'root': os.environ.get('YIAN_ROOT'), 'lib': os.environ.get('YIAN_LIB')}\n"
                "with open(os.environ['CALL_LOG'], 'a', encoding='utf-8') as stream:\n"
                "    stream.write(json.dumps(event) + '\\n')\n"
                "if len(args) < 2 or args[0] != 'build':\n"
                "    raise SystemExit(2)\n"
                "subprocess.run([os.environ['YIANC'], '--help'], check=True)\n"
                "project = pathlib.Path(args[1])\n"
                "if os.environ.get('FAIL_PACKAGE') == project.name:\n"
                "    raise SystemExit(17)\n"
                "output = project / 'build' / 'app'\n"
                "output.write_text('#!/bin/sh\\nexit 0\\n# fresh ' + project.name + '\\n', encoding='utf-8')\n"
                "output.chmod(0o755)\n",
                encoding="utf-8",
            )
            fake_python.write_text(
                "#!/usr/bin/env python3\n"
                "import json, os, pathlib, shutil, sys\n"
                "args = sys.argv[1:]\n"
                "def record(event):\n"
                "    with open(os.environ['CALL_LOG'], 'a', encoding='utf-8') as stream:\n"
                "        stream.write(json.dumps(event) + '\\n')\n"
                "if args and args[0] == '-c':\n"
                "    if 'sysconfig' in args[1]:\n"
                "        print(os.environ['PYTHON_SCRIPTS_DIR'])\n"
                "    raise SystemExit(0)\n"
                "if args and args[0] == os.environ['CHECKOUT'] + '/scripts/_toolchain/cold.py':\n"
                "    completed = __import__('subprocess').run([sys.executable, *args], check=False)\n"
                "    raise SystemExit(completed.returncode)\n"
                "if args and args[0] == os.environ['CHECKOUT'] + '/scripts/_toolchain/bootstrap_anx.py':\n"
                "    record({'kind': 'bootstrap', 'argv': args[1:]})\n"
                "    output = pathlib.Path(args[args.index('--output') + 1])\n"
                "    shutil.copyfile(os.environ['SEED_TEMPLATE'], output)\n"
                "    output.chmod(0o755)\n"
                "    raise SystemExit(0)\n"
                "if len(args) >= 2 and args[:2] == ['-m', 'yianc.main']:\n"
                "    record({'kind': 'python-yianc', 'argv': args[2:]})\n"
                "    raise SystemExit(0)\n"
                "raise SystemExit(2)\n",
                encoding="utf-8",
            )
            fake_python.chmod(0o755)

            def run_install(prefix: Path, *, fail_package: str | None = None) -> subprocess.CompletedProcess[str]:
                environment = os.environ.copy()
                environment.update(
                    {
                        "CALL_LOG": str(call_log),
                        "CHECKOUT": str(checkout),
                        "FAIL_PACKAGE": fail_package or "",
                        "PYTHON_SCRIPTS_DIR": str(python_scripts),
                        "SEED_TEMPLATE": str(seed_template),
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
            installed_yianc = successful_prefix / "bin" / "yianc"
            installed_anx = successful_prefix / "bin" / "anx"
            self.assertIn("fresh yianc", installed_yianc.read_text(encoding="utf-8"))
            self.assertIn("fresh anx", installed_anx.read_text(encoding="utf-8"))
            self.assertTrue((successful_prefix / "share/yian/lib/src").is_dir())
            self.assertEqual(old_yianc.read_text(encoding="utf-8"), "stale yianc artifact\n")
            self.assertEqual(old_anx.read_text(encoding="utf-8"), "stale anx artifact\n")

            success_events = [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()]
            bootstrap = [event for event in success_events if event["kind"] == "bootstrap"]
            native_calls = [event for event in success_events if event["kind"] == "anx"]
            python_calls = [event for event in success_events if event["kind"] == "python-yianc"]
            self.assertEqual(len(bootstrap), 1)
            self.assertEqual(len(native_calls), 2)
            self.assertEqual(len(python_calls), 1)
            self.assertEqual(
                [event["kind"] for event in success_events],
                ["bootstrap", "anx", "python-yianc", "anx"],
            )
            bootstrap_args = bootstrap[0]["argv"]
            self.assertEqual(bootstrap_args[bootstrap_args.index("--python") + 1], str(fake_python))
            self.assertTrue(Path(bootstrap_args[bootstrap_args.index("--output") + 1]).is_absolute())
            python_compiler = Path(native_calls[0]["yianc"])
            staged_yianc = Path(native_calls[1]["yianc"])
            self.assertEqual(python_compiler.name, "yianc-python")
            self.assertEqual(staged_yianc, python_compiler.parent.parent / "bundle" / "bin" / "yianc")
            self.assertEqual(Path(native_calls[0]["argv"][1]).name, "yianc")
            self.assertEqual(Path(native_calls[1]["argv"][1]).name, "anx")
            self.assertEqual(native_calls[0]["root"], str(checkout))
            self.assertEqual(native_calls[0]["lib"], str(checkout / "lib" / "src"))
            self.assertEqual(python_calls[0]["argv"], ["--help"])

            call_log.unlink()
            failing_prefix = workspace / "existing prefix"
            old_bin = failing_prefix / "bin"
            old_data = failing_prefix / "share/yian"
            old_bin.mkdir(parents=True)
            old_data.mkdir(parents=True)
            (old_data / ".native-install").write_text("yian-native-install-v1\n", encoding="utf-8")
            (old_data / "sentinel").write_text("old resources\n", encoding="utf-8")
            (old_bin / "yianc").write_text("old yianc\n", encoding="utf-8")
            (old_bin / "anx").write_text("old anx\n", encoding="utf-8")
            (old_data / ".native-install").write_text(
                "yian-native-install-v1\n"
                f"yianc={file_sha256(old_bin / 'yianc')}\n"
                f"anx={file_sha256(old_bin / 'anx')}\n",
                encoding="utf-8",
            )

            failed = run_install(failing_prefix, fail_package="anx")
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("build-anx failed", failed.stderr)
            self.assertEqual((old_data / "sentinel").read_text(encoding="utf-8"), "old resources\n")
            self.assertEqual((old_bin / "yianc").read_text(encoding="utf-8"), "old yianc\n")
            self.assertEqual((old_bin / "anx").read_text(encoding="utf-8"), "old anx\n")
            self.assertEqual(old_yianc.read_text(encoding="utf-8"), "stale yianc artifact\n")
            self.assertEqual(old_anx.read_text(encoding="utf-8"), "stale anx artifact\n")
            failure_events = [json.loads(line) for line in call_log.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(
                [event["kind"] for event in failure_events],
                ["bootstrap", "anx", "python-yianc", "anx"],
            )

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
                "  lsp) cp \"$FAKE_LSP\" \"$project/build/app\" ;;\n"
                "  *) exit 3 ;;\n"
                "esac\n"
                "chmod +x \"$project/build/app\"\n",
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
            for executable in (yianc_template, anx_template, lsp_template):
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
                    "FAKE_LSP": str(lsp_template),
                    "YIAN_ROOT": "/invalid/inherited/root",
                    "YIAN_LIB": "/invalid/inherited/lib",
                    "PYTHONPATH": "/invalid/inherited/pythonpath",
                }
            )

            def run_refresh(*, fail_handshake: bool = False) -> subprocess.CompletedProcess[str]:
                run_environment = environment.copy()
                run_environment["FAIL_LSP_HANDSHAKE"] = "1" if fail_handshake else "0"
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
                for project in (ROOT / "bootstrap/yianc", ROOT / "bootstrap/anx", ROOT / "lsp")
                if (project / "build/app").is_file()
            }
            succeeded = run_refresh()
            self.assertEqual(succeeded.returncode, 0, f"{succeeded.stdout}\n{succeeded.stderr}")
            self.assertFalse(python_called.exists(), "warm refresh invoked a Python sentinel")
            events = [line.split("\t") for line in calls.read_text(encoding="utf-8").splitlines()]
            self.assertEqual([event[1] for event in events], ["yianc", "anx", "lsp"])
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
            self.assertEqual(Path(events[2][2]), staged_yianc)
            self.assertIn(str(ROOT / "bootstrap/yianc"), events[0][3])
            self.assertIn(str(ROOT / "bootstrap/anx"), events[1][3])
            self.assertIn(str(ROOT / "lsp"), events[2][3])
            self.assertIn("--raw-pointers", events[2][3])
            for project in (ROOT / "bootstrap/yianc", ROOT / "bootstrap/anx", ROOT / "lsp"):
                if project in old_apps:
                    self.assertEqual((project / "build/app").read_bytes(), old_apps[project])
                else:
                    self.assertFalse((project / "build/app").exists())

            installed = prefix / "bin"
            build_info = json.loads((prefix / "share/yian/.build-info").read_text(encoding="utf-8"))
            for name in ("yianc", "anx", "yian-lsp"):
                self.assertEqual(
                    build_info["components"][name]["artifact_sha256"],
                    file_sha256(installed / name),
                )
            self.assertEqual(build_info["components"]["yian-lsp"]["build_mode"], "raw")
            self.assertEqual(build_info["components"]["yian-lsp"]["optimization"], 2)
            self.assertEqual((prefix / "share/yian-lsp/.native-install").read_text(encoding="utf-8").splitlines()[0], "yian-lsp-install-v1")
            previous_hashes = {name: file_sha256(installed / name) for name in ("yianc", "anx", "yian-lsp")}
            previous_marker = (prefix / "share/yian/.native-install").read_bytes()
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

    def test_publish_restores_previous_install(self) -> None:
        with tempfile.TemporaryDirectory(prefix="native install rollback ") as temporary:
            root = Path(temporary)
            prefix = root / "installed prefix"
            bundle = root / "stage/bundle"
            staged_bin = bundle / "bin"
            staged_data = bundle / "share/yian"
            staged_bin.mkdir(parents=True)
            for name in ("yianc", "anx"):
                (staged_bin / name).write_text(f"new {name}\n", encoding="utf-8")
                (staged_bin / name).chmod(0o755)
            (staged_data / "lib/src").mkdir(parents=True)
            (staged_data / "runtime/src").mkdir(parents=True)
            (staged_data / "runtime/include").mkdir(parents=True)
            (staged_data / "lib/src/core.an").write_text("fn main() {}\n", encoding="utf-8")
            component_info = {
                name: {"artifact_sha256": file_sha256(staged_bin / name)}
                for name in ("yianc", "anx")
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
                f"resources={resource_hash}\n",
                encoding="utf-8",
            )

            fake_bin = root / "fake bin"
            fake_bin.mkdir()
            fail_once = root / "fail-once"
            fake_mv = fake_bin / "mv"
            fake_mv.write_text(
                "#!/usr/bin/env bash\n"
                "source_path=\"${@:2:1}\"\n"
                "target_path=\"${@:3:1}\"\n"
                f"if [[ \"$source_path\" == */candidates/2 && \"$target_path\" == *'/bin/anx' && ! -e {json.dumps(str(fail_once))} ]]; then\n"
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
            (old_data / ".native-install").write_text(
                "yian-native-install-v1\n"
                f"yianc={file_sha256(old_bin / 'yianc')}\n"
                f"anx={file_sha256(old_bin / 'anx')}\n",
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
            self.assertEqual((old_data / "sentinel").read_text(encoding="utf-8"), "old resources\n")
            self.assertFalse(any(path.name.startswith(".yian-publish-") for path in prefix.iterdir()))

            source_root = root / "source checkout"
            package = source_root / "bootstrap/yianc"
            dependency = source_root / "bootstrap/json"
            for package_root, name, dependency_text in (
                (package, "yianc", 'json = { path = "../json" }\n'),
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
