# anx 包管理器设计

anx 是 YIAN 的项目与依赖管理工具，负责项目发现、清单解析、依赖图构建、源码定位和构建编排。
本设计在现有 Python 实现的基础上，收敛出一个**纯函数式、可被语言服务器复用的项目模型**，
并明确它和编译器 `--packages` 模式的契约边界。

## 1. 目标与范围

### 1.1 目标

1. 为命令行构建和编辑器提供**同一份**项目、模块与依赖信息，避免两套解析规则。
2. 把项目模型从 CLI 中剥离为无副作用的库：导入模块不产生 I/O、不启动子进程。
3. 统一「包名 — 依赖 — 源码路径 — 导入路径」四者的解析规则，并把失败变成稳定、可定位的诊断。
4. 明确源码、测试和构建产物的目录规范。
5. 为后续增量分析与编辑器缓存失效预留结构（文件归属、导入图、依赖图）。

### 1.2 非目标

- 不实现远程依赖下载与版本求解（本阶段只产出书面结论，见 §10-A5）。
- 不在 anx 中重复编译器的词法、语法、类型和符号可见性规则。
- 不引入构建缓存/增量编译本身；只为增量提供数据结构。按包产物的前置约束见 §6.3。
- 不改变 YIAN 语言语义，也不改变 Standalone 模式的相对路径导入规则。
  `--packages` 的 JSON 契约按 §6.1 升级为 v2。

### 1.3 设计原则

1. **单一事实来源**：「规范名 → 源码根」（定义见 §3.1）的映射只由 anx 从 `package.anx`
   产生一次，命令行（经 `--packages`）与编辑器（经 `Project`）都消费同一结果。
   标准库根也走同一处：`compiler/analysis/source_provenance.resolve_stdlib_root()` 是
   A4 之后唯一的解析入口，`anx` 与 `yianc` 都调用它；package 模式下编译器改读
   `packages["std"].sourceRoot`（A2）。
2. **模型与 I/O 分离**：`Project` 是数据；加载、诊断收集和子进程调用是外层行为。
3. **失败可定位**：每个失败携带诊断码、涉及的文件路径和（若可得）源码范围。
4. **解析规则与编译器一致**：`anx` 解析出的目标文件，必须与 `GlobalResolve.__resolve_import_path` 解析出的是同一个。
5. **契约可演进**：`--packages` 的 JSON 由 anx 生成、编译器消费，两侧同属本仓库，
   可以按设计需要升级；升级必须标注格式版本，并在一次改动内同时更新两侧与文档。

## 2. 现状

### 2.1 现有实现

| 文件 | 职责 | 规模 |
| --- | --- | --- |
| `anx/main.py` | CLI 分发、`new/build/run/check/test`、生成 v2 `build/pkg.json`、调用编译器入口 | 183 行 |
| `anx/project.py` | 项目模型：`load()`、包/文件索引、`resolve_import()`、v2 契约生成（A0 起） | 560 行 |
| `anx/manifest.py` | 校验并读取 `package.anx`，产出 `Manifest` / `DependencySpec`（A1 重写） | 176 行 |
| `anx/diagnostics.py` | `Diagnostic` 与 `AX001`–`AX015` 目录、稳定排序与渲染（A1 新增） | 68 行 |
| `anx/scaffold.py` | `new --kind bin|lib|hybrid` 模板 | 41 行 |
| （测试体系） | package 模式的 fixture 在 `tests/package/`，由 `scripts/run_tests.py --suite package` 驱动（A0.5 已完成） | — |
| `pyproject.toml` | 打包元数据与 `yianc` / `anx` 两个 console scripts | — |

> `anx/resolver.py` 在 A0 曾作为 `resolve()` 的兼容包装保留，A2 完成 v2 契约后已删除（`Project` 直接提供 `files` / `packages` / `compiler_package_map()`）。

### 2.2 已有能力

- 项目初始化：`new`、`new --lib`。
- 清单读取：`[package] name/version`，`[dependencies] <key> = { path = "..." }`。
- 路径依赖：递归遍历、环检测（`CycleError`）。
- 构建编排：把解析结果写成 `build/pkg.json`，调用编译器入口（优先 `yianc`）。
- 命令：`build`（`-t exe`）、`run`（构建后执行）、`check`（`-t none`）。
- 命令行交付：`pyproject.toml` 声明 `yianc` / `anx` 两个 console scripts，可本地 `pip install -e .`（§8.4）。

### 2.3 现存缺口

| 编号 | 缺口 | 证据 |
| --- | --- | --- |
| G1 | `resolve()` 只返回扁平文件列表和根映射，没有包归属、导入图和依赖边，编辑器无法增量失效 | `resolver.py:34` 返回 `tuple[list[Path], dict[str, Path]]`。**A0 已完成**：`anx/project.py` 的 `Project` 提供包归属、依赖边与只读映射 |
| G2 | 缺少清单校验：依赖路径不存在时泄漏 Python 异常文本 | `errors/missing_dep/expected.stderr` 为 `No such file or directory`。**A1 已完成**：报 `error[AX003]` |
| G3 | **依赖键**与依赖包清单 `name` 可以不一致；实现以依赖键作为导入名和图节点名 | `errors/cycle`：依赖键 `lib` → `../lib`，而该包清单 `name = "libb"`；该 fixture 源码无 `import`，此行为无覆盖。§3.5 规定依赖键必须等于被依赖包的规范名。**A1 已完成**：不匹配报 `AX005`，实现只按规范名建图 |
| G4 | 单段包导入解析错误：`from pkg import x` 会去找 `src.an` | `Path('src').joinpath().with_suffix('.an') == 'src.an'`。§5.1 规定导入必须解析到 `.an` 源文件。**A2 已完成**：Package 模式报 `AX010` |
| G5 | 同名包冲突静默保留先到者 | `resolver.py:40` `if name in adj: return`。**A1 已完成**：路径不同报 `AX007` |
| G6 | 传递依赖全局可见：未声明的包也能按名字导入 | `deps/deep`：`app` 只声明 `libb`，`libb` 声明 `libc`，二者根都进 `pkg_roots`。§4.3 收紧为仅直接声明的依赖。**A2 已完成**：编译器按 v2 依赖边校验，未声明报 `AX009`（`errors/undeclared_dep`） |
| G7 | 可见性错误与「符号不存在」混为一谈 | 编译器 `global_resolve.py:201` 对非 `pub` 符号报 `is not found`。**A1 已完成**：区分「未公开」与「不存在」 |
| G8 | `build/run/check` 无法传递编译选项（`-O`、`--raw-pointers`）、程序参数和标准输入；程序退出码不传播 | `main.py:50-75` 硬编码参数，`run` 使用 `check=True`。**A3 已完成**：选项白名单透传、`run` 转发参数与 stdin、退出码分两段 |
| G9 | `anx test` 运行的是 anx 自身的集成套件，而不是用户项目的测试 | `main.py:73-75`。**A3 已完成**：`anx test` 跑项目自己的 `tests/`（§8.5），anx 自身套件改由 `scripts/run_tests.py --suite package` 调用 |
| G10 | anx 只能在仓库检出内工作：`_YIAN_ROOT` 由文件位置推导，无安装入口和编译器定位配置 | `main.py:14-15`。**A4 已完成**：`resolve_stdlib_root()`（`--compiler-root` / `YIAN_LIB` / `YIAN_ROOT`）+ `install.sh --regular`；根目录从不从输入路径推断 |
| G11 | `build/pkg.json` 每次构建无条件重写，无版本标记 | `main.py:32-34`。**A3 已完成**：内容不变则不重写 `build/pkg.json`（§6.2） |
| G12 | 诊断只有 `stderr` 文本，没有结构化结果，编辑器无法直接消费 | `main.py:29` |
| G13 | Package 模式第一段只查包名、无相对回退；本包内目录与某个依赖包同名时，**依赖包无条件遮蔽本包模块**且无任何诊断 | 实测：本包 `src/dup/foo.an` 与依赖 `dup` 各有 `which()`，`from dup.foo` 取到依赖的值；断言本包值时运行期失败。§5.1 需固定该情形。**A2 已完成**：语义由 `self_shadow_dir` 固定，编译器额外给出 `warning:` 提示 |
| G14 | 依赖键与根包名相同时，该依赖被丢弃并报**误导性**的循环诊断 | `app` 的依赖键写作 `app` → `error: Circular dependency: app → app`（`resolver.py:40` 的 `if name in adj: return`）。**A1 已完成**：报 `AX007`（`errors/duplicate_name`） |
| G15 | 编译器不计算「导入方属于哪个包」 | `__resolve_import_path` 的 Package 分支不使用 `unit`，只用 `paths[0]` 查表；§4.3 的可见性校验缺少这一前置。**A2 已完成**：按 `sourceRoot` 最长前缀归属 |
| G16 | Standalone 模式按**导入方文件目录**解析、无 `..`；且编译器从不按 `import` 加载文件，只在本次命令行传入的 unit 里查 | `src/main.an` 的 `from utils.strings` → `src/utils/strings.an`；同一行写在 `src/sub/deep.an` 则去找 `src/sub/utils/strings.an`；目标文件存在但未传入仍报 `Cannot resolve import path`。**A2 已完成**：差异写进 §5.2，且未改变 Standalone 行为 |
| G17 | `yianc` 的位置参数是 `nargs='+'`，选项夹在路径之间即解析失败 | `yianc lib a.an -t none` → `unrecognized arguments: a.an`；必须「选项在前、路径连续放在最后」。**A3 已完成**：改用 `parse_intermixed_args`，任意顺序可混写 |
| G18 | **库包无法被分析**：编译器无条件要求 `main`，`anx check` 一个 `new --lib` 出来的包直接失败；清单里也没有任何字段表明包类型 | 实测 `anx new --lib mylib && anx check mylib` → `error: No 'main' function found`（`type_check.py:__find_main`）；`anx build` 也总是产出可执行文件。**A3 已完成**：`lib` 根的 `check` 通过（`lib_pkg`），`build`/`run` 明确报不支持 |
| G19 | 路径依赖可以位于另一个包的 `src/` 之下，父包的 `rglob("*.an")` 会重复收集子包源码，破坏「文件唯一归属」 | §3.6 的断言只在源码根互不嵌套时成立；`resolver.py` 递归扫描时没有排除已知的嵌套包根。**A1 已完成**：按最长源码根归属并报 `AX013` |
| G20 | **测试体系三套并存**：`tests/` 只覆盖 standalone 模式（`scripts/run_tests.py` + `run_safety_tests.py`），package 模式的 19 个用例在 `anx/tests/`，由另一套 runner 和另一套期望文件约定驱动 | `anx/tests/*/{package.anx,expected.stdout,expected.stderr}` 与 `tests/output/**/*.ans` 是两套约定；`tests/` 下没有任何 package 模式覆盖。**A0.5 已完成**：并入 `tests/package/`，统一由 `scripts/run_tests.py` 驱动 |
| G21 | **不可达定义完全不被检查**：类型检查的唯一根是 `main`，因此从未被调用的函数/方法（bin 里也一样）不会报错；库包更是直接失败 | 实测：`fn bad() { let x: i32 = "hello"; }` 且 `main` 不调用它 → `yianc -t none` 通过，被调用才报错；未实例化的泛型体同样不检查，用 `bool` 实例化 `add<T>(a,b){a+b}` 才在 `+` 处报错（§2.5、A3）。**A3 已完成**：检查集合 = 根包全部顶层定义（排除泛型），生成集合仍是 `main` 可达；依赖包（含 `std`）按需检查 |
| G22 | **程序入口被当成「全局唯一」**：`__find_main` 扫描所有 unit 找唯一 `main`，所以依赖包里只要也有 `main` 就冲突 | 实测：`app`(bin) 依赖 `tool`(bin，自带 `main`) → `error: Multiple 'main' functions found`。修法：程序入口取 `packages[root].entry`，并以 `kind` 拒绝 bin 作依赖（§3.2、§6.1）。**A1/A2 已完成**：`AX015` + `packages[root].entry`（`hybrid_dep`） |
| G23 | **放宽检查根所需的机制缺失**：① `TypeCtx` 只有 `add_procedure` / `get_procedure`，没有枚举接口；② `DefPoint` 没有「已生成」标记，而 `def_points` 现在**隐式**等于 main 可达集；③ LLVM 层按**函数名** `main` → `__yian_main` 识别程序入口 | `context.py:104/556/567`；`main.py:413` `def_points = type_checker.export()` → `__cfg(def_points)` → LLVM 全量消费；`llvm/module.py:137`。这三处不补，A3 无法安全落地（S1、A2、A3）。**S1 已完成** ① ②（`iter_procedures`、`__generated` 集合）；③ 的入口识别留给 A3，**A3 已完成**：入口 `type_id` 由 `TypeCheck` 显式传给 LLVM 层，不再按函数名判断 |

### 2.4 与编译器的现有契约

`anx` 生成 `build/pkg.json`（扁平 JSON）：

```json
{
  "app": "/abs/path/app/src",
  "mathlib": "/abs/path/lib/src",
  "std": "/abs/path/yian/lib/src"
}
```

