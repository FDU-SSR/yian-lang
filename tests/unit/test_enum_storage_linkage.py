"""Focused native probes for enum place storage and module-local linkage.

The driver requires an explicit native prefix through ``YIAN_ENUM_TEST_PREFIX``.
It never refreshes tools, discovers other fixtures, or runs package suites.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
ENUM_FIXTURE = ROOT / "tests" / "basic" / "enum" / "storage_access.an"
CFG_FIXTURE = ROOT / "yianc" / "tests" / "unit" / "codegen" / "enum_storage.an"

ENUM_PROBE = """\
enum Packet {
    Empty,
    Data { number: i32, count: i64 },
}

fn main() {
    let packet = Packet.Data(7, 9);
    let by_value: i64 = match packet {
        Packet.Data(number, count) => i64(number) + count,
        Packet.Empty => 0i64,
    };
    assert by_value == 16;

    let reference: Packet& = &packet;
    let by_reference: i64 = match reference {
        Packet.Data(number, count) => i64(*number) + *count,
        Packet.Empty => 0i64,
    };
    assert by_reference == 16;

    match &packet {
        Packet.Data(number, count) => {
            let first: i32& = number;
            assert *first == 7 && *count == 9;
        },
        Packet.Empty => assert false,
    }
}
"""

LINKAGE_PROBE = """\
extern "C" {
    fn abs(value: c_int) -> c_int;
}

pub ffi fn absolute(value: i32) -> i32 {
    return i32(abs(c_int(value)));
}

pub fn helper() -> i32 {
    return 42;
}

fn main() {
    assert helper() == 42;
    assert absolute(-7) == 7;
}
"""

CONSUMERS_PROBE = """\
extern "C" {
    fn abs(value: c_int) -> c_int;
}

pub ffi fn absolute(value: i32) -> i32 {
    return i32(abs(c_int(value)));
}

enum Packet {
    Empty,
    Data { number: i32, count: i64 },
}

fn helper(value: i32) -> i32 {
    return value + 1;
}

fn echo(packet: Packet) -> Packet {
    return packet;
}

trait Show {
    fn show() -> i32;
}

struct Item {
    value: i32,
}

impl Show for Item {
    fn show() -> i32 { return self.value; }
}

fn render(value: Show&) -> i32 {
    return value.show();
}

