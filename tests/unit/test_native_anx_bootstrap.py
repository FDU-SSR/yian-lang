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

from bootstrap_anx import PACKAGE_ORDER, PACKAGES, build_recipe
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
            shutil.copy2(ROOT / "scripts" / "install.sh", checkout / "scripts" / "install.sh")
            shutil.copy2(ROOT / "scripts" / "bootstrap_anx.py", checkout / "scripts" / "bootstrap_anx.py")
            for relative in (
                "lib/src",
                "runtime/src",
                "runtime/include",
                "bootstrap/yianc/build",
                "bootstrap/anx/build",
            ):
                (checkout / relative).mkdir(parents=True)

            old_yianc = checkout / "bootstrap/yianc/build/app"
            old_anx = checkout / "bootstrap/anx/build/app"
            old_yianc.write_text("stale yianc artifact\n", encoding="utf-8")
            old_anx.write_text("stale anx artifact\n", encoding="utf-8")

            python_scripts = workspace / "python scripts"
            python_scripts.mkdir()
            fake_python = workspace / "selected python" / "python"
            fake_python.parent.mkdir()
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
                "if args and args[0] == os.environ['CHECKOUT'] + '/scripts/bootstrap_anx.py':\n"
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
                    }
                )
                environment.pop("YIANC", None)
                return subprocess.run(
                    ["bash", str(checkout / "scripts" / "install.sh"), "--prefix", str(prefix), "--python", str(fake_python)],
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
            self.assertEqual(staged_yianc, python_compiler.parent / "bin" / "yianc")
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

            failed = run_install(failing_prefix, fail_package="anx")
            self.assertNotEqual(failed.returncode, 0)
            self.assertIn("failed to build", failed.stderr)
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


if __name__ == "__main__":
    unittest.main()