编译器侧对应行为（`compiler/analysis/passes/global_resolve.py:208-232`）：

- 传入 `--packages` 即进入 Package 模式，`import` 的第一段必须是已知包名，否则报
  `Unknown package '<name>'`。
- 目标文件 = `pkg_roots[第一段] / 其余路径段... .with_suffix(".an")`；查不到则报
  `Cannot resolve import path: <path>`。
- 不传 `--packages` 时是 Standalone 模式：`std` 先查标准库查找表，其余各段拼接后
  **相对导入方文件所在目录**解析（`unit.path.parent.joinpath(*paths)`），没有 `..`。
- 符号可见性由编译器的 `lookup_exportable` 决定，与 anx 无关。

两种模式的优先级：

| 模式 | 查找顺序 | 回退 |
| --- | --- | --- |
| Package（有 `--packages`） | ① `pkg_roots[第一段]`（本包与 `std` 也在其中）→ ② 报 `Unknown package` | 无 |
| Standalone（无 `--packages`） | ① `std` 查找表 → ② 相对导入方目录 | 无 |

两条必须记住的实现事实：

- **编译器从不通过 `import` 加载文件。** `__path_lookup` 只包含本次命令行传入的 unit，
  所以 Standalone 模式下目标文件即使存在，没被传入一样报 `Cannot resolve import path`；
  anx 不受影响只是因为它一次性传入了依赖图里的全部文件。
- **编译器不计算「导入方属于哪个包」。** Package 分支只用 `paths[0]` 查表，`unit` 仅用于
  Standalone 的相对解析。§4.3 的可见性校验必须补上这一步（按 `roots` 最长前缀归属）。

该契约由 anx 生成、编译器消费，两侧都在本仓库内，因此可以按设计需要升级。
本设计将其升级为 **v2**（§6.1）：把扁平的「包名 → 源码根」扩展为带格式版本、根映射和
**依赖边**的结构，使编译器也能执行 §4.3 的依赖可见性规则。
仓库中没有任何被跟踪的 `pkg.json`（根 `.gitignore` 的 `build/` 覆盖了编译器产物与
package fixture 的 `build/`），
因此升级不涉及产物迁移，只改生成端、消费端和文档。

### 2.5 编译单元是整个程序

`anx build` 只调用编译器**一次**，把根包、所有传递依赖和标准库的文件一次性传入；
编译器侧是单进程、单 `TypeCtx`、单 LLVM module：

| 阶段 | 覆盖范围 |
| --- | --- |
| 词法 / 语法 | 所有传入文件 |
| 全局解析（符号、类型、导入、impl 注册表） | 所有 unit，全局共享 |
| 类型检查 / CFG / LLVM | 只有从 `main` **可达**的定义（含按需实例化的泛型与方法） |

因此：

- 包与包之间**不存在任何编译中间产物**——它们的「接口」就是共享的内存状态
  （同一 `TypeCtx`、符号表与全局 impl 注册表）；
- 一个从未被 `main` 调用到的库函数会被解析，但**不会被类型检查，也不会生成代码**
  （这也是 `ide-support-plan.md` 需要分析未达定义的原因）；
- `build/` 下没有 per-package 产物，只有输入 `pkg.json`、`compile.log`、可选的 `ir.ll`
  和最终可执行文件。

「按包编译」因此不是优化问题，而是**编译模型的改变**；其前置约束见 §6.3。

**检查的根只有 `main`。** 上表最后一行的种子是 `type_check.py:__find_main`，之后由 lowering
按需 `report_def` 加入被实例化的泛型与方法。两个后果：

- 从未被调用到的定义**完全不被检查**（bin 也一样），见 G21；
- 库包没有 `main`，因此根本无法分析（G18）。

而 `global_resolve` 已经把**每一个**顶层函数与方法体注册进 `TypeCtx`
（`global_resolve.py:296/377/419`），所以「可检查的定义清单」是完整的、缺的只是「谁来当检查根」
这个决定。规则见 A3。

## 3. 项目模型

### 3.1 目录规范

```
<project>/
├── package.anx           # 清单（必需）
├── src/                  # 包源码根（必需）
│   ├── main.an           # 默认入口（仅 bin；可用 entry 覆盖）
│   └── utils/math.an     # 模块路径 = 相对 src/ 的路径
├── tests/                # 项目测试源码（可选，本设计只约定位置）
└── build/                # 构建产物（anx 生成，不纳入版本管理）
    ├── pkg.json
    └── app
```

约定：

- **源码根**一律是 `<包根>/src` 的绝对路径（`Package.source_root`），它同时是两个东西：
  ①**收集边界**——anx 递归扫描它，得到要交给编译器的 `.an` 文件集合；②**解析基准**——
  `from pkg.a.b import x` 解析为 `源码根/a/b.an`。源码根固定为 `src/`，标准库也不例外，
  清单不暴露可配置源码根。注意与**包根**（含 `package.anx` 的目录，`Package.root`）和
  **项目根**（根包的包根，由 `discover` 找到）区分。
- `src/` 硬编码在三处：`anx/project.py`（扫描与索引）、`anx/scaffold.py`（创建）、
  `scripts/run_tests.py`（测试发现，仅用于定位 fixture 的包根）。固定为 `src/` 之后这是有意的，
  三处必须保持一致。
- 标准库同样遵循该规则：包根 `lib/`，源码根 `lib/src/`，`lib/package.anx` 声明
  `name = "std"` 与 `kind = "lib"`；
  模块 `std.core.io` → `lib/src/core/io.an`。此前 `pkg_roots["std"]` 曾直接指向 `lib`（源码根 = 包根），
  编译器为此在 `compiler/main.py:__build_unit_names` 里用「路径中出现 `lib` 段」生成 LLVM 单元名；
  该 hack 对任何含 `lib` 段的用户路径同样生效，且不同路径可能算出同名（实测
  `/a/lib/foo/x.an` 与 `/b/lib/foo/x.an` 都得到 `foo_x`）。**A2 已清理**：`__build_unit_names`
  改为按「文件 → 包归属」生成 `<包名>_<模块路径>`，包外的文件退回文件 stem。
- 模块路径由文件相对 `src/` 的路径决定：`src/utils/math.an` → 模块 `utils.math`。
- **入口默认是 `src/main.an`，但可以在清单里覆盖**（`entry`，§3.2）。`kind = "bin"` 与
  `"hybrid"` 的包必须有入口、且入口中定义唯一的 `main` 函数；`kind = "lib"` 的包不得有入口。
  注意编译器**不应**再扫描所有 unit 去找 `main`：程序入口就是 `packages[root].entry`（§6.1），
  否则「依赖一个 `hybrid` 包」会因为对方也有 `main` 而报 `Multiple 'main' functions found`（G22）。
  Cargo 的 `[[bin]] path`、dune 的 `(executable (name <module>))`、SwiftPM 5.4 起的
  `.executableTarget` 都不把入口文件名写死；本设计沿用「默认约定 + 显式覆盖」，
  后续「一个包多个可执行文件」也从这里扩展。
- **入口模块不可被其他模块导入**：`from <pkg>.<入口模块> import x` 报 `AX014`。入口是程序根，
  把它当库模块用会带来执行顺序与重复求值的困惑；Cargo 用「bin 是独立 crate root」、
  Go 用「`package main` 不可导入」达到同样效果。
- `build/` 全部内容都是产物，可随时删除重建。

### 3.2 清单格式

```toml
[package]
name = "app"            # 必需；同时是导入时使用的第一段（见 §3.5）
kind = "bin"            # "bin" | "lib" | "hybrid"；省略时视为 "bin"
entry = "src/main.an"   # 可选；仅 kind = "bin"，默认即此值（见下）
version = "0.1.0"       # 可选，默认 "0.1.0"；本阶段不参与版本求解

[dependencies]
mathlib = { path = "../lib" }   # 键必须等于 ../lib 清单里的 [package].name
```

规则：

- `[package].name` 必需、非空且是合法标识符；缺失或非法是 `AX002`。
- `[package].name` 不得是保留名（当前保留 `std`），否则 `AX011`。
- `kind` 可选，取值只能是 `"bin"` / `"lib"` / `"hybrid"`，取值非法是 `AX002`。
- `entry` 可选，**仅对 `"bin"` 与 `"hybrid"` 有效**，默认 `src/main.an`。相对包根解析，
  必须是落在该包源码根之内、且确实存在的 `.an` 文件。
- 三种 `kind` 的语义（对应 Cargo 的 bin-only / lib-only / lib+bin）：

  | kind | 入口 | 可否作依赖 | `build` / `run` |
  | --- | --- | --- | --- |
  | `bin` | 必须有 | **不可以**（`AX015`） | 产出 `build/app` / 构建后执行 |
  | `lib` | 不得有 | 可以 | **不支持**（本阶段无库产物，§2.5、§6.3） |
  | `hybrid` | 必须有 | 可以（可导入其非入口模块） | 同 `bin` |

  - 入口中缺少 `main` 或存在多个 `main` 由编译器报错（§7.4）。
  - 入口与 `kind` 不符（`lib` 声明了 `entry` 或含 `src/main.an`；`bin` / `hybrid` 的入口
    不存在、不在源码根内；`bin` / `hybrid` 既未声明 `entry` 又没有默认入口）是 `AX008`。
- 为什么需要第三种 `hybrid`：`kind` 只有 bin / lib 时，「既是库又有 CLI」无法表达。
  Cargo 允许一个包同时有 `src/lib.rs` 与 `src/main.rs`，Nimble 的 `nimble init` 直接给出
  Library / Binary / Hybrid 三选一。`hybrid` 的**库接口 = 除入口之外的全部模块**
  （入口本身不可导入，§5.1）。
- `bin` 为什么不能作依赖：它没有对外的库接口，被依赖时不成立——Cargo 同样拒绝依赖
  bin-only crate（bin 不产出可链接产物）。需要被复用的代码应做成 `lib` 或 `hybrid`。
- 为什么不把入口文件名写死：Cargo 允许 `[[bin]] path`，dune 按模块名指定，SwiftPM 5.4 起
  用 `.executableTarget` 显式声明——**默认约定 + 可覆盖**是通行做法，也为后续「一个包多个
  可执行文件」留出入口。`src/main.an` 同时是合法模块路径，所以必须能改。
- 为什么用显式 `kind` 而不是从文件推断：`kind` 决定 `build`/`run` 是否可用（`lib` 本阶段
  没有产物），属于包的**用途声明**；从「是否存在入口文件」推断会让重命名或移动源文件改变
  包的用途，也会与 `entry` 覆盖互相干扰。
- `version` 必须是可解析的版本字符串，但本阶段只做格式校验，不做约束求解。
- `[dependencies]` 的每个键**必须等于**被依赖包清单的 `[package].name`，否则 `AX005`。
- 依赖条目的 `path` 必需，相对清单所在目录解析为绝对路径。
- 本阶段**不支持依赖重命名**；将来若加入 `package = "<declared>"`，必须按 §3.5 的前提实现。

### 3.3 数据模型

模型全部为不可变数据类，定义在纯模块 `anx/project.py` 中，不 import `subprocess`：

```python
class PackageKind(Enum):
    BIN = "bin"             # 有入口，产出可执行文件，不可作依赖
    LIB = "lib"             # 无入口，只做分析，可作依赖
    HYBRID = "hybrid"       # 有入口且可作依赖（库 + CLI）

@dataclass(frozen=True)
class Dependency:
    name: str               # 被依赖包的规范名，即本包 [dependencies] 的键
    path: Path              # 依赖包根目录（已 resolve 的绝对路径）

@dataclass(frozen=True)
class Package:
    name: str               # 清单中的 [package].name，也是导入名
    kind: PackageKind       # BIN | LIB（§3.2）
    version: str
    root: Path              # 包根目录
    manifest_path: Path
    source_root: Path       # root / "src"
    entry: Path | None      # BIN / HYBRID 的入口文件（默认 src/main.an）；LIB 为 None
    dependencies: tuple[Dependency, ...]   # 按 name 排序

@dataclass(frozen=True)
class SourceFile:
    path: Path              # 绝对路径
    package: str            # 所属包的规范名
    module: tuple[str, ...] # 相对 source_root 的模块路径段，不含 .an

@dataclass(frozen=True)
class Project:
    root_package: str
    packages: Mapping[str, Package]              # 规范名 -> 包（只读）
    files: Mapping[Path, SourceFile]             # 绝对路径 -> 文件归属（只读）
    dependencies: Mapping[str, tuple[str, ...]]  # 规范名 -> 直接依赖的规范名（只读）
    std_package: str                             # 标准库的规范名，固定 "std"
```

加载期的诊断不在 `Project` 上，而在 `LoadResult` 里（§3.4）——因为「清单损坏」时并不存在
可用的 `Project`，把诊断挂在 `Project` 上就无法表达这种情况。

辅助方法（编辑器与 CLI 共用）：

```python
def file_of(self, path: Path) -> SourceFile | None
def package_of(self, path: Path) -> Package | None
def resolve_import(self, importer: Path, paths: Sequence[str]) -> ImportResolution
```