fn main() {
    assert absolute(-7) == 7: "C call";

    let pointer: fn(i32) -> i32 = &helper;
    assert helper(1) == 2: "direct call";
    assert pointer(2) == 3: "indirect call";

    let captured: i32 = 10;
    let closure = |x_in = captured| () -> i32 {
        x_in + 5
    };
    let from_closure: i32 = closure();
    assert from_closure == 15: "closure call";

    let item = Item(4);
    assert render(&item) == 4: "trait object call";

    let packet = Packet.Data(6, 7);
    let returned = echo(packet);
    match returned {
        Packet.Data(number, count) => assert number == 6 && count == 7: "echo result",
        Packet.Empty => assert false: "echo result",
    }
    match packet {
        Packet.Data(number, count) => assert number == 6 && count == 7: "echo source stays readable",
        Packet.Empty => assert false: "echo source stays readable",
    }
}
"""

FUNCTION_LABEL = re.compile(r'^"?[A-Za-z0-9_.$]+":')
DEFINE = re.compile(r"^define ((?:internal |private )?)(.+?) @([^\s(]+)\((.*)\)")
TYPE_DEFINITION = re.compile(r"^(%yian\.type\.\d+) = type (.*)$")


def find_tool(name: str, *fallbacks: str) -> str:
    located = shutil.which(name)
    if located:
        return located
    for candidate in fallbacks:
        if Path(candidate).is_file():
            return candidate
    raise AssertionError(f"required LLVM tool is missing: {name} (tried {fallbacks})")


class NativeProbe(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        prefix_value = os.environ.get("YIAN_ENUM_TEST_PREFIX")
        if not prefix_value:
            raise AssertionError("YIAN_ENUM_TEST_PREFIX must name the authorized native prefix")
        requested = Path(prefix_value)
        if not requested.is_absolute():
            raise AssertionError("YIAN_ENUM_TEST_PREFIX must be absolute")
        cls.prefix = requested.resolve()
        cls.yianc = cls.prefix / "bin" / "yianc"
        cls.anx = cls.prefix / "bin" / "anx"
        for tool in (cls.yianc, cls.anx):
            if not tool.is_file() or not os.access(tool, os.X_OK):
                raise AssertionError(f"native tool is missing from the authorized prefix: {tool}")
        cls.nm = find_tool("llvm-nm", "/usr/lib/llvm-22/bin/llvm-nm")
        cls.opt = find_tool("opt", "/usr/lib/llvm-22/bin/opt")
        cls.environment = os.environ.copy()
        cls.environment["YIANC"] = str(cls.yianc)
        cls.environment["YIAN_ROOT"] = str(ROOT)
        cls.environment["YIAN_LIB"] = str(ROOT / "lib" / "src")
        cls.environment["YIAN_CC"] = cls.environment.get("YIAN_CC", "/usr/lib/llvm-22/bin/clang")
        cls.environment["PATH"] = str(cls.prefix / "bin") + os.pathsep + cls.environment.get("PATH", "")

    def setUp(self) -> None:
        self.temporary = tempfile.TemporaryDirectory(prefix="enum storage probe ")
        self.work = Path(self.temporary.name)

    def tearDown(self) -> None:
        self.temporary.cleanup()

    def source(self, name: str, text: str) -> Path:
        path = self.work / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return path

    def compiler(self, source: Path, output: Path, *, target: str, raw: bool = False,
                 optimization: int = 0, dump: bool = False, allow_ffi: bool = False) -> subprocess.CompletedProcess[str]:
        arguments = [str(self.yianc), "--compiler-root", str(ROOT), f"-O{optimization}", "-t", target,
                     "-o", str(output)]
        if raw:
            arguments.append("--raw-pointers")
        if dump:
            arguments.append("--dump")
        if allow_ffi:
            arguments.append("--allow-ffi")
        arguments.extend([str(ROOT / "lib" / "src"), str(source)])
        completed = subprocess.run(arguments, cwd=output.parent, env=self.environment, text=True,
                                   capture_output=True, check=False, timeout=600)
        self.assertEqual(completed.returncode, 0,
                         f"compilation failed for {source.name} ({target}):\n{completed.stdout}\n{completed.stderr}")
        self.assertEqual(completed.stderr, "", f"compilation wrote diagnostics for {source.name}:\n{completed.stderr}")
        return completed

    def executable(self, source: Path, name: str, *, raw: bool, optimization: int = 0,
                   allow_ffi: bool = False) -> Path:
        output = self.work / name
        self.compiler(source, output, target="exe", raw=raw, optimization=optimization, allow_ffi=allow_ffi)
        return output

    def run_program(self, executable: Path) -> subprocess.CompletedProcess[str]:
        completed = subprocess.run([str(executable)], cwd=self.work, env=self.environment, text=True,
                                   capture_output=True, check=False, timeout=120)
        self.assertEqual(completed.returncode, 0,
                         f"probe exited with {completed.returncode}:\n{completed.stdout}\n{completed.stderr}")
        self.assertEqual(completed.stdout, "", "probe wrote unexpected stdout")
        self.assertEqual(completed.stderr, "", "probe wrote unexpected stderr")
        return completed

    def assert_entry_allocas(self, ir: str) -> None:
        bodies = re.findall(r"(?ms)^define .*?^}\n", ir)
        self.assertTrue(bodies, "the module defines no function")
        checked = 0
        for body in bodies:
            labels = [match.start() for match in re.finditer(r'(?m)^"?[A-Za-z0-9_.$]+":', body)]
            self.assertTrue(labels, "every function has an entry label")
            entry = labels[0]
            second = labels[1] if len(labels) > 1 else len(body)
            for match in re.finditer(r"(?m)^\s*%[^\n=]+= alloca ", body):
                self.assertTrue(entry < match.start() < second,
                                "every alloca belongs to the entry block")
                checked += 1
        self.assertGreater(checked, 0, "the probe contains local slots")


class EnumStorageTests(NativeProbe):
    def test_ir_shape(self) -> None:
        source = self.source("probe.an", ENUM_PROBE)
        for raw in (False, True):
            mode = "raw" if raw else "fat"
            output = self.work / f"probe-{mode}.ll"
            self.compiler(source, output, target="ll", raw=raw, dump=True)
            ir = output.read_text(encoding="utf-8")

            dump = json.loads((output.parent / "build" / "cfg.txt").read_text(encoding="utf-8"))
            operations: set[str] = set()
            enums: set[int] = set()
            for function in dump:
                for block in function["blocks"]:
                    for instruction in block["instructions"]:
                        operations.add(instruction["operation"])
                        if "enum" in instruction:
                            enums.add(instruction["enum"])
            for name in ("enum-init-at", "enum-tag-at", "enum-field-at", "enum-copy-at"):
                self.assertIn(name, operations, f"the probe exercises {name}")
            for name in ("enum", "tag", "payload"):
                self.assertNotIn(name, operations, f"the tagged path does not fall back to {name}")
            self.assertEqual(len(enums), 1, "the probe uses one tagged enum")
            enum_id = next(iter(enums))
            enum_type = f"%yian.type.{enum_id}"

            definitions = {}
            for line in ir.splitlines():
                match = TYPE_DEFINITION.match(line)
                if match:
                    definitions[match.group(1)] = match.group(2)
            self.assertIn(enum_type, definitions, "the probe declares its enum type")
            self.assertRegex(definitions[enum_type], r"^\{ i32, \[\d+ x i32\] \}$")
            payloads = [name for name, body in definitions.items() if body == "{ i32, i64 }"]
            self.assertEqual(len(payloads), 1, "the probe declares one two-field payload")
            payload_type = payloads[0]

            for name, body in ((enum_type, "enum"), (payload_type, "payload")):
                self.assertNotIn(f"load {name},", ir, f"the field path does not load the whole {body}")
                self.assertNotIn(f"load {name} ", ir, f"the field path does not load the whole {body}")
                self.assertNotIn(f"store {name} ", ir, f"the construction path does not store the whole {body}")
            self.assertIn("@llvm.memmove", ir, "the by-value match copies one snapshot")
            self.assertIn("@llvm.memset", ir, "the inactive payload area is zero initialized")
            self.assertRegex(ir, rf"getelementptr {re.escape(enum_type)}, ptr %\S+, i32 0, i32 1",
                             "payload fields are addressed through the payload area")
            self.assertIn("load i32", ir)
            self.assertIn("load i64", ir)
            self.assert_entry_allocas(ir)

    def test_value_and_reference_semantics(self) -> None:
        for raw in (False, True):
            mode = "raw" if raw else "fat"
            self.run_program(self.executable(ENUM_FIXTURE, f"storage-{mode}", raw=raw))

    def test_cfg_contract(self) -> None:
        project = self.work / "cfg-probe"
        (project / "src").mkdir(parents=True)
        (project / "package.anx").write_text(
            '[package]\nname = "enum_storage_probe"\nkind = "bin"\nversion = "0.1.0"\n\n'
            f'[dependencies]\nyianc = {{ path = "{ROOT / "yianc"}" }}\n'
            f'llvm = {{ path = "{ROOT / "bootstrap" / "llvm"}" }}\n',
            encoding="utf-8",
        )
        (project / "src" / "main.an").write_text(CFG_FIXTURE.read_text(encoding="utf-8"), encoding="utf-8")
        built = subprocess.run([str(self.anx), "build", str(project), "-O0"], cwd=project,
                               env=self.environment, text=True, capture_output=True, check=False, timeout=600)
        self.assertEqual(built.returncode, 0, f"CFG probe build failed:\n{built.stdout}\n{built.stderr}")
        executable = project / "build" / "app"
        self.assertTrue(executable.is_file(), "the CFG probe produced no executable")
        ran = subprocess.run([str(executable)], cwd=CFG_FIXTURE.parent, env=self.environment, text=True,
                             capture_output=True, check=False, timeout=120)
        self.assertEqual(ran.returncode, 0, f"CFG probe failed:\n{ran.stdout}\n{ran.stderr}")
        self.assertEqual(ran.stdout, "", "CFG probe wrote unexpected stdout")
        self.assertEqual(ran.stderr, "", "CFG probe wrote unexpected stderr")


class LinkageTests(NativeProbe):
    def test_targets_and_elimination(self) -> None:
        source = self.source("linkage.an", LINKAGE_PROBE)
        outputs = {
            "ll": self.work / "linkage.ll",
            "bc": self.work / "linkage.bc",
            "obj": self.work / "linkage.o",
            "asm": self.work / "linkage.s",
            "exe": self.work / "linkage",
        }
        for target, output in outputs.items():
            self.compiler(source, output, target=target, allow_ffi=True)
            self.assertTrue(output.is_file(), f"target {target} produced no artifact")

        ir = outputs["ll"].read_text(encoding="utf-8")
        definitions = {}
        parameters = {}
        for line in ir.splitlines():
            match = DEFINE.match(line)
            if match:
                definitions[match.group(3)] = match.group(1)
                parameters[match.group(3)] = match.group(4)
        self.assertIn("__yian_main", definitions)
        self.assertEqual(definitions["__yian_main"], "", "the entry keeps external linkage")
        generated = [name for name in definitions if name != "__yian_main"]
        self.assertGreaterEqual(len(generated), 1, "the probe defines generated functions")
        for name in generated:
            self.assertEqual(definitions[name], "internal ", f"a generated function is internal: {name}")
        helpers = [name for name in generated if parameters[name] == ""]
        self.assertEqual(len(helpers), 1, f"the probe defines one parameterless helper: {sorted(parameters)}")
        helper = helpers[0]
        self.assertIn("declare i32 @abs(i32)", ir, "the extern declaration keeps its C symbol")
        self.assertNotIn(f"internal i32 @abs", ir, "an extern declaration is never internalized")

        import llvmlite.binding as llvm  # noqa: PLC0415 - optional until the bitcode probe runs.

        module = llvm.parse_bitcode(outputs["bc"].read_bytes())
        linkages = {function.name: function.linkage for function in module.functions}
        self.assertEqual(linkages["__yian_main"], 0, "the entry keeps external linkage in bitcode")
        self.assertEqual(linkages[helper], 8, "a generated function is internal in bitcode")
        self.assertEqual(linkages["abs"], 0, "the extern declaration keeps external linkage in bitcode")

        symbols = {}
        for artifact in ("obj", "exe"):
            listing = subprocess.run([self.nm, str(outputs[artifact])], text=True, capture_output=True,
                                     check=False, timeout=120)
            self.assertEqual(listing.returncode, 0, f"llvm-nm failed for {artifact}:\n{listing.stderr}")
            table = {}
            for line in listing.stdout.splitlines():
                fields = line.split()
                if len(fields) >= 2:
                    table[fields[-1].split("@")[0]] = fields[-2]
            symbols[artifact] = table
            self.assertEqual(table["__yian_main"], "T", f"the entry stays global in {artifact}")
            self.assertEqual(table[helper], "t", f"a generated function is local in {artifact}")
            self.assertEqual(table["abs"], "U", f"the extern symbol stays undefined in {artifact}")

        assembly = outputs["asm"].read_text(encoding="utf-8")
        exported = re.findall(r"(?m)^\s*\.globl\s+(\S+)", assembly)
        self.assertIn("__yian_main", exported, "the entry is exported in assembly")
        self.assertNotIn(helper, exported, "a generated function is not exported in assembly")
        self.assertIn(f"{helper}:", assembly, "the generated function keeps its body in assembly")

        optimized = self.work / "linkage.opt.ll"
        optimized_run = subprocess.run([self.opt, "-passes=default<O2>", "-S", str(outputs["ll"]), "-o",
                                        str(optimized)], text=True, capture_output=True, check=False, timeout=300)
        self.assertEqual(optimized_run.returncode, 0, f"opt failed:\n{optimized_run.stderr}")
        optimized_ir = optimized.read_text(encoding="utf-8")
        self.assertNotRegex(optimized_ir, rf"(?m)^define [^\n]*@{re.escape(helper)}\(",
                            "an unused internal helper is deleted after inlining")
        self.assertRegex(optimized_ir, r"(?m)^define [^\n]*@__yian_main\(",
                         "the entry survives optimization")

    def test_call_consumers(self) -> None:
        source = self.source("consumers.an", CONSUMERS_PROBE)
        for raw in (False, True):
            mode = "raw" if raw else "fat"
            self.run_program(self.executable(source, f"consumers-{mode}", raw=raw, optimization=2,
                                             allow_ffi=True))

        signature = self.work / "consumers.ll"
        self.compiler(source, signature, target="ll", raw=True, allow_ffi=True)
        ir = signature.read_text(encoding="utf-8")
        enums = re.findall(r"(?m)^(%yian\.type\.\d+) = type \{ i32, \[\d+ x i32\] \}$", ir)
        self.assertEqual(len(enums), 1, "the probe declares one tagged enum")
        enum_type = enums[0]
        matches = []
        for line in ir.splitlines():
            match = DEFINE.match(line)
            if not match:
                continue
            if match.group(2) != enum_type:
                continue
            parameters = match.group(4).strip()
            if parameters.startswith(f"{enum_type} ") and "," not in parameters:
                matches.append(line)
        self.assertEqual(len(matches), 1, "the probe defines one by-value enum echo function")
        self.assertNotIn("sret", matches[0], "the enum result keeps its value representation")
        self.assertNotIn("byval", matches[0], "the enum parameter keeps its value representation")
        self.assertIn("declare i32 @abs(i32)", ir, "the C declaration keeps its ABI")
        self.assertRegex(ir, r"(?m)^define [^\n]*@__yian_main\(", "the entry keeps its signature")


if __name__ == "__main__":
    unittest.main()