`resolve_import` **不返回 `None`**，而是返回带失败原因的判别结果——否则调用方无法区分
「包不存在」「包存在但本包没声明」「路径不是模块」这三种情况：

```python
class ImportFailure(Enum):
    UNKNOWN_PACKAGE = "unknown_package"   # 第一段不在 roots 中            → AX012
    NOT_VISIBLE = "not_visible"           # 在 roots 中但不在本包可见集合  → AX009
    NOT_A_MODULE = "not_a_module"         # 未解析到 `.an` 源文件          → AX010
    ENTRY_MODULE = "entry_module"         # 目标是某个包的入口模块          → AX014

@dataclass(frozen=True)
class ImportResolution:
    target: SourceFile | None       # 成功时非空
    failure: ImportFailure | None   # 失败时非空
```

不变式：`target` 与 `failure` 恰有一个非空。该函数是**纯函数**，不做 I/O、不解析源文件。

**不可变的边界**：`frozen=True` 只禁止字段重新赋值，不保护容器内容——直接暴露 `dict`
仍然是可变的。因此模型中的映射一律以只读视图交给调用方（构造时用 `MappingProxyType` 包装，
或直接保存排序后的 `tuple`）。编辑器会把模型当作长期缓存持有，这一点必须成立。

本阶段**不提供** `importers_of`（反向导入图）：它需要全量导入分析，与 §5.3 的延期决定冲突。

### 3.4 项目发现与加载

```python
def discover(start: Path) -> Path | None
def load(root: Path, *, std_root: Path | None = None) -> LoadResult
```

- `discover`：从 `start`（文件则取其父目录）逐级向上查找 `package.anx`，返回最近的项目根；
  不越过文件系统根，找不到返回 `None`。编辑器用它把「当前编辑的文件」映射到项目；
  CLI 用它把 `anx build` 的默认目录补全。**A3 已实现**（`anx/project.py::discover`）。
- `load` 返回 `LoadResult` 而不是 `Project`：

```python
@dataclass(frozen=True)
class LoadResult:
    project: Project | None
    diagnostics: tuple[Diagnostic, ...]
```

- **`project is None` 当且仅当根包本身不可用**：找不到根清单（`AX001`）、根清单无法解析或
  `name`/`kind` 非法（`AX002`）、根包名是保留名（`AX011`，此时 `packages["std"]` 会与它冲突）、
  根包目录结构与 `kind` 不符（`AX008`）。此时没有可用的 `Project` 可返回，诊断照常给出。
- **`project` 可用时 `diagnostics` 仍可非空**：单个依赖路径缺失（`AX003`）、依赖目录没有清单
  （`AX004`）、依赖键不匹配（`AX005`）、同名包冲突（`AX007`）、保留名（`AX011`）、源码根嵌套
  （`AX013`）都只影响对应子树，其余部分继续加载。
- 允许继续的含义是：**出错的那棵子树被跳过**，其内部不再继续扫描。因此「一次性返回全部诊断」
  的准确含义是「所有**可独立发现**的诊断」。
- 诊断顺序固定：先按 `path` 排序（`None` 排最前），再按 `span` 起点，再按 `code`。
  同一输入下输出稳定，可以直接写进预期文件比对。
- CLI（`build`/`run`/`check`）在 `diagnostics` 非空时报告并退出 1，不进入编译；
  编辑器可以继续使用可用的 `project`（例如依赖尚未就绪时，本包内的补全仍然有意义）。

### 3.5 包名、依赖键与导入名

**依赖键**是**依赖方** `package.anx` 中 `[dependencies]` 表里的键名；**清单 name** 是**被依赖方**
自己 `package.anx` 中 `[package].name`。两者来自两份不同的清单：

```toml
# app/package.anx —— 依赖方
[dependencies]
mathlib = { path = "../lib" }   # "mathlib" 是依赖键

# ../lib/package.anx —— 被依赖方
[package]
name = "mathlib"                # 这是清单 name
```

当前实现取**依赖键**作为导入名和图节点名：`from <依赖键>.模块 import ...` 能解析成功，
`pkg_roots` / `pkg.json` 的键也是依赖键（`resolver.py:45-48` 用 `dep_name` 建图，
`global_resolve.py:217` 用 import 第一段查表）；而根包节点用的是**清单 name**
（`resolver.py:50` 的 `walk(manifest.name, cwd)`）。两者口径并不一致。

> 覆盖缺口：`errors/cycle` 是仓库中唯一依赖键与清单 `name` 不一致的 fixture，但它的源码
> 没有任何 `import`，所以「导入名取依赖键」这一行为目前**没有测试覆盖**。

> 重命名的实际后果：依赖键不只是「显示名字」。`pkg_roots` 只以依赖键为键，因此若父包把清单
> 名为 `libb` 的依赖写成键 `renamed`，该依赖**内部**的 `from libb.ops import ...` 自导入会
> 失败（`Unknown package 'libb'`）。实测：键与清单 `name` 一致时同一项目构建运行通过；改成
> `renamed` 后编译在依赖自身源码处报错。也就是说，允许不一致不是「能用但有歧义」，而是会产生
> 无法自洽的包——子包无从知道自己会被父包改成什么名字。

每个包只有一个**规范名**，即它自己清单里的 `[package].name`；
该名字同时是导入名、依赖图节点名和 `pkg.json` 的键。

- `[dependencies]` 的键**必须等于**被依赖包清单的 `[package].name`，否则报 `AX005`。
- 依赖**不可重命名**。理由见上：`pkg_roots` 只以依赖键为键，改名会打断依赖自身的自导入，
  属于正确性问题而不是风格问题；而路径依赖、无版本的现状也没有改名的使用场景。
- 根包与依赖包一律使用规范名，消除当前根包用清单 `name`（`resolver.py:50`）、
  依赖用键（`:45-48`）的口径差异。
- `std` 是保留名，用户包（根包或依赖）不得使用，否则 `AX011`。
- 将来若确需重命名（例如 vendoring 上游 fork），再引入 `package = "<declared>"`，但必须按
  「规范名解析 + 别名可读」实现：依赖树内部一律按规范名解析，别名只是引用方可见的附加名字，
  从而保证依赖自身的自导入仍然有效。本阶段不做。

> 迁移影响：现有 `errors/cycle` fixture 依赖键为 `lib`、清单名为 `libb`，会先报
> `AX005`，从而掩盖它本要验证的环检测。该 fixture 应把键改为 `libb` 以保留环的语义。
> 这属于实施项 A1，不是本设计的代码改动。

### 3.6 文件归属

加载时为每个包的源码根递归收集 `*.an`，建立 `路径 -> SourceFile` 索引：

- `module` 由相对路径去掉 `.an` 得到。
- `package` 记录规范名，供诊断和编辑器按包分组使用。
- **嵌套源码根必须排除。** 路径依赖可以落在另一个包的 `src/` 之下（例如
  `app/src/vendor/foo`），父包直接 `rglob("*.an")` 会把子包源码重复收集，既重复编译又
  破坏文件归属。规则：
  1. 归属按**最长匹配的源码根**判定（与 §6.1 的「取最长前缀」一致）；
  2. 收集某个包的文件时，**排除落在更深源码根之下的文件**；
  3. 检测到任意两个源码根互相嵌套时报 `AX013`：不阻止加载，但必须让用户看见。
- 满足以上规则后，同一文件只属于一个包，「文件唯一归属」才真正成立。

## 4. 依赖解析

### 4.1 图的构建

以**规范名**（`[package].name`）为节点，`[dependencies]` 为有向边，从根包出发做深度优先遍历：

1. 读取根包清单，校验名字（合法标识符、非保留名），入图。
2. 对每个依赖：解析 `path` 为绝对路径，读取其清单，校验「依赖键 == 被依赖包规范名」（§3.5）；
   若被依赖包 `kind = "bin"` → `AX015`（bin 没有库接口，不能作依赖，§3.2）；入图并递归。
3. 同一规范名再次出现时（菱形依赖），比较解析后的绝对路径：
   - 相同 → 复用，跳过；
   - 不同 → `AX007` 同名包冲突（替换当前的静默先到者）。

### 4.2 环检测

- 沿用 DFS + 递归栈，报错信息包含完整环路径（`a → b → a`）。
- 环检测在**读取完所有清单之后、收集源码之前**完成，避免部分加载的中间状态。

> 已知盲区：`kind = "bin"` 的依赖被 `AX015` 拒绝后**不入图**（§4.1 的「入图并递归」只对通过校验
> 的依赖成立），因此穿过 `bin` 依赖的环不会被发现。这类项目本来就不合法（`AX015` 已经报错），
> 所以不额外处理；`tests/package/errors/cycle` 的根包因此必须是 `hybrid` 才能验证环检测。

### 4.3 传递依赖的可见性

包只能导入 ①自己、②**自己直接声明**的依赖、③`std`。
传递依赖一律不可见——即使它出现在依赖图里，只要没有被本包显式声明，就不能导入。

理由：当前实现把所有传递依赖放进同一个扁平命名空间（G6），既掩盖了「忘记声明依赖」的错误，
也让编辑器的补全结果不可解释。收紧后 `deps/deep` 仍然成立（`libb` 声明了 `libc`），
而 `app` 直接用 `libc` 会得到 `AX009`。

> 实测：在 `deps/deep` 上把 `app/src/main.an` 改成同时导入 `libb.core` 与 `libc.secret`
> （`app` 并未声明 `libc`），当前实现构建并运行成功；生成的 `pkg.json` 把 `app`、`libb`、
> `libc`、`std` 平铺在同一层。也就是说 `app` 的清单说它只依赖 `libb`，实际却在用 `libc`，
> 而没有任何检查会指出这一点。该行为同样**没有测试覆盖**（没有 fixture 导入传递依赖）。

编译器的 Package 模式目前只看 `pkg.json` 的键，不做「谁声明了谁」的检查。
升级后的 v2 契约携带依赖边（§6.1），编译器据此计算每个包的可见集合
`{自身} ∪ 直接依赖 ∪ {std}`：import 的第一段不在集合内时报「未声明依赖」，
完全不在根映射内时报「未知包」。这样可见性规则在命令行和编辑器两条路径上由同一份数据保证。

本阶段**不提供重导出（re-export）**：外观包无法把传递依赖的符号再暴露给自己的使用者，
使用方必须自行声明。代价是偶尔多写一行 `[dependencies]`，收益是「一个文件能导入什么」
完全由它所属包的清单决定。将来若出现真实痛点，再引入显式的 re-export。

### 4.4 依赖路径诊断

每个依赖路径要能区分：

- 路径不存在 → `AX003`；
- 目录存在但没有 `package.anx` → `AX004`；
- 清单存在但解析失败或缺 `name` → `AX002`。

三者都不能再泄漏 `FileNotFoundError` / `tomllib` 的原始信息。

### 4.5 与标准库的关系

- 标准库作为内置包名为 `std`，由 anx 注入 `packages["std"]`，用户无需声明。
- `std` 的源码根固定为 `<checkout>/lib/src`（`lib/` 是包根，`lib/package.anx` 声明
  `name = "std"`），由 anx / 编译器内置定位，不参与用户依赖遍历（见 §10-A4）。
- `std` 是保留名：根包或依赖声明 `name = "std"` 时报 `AX011`。
- 标准库是**库包**（`kind = "lib"`）：没有入口，不参与 `bin` 的构建流程，`anx check` 只做分析。
- `std` 对所有包可见：任何包都可直接导入 `std`。

## 5. 导入解析

### 5.1 规则

先固定术语：**模块就是一个 `.an` 文件**。目录不是模块，只是模块路径中的一段；
`源码根/a/b.an` 对应模块 `a.b`，而 `a/` 目录本身不构成任何模块。

给定导入方文件 `F` 和导入路径 `paths = (p0, p1, ..., pn)`：

1. `p0` 是包规范名，对应包的**源码根目录**；它必须是 `F` 所属包可见的（§4.3），否则 `AX009`。
   包名段是导入路径中**唯一允许对应目录**的一段。
2. 其余段必须恰好是某个模块的路径：`source_root / p1 / ... / pn` 加上 `.an` 之后，
   是一个已存在、且已在 `files` 索引中的 `.an` 文件，否则 `AX010`。
3. 因为目录不构成模块，任何解析不到 `.an` 文件的导入都落在 `AX010`，包括：
   - `n == 0`（如 `from mylib import x`）：剩余段为空，目标是源码根目录本身，不是模块；
     应写成 `from mylib.core import x`。当前实现会错误地去解析 `src.an`（G4）。
   - 目标文件不存在。
4. 解析到的文件不得是**任何包的入口**（`Package.entry`，§3.2）：入口是程序根，不可被导入，
   否则 `AX014`。此检查在解析成功之后进行，与可见性无关——即使入口属于本包自己，也不可导入。

单段导入不作为特例处理，而是被统一规则自然排除——
「导入路径必须解析到一个 `.an` 源文件」。

**包名段优先于同名的包内目录。** 若本包有 `src/dup/foo.an`，同时某个可见依赖的规范名是
`dup`，那么 `from dup.foo` 永远解析到那个依赖，本包的 `dup/foo.an` **不可导入**（G13）。
这是「第一段是包名」这条规则的必然结果，不是缺陷；要让该模块可导入，应改目录名或依赖名。
加载期可以据此给出提示（见 A2）。

**规则由谁执行。** `anx.load()` **不读源文件、不解析 `import`**（§5.3），所以
`AX009/AX010/AX012/AX014` 不来自加载期：Package 模式下由编译器在解析 import 时判定
（读 v2 的 `packages` 表，§6.1）；编辑器把已经解析出的 `paths` 交给
`Project.resolve_import()`，按返回的 `ImportFailure` 生成同样的诊断。三者共用本节与
§4.3 的规则，诊断责任划分见 §7.4。

### 5.2 与编译器保持一致

- anx 的解析结果必须等于 `GlobalResolve.__resolve_import_path` 的结果。
- 收敛方式：以 anx 的 `Project.resolve_import` 为规范实现；编译器在没有 `--packages`
  时保持现有 Standalone 规则，有 `--packages` 时行为必须与本设计一致。
- Package 模式的可见性校验需要「导入方所属的包」，由 `roots` 最长前缀归属提供（§2.4、A2）。
- Standalone 与 Package 的语义差异必须写进对照 fixture 的前提：Standalone 按导入方目录
  解析且**不会自动加载文件**（§2.4），Package 只认包名（G16）。
- 二者的差异必须由一处共享规则或一组对照 fixture 固定，不能靠分别维护。

**A2 落地后的对照表**（同一份源码在两种模式下的差别）：

| | Standalone（无 `--packages`） | Package（有 `--packages`） |
| --- | --- | --- |
| 第一段 | 先查 `std` 查找表，否则相对导入方目录 | 必须是 `packages` 中的包名 |
| 可见性 | 无（能查到就能用） | `{自身} ∪ 直接依赖 ∪ {std}`，否则 `AX009` |
| 目标 | `source_root / 各段 .an`，**必须已出现在命令行里** | `packages[p0].sourceRoot / 其余段 .an`，必须存在于索引中 |
| 单段导入 | 报 `Cannot resolve import path: <path>` | 报 `error[AX010]`（目录不是模块） |
| 未解析到文件 | 报 `Cannot resolve import path: <path>` | 报 `error[AX010]` |
| 未知包名 | 不适用（没有包名概念） | `error[AX012]` |
| 未声明依赖 | 不适用 | `error[AX009]` |
| 入口模块 | 不适用（没有 `entry` 概念） | `error[AX014]` |

Standalone 的报错文本保持原样：它是没有项目上下文的低层模式，`AXnnn` 目录只覆盖 Package 模式。
两者共享的是**匹配规则**（第一段是包名、其余段必须解析到 `.an` 文件），由 `Project.resolve_import`
与 `GlobalResolve.__resolve_package_import` 各实现一份并互相固定。

### 5.3 导入图与 `resolve_import`

`Project` 只强制构建**依赖图**（§3.3 `dependencies`）。**导入图**（文件 → 被导入文件）
需要解析每个源文件的 `import` 语句，属于编译器前端的职责：

- anx **不解析源文件**，避免重复实现前端；`anx.load()` 只产出项目与清单诊断（§7.4）。
  因此 A2 不要求 anx 报告 `AX009/AX010`，只要求它在 CLI 层**原样转发**编译器的诊断与退出码。
- 导入图由编译器在有需要时提供（`--packages` 模式下已有模块级信息），
  或由编辑器在分析会话中维护。
- anx 为编辑器提供**纯函数** `Project.resolve_import(importer, paths)`：输入是已经解析出的
  路径段，输出带失败原因的 `ImportResolution`（§3.3）。它不需要导入图，也不做 I/O。
- 本阶段不提供 `importers_of`（反向导入图）：它需要全量导入分析，与本节的延期决定冲突，
  因此不出现在 A0 的接口里。

## 6. 构建产物与缓存

### 6.1 `build/pkg.json`（契约 v2）

`--packages` 的 JSON 从**扁平映射**升级为**单一 `packages` 表 + 顶层 `root`**。
不再用「`roots` / `dependencies` / `entries` 三张并行表」：按包属性已经有三项，
并行表容易出现「某个包只出现在其中一张」的不一致，合表后每包一条、自洽。

现状（v1）：

```json
{
  "app": "/abs/app/src",
  "std": "/abs/yian/lib/src"
}
```

目标（v2）：

```json
{
  "format": 2,
  "root": "app",
  "packages": {
    "app": {
      "sourceRoot": "/abs/app/src",
      "kind": "hybrid",
      "entry": "/abs/app/src/main.an",
      "dependencies": ["mathlib"]
    },
    "mathlib": {
      "sourceRoot": "/abs/lib/src",
      "kind": "lib",
      "dependencies": []
    },
    "std": {
      "sourceRoot": "/abs/yian/lib/src",
      "kind": "lib",
      "dependencies": []
    }
  }
}
```

语义：

- `root`：本次构建 / 检查的**根包**规范名。程序入口取 `packages[root].entry`；
  类型检查的根集合也以它为界（A3）。standalone 模式没有这个字段，此时**根包 = 本次传入的、
  不属于标准库的文件**（用 `source_provenance.build_source_trust` 判定）；`std` 与其它依赖
  一样按需检查。
- `packages`：规范名 → 该包的全部信息，一条里自洽：
  - `sourceRoot`：源码根；`std` 必须存在，编译器也用它做可信源判定。
  - `kind`：`"bin"` / `"lib"` / `"hybrid"`（§3.2）。
  - `entry`：入口文件绝对路径；`"lib"` 没有这个字段，`"bin"` / `"hybrid"` 必须有。
  - `dependencies`：**直接**依赖的规范名；缺省视为空。
- 包 `P` 的可见集合 = `{P} ∪ packages[P].dependencies ∪ {"std"}`。
- 导入方包名由「文件落在哪个 `sourceRoot` 之下」确定；命中多个时取最长前缀。
- 任何包的 `entry` 都不可被导入（§5.1，`AX014`）。
- `format` 不是已知版本时，编译器明确报错，不回退猜测。

改动范围（必须在一次改动内完成，否则命令行与编译器会短时不一致）：

- `compiler/main.py`：解析 v2。
- `compiler/analysis/passes/global_resolve.py`：按可见集合校验 import 第一段，
  并按 `entry` 集合拒绝入口导入。
- `compiler/analysis/passes/type_check.py`：程序入口取 `packages[root].entry`，不再扫描所有
  unit 找 `main`（G22）；检查根按 A3 的规则。
- `anx/main.py`：生成 v2（含 `root`）。
- `docs/compile_script.md` 2.6 节：同步契约示例与语义说明。

不保留 v1 兼容读取：`--packages` 只由 anx 生成，仓库内没有被跟踪的 `pkg.json`
（根 `.gitignore` 的 `build/` 覆盖了编译器产物与 package fixture 的 `build/`）。

### 6.2 失效与增量

本阶段只提供失效所需的**结构**，不实现缓存：

- 编辑器按 `SourceFile.package` 分组，包内文件变化只影响该包及其导入方；
- `dependencies` 提供包级反向遍历的依据；
- `build/pkg.json` 的内容由 `Project` 纯函数式生成，可比较新旧内容决定是否重写。
  **A3 已实现**：内容不变时不重写（`anx.main._write_package_map`）。

### 6.3 未来的按包产物（研究结论）

本阶段不实现按包编译。这里先把结论固定下来，避免将来返工。调研范围限定为与 YIAN 同类的
编译型语言（编译到原生、静态类型、泛型单态化、trait/接口、显式内存管理）：
Rust、Swift、Zig、C++20、D、Nim；带 GC 的 OCaml / GHC / Go 只作对照。

三条路线：

| 路线 | 代表 | 包间交接物 |
| --- | --- | --- |
| 签名派 | C++ 头文件、D `.di` | 接口就是源码，泛型在依赖方的 TU 里重新实例化 |
| 元数据派 | Rust `rlib`/`rmeta`、Swift `.swiftmodule`/`.swiftinterface` | 签名 + 部分函数体/中间表示 |
| 无产物派 | Zig | 不产出包间接口文件，`@import` 读源码；复用靠内容寻址缓存 |

对 YIAN 的四条前置约束：

1. **产物必须含泛型定义与 trait impl**，不能是 `.d.ts` 式的纯声明——这类语言的单态化发生在
   依赖方。反例：C++ 的 Reduced BMI 会剔除「从接口 purview 不可达」的实体，导致依赖方
   `use_g<int>()` 无法实例化而报错。
2. **泛型实例要内容寻址命名**。Nim 的实例名是
   `MD5(泛型身份 + 每个具体类型实参的 typeKey)`，于是同一实例在不同模块得到相同的
   `<ident>.<disamb>`，可以确定性去重。YIAN 现在是 `TypeSpace` 的
   `instance_cache: (type_id, generic_args)`，而 `type_id` 只在单次编译内有意义——
   跨包必须换成内容键。
3. **失效要分两层**。Nim 每模块两个哈希：**iface cookie**（只覆盖导入方可见的签名，
   函数体排除）与 **impl cookie**（覆盖实现）；只有当依赖方在语义阶段**消费了对方的函数体**
   （宏展开、泛型实例化、编译期调用）时，才通过 **NeedsImpl 边**升级为 impl 依赖。
   GHC 用声明级指纹做同一件事。YIAN 的 trait impl 查找是全局的，依赖里的 impl 被下游
   用到即属于 impl 依赖，不能只看签名。
4. **产物键必须含编译配置**。`--raw-pointers` 改变指针布局（fat 40B/32B/24B vs
   raw 8B/16B），胖指针表示本身也在迭代；产物需携带**编译器构建 ID + 布局模式**，
   不匹配即失效。Rust 的 SVH 与 Swift 的编译器版本约束是同类做法。

一条范围结论：**YIAN / anx 属于「永远从源码一起构建」这一档**（路径依赖、无版本求解、
`anx test` 跑源码），因此**不需要**可分发的稳定接口格式——那是 Swift 库演进
（`-enable-library-evolution` + `.swiftinterface`）和 C++ BMI 分发才要付的代价；
需要的是**本机内容寻址缓存 + 包边界**，即 Zig / Go 的路线。

归属：proposal 实施顺序「补齐自举所需基础设施」之前的前置研究，本设计只登记约束。

主要依据：[Rust 编译器开发指南 · Libraries and metadata](https://rustc-dev-guide.rust-lang.org/backend/libs-and-metadata.html)、
[Cargo Build Cache](https://doc.rust-lang.org/cargo/reference/build-cache.html)、
[Clang Standard C++ Modules](https://clang.llvm.org/docs/StandardCPlusPlusModules.html)、
[Swift Library Evolution](https://www.swift.org/blog/library-evolution/)、
[Nim Incremental Compilation](https://nim-lang.github.io/Nim/ic.html)、
[Zig Build System](https://ziglang.org/learn/build-system/)。

## 7. 诊断模型

### 7.1 结构

```python
@dataclass(frozen=True)
class Diagnostic:
    code: str                        # 稳定错误码
    message: str                     # 面向用户的说明
    path: Path | None                # 相关文件
    span: tuple[int, int] | None     # 字节偏移区间（清单文件可得）
    hint: str | None                 # 可选修复建议
```

- CLI 层把 `Diagnostic` 渲染为 `error[AX003]: ...` 文本并退出码 1。
- 编辑器直接消费对象，不需要解析文本。
- 多个诊断一次性返回，不在第一个错误处中断（清单/依赖校验场景）。

### 7.2 错误码

| 码 | 含义 | 触发点 |
| --- | --- | --- |
| `AX001` | 找不到项目根（向上无 `package.anx`） | `discover` / CLI |
| `AX002` | 清单缺失、解析失败，或 `name` / `kind` 非法 | 清单读取 |
| `AX003` | 依赖路径不存在 | 依赖遍历 |
| `AX004` | 依赖目录没有清单 | 依赖遍历 |
| `AX005` | 依赖键与被依赖包清单 `name` 不一致 | 依赖校验（§3.5） |
| `AX006` | 依赖环 | 环检测 |
| `AX007` | 同名包指向不同路径 | 依赖图构建 |
| `AX008` | 入口或目录结构与 `kind` 不符（缺 `src/`；`bin` / `hybrid` 的入口不存在、不在源码根内或缺失；`lib` 声明了 `entry` 或含 `src/main.an`） | 源码收集（§3.2） |
| `AX009` | 导入了本包未显式声明的包（在 `roots` 中但不在可见集合） | 编译器 / 编辑器，经 `resolve_import`（§4.3、§7.4） |
| `AX010` | 导入未解析到 `.an` 源文件（剩余段为空、文件不存在） | 编译器 / 编辑器，经 `resolve_import`（§5.1、§7.4） |
| `AX011` | 包名使用保留名 `std` | 清单校验（§3.2） |
| `AX012` | 未知包名：import 第一段不在 `roots` 中 | 编译器 / 编辑器，经 `resolve_import`（§5.1、§7.4） |
| `AX013` | 任意两个包的源码根互相嵌套 | 源码收集（§3.6） |
| `AX014` | 导入了某个包的入口模块（入口是程序根，不可导入） | 编译器 / 编辑器，经 `resolve_import`（§5.1、§7.4） |
| `AX015` | 依赖了 `kind = "bin"` 的包（bin 没有库接口） | 依赖校验（§3.2、§4.1） |

### 7.3 可见性错误的边界

符号级可见性（`pub`）属于编译器的符号层，anx 不实现。
anx 的职责是「不丢失地转发」：

- `build/check` 保留编译器的退出码与诊断输出；
- 编辑器路径由分析会话直接使用编译器诊断。

但需要修正编译器的诊断文本：目前非 `pub` 符号报
`Symbol '<name>' is not found in the imported unit`，把「未公开」说成了「不存在」。
建议编译器区分 `not found` 与 `not public`，并在后者给出「该符号未声明为 `pub`」的提示。
这属于编译器改动，登记为实施项 A1 的跨组件前置项。

### 7.4 诊断责任划分

| 层 | 产生的诊断 | 说明 |
| --- | --- | --- |
| `anx.load()` | `AX001`–`AX008`、`AX011`、`AX013`、`AX015` | 只覆盖项目与清单：发现、清单、依赖路径、环、同名包、目录结构、保留名、源码根嵌套、bin 作依赖 |
| 编译器（源码中的 `import`） | `AX009`、`AX010`、`AX012`、`AX014` | anx 不解析源文件 import（§5.3），只在 CLI 层**原样转发**编译器的诊断与退出码 |
| 编辑器分析会话 | 同上，经 `Project.resolve_import()` | 编辑器把已经解析出的 `paths` 传入，按 `ImportFailure` 生成同样的诊断 |

推论：

- `AX009/AX010/AX012/AX014` **不出现在** `anx.load()` 的产出里，也不作为 anx 自身测试的验收对象；
  它们由 A2 的编译器改动实现，anx 侧只验证「不丢失地转发」。
- 三层共用 §5.1 与 §4.3 的同一套规则；`Project.resolve_import` 是它的纯函数实现，
  编译器的 Package 模式是另一份实现，两者必须由对照 fixture 固定（§5.2）。
- `load()` 不产生源码级诊断，因此不需要读 `.an` 文件：加载期保持廉价，编辑器在文件变化时
  只需重跑 `resolve_import` 与编译器分析，而不必重新 `load`。

## 8. CLI 设计

### 8.1 命令面

| 命令 | 说明 | 现状（A3 后） |
| --- | --- | --- |
| `anx new <name> [--kind bin\|lib\|hybrid] [--lib]` | 创建项目/库 | 已实现 |
| `anx build [project] [-O n] [--raw-pointers] [--release]` | 构建可执行文件到 `build/app`（仅 `bin`/`hybrid`） | 已实现 |
| `anx run [project] [-O n] [--raw-pointers] [--release] [-- args...]` | 构建并运行，转发参数与标准输入，传播退出码（仅 `bin`/`hybrid`） | 已实现 |
| `anx check [project] [-O n] [--raw-pointers]` | 只做分析（`-t none`），`lib` 根也可用 | 已实现 |
| `anx test [project]` | 运行**项目**测试（`tests/`，D6、§8.5） | 已实现 |
| `anx graph [project] [--json]` | 输出依赖图、文件索引与诊断（编辑器/调试用）；有诊断时打印载荷并退出 1 | 已实现 |

行为随根包的 `kind` 变化（§3.2）：

| 命令 | `kind = "bin"` | `kind = "lib"` | `kind = "hybrid"` |
| --- | --- | --- | --- |
| `build` | 产出 `build/app` | **不支持**：本阶段没有库的编译产物（§2.5、§6.3），报告原因并退出 1 | 同 `bin` |
| `run` | 构建后执行，转发参数与 stdin | **不支持**（同上） | 同 `bin` |
| `check` | 分析根包的全部顶层定义 | 同左；泛型体留给实例化（A3、G18） | 同 `bin` |
| `test` | 运行 `tests/` | 运行 `tests/`（用例自身是可执行单元） | 运行 `tests/` |
| 作依赖 | **不允许**（`AX015`） | 允许 | 允许（可导入其非入口模块） |

### 8.2 构建流程

```
discover（从 [project] 向上找 package.anx，缺省目录是 cwd）
        → load() -> LoadResult
        → diagnostics 非空 → 报告并退出 1
        → 根包为 lib 且命令是 build/run → 报「不支持」并退出 1
        → 生成 pkg.json（内容不变则不重写，§6.2）
        → 调用 compiler.main --packages <pkg.json> -t <target> [用户选项] <所有源文件>
        → 转发编译器输出；失败时按 §8.3 退出 1
```

- `discover()` 让 `anx build` 在项目的任意子目录下都能工作（A3 实现）。
- 用户选项按白名单透传（`-O` / `--raw-pointers`），避免 anx 与编译器选项长期不同步。
- 编译器的诊断**原样转发**；`AX009/AX010/AX012/AX014` 属于编译器产出（§7.4），
  anx 不重新解释。退出码则按 §8.3 归一为 anx 自己的码——编译器的 255（诊断）不直接透出。
- 成功路径也会转发编译器的两个流，否则 `warning:`（例如 G13 的遮蔽提示）会被吞掉。

### 8.3 退出码

`build` / `check` 使用 anx 自己的码：

| 码 | 含义 |
| --- | --- |
| `0` | 成功 |
| `1` | 用户错误（清单/依赖诊断、编译失败、`lib` 的 `build`/`run` 不被支持） |
| `2` | anx 内部错误（不应出现；保留用于区分） |

`run` 分两段，**不能在整条路径上混用同一套含义**：

- **子进程成功启动之前**（清单诊断、编译失败）：按上表返回 `1` / `2`。
- **子进程成功启动之后**：直接返回子进程的退出码，**原样传播、不做任何映射**。
  因此程序退出 `2` 不会被当作 anx 内部错误，也不会与 anx 的 `1` 混淆。

`anx test`：`0` 表示全部通过，`1` 表示有失败或无法运行。

其他：

- `check`/`build` 成功时不吞掉编译器的警告输出（当前只在失败时转发）。

### 8.4 安装与迭代

两个工具以 console scripts 形式交付，已在 `pyproject.toml` 声明：

```toml
[project.scripts]
yianc = "compiler.main:main"
anx = "anx.main:main"
```

本地开发用 **editable 安装**（默认），只想要一份拷贝时用 `--regular`：

```bash
scripts/install.sh             # 等价于 python3 -m pip install -e . --no-deps
scripts/install.sh --regular   # 非 editable：pip install . --no-deps
scripts/install.sh --with-deps # 让 pip 解析依赖，而不是依赖已有环境
scripts/uninstall.sh           # 卸载 yian 发行包
```

脚本会预检 Python >= 3.11 与 llvmlite，并在已安装 setuptools >= 68 时加上
`--no-build-isolation`，使离线环境也能完成安装；安装后校验两个入口是否在 `PATH` 上。

```bash
yianc lib path/to/main.an -o build/app
anx build
```

- editable 安装让两个命令直接指向检出，编辑 `compiler/`、`anx/`（**含新增模块**）后无需重装；
  只有改动 `[project.scripts]`、依赖列表或新增顶层包时才需要重装。
- `compiler/` 是 PEP 420 命名空间包（顶层与部分子目录没有 `__init__.py`），
  `pyproject.toml` 用 `[tool.setuptools.packages.find] namespaces = true` 适配。
- 标准库根由 `compiler/analysis/source_provenance.py` 的 `resolve_stdlib_root()`
  解析，优先级为 **`--compiler-root`** → **`YIAN_LIB`**（直接给 `lib/src`）→
  **`YIAN_ROOT`**（给检出根）→ `default_stdlib_root()`（本模块所在检出，editable 用）。
  `anx` 用同一个函数，两边永远看到同一个根。
- `lib/` 不在 wheel 里（`packages.find` 只收 `compiler*` / `anx*`）：把整个 `lib`
  装成顶层命名空间包会污染 site-packages，所以采用**配置**而不是打包。非 editable
  安装因此必须设置 `YIAN_LIB` 或 `YIAN_ROOT`，否则两个命令都会在启动时给出明确错误
  （含它实际查找的路径）。根目录**从不**从输入路径推断——一个叫 `lib` 的目录或一份
  自称 `name = "std"` 的清单都不该换来标准库权限（`source_provenance` 的设计前提）。
- anx 通过 `anx.main.compiler_command()` 解析编译器入口，优先级为
  `YIANC` 环境变量 → `PATH` 上的 `yianc` → `python3 -m compiler.main` 回退；
  这样用户脚本、CI 与 anx 共用同一个入口。回退只在源码检出下才补 `PYTHONPATH`。
- `anx` 的编译子进程以**项目根**为工作目录，编译器的 `build/` 产物落在项目里，
  而不是安装目录里。

### 8.5 项目测试（`anx test`）

`anx test [project]` 运行**项目自身**的测试，不再运行 anx 的集成套件。

- 测试源约定放在 `<项目根>/tests/`（§3.1），递归发现 `*.an`。
- 期望文件沿用仓库现有 runner 的约定，使项目与仓库共用同一套心智模型：
  - `<name>.ans` 给出期望 stdout；若首行为 `Exit code <N>`，则指定期望退出码；
  - `<name>.err.an` 表示期望编译失败，其 `.ans` 给出必须出现的诊断子串；
  - `tests/input/<name>.args` 与 `tests/input/<name>.stdin` 提供进程参数与标准输入。
- 发现与比对的实现只保留一份：若项目测试逻辑足够小，抽为可复用的共享模块，
  而不是复制 `scripts/run_tests.py`（该脚本还带有 fat/raw 双模式、编译变体等仓库专有机制）。
- anx 自身的集成套件不再有独立目录与独立 runner：并入 `tests/`，由
  `python3 scripts/run_tests.py --suite package` 调用（§10-A0.5）。

**A3 落地后的实际布局**（`anx/project_tests.py`）：

| 用途 | 路径 |
| --- | --- |
| 测试源 | `<项目根>/tests/**.an` |
| 期望 stdout（首行可为 `Exit code <N>`） | `<项目根>/tests/output/<name>.an.ans` |
| 期望编译失败 | `<项目根>/tests/**/<name>.err.an` + `tests/output/**/<name>.an.ans`（诊断子串） |
| 进程参数 / 标准输入 | `<项目根>/tests/input/<name>.args` / `.stdin` |

- 期望文件放在 `tests/output/`（而非与源并排），是因为要照搬仓库「源在 `tests/<suite>/`、
  期望在 `tests/output/<suite>/`」的形状。
- **测试以 package 模式编译**：每个用例额外注册一个**合成测试包**
  （`packages["__anx_test"]`），它的 `sourceRoot` 是 `<项目根>/tests`、`entry` 是当前用例文件、
  `dependencies` 是**根包 + 根包的直接依赖**。于是：
  - 用例可以像项目代码一样导入项目（`from <包名>.<模块> import ...`），根包自己的 `main`
    只是一个普通函数（入口是当前用例），不会撞 `Multiple 'main' functions found`（G22）；
  - 可见性规则（§4.3）对测试同样成立——**传递依赖不可见**：`mathlib` 的依赖 `inner`
    在用例里导入报 `AX009`，入口模块报 `AX014`；这两条已由 `project_tests` fixture 固定；
  - 检查根落在测试包上，因此只有当前用例会被全量检查，项目代码按需检查（§10-A3 的口径）。
  合成包名与项目里已有的包冲突时直接报错，不静默遮蔽。
- 与 `scripts/run_tests.py` 的关系：仓库 runner 的 `.ans` / `.err.an` / `Exit code N`
  **约定**一致，但实现没有合并——`scripts/` 是开发期工具，`anx` 是交付物，
  让安装后的 CLI 依赖 `scripts/`（或反过来）都会引入错误的耦合。仓库 runner 另外还有
  fat/raw 双模式与编译变体等专有机制。这是对「只保留一份实现」的**有意偏离**，
  双方由同一套约定与同一批 fixture 固定。

实施归属：A3（已完成）。

## 9. 与语言服务器的共享接口

`docs/plan/ide-support-plan.md` 的 P3 依赖同一份项目模型。anx 需要提供：

| 能力 | 接口 |
| --- | --- |
| 项目发现 | `discover(start: Path) -> Path \| None` |
| 项目加载 | `load(root: Path, *, std_root: Path \| None = None) -> LoadResult` |
| 文件归属 | `Project.file_of` / `Project.package_of` |
| 导入解析 | `Project.resolve_import(importer, paths) -> ImportResolution`（带失败原因，不返回 `None`） |
| 依赖关系 | `Project.dependencies`（只读映射） |
| 诊断 | `LoadResult.diagnostics`（结构化、排序稳定）；`Project` 本身不携带诊断 |
| 结构化输出 | `Project.describe(diagnostics) -> dict`（`anx graph --json` 的载荷，LSP 可直接复用） |

约束：

- 模型模块 `anx/project.py` 只依赖标准库，不 import `compiler`、不 import `subprocess`，
  可在编辑器进程内直接加载。`anx/main.py`（CLI）额外 import
  `compiler.analysis.source_provenance.resolve_stdlib_root`——那是纯路径解析，
  只依赖 `os`/`pathlib`，不会把编译器拖进编辑器进程。
- 加载**不执行**编译器、不写 `build/`；写产物只发生在 CLI 的 `build/run/check` 里。
- 与 `ide-support-plan.md` P0 的「坐标约定」无关：anx 只给文件与路径，位置换算由分析会话负责。

## 10. 实施阶段与验收标准

> 验收用例的落地位置暂缓安排：IDE / 语言服务器计划后续会调整，届时再统一决定。
> 下列「用例」仅指测试场景，不预设其存放位置。
>
> **实现期间的测试口径（本轮约定）**：A0–A5 的**中间提交允许留有失败用例**，包括因规则变更
> 而第一次暴露的既有错误（例如打开「全部顶层定义」检查后 `lib/` 里的问题）。因此：
>
> - 不要求每个提交把套件修绿；**更不得为了让用例变绿而放松 §3–§7 的规则**；
> - 每处红灯都要有归属：要么在本阶段修掉，要么进入**迁移清单**（现象 + 涉及文件 + 归属阶段）；
> - **A5 结束时必须清零**：`basic`、`safety`、`package` 三套全绿，是本轮实现完成的判据之一。
> - A0 与 A0.5 例外：它们是「行为不变」的重构与测试搬迁，验收仍要求当时的套件保持全绿。

### S1 前置 spike：检查根放开的红灯普查

**状态：已完成**（结论见下；双集合与过程枚举接口保留在 A3 的实现里，测量用的
`YIAN_CHECK_ALL` / `YIAN_CHECK_ALL_STDLIB` 开关在 A3 删除——全量检查根包已是默认行为）。

**目标**：把「全量检查根包顶层定义」的影响面从**未知**变成**数字**。这是整个计划里唯一可能
改变可行性判断的未知量（§10 口径允许红灯，但需要知道规模）。

**做法**（临时代码，只为测量）：

- `TypeCtx` 增加 procedure 枚举接口（A2/A3 最终也要用，可保留）。
- `TypeChecker` 拆出**生成集合**与**检查集合**：生成集合仍是现有的 main 可达闭包，
  代码生成路径一行不改；检查集合按 A3 的规则播种（根包全部顶层定义、排除泛型）。
- 用环境变量 `YIAN_CHECK_ALL=1` 打开（避免改 runner），跑
  `YIAN_CHECK_ALL=1 python3 scripts/run_tests.py -q` 与 safety 套件。

**判据**：红灯数量决定 A3 的排期——几十个量级按原顺序推进；几百个则说明 `lib/` 与测试里存在
系统性语义问题，需要先重新评估。

**测量结果（已执行）**：

| 范围 | 红灯 |
| --- | --- |
| 根包（standalone 的测试文件） | `basic` **722/722**、`safety` **156/156**、package **19/19**，**0 红** |
| stdlib 作为根包（把 `lib/` 也纳入播种） | **共 3 个真实错误，已全部修复**（见下）。修完后 stdlib 全量检查通过 |

stdlib 的 3 个既有错误（都因「从未被调用」而长期隐藏，正常调用也会报错）：

| 位置 | 现象 | 修法 |
| --- | --- | --- |
| `lib/src/core/io.an:292` `readline` | 声明 `-> String`，但 `Stdin.read_line().unwrap()` 给出 `String*` | 改为 `.unwrap().clone()` |
| `lib/src/core/str.an:332` `CharIndicesIter::next` | 两条语句写成 `u64 index = ...`（无 `let`、无分号），**不是合法语法** | 改为 `let index: u64 = ...;` |
| `lib/src/core/str.an:111` `starts_with_char` | `first_char.unwrap() == prefix` 比较的是 `char*` 与 `char` | 改为 `*first_char.unwrap() == prefix` |

三处均已用运行期用例验证（`readline` 输出、`starts_with_char` 真假两例、`char_indices` 迭代计数）。

播种规模：以 `tests/basic/call/inherent_method.an` 为例，纳入 stdlib 后额外播种 **158** 个定义、
跳过 **125** 个泛型；仅根包范围时该用例播种 **0** 个（用例本身几乎全可达）。

**结论**：

1. **用户项目范围（A3 的目标）0 红灯** —— 全量检查根包定义可以直接落地，不需要大规模迁移。
   注意 corpus 的覆盖偏弱：standalone 用例都很小、代码基本可达，所以这个 0 主要说明
   「没有既有红灯」，不完全等于「全量检查已被充分验证」。
2. **stdlib 自身曾有 3 个既有错误，现已全部修复**，修完 stdlib 全量检查通过；这印证了
   G21 那类「不可达代码从不被检查」的漏洞确实会藏真 bug。A3 的迁移清单因此清零。
3. 双集合（生成集合 / 检查集合）已验证**不影响代码生成**：开关打开前后 `-t ll` 输出逐字相同。
4. **普查方法本身有价值**：编译器在首个错误处终止，所以「修一个、看下一个」是当前唯一的
   枚举手段；将来若常见，可以考虑让类型检查收集多个错误再统一报告。

**性质**：不依赖 A0/A1/A2（不需要项目模型、v2 契约或 `kind`），因此放在最前面；它不是交付物，
测量完把结论并入 A3 的迁移清单。双集合与枚举接口本身是 A3 的实现，可以保留并演进。

### A0 抽出纯项目模型（不改行为）

**状态：已完成**（commit `cf4a74e`）。

- 新增 `anx/project.py`：`PackageKind`、`Dependency/Package/SourceFile/Project`、
  `LoadResult`、`Diagnostic`、`ImportResolution/ImportFailure` 与 `load`。
- 映射字段一律以只读视图暴露（`MappingProxyType` 或排序后的 tuple），不把可变 `dict`
  交给调用方；接口中**不出现** `importers_of`，`Project` 也**不携带诊断**。
- `resolver.py` 的 `resolve()` 改为在 `load()` 之上的兼容包装，`main.py` 不改外部行为。
- **验收**：`anx.main test` 19/19 不变；`import anx.project` 不产生 I/O；`pkg.json` 仍是 v1
  且内容与改动前逐字一致（v2 升级在 A2 一次完成）；构造出的 `Project` 无法被调用方修改。

**实现结果**：`anx test` 19/19；`basic`、`deps/app`、`deps/deep/app`、`deps/multi/app`、`same_pkg`、
`std_usage` 六个 fixture 的 `build/pkg.json` **与** `build/app` 二进制均与改动前逐字节相同；
`import anx.project` 只创建 `.pyc`、不打开任何项目文件；映射赋值抛 `TypeError`；pyright 0 错误。
唯一的接口偏差：`resolve()` 由 `resolve(root)` 变为 `resolve(root, std_lib)`（`main.py` 传入
`lib/src`，与 §5 的「源码根一律 `<包根>/src`」一致）。

### A0.5 测试体系合并：package 模式并入 `tests/`

**状态：已完成**（commit `d8e0045`）。

**现状（G20）**：三套互不相通的入口——`scripts/run_tests.py`（`tests/basic`，standalone，
fat/raw 双模式展开）、`scripts/run_safety_tests.py`（`tests/safety`）、`anx/tests/run.py`
（`anx/tests`，package 模式，registry 是 `rglob("package.anx")`）。`anx test` 调用的是第三套
（G9），而 `tests/` 下没有任何 package 模式覆盖。

**目标**：一套 runner、一个 `tests/` 数据目录；standalone 与 package 是同一体系里的两类用例。
fat/raw 矩阵与 D6 的 `anx test` 语义留到 A3。

**范围界定**：本阶段只做**数据搬迁 + runner 支持 + 命令重定向**，且只用**今天已有的 anx 能力**。
下面三项依赖 A3，不在这里做：

| 本阶段不做 | 原因 |
| --- | --- |
| package suite 的 fat/raw 双模式展开 | anx 现在没有 `--raw-pointers` 透传（`cmd_build` / `cmd_check` 的参数是硬编码的），A3 才加 |
| 选项透传（`-O`、`--raw-pointers`、程序参数与 stdin） | 同上，属于 A3 |
| `anx test` 改成「跑项目测试」（D6） | D6 属于 A3；本阶段只**重定向**，不改语义 |

**改动**：

- 新增 suite `tests/package/`，fixture 保持 package 项目的形状（`package.anx` + `src/` + 依赖目录），
  把 `anx/tests/{basic,deps,errors,migrated,same_pkg,std_usage}` 整体迁入（迁移基线 19 个）。
- **期望文件**：沿用 `tests/` 现有约定，并为**目录型 fixture** 补一条明确规则。现状是三条——
  `<name>.an` → `<name>.an.ans`、`<name>.err.an` → `<name>.an.ans`、目录 fixture → `<dir>.ans`
  （`_find_ans` 对不含 `.an` 后缀的名字只找 `<dir>.ans`）。package 的错误用例需要第四档，
  因此扩展为**依次尝试**：

  | fixture | 期望文件 |
  | --- | --- |
  | `<name>.an` | `tests/output/package/<name>.an.ans` |
  | `<name>.err.an` | `tests/output/package/<name>.an.ans` |
  | 目录 fixture，期望失败 | `tests/output/package/<dir>.err.ans`（内容是必须出现的诊断子串） |
  | 目录 fixture，期望成功 | `tests/output/package/<dir>.ans`（首行 `Exit code <N>` 或纯 stdout） |

  `tests/package/**` 下的 `expected.stdout` / `expected.stderr` / `expected.exit` 全部退役。
- `scripts/run_tests.py` 增加 `--suite {basic,safety,package}` 与 `--all`：目前 `--suite` 只是
  函数参数，命令行并不认（实测 `--suite safety` 报 `unrecognized arguments`）；
  `run_safety_tests.py` 收敛为它的薄包装或直接删除。
- `anx test` 改为调用统一 runner（`scripts/run_tests.py --suite package`），**语义不变**
  （仍是跑 anx 自身套件）；改完再删除 `anx/tests/run.py` 与 `anx/tests/`，命令全程可用。
- 测试**代码**留在 `scripts/`，`tests/` 只放数据——这与仓库「`tests/` 只放数据」的既有决定
  一致，也把 D7 里 anx / IDE 两侧的测试位置冲突缩小到只剩 IDE 一侧。

**验收**：

- `python3 scripts/run_tests.py --suite package` 跑完迁入的用例并全绿（基线 19 个，仅 fat 模式）；
- `python3 scripts/run_tests.py --all` 一次跑完 basic + safety + package；
- `tests/package/**` 下不再出现 `expected.stdout/stderr/exit`；`anx/tests/` 与
  `anx/tests/run.py` 不存在；
- `anx test` 仍能跑通，行为与改动前一致（只是换了实现位置）；
- package suite 覆盖：单包构建/运行、路径依赖、传递依赖、包自导入、`std` 使用，
  以及现有依赖与清单诊断（环、缺依赖、未知包、可见性）。

**实现结果**：19 个 fixture 迁入 `tests/package/`（含 7 个被依赖的包），期望文件落在
`tests/output/package/`（11 个：`basic.ans`、`same_pkg.ans`、`std_usage.ans`、`migrated/itype.ans`，
以及 `errors/{cycle,app,missing_dep,no_pub/app,unknown_pkg}` 与 `migrated/{iterr,pferr,pierr}`
的 `.err.ans`）；`anx/tests/` 整体删除（含 `run.py` 与 14 个 `expected.*`）。
`--suite package` 19/19（4.6s），`--all` = 722 + 156 + 19 = **897 全绿**，`--no-run` 19/19，
`-f "deps/"` 命中 3 个，`anx test` 19/19，`run_safety_tests.py` 156/156，pyright 0 错误。

**顺序**：留在 A0 之后、A1 之前，**不能整体挪到 A3 之后**——A1/A2 新增的诊断 fixture
（`name_mismatch`、`reserved_name`、`duplicate_name`、`lib_with_entry`、`bad_entry`、
`bin_as_dependency`、`nested_source_roots`）全部是 package 模式，必须有 `tests/package/`
与 runner 支持才能落盘，否则它们会先写进 `anx/tests` 再搬一次。
A0 验收里的「`anx.main test` 19/19 不变」由本项的「`--suite package` 全绿」接替。

### A1 清单与依赖诊断

**状态：已完成**（commit `79c3092`）。

- 实现 `AX002/AX003/AX004/AX005/AX007/AX008/AX011/AX013/AX015`，一次性返回全部诊断。
- 落实 `kind` 与 `entry`：清单解析与入口校验（`AX002`/`AX008`）；`bin` / `hybrid` 必须能解析
  出入口，`lib` 不得有入口；`bin` 作依赖报 `AX015`（在 §4.1 的依赖校验里）。
- `anx new` 支持三种类型：`--kind bin|lib|hybrid`（`--lib` 保留为 `--kind lib` 的简写），
  模板写入 `kind`；`lib/package.anx` 补 `kind = "lib"`。
- **既有 fixture 的清单迁移**（`kind` 默认 `bin` 之后，实测有 9 个不再成立）：
  - 7 个没有 `main` 的库包补 `kind = "lib"`：`deps/lib`、`deps/deep/libb`、`deps/deep/libc`、
    `deps/multi/adder`、`deps/multi/multiplier`、`errors/cycle/lib`、`errors/no_pub/lib`；
  - 2 个把 `main` 写在非常规文件里的包补 `entry`：`migrated/pferr`（`src/field.an`）、
    `migrated/pierr`（`src/import.an`）——它们现在靠「扫描所有 unit 找 `main`」才能工作，
    改成「入口取 `packages[root].entry`」后会直接 `AX008`。
- 依赖键必须等于被依赖包清单 `name`；包名非法或为保留名 `std` 均报错；
  把 `errors/cycle` 的键从 `lib` 改为 `libb`，保留环检测语义。
- `LoadResult`：根包不可用时 `project is None`，否则 `project` 可用且诊断可非空；
  诊断排序固定为 `path` → `span` → `code`；「全部诊断」= 所有**可独立发现**的诊断。
- 修正 `errors/missing_dep` 预期，不再匹配 Python 异常文本。
- 跨组件：编译器区分「未公开」与「不存在」（§7.3）。
- **验收**：新增/更新 `missing_dep`、`name_mismatch`、`reserved_name`、`duplicate_name`、
  `lib_with_entry`、`bad_entry`（入口不存在或不在源码根内）、`bin_as_dependency`（`AX015`）、
  `nested_source_roots` 场景；`errors/cycle` 仍验证环检测；根清单损坏时 `load()` 返回
  `project is None` 且诊断非空；同一输入的诊断顺序稳定。

**实现结果**：

- 新增 `anx/diagnostics.py`：`Diagnostic`、`AX001`–`AX015` 常量、稳定排序
  （`path`（`None` 最前）→ `span` 起点 → `code` → `message`）与 `error[AXnnn]: ...` 渲染。
- `anx/manifest.py` 重写为 `read_manifest()`，不再抛出用户错误：`name`/`kind` 不可用时返回
  `None`（整棵子树跳过），`entry`/`version` 类型错误或单个依赖条目不合法时只丢弃对应部分。
- `anx/project.py` 的 `load()` 一次遍历收集全部**可独立发现**的诊断。根包不可用
  （`AX001`/`AX002`/`AX008`，以及根名 `AX011`）时 `project is None`；依赖子树出错只跳过该子树
  （`AX003`/`AX004`/`AX005`/`AX007`/`AX008`/`AX011`/`AX015`）。文件归属改为「最长源码根前缀」，
  任意嵌套源码根报 `AX013` 但不阻止加载。
- `AX005` 只报「键 ≠ 名字」，依赖仍按**规范名**入图（否则依赖自身的自导入会断）；
  `AX015` 与 `AX008` 的依赖子树整体跳过。
- `resolver.py` 的 `resolve()` 保留为兼容包装，诊断非空时抛 `ProjectError`；`main.py` 改走 `load()`。
- CLI：`build`/`run`/`check` 先加载，诊断非空时按 `error[AXnnn]` 打印并退出 1；
  `kind = "lib"` 的 `build`/`run` 报「本阶段无库产物」并退出 1；
  `anx new --kind bin|lib|hybrid`（`--lib` 保留为 `--kind lib` 的简写）。
- 编译器：`SymbolCtx.lookup_global()` 只查顶层作用域，使导入能区分「未公开」与「不存在」；
  `errors/no_pub/app`、`migrated/iterr`、`migrated/pierr` 的预期改为
  `Symbol '<x>' exists in the imported unit but is not declared 'pub'`。

**迁移清单（除计划内的 9 个之外，实测新增 2 处）**：

| 位置 | 现象 | 处理 |
| --- | --- | --- |
| `errors/cycle/app` | 根包 `appa` 是 `bin`，被 `libb` 依赖时报 `AX015`，依赖不入图，环也就检测不到 | 根包改 `kind = "hybrid"`：程序入口与「可被依赖」只有 `hybrid` 能同时表达，环检测因此恢复 |
| `errors/missing_dep` | 预期匹配 Python 的 `No such file or directory` | 改为 `error[AX003]` |

**新增 fixture（12 个目录）**：`name_mismatch`、`reserved_name`、`duplicate_name`、
`lib_with_entry`、`bad_entry`、`bin_as_dependency`、`nested_source_roots`，
以及同一代码路径上计划未列但必须覆盖的 `lib_with_main`（`lib` 含 `src/main.an`）、
`entry_outside_src`（入口在源码根之外）、`broken_manifest`（`AX002` 解析失败）、
`dep_without_manifest`（`AX004`）、`bad_kind`（`AX002` kind 取值非法）。

`load()` 级性质（根包不可用 → `project is None`、依赖出错 → `project` 仍可用、
嵌套源码根不阻止加载且文件归属唯一、映射只读、诊断顺序稳定）在开发中用一次性脚本核验，
按仓库「不新增测试脚本」的口径未留在仓库里；仓库套件只保留端到端 fixture。

### A2 导入范围、文件索引与 `--packages` v2

**状态：已完成**（commit `3ba54dc`）。

- 编译器按 `roots` 最长前缀计算**导入方所属的包**，并实现可见性规则
  （只允许自身、直接声明的依赖、`std`）与「入口不可导入」；`AX009/AX010/AX012/AX014`
  由编译器产出（G15、§7.4）。
- `Project.resolve_import` 按 §5.1 实现同一套规则，返回 `ImportResolution`
  （`UNKNOWN_PACKAGE` / `NOT_VISIBLE` / `NOT_A_MODULE` / `ENTRY_MODULE`）。
- anx 侧**不实现** `AX009/AX010/AX012/AX014`，只验证编译器诊断与退出码被原样转发（§7.4）。
- 建立 `files` 索引：按最长匹配源码根归属，收集时排除嵌套源码根（§3.6、G19）。
- 固定包名段优先于同名包内目录的语义，并在加载期给出提示（G13）。
- 依赖键与根包名冲突时报 `AX007`，不再落到误导性的 `Circular dependency: app → app`（G14）。
- 固定 Standalone 语义（相对导入方目录、不自动加载文件）并在 §5.2 写明与 Package 模式的差异（G16）。
- 一次改动内完成 §6.1 的契约 v2：改为**单一 `packages` 表 + 顶层 `root`**（每包含
  `sourceRoot` / `kind` / `entry` / `dependencies`），编译器读取 v2 并按依赖边校验 import
  第一段、按 `entry` 集合拒绝入口导入，`anx` 生成 v2，同步 `docs/compile_script.md` 2.6 节；
  不保留 v1 兼容读取。
- 清理 `compiler/main.py:__build_unit_names` 中按路径段 `lib` 生成 LLVM 单元名的 hack，
  改为基于文件→包归属的确定性命名（依赖本项建立的文件→包索引）。
- **验收**：`deps/deep` 保持通过；编译器侧「未声明传递依赖」（`app` 直接导入 `libc`）报
  `AX009`、未知包报 `AX012`、解析不到 `.an` 文件报 `AX010`、导入入口模块报 `AX014`，
  anx 原样转发；`resolve_import` 对四种失败各返回对应的 `ImportFailure`；
  嵌套源码根不产生重复的文件归属；「本包目录与依赖同名」（G13）给出明确提示；
  「依赖键 == 根包名」（G14）报 `AX007` 而非循环依赖；`docs/compile_script.md`
  的示例与实际输出一致。

**实现结果**：

- 新增 `compiler/analysis/package_map.py`：解析并校验 v2（`format` / `root` / `packages`），
  提供 `package_of()`（最长 `sourceRoot` 前缀）、`visible_from()`（自身 ∪ 直接依赖 ∪ `std`）、
  `entry_paths()`；`format` 不是 `2`、缺 `std`、`root` 不在表中等情况直接报错，不回退猜测。
- `GlobalResolve.__resolve_package_import()`：按 §5.1 的顺序产出 `AX012` → `AX009` →
  `AX010`（含 `n == 0` 的目录导入）→ `AX014`；Standalone 分支一字未改（报错文本仍是
  `Cannot resolve import path`），对照表见 §5.2。
- `TypeCheck.__find_main()`：Package 模式下入口恰为 `packages[root].entry`，依赖包的 `main`
  不再参与（G22）；`root` 缺省时退回「非标准库 unit 中唯一的 `main`」，并保留原有的
  「泛型 `main`」「多个 `main`」「返回值必须为 void」检查。根包 `kind = "lib"` 时明确报
  「没有程序入口」（库根分析在 A3）。
- `Project.resolve_import()` 以纯函数形式实现同一套规则，返回 `ImportResolution` 的四种失败。
- `Project.compiler_package_map()` 生成 v2，`anx/main.py` 直接落盘；A0 的兼容包装
  `anx/resolver.py` 随之删除。
- `__build_unit_names()` 不再按路径段 `lib` 猜名字：单元名 = `<包名>_<模块路径>`，包外的
  文件退回文件 stem（G23 ③ 的入口识别仍留给 A3）。
- G13 提示由编译器在 `stderr` 输出 `warning:`；同时把 `anx` 的成功路径也改为转发编译器
  两个流，否则提示会被吞掉（§8.3 记录的同一问题）。
- §5.2 补上两种模式的对照表。

**迁移清单（2 处）**：

| 位置 | 现象 | 处理 |
| --- | --- | --- |
| `migrated/icycle` | 该 fixture 让入口 `main.an` 参与循环导入（`a.an` 反过来导入 `cyctest.main`），A2 后入口不可导入 → `AX014` | 把环移到两个库模块之间（`a.an` ↔ `b.an`），入口只导入 `a`；用例意图（循环导入 + 泛型结构体）不变 |
| `errors/unknown_pkg` | 预期文本是 v1 的 `Unknown package '...'` | 改为 `error[AX012]` |

**新增 fixture（6 个）**：`errors/undeclared_dep`（`AX009`）、`errors/import_not_module`
（`AX010`，`n == 0`）、`errors/import_missing_module`（`AX010`）、`errors/import_entry_module`
（`AX014`）、`hybrid_dep`（hybrid 作依赖 + 依赖自带 `main`，G22）、`self_shadow_dir`（G13 语义，
运行期断言依赖包胜出）。

**验收**：`basic` 722 + `safety` 156 + `package` 37 全绿；`docs/compile_script.md` 2.6 与
实际输出一致；`hybrid_dep` 证明依赖包的 `main` 不再冲突；`self_shadow_dir` 证明包名段胜出
并打印提示。

### A3 构建/运行/检查流程

**状态：已完成**（commit `67ccf93`）。

- 选项透传、`run` 转发参数与 stdin、退出码按 §8.3 分两段处理、成功时保留编译器告警。
- 按 `kind` 分流：`lib` 的 `build`/`run` 报「不支持」；`bin` / `hybrid` 的入口缺失、不在
  源码根内，或 `lib` 声明了 `entry` / 含默认入口，均报 `AX008`。
- 跨组件：**程序入口改为 `packages[root].entry`**（G22）——`type_check.py:__find_main` 现在
  扫描所有 unit 找唯一 `main`，所以只要依赖了一个自带 `main` 的 `hybrid` 包就会误报
  `Multiple 'main' functions found`。改成按 `root` 定位入口后，依赖包的 `main` 只是一个
  没人调用的普通函数（按需检查，且不会被代码生成）。
- 跨组件：**检查根规则**（G18/G21）——编译器的类型检查不再以 `main` 为唯一根：
  - **实现形状（双集合，G23）**：保留现有 main 可达闭包作为**生成集合**，代码生成路径不改；
    另建**检查集合**（根包全部顶层定义、排除泛型），只做类型检查、**不进入 CFG/LLVM**。
    `DefPoint` 增加「已生成」归属或由 `TypeChecker` 维护两个映射；`main.py` 用生成集合喂 `__cfg`。
    不能简单地把全部定义塞进现有 `def_points`——那样 LLVM 会把死代码也编出来。
  - **前置 API（G23）**：`TypeCtx` 需要按 `unit_id` 枚举 procedure 的接口（`__procedures` 现为私有）。
  - **程序入口要传到 LLVM 层**：`llvm/module.py:137` 现在按**函数名** `main` → `__yian_main`，
    一旦依赖的 `hybrid` 包也有 `main` 并进入生成集合就会重名。改为由前端显式给出入口的
    `type_id`，不再按名字判断。
  - **standalone 模式的入口规则**：package 模式取 `packages[root].entry`；standalone 没有清单，
    因此**保留现有行为——按函数名寻找唯一的 `main`**（`tests/basic` 的每个用例都是
    `xxx.an` 里带 `fn main()`）。standalone 下没有「声明入口」，`AX014` 不适用。
  - **检查根** = 根包的全部顶层定义，含私有函数、`impl` 方法与 trait 的默认方法体。
    为此 `TypeChecker` 的种子从「`main`」改为「本次要检查的定义集合」；`global_resolve`
    已经把每个顶层定义注册进 `TypeCtx`，清单是现成的（§2.5）。
  - **泛型定义**只检查签名与其中引用的类型，函数体留给实例化。语言没有泛型参数约束
    （只有条件 `impl`），直接检查未实例化的体会在 `a + b` 这类表达式上误报；
    未被实例化的泛型体不检查、也不报错。
  - **代码生成根**仍是 `main` 可达（bin）；lib 本阶段不生成产物（§6.3）。
  - **范围**以根包为界：package 模式取 v2 的 `root` 字段（§6.1）；standalone 模式的根包 =
    本次传入的**非 stdlib** 文件（不属于 `default_stdlib_root()` 的那些）。因此
    `yianc lib tests/basic/x.an` 会**全量检查 `x.an` 自己**（G21 不再漏检），但不会顺带把
    整个 `lib/` 拉进全量检查——stdlib 在两种模式下的待遇一致。
  - **依赖包（含 `std`）只按需检查**——这是本阶段的决定：每个包在自己目录里负责自己的
    `check`，与 D6「项目自己的测试」同一套思路。注意这与 `cargo check`（检查整张依赖图）
    不同；改成全图检查是另一个（更慢的）决定，本阶段不做。
  - 这同时是 LSP 的前置：`ide-support-plan.md` §2.3 第 5 条要求分析「没有被 `main` 调用的
    辅助函数和库文件」。
- **迁移清单（S1 已普查，当前为空）**：按 S1 的测量，**用户项目范围 0 红灯**（basic 722 +
  safety 156 + package 19 全绿），stdlib 的 3 个既有错误也已修复，因此全量检查可以直接落地，
  不需要大规模迁移。按 §10 口径，若后续又出现红灯，可以留在中间提交里，但必须登记
  （现象 + 文件 + 归属阶段）并在 A5 前清零；**规则不因红灯而放松**。
- 相应地，本项验收只要求**新增行为成立**（不可达定义的错误被报出、未实例化泛型体不报错、
  库包可 `check`），不要求套件此刻全绿。
- 修正 `parse_cli` 的位置参数与选项顺序限制（G17），使「路径 … 选项 … 路径」也能解析。
- package suite 纳入 **fat/raw 双模式**展开（依赖本项的 `--raw-pointers` 透传）；A0.5 刻意没做。
- `anx test [project]` 改为运行**项目**测试（D6、§8.5）；anx 自身套件不再由 `anx test` 调用，
  而是直接 `python3 scripts/run_tests.py --suite package`（A0.5 只做了重定向，没改语义）。
- **验收**：`run` 场景覆盖参数、标准输入，以及程序退出码 `0/1/2` 的**原样传播**；
  `build -O3`、`build --raw-pointers` 可用；`anx check` 对 `kind = "lib"` 的包成功；
  依赖一个**自带 `main` 的 `hybrid` 包**可以正常构建（不再报 `Multiple 'main' functions found`，
  G22）；不可达函数体内的错误必须被报出（G21）；未被实例化的泛型体**不**报错；
  standalone 调用（`yianc lib <file>`）同样报出 `<file>` 内不可达定义的错误，但**不**把
  stdlib 全量拉进检查；**standalone 仍按函数名找到 `main`**（`tests/basic` 的用例不受影响）；
  依赖包里的不可达错误**不**由根项目的 `check` 报出（按需范围的定义）；
  `lib` 的 `build`/`run` 给出明确的不支持；`yianc` 的选项与位置参数可任意顺序混写；
  含 `tests/` 的项目可被发现并正确报告失败；`python3 scripts/run_tests.py --suite package`
  的用例要么通过、要么进入迁移清单（不强求此刻全绿，§10 口径）。

**实现结果**：

- **检查根（G18/G21）**：`TypeCheck` 保留两个集合——生成集合仍是 `main` 可达闭包，
  代码生成路径未改；检查集合在 `main` 之后按「根包的全部顶层定义、排除泛型」播种。
  根包范围：package 模式取 v2 的 `root`，standalone 取非 stdlib 的传入文件。S1 的环境变量
  `YIAN_CHECK_ALL` / `YIAN_CHECK_ALL_STDLIB` 删除，双集合成为默认行为。
- **入口（G22/G23③）**：程序入口取 `packages[root].entry`；入口的 `type_id` 由 `TypeCheck`
  显式传给 `LLTranslator` / `LLModule`，LLVM 层不再按函数名 `main` 判断，依赖包里同名的
  `main` 只是普通函数（`main.<type_id>`）。`lib` 根 + `-t exe` 明确报「没有程序入口」。
- **`parse_cli`（G17）**：改用 `parse_intermixed_args`，「路径 … 选项 … 路径」可混写。
- **anx CLI（G8/G11）**：`build`/`run`/`check`/`test` 接受 `-O` / `--release` /
  `--raw-pointers` 并按白名单透传；`run` 继承 stdio 并原样传播程序退出码；编译失败统一退出 1
  （不再透出编译器的 255）；`build/pkg.json` 内容不变则不重写；`discover()` 让命令在项目
  任意子目录下可用；找不到项目报 `AX001`。
- **`anx test`（G9/D6/§8.5）**：新增 `anx/project_tests.py`，按 §8.5 的布局发现并运行
  `<项目根>/tests/**.an`，支持期望 stdout、`Exit code <N>`、`err.an` 诊断子串、
  `input/*.args|.stdin`；anx 自身套件不再由 `anx test` 调用。A3 首版按 Standalone 模式编译，
  随后改为 **package 模式**（合成 `__anx_test` 包，见 §8.5）：用例因此能导入项目的包，
  同时可见性规则仍由编译器强制。`compiler_package_map()` 拆出 `package_specs()`
  以便调用方追加这个合成包；`Project` 模型本身不变。
- **package suite 纳入 fat/raw 双模式**：`anx` 侧用 `anx_args` 携带 `--raw-pointers`，
  每个 package fixture 展开成两条用例（A0.5 刻意没做）。
- 新增 `anx` fixture 驱动方式：`tests/input/package/<fixture>.command` 可把某个 fixture
  改为 `check` 或 `test` 驱动（`lib` 根只能 `check`，项目测试 fixture 走 `test`）。

**验收结果**：

| 验收点 | 证据 |
| --- | --- |
| 不可达函数体内的错误被报出 | `tests/basic/error/unreachable_type_error.err.an`、`errors/unreachable_error`（package） |
| 未实例化泛型体不报错 | `tests/basic/generics/uninstantiated_body.an` |
| 依赖包的不可达错误不出现在根项目 | `dep_unreachable`（根 `build` 通过），`anx check dep` 单独报出 |
| `lib` 根可 `check` | `lib_pkg`（clean，exit 0）、`errors/lib_check_error`（报错） |
| 依赖自带 `main` 的 `hybrid` 包可构建 | `hybrid_dep` |
| `run` 转发参数 / stdin / 退出码 | `project_tests` fixture 的 args/stdin/exit-code 用例；手工核对 `anx run -- x` 得到程序自身的 2 |
| `anx test` 发现项目测试并报告失败 | `project_tests`（exit 0）、`errors/project_test_failure`（`1 failed`） |
| `yianc` 选项与位置参数任意顺序 | 手工核对 `lib -t none <file>` 与 `lib <file> -t none -O3` |
| standalone 仍按函数名找 `main` | `tests/basic` 726 条全绿 |

**迁移清单（空）**：S1 已普查过检查根放开的红灯，本次实现后 `basic` 726 + `safety` 156 +
`package` 86 全绿，无需迁移项。

### A4 交付形态

**状态：已完成**（commit `e09c872`）。

已完成：

- `pyproject.toml` 声明 console scripts `yianc = compiler.main:main`、
  `anx = anx.main:main`；`pip install -e .` 后两者在 `PATH` 上，且直接指向检出
  （改源码即时生效，含新增模块）。仓库提供 `scripts/install.sh` 与
  `scripts/uninstall.sh` 一键装卸（§8.4）。
- `anx` 经 `anx.main.compiler_command()` 优先调用 `yianc`（§8.4）。
- **标准库根可配置（G10）**：`resolve_stdlib_root()` 支持 `--compiler-root` /
  `YIAN_LIB` / `YIAN_ROOT` / 检出默认四级；`install.sh --regular` 支持非 editable 安装，
  并在安装后打印该检出对应的 `export YIAN_LIB=...`。两个命令在根目录不存在时于启动阶段
  给出含实际路径的错误，而不是让 stdlib 因为「不被信任」而在 `@runtime_fail` 处炸开。
- **`anx graph [--json]`**：输出根包、每个包的 `kind`/`root`/`manifest`/`sourceRoot`/
  `entry`/`dependencies`、文件索引（路径 → 包 + 模块路径）、依赖映射与结构化诊断
  （`code`/`message`/`path`/`span`/`hint`）。载荷**总是**打印，退出码 1 表示项目不可用
  或带诊断，0 表示干净，因此脚本可以既读载荷又按退出码分支。

**验收结果**（在 `python -m venv --system-site-packages` 的干净 venv 里实测）：

| 场景 | 结果 |
| --- | --- |
| `pip install .`（非 editable，无 `--deps`） | `compiler/` 与 `anx/` 进入 site-packages，`lib/` 不在其中 |
| 未配置根时 `yianc <lib/src> main.an -t none` | 明确报「standard library source root … 不存在」并提示 `YIAN_LIB` / `YIAN_ROOT` / `--compiler-root`，退出 1 |
| `--compiler-root <checkout>` / `YIAN_ROOT=<checkout>` / `YIAN_LIB=<checkout>/lib/src` | 三种方式下 `yianc ... -t none` 通过 |
| 非 editable 安装 + `YIAN_LIB` 下的 `anx build` / `anx run` / `anx check` / `anx test` / `anx graph --json` | 全部可用（`anx run` 输出程序结果） |
| `anx new`（不需要标准库） | 未配置根时仍可用 |
| `graph --json` 可被脚本消费 | `python3 -c "json.load(...)"` 解析成功；带诊断的项目退出 1 且 `AX003` 出现在载荷里（fixture `errors/graph_diagnostics`） |

### A5 远程依赖与版本（只出结论）

**状态：已完成**（结论见 `docs/plan/anx-remote-deps.md`）。

- 评估远程依赖、版本约束和锁文件的需求，产出书面建议，不写实现。
- **验收**：`docs/plan/` 下有一份结论文档，明确纳入/不纳入及理由。

**结论摘要**：三件事都不在本阶段纳入，但**它们是同一件事的三半**——远程来源、
锁文件、内容哈希必须同时到来，缺任何一个都会得到比现在更差的模型。

| 事项 | 结论 | 重审条件 |
| --- | --- | --- |
| 远程依赖（git/URL/tarball） | 不纳入 | 出现跨项目复用同一包的真实需求时 |
| registry 与发布流程 | 不纳入，且不在近期计划 | 有第三方发布者时 |
| 版本约束 / 求解 | 不纳入；`version` 只做格式校验 | 远程依赖落地后作为配套 |
| 锁文件 | 暂不纳入（当前无「约束 → 精确版本」这一步） | 与远程依赖**同一次设计** |
| 内容哈希校验 | 不纳入 | 同上 |

理由要点：YIAN「整体源码编译 + 单一顶层命名空间」意味着同一个包的两个版本无法共存，
版本选择因此在一般形式下是 NP-完全的（`docs/plan/anx-remote-deps.md` §3.2 引 Russ Cox 的
证明）；而当前依赖只有本地路径、`path` 已精确到目录，锁文件此时只是把 manifest 抄一遍。
推荐路线是先做「内容寻址的包身份」（与 §6.3 的按包产物前置约束同向），再谈远程与版本。

## 11. 与学期计划的对应关系

对应 `docs/proposal.md` 「anx 包管理器」小节：

| 计划条目 | 本设计落点 |
| --- | --- |
| 完善项目初始化模板，明确目录规范 | §3.1、A0（`new` 模板补 `tests/`、`.gitignore` 说明） |
| 理清模块导入与包依赖关系，统一解析规则 | §3.5、§4.3、§5 |
| 完善依赖解析及错误诊断（缺失/环/可见性） | §4.4、§7；可见性边界见 §7.3 |
| 完善以项目为单位的构建/运行/检查，补充多包集成测试 | §8、A3；package 模式测试并入 `tests/`：A0.5 |
| 评估远程依赖和版本解析 | §10-A5；结论文档 `docs/plan/anx-remote-deps.md` |
| （前置研究）按包编译与产物格式 | §2.5（现状：编译单元是整个程序）、§6.3（四条约束）；归属 proposal 实施顺序「补齐自举所需基础设施」之前 |

## 12. 文档定位说明

本文件位于 `docs/plan/`，与 `ide-support-plan.md`、`anx-remote-deps.md` 并列。
原先两处指向 anx 设计文档的失效链接已在 A5 一并修好：

- `README.md` → `docs/plan/anx-design.md`（原指向不存在的 `bak/anx_design.md`）；
- `docs/manual/index.md` → `../plan/anx-design.md`（原指向不存在的 `docs/manual/anx.md`）。
