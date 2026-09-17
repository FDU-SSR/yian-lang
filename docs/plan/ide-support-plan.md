# YIAN IDE / VS Code 支持开发计划

> 跨阶段政策（详见 §5）：**不为 IDE / LSP 建立自动化测试**，功能由实际使用验收，编译器
> `basic` / `safety` / `package` 三套件是不回归底线；**VS Code 扩展与语言服务器分层存放**
> （`ide-support/vscode/` 与顶层 `lsp/`）；**分析能力继续留在 `compiler/`，把它改造成 IDE 友好，
> 不重写语言前端**（§5.8）；**接入方式采用语言服务器（LSP），服务器是独立进程、由扩展经 stdio
> 启动**（§5.9、§5.10）。

## 1. 目标、范围与基本原则

### 1.1 总体目标

为 YIAN 提供一套可安装、可调试的编辑器支持，使开发者在编辑 `.an` 文件时能够完成以下工作：

- 识别 YIAN 文件并提供注释、括号、折叠和自动闭合等基础编辑行为；
- 对关键字、字符串、数字、类型、函数、变量和运算符进行基础语法高亮；
- 在代码未保存、尚未完整输入时给出尽可能稳定的语法和语义诊断；
- 跳转到函数、结构体、枚举、类型别名、变量、字段和方法的定义；
- 查看当前符号的类型、签名和文档；
- 根据作用域、导入关系和接收者类型提供上下文相关的代码补全；
- 区分声明、引用、类型、函数、变量、参数、字段和枚举成员等语义对象的颜色；
- 为后续的查找引用、重命名、格式化和代码操作留下接口。

### 1.2 暂不纳入第一阶段的范围

- 调试器、断点、单步执行和变量查看；
- 自动修复所有类型错误；
- 完整的 IDE 项目管理界面；
- 为了编辑器支持而改变 YIAN 的语言语义；
- 在没有性能数据的情况下直接引入增量编译器、增量解析器或新的类型系统；
- 重写一套 IDE 专用的词法/语法/类型前端（理由见 §5.8.1）；
- 面向 IDE / LSP 的自动化测试基础设施（见 §5.6）。

### 1.3 基本原则

1. 编译器前端是语言规则的主要来源，插件中不重复实现一套独立的 YIAN 语法和类型规则。
2. 编辑器分析与 LLVM、clang、可执行文件生成解耦；编辑器功能只需要进行词法、语法、符号和类型分析。
3. 先定义稳定的分析数据模型和功能边界，再决定采用哪种 VS Code API、通信协议和实现语言。
4. 所有功能都必须考虑未保存文本、半成品代码、错误恢复和跨文件依赖。
5. **不重复造项目模型**：项目根、标准库、包依赖和导入解析一律复用 `anx` 现成的接口（§2.2）。
6. **不重写语言前端**：分析能力继续落在 `compiler/`，只把它改造成 IDE 友好（§5.8），
   不新建第二套词法/语法/类型规则。

## 2. 现有基础与需要先解决的问题

### 2.1 编辑器侧的现状

`ide-support/` 下目前只有 P0 的两个 YIAN 验收工程（`sample/`、`sample-errors/`）与一个打包产物
`yian-language-support-0.0.10.vsix`；**没有扩展源码树，也没有构建脚本**。该产物内容陈旧：
`activate()` 是空的，没有语言服务器，也没有补全、跳转、悬停或诊断逻辑，grammar 的关键字集合与
当前语言不一致。它**不作为开发基线**，P1 直接重建源码树（§7 P1）。

仅作为参考，VSIX 内部的 `extension/` 包含：`package.json`（语言 id `yian`、`.an` 关联、
`onLanguage:yian` 激活）、`language-configuration.json`（注释、括号、自动闭合）、
`syntaxes/yian.tmLanguage.json`（词法级高亮）与 `src/extension.ts`。注意这里的 `extension/` 是
VSIX 内部目录，与重建后的 `ide-support/vscode/` 无关（§5.7）。

### 2.2 已经具备、可直接复用的能力

以下能力已在仓库中实现，本计划直接复用，不重复建设：

| 能力 | 现成接口 | 说明 |
| --- | --- | --- |
| 分析未被 `main` 调用的定义 | `compiler/analysis/passes/type_check.py::__check_root_definitions` | 检查根 = 根包全部顶层定义（未实例化的泛型体除外）；编辑器无需另建"库文件分析"路径 |
| 项目 / 依赖 / 导入规则 | `anx/project.py` 的 `Project`：`discover`、`load`、`files`、`dependencies`、`dev_dependencies`、`resolve_import`、`describe` | 纯数据模型，只依赖标准库、不 import 编译器、不做副作用，编辑器进程可直接加载 |
| 项目形状的结构化输出 | `Project.describe()` / `anx graph --json` | 根包、每个包的 `kind`/`sourceRoot`/`entry`/`dependencies`/`devDependencies`、文件索引、结构化诊断 |

命令行与编辑器共用同一份「包名 → 源码根 → 依赖边 → 可视集合」规则，这是 §1.3 第 5 条的依据。

### 2.3 需要先解决的关键问题

下面五条就是 YIAN 相对 §5.8 那份"IDE 友好清单"缺的项，P2/P3 的工作范围由它们确定。

1. **Lexer 以文件路径为输入**：`CharStream.__init__(path)` 直接 `path.read_text()`，
   语言工具需要直接接收编辑器内存中的文本。
2. **Parser 遇到错误立即停止**：解析器通篇 `raise ParseError(...)`，没有错误恢复；
   编辑器需要尽量继续产生可用的部分结果。全编译器共有 **224 个"第一个错误即中止"的抛出点**
   （`raise ParseError` 24、`raise LexError` 9、`raise AnalysisError` 191），所以错误恢复必须
   按"先做到不崩、能返回部分结果"分阶段做，不能一步改成多诊断（§5.11）。
3. **位置与文档标识模型不匹配**：`SrcPosition(row, col, path)` 是 **row 0 基、col 1 基**，
   并且携带 `Path` 而不是 uri + 版本号；LSP 使用 0 基行列、字符按 UTF-16 计数。
   处理方式见 §5.1。
4. **符号无法追溯到声明位置**：`Symbol` 只有 `symbol_id`、`name`、`kind`、`type_id`、
   `attributes`，**没有 span**；`TypeCtx.get_span` 只覆盖类型定义。变量、参数、字段、
   方法、枚举成员都还没有来源位置，导入别名也没有记录。
5. **没有进程内分析入口，也没有可复用的快照**：所有分析都从 `compiler/main.py::main()` 出发，
   错误路径 `__print_source_error` **打印 Python traceback、输出到 stdout、`sys.exit(-1)`
   （退出码 255）**，而且它自己还会再 `path.read_text()` 一次——连渲染诊断都依赖磁盘文件。
   这与 `docs/grammar/16` 的约定不一致（编译诊断应输出到 stderr，退出码只区分成功/失败），
   也无法让分析接口返回结构化结果。

另有一条影响设计的既有事实：**`TypeCtx` 是单个实例、显式传递的**（`compiler/main.py` 建一次，
之后一路传下去），这对复用有利，不需要再引入状态容器；但 `inject_prelude(unit_datas.values())`
等 pass **原地修改 AST**，所以同一份内存 AST 不能被分析两次——第一版必须每次从文本重新走一遍
（§5.3）。

## 3. 环境准备

### 3.1 已有工具

| 工具 | 版本 | 用途 |
| --- | --- | --- |
| `node` / `npm` | v24.16.0 / 11.13.0 | 扩展构建与打包 |
| `code`（VS Code CLI） | 1.137.0 | 安装 VSIX、做实际使用验收 |
| Python + llvmlite | `/home/zhx/miniconda3/bin/python3`，`yianc` / `anx` 已 editable 安装 | 分析接口与语言服务器 |
| clang | 已装 | 编译器链接可执行文件 |

### 3.2 需要安装的依赖（联网执行）

`ide-support/vscode/` 要等 P1a 建出源码树后才会存在，所以第二段目前还不能执行。

```bash
# ── 现在就可以装：打包工具，装成全局命令，避免 npx 每次联网拉包
npm install -g @vscode/vsce

# ── P1a 建好源码树之后：扩展依赖 + 编译（拉 typescript、@types/node、@types/vscode）
cd /home/zhx/workspace/yian/ide-support/vscode
npm install
npm run compile

# 打包（第一段已装全局）
vsce package
# 或临时使用（不装全局）：npx @vscode/vsce package

# 装进 VS Code，版本号按实际产物替换
code --install-extension /home/zhx/workspace/yian/ide-support/yian-language-support-0.1.0.vsix
code --list-extensions --show-versions | grep -i yian

# ── P2 起需要：语言服务器侧的 Python 依赖（接入方式已定为 LSP，§5.9）
/home/zhx/miniconda3/bin/python3 -m pip install pygls
# ── P3 起需要：扩展侧的 LSP 客户端库
cd /home/zhx/workspace/yian/ide-support/vscode && npm install vscode-languageclient
```

`yian-lsp` 由 P3 创建的 `lsp/` 包提供（`[project.scripts]`，§5.7）；在 P2/P3 期间按 §6.1 的
候选 ② 依赖本机 Python 与已有的 editable 安装。

这些安装都需要联网：`npx --no-install @vscode/vsce` 在本地没有 `@vscode/vsce` 时会失败，
离线环境需要预先准备 `@vscode/vsce` 与扩展的 `node_modules`（内网镜像或离线安装包）。

## 4. 功能分层与阶段总览

下面的阶段是能力依赖关系，不是固定工期。每个阶段完成后才能进入下一阶段；如果实际使用中发现
性能或架构问题，应允许在当前阶段回退或调整技术路线。

| 阶段 | 名称 | 主要交付物 | 阶段门槛 |
| --- | --- | --- | --- |
| P0 | 范围、决策与示例工程 | 功能矩阵、示例工程、诊断结构与错误码方案定案 | "支持什么""怎么算做完"不再有歧义 |
| P1 | 扩展工程 | 扩展源码树；语言注册、基础高亮和编辑行为 | 新环境能构建 VSIX，安装后 `.an` 文件识别与编辑正常 |
| P2 | 编译器前端改造与分析接口 | 文本输入与解析容错（§5.8.2 第 1/2/4/8 条）、面向内存文本的分析会话、位置与 URI 转换、结构化诊断 | 不生成 LLVM/可执行文件也能对未保存文本得到稳定分析结果 |
| P3 | 工作区、符号索引与快照 | 顶层 `lsp/` 包与扩展侧客户端接线、项目发现、依赖解析、符号声明位置、不可变快照与失效规则 | 多文件项目中能定位符号并跟上依赖变化，扩展能与服务器握手 |
| P4 | 实时诊断 | `publishDiagnostics`：词法、语法、导入、名称、类型诊断 | 修改未保存文本后诊断及时更新且不残留旧结果 |
| P5 | 导航与类型查看 | `definition`、`documentSymbol`、`hover` | 本地、跨文件、标准库和成员符号都能验证 |
| P6 | 补全与语义高亮 | `completion`、`signatureHelp`、`semanticTokens` | 补全结果与当前作用域和类型一致 |
| P7 | 高级编辑能力 | `references`、`rename`、`codeAction`、格式化（视评估） | 修改范围准确，不误改无关文本 |
| P8 | 性能、打包与发布 | 性能记录、VSIX 发布流程、安装与故障排查文档 | 其他人可以独立安装、运行并反馈问题 |

## 5. 已确定的决策

### 5.1 位置模型：内部不动，边界转换

编译器的 `SrcPosition` / `SrcSpan` 是**前端与 IR 的稳定表示**（row 0 基、col 1 基、携带
`Path`），本计划**不修改它**。编辑器侧的差异在边界层消化，新增一个转换模块负责：

- 行：内部 row 已经是 0 基，直接透传；
- 列：内部 col 是 1 基 → LSP 的 0 基；
- 字符计数：内部按 Python 字符串下标（码点）→ LSP 的 UTF-16 代码单元（涉及代理对时需换算）；
- 文档标识：`Path` ↔ uri（`file://` 或编辑器给的 uri）+ 文档版本号。

转换模块是唯一允许出现"两套坐标"的地方，其余模块只使用其中一套。

### 5.2 分析接口与通信协议解耦，并按层落位

分析会话（`DocumentStore`、`AnalysisSession`、`AnalysisSnapshot`、结构化诊断）做成与协议无关的
库；VS Code 直连 API、LSP 或命令行都只是它的消费者。P2 只交付这个库与最小驱动。

**该库住在 `compiler/` 内部**（如 `compiler/analysis/session.py` 与配套的诊断/位置转换模块），
不另起顶层包：它需要直接使用 lexer、parser、类型检查的内部结构，搬到 `compiler/` 外面要么形成
"新包 → 编译器内部符号"的脆弱依赖，要么迫使编译器先导出一整套 API。放在 `compiler/` 内，命令行
暴露它（`yianc --analyze --json` 一类）几乎不需要额外工作，而语言服务器只是它的薄适配层。这条
分工与主流实现一致：gopls 把 `go/types` 的结果收进自己的 `Snapshot`，语言服务器层不重新实现分析
（§5.8）。

### 5.3 第一版采用全量重分析

打开文件后对当前内存文本做一次完整分析，先保证正确性。只有在 §5.12 的指标无法满足时才考虑
增量解析或缓存；缓存必须有明确失效规则，不能返回过期的定义、类型或诊断。

这一条不只是"先简单后复杂"的取舍，也是被现状逼出来的：`inject_prelude` 等 pass **原地修改
AST**，同一份内存 AST 无法分析两次，而要让每个 pass 都可重复执行、可回滚，成本远高于重新解析
一次文本。按 §5.8.1 的判断，YIAN 的前端足够小，重新解析是可接受的第一版；把"可重复执行"
作为 P3 快照设计的一部分再说。

### 5.4 高亮：TextMate 作为回退，语义 token 叠加

P1 建立的 TextMate grammar 作为基础高亮；真实类型、函数、变量和字段的区分由语义 token 提供。
语义分析失败时不得影响基础高亮（不引入 tree-sitter 等新的解析前端）。

### 5.5 项目配置：复用 `anx`，不引入第二套配置

项目发现、依赖解析和标准库定位一律用 `Project.discover` / `load` / `resolve_import`，标准库
定位复用 `compiler.analysis.source_provenance.resolve_stdlib_root`（`--compiler-root` /
`YIAN_LIB` / `YIAN_ROOT`）。**不新增 YIAN 工具专用配置文件**，避免与 `package.anx` 产生两套
互相矛盾的项目规则。`--packages`（v2 契约）是命令行与编辑器共同的事实来源。

### 5.6 验收方式：实际使用 + 编译器套件不回归

- **不为 IDE / LSP 建立自动化测试**：阶段验收由实际使用完成（§7 每个阶段给出"实际使用清单"，
  由维护者按清单在 VS Code 中操作确认）。
- **不回归底线**：`basic` / `safety` / `package` 三套件必须保持全绿
  （`scripts/run_tests.py --all`，运行前需已安装 `yianc` / `anx`）。为此 P2 引入的分析接口
  必须与编译器的既有行为一致，而不是另写一套解析逻辑。
- 因此本计划**不新增** fixture 目录、不新增 runner 套件、不定义 IDE 期望文件格式。

### 5.7 目录分层：按依赖方向切

扩展与语言服务器**不放在同一个目录下**。切分依据是**谁复用谁、谁是某个编辑器的专属产物**：

| 层 | 位置 | 内容 | 谁依赖它 |
| --- | --- | --- | --- |
| 语言引擎 | `compiler/`（已有） | 词法/语法/类型分析，以及 §5.2 的分析会话、诊断、位置转换 | `yianc` CLI、`lsp/` |
| 项目模型 | `anx/`（已有） | 项目发现、依赖与导入规则 | `anx` CLI、`lsp/` |
| 协议服务 | **`lsp/`（新顶层包）** | 协议适配、服务进程、文档版本与取消语义 | 所有 LSP 客户端 |
| 编辑器客户端 | **`ide-support/vscode/`** | TS 扩展、grammar、language-configuration、VSIX | 无（终端产物） |

约定：

- **依赖方向单向**：`ide-support/vscode/ → lsp/ → compiler/ + anx/`，任何一层都不得反向 import，
  客户端里不得出现 YIAN 语言规则。
- **`ide-support/` 放编辑器支持资产**：客户端目录用 `vscode/`（P1 创建），第二个客户端作为并列
  子目录（`ide-support/neovim/` 等）；P0 的 YIAN 验收工程放 `ide-support/sample/` 与
  `ide-support/sample-errors/`（§7 P0）。
- **`lsp/` 是必建项**：接入方式已定为语言服务器（§5.9），所以协议服务层从 P3 起存在；P2 先把
  分析会话做在 `compiler/` 内，P3 再把 `lsp/` 建起来接上它。用户级安装因此从 P3 起需要
  `pygls`（服务器侧）与 `vscode-languageclient`（扩展侧），见 §3.2。
- **`lsp/` 与 `compiler/`、`anx/` 同等待遇**：遵守 Python 双下划线私有命名约定，纳入 pyright
  strict 覆盖，并在 `pyproject.toml` 里登记。创建时需要同时改三处配置：

  ```toml
  [project.scripts]
  yian-lsp = "lsp.main:main"

  [tool.setuptools.packages.find]
  include = ["compiler*", "anx*", "lsp*"]

  [tool.pyright]
  include = ["compiler", "anx", "lsp"]
  ```

- **两套工具链互不污染**：`node_modules/`、`out/`、`*.vsix` 只出现在 `ide-support/vscode/`；
  pyright、`compileall` 与打包清单只覆盖 Python 侧。

### 5.8 复用策略：改造 `compiler/`，不重写前端

#### 5.8.1 主流语言服务器怎么复用编译器

调研过的主流实现（引用见 §9）可以归成五种路线，YIAN 的选择由它们的代价记录决定：

| 路线 | 代表 | 做法 | 已知代价 |
| --- | --- | --- | --- |
| A 编译器即平台 | Roslyn、TypeScript、Pyright、gopls、HLS/GHC | 语言服务器是编译器（库）的一个前端，只补状态/快照层 | 要求编译器库能被长期驻留、容错复用；gopls 的官方设计文档自陈 `go/types` 等库"不为错误与增量设计"，只好自己补 tree repair |
| B 批编译器 + 快照 | clangd、Merlin | 复用整个批编译器，把"导入之后"的状态做成不可变快照（preamble），只对本地部分重解析 | 需要 declaration-before-use/headers；跨文件信息要靠 index 另建 |
| C 展示编译器 | Scala/Metals、HLS | 在**不完整代码**上跑真正的类型检查（允许报错继续），常独立进程 | 进程与生命周期复杂（这两个我未逐条核对一手文档，仅作同模式参考） |
| D 独立前端 | rust-analyzer | 只复用底层库（`chalk`、少量 `rustc_*`），parser/HIR/类型推导全部重写 | 两套前端要长期同步语言规则；rust-analyzer 自陈增量引擎的 "main drawback is extra complexity, slower performance" |
| E 改造共享前端设施 | Merlin | 改 lexer/parser 生成器本身，让容错与增量化成为语法基础设施能力，两边共用 | 需要改动生成器 |

关键判据来自两条"失败/放弃"的记录：**RLS 复用了 rustc 却被判定不够快而废弃**；clangd 作者明确说
增量能力"I don't believe this is something we could bolt onto clang"。反过来，KCL 把"不要像
rustc 和 rust-analyzer 那样维护两套前端"写成了明确目标，并在同一份清单里列出了一份改造作业单。

**结论：YIAN 以路线 A 为主，借用路线 B 的快照思路，明确不走 D。** 理由：YIAN 没有宏展开、没有
proc macro、没有开放世界 trait 求解，也没有 headers 那类编译单元约束，重写前端的动机（rustc 那种
不可容错、不可增量、宏展开不可控）在 YIAN 不存在；而全程序编译天然持有全局信息，比 C++ 更适合
"快照 + 全量重分析"（B 的 preamble 机制本身用不上，可借用的是"把分析状态做成不可变快照"）。
代价是要按下面清单补齐编译器缺的 IDE 友好性——这正是 P2/P3 的范围。

#### 5.8.2 IDE 友好清单（P2/P3 的工作范围由此确定）

| # | 编译器需要提供 | 主流先例 | YIAN 现状 |
| --- | --- | --- | --- |
| 1 | 文本输入而非文件路径 | HLS：GHC 被改成接受 string buffer | 缺（`CharStream(path)`、`__print_source_error` 再读盘） |
| 2 | 解析不抛异常，返回 `(树, 错误列表)` | rust-analyzer 架构不变量 | 缺（224 个抛出点） |
| 3 | 错误在树里的表示（缺失/跳过 token） | Roslyn `IsMissing` / `SkippedTokens` | 缺 |
| 4 | token 级位置与全保真信息 | Roslyn trivia、`Span`/`FullSpan`；clangd `TokenBuffer` | 部分（AST 有 span，但遍历不到的部分拿不到位置） |
| 5 | 所有符号都能定位到声明 | TS `getSymbolAtLocation` | 缺（`Symbol` 无 span） |
| 6 | 文档抽象 = uri + 版本号 + 内存快照 | TS `ScriptSnapshot`/`version`；Roslyn `Document` | 缺（`SrcPosition` 带 `Path`） |
| 7 | 不可变快照 + 明确失效规则 | Roslyn `Solution`/`Compilation`；gopls `Snapshot` | 缺（且 pass 原地改 AST，同一份 AST 不能分析两次） |
| 8 | 不 `sys.exit`，错误结构化返回 | rust-analyzer 每个请求 `catch_unwind` | 缺（traceback + stdout + `sys.exit(-1)`） |
| 9 | 取消与优先序 | clangd `ASTWorker` 队列去重/去抖 | 缺 |

顺序上先做 1、2、4、8（P2 的 spike 就是这个），再做 5、6、7（P3），最后按需做 3、9。

第 8 条附带一条硬约束：服务器是 stdio 进程（§5.10），**stdout 是协议通道**，所以 `compiler/`
与 `anx/` 的分析路径不得向 stdout 写任何内容（日志走 stderr）。

#### 5.8.3 明确不采用的替代方案

- **不重写前端**（路线 D）：见 5.8.1。若将来发现 P2 的改造无法收敛，再重新评估，而不是现在先分叉。
- **不新建 stub/index 式的跨文件索引**（路线 B 的 clangd 部分）：那是 C++ 无法从单个 TU 看到全局
  信息的补丁；YIAN 一次分析整个程序，直接分析全量文件即可（P3 只做索引与失效，不做 stub）。
- **暂不引入 salsa 式细粒度增量**：先按 5.8.2 第 7 条做"不可变快照 + 变更即清缓存"（gopls 的做法），
  只有实测不达标才升级（§5.12）。

### 5.9 接入方式：语言服务器（LSP）

**决定：通过 Language Server 接入，扩展只做 LSP 客户端，不直接用 VS Code 的 `languages.*` API。**

理由：

- 分析能力已经在 `compiler/` 里、并且按 §5.8 的清单改造成进程内可长期调用的形态，LSP 只是它的
  薄适配层（§5.2）；这和 §5.8.1 路线 A 的分工一致，语言服务器层不重新实现分析。
- 用 `languages.*` 直连会把功能绑死在 VS Code；而 LSP 保留了其他编辑器的可能性，且协议边界是
  稳定的（§9 里 rust-analyzer 把"只有 `rust-analyzer` crate 知道 LSP"列为架构不变量）。
- 编辑器侧因此只需要 TS 客户端代码，YIAN 的语言规则不会渗进扩展（§1.3 第 1 条）。

直接后果（写进后续阶段）：

- **`lsp/` 从 P3 起是必建交付物**（§5.7），同步 `pyproject.toml` 的三处配置；P2 先把分析会话
  做在 `compiler/` 内，P3 接上 `lsp/`。
- **P4–P7 的功能直接以 LSP 方法实现**，各阶段的工作内容按 LSP 的方法名对齐：
  `publishDiagnostics`（P4）、`definition` / `documentSymbol` / `hover`（P5）、
  `completion` / `signatureHelp` / `semanticTokens`（P6）、`references` / `rename` / `codeAction`（P7）。
  不再为 VS Code 直连 API 准备第二套实现。
- **P2 的结构化 CLI 出口（`yianc --analyze --json`）降级为调试与验收手段**，不再是备选接入方式。
- 语义高亮走 LSP 的 semantic tokens（§5.4 已按此设计）；位置转换层面向 LSP 的 0 基行与 UTF-16
  列（§5.1 已按此设计）。
- **进程形态已定**（§5.10）：`lsp/` 是独立进程，扩展通过 stdio 启动它。
- **分发仍是开放项**（§6.1）：服务器需要 Python 运行时与 `pygls`。P2/P3 先采用"依赖本机 Python
  （复用已有的 editable 安装）"，把打包形式留给 P8 决定。

### 5.10 进程形态：`lsp/` 独立进程，扩展用 stdio 启动

**决定：语言服务器是独立进程，VS Code 扩展通过 stdio 启动它；分析在服务器进程内以库调用完成，
不为每个请求另起子进程。**

理由：

- 这是 LSP 的常规形态（也是 §9 里 gopls、clangd、rust-analyzer 的共同做法）：进程由编辑器管理，
  生命周期与编辑器一致，stdio 上的 JSON-RPC 不需要额外端口或 daemon 管理。
- 分析会话（§5.2）是长驻内存对象，必须在服务器进程内长期持有；若每个请求都起子进程，就等于把
  §5.3 的"全量重分析"成本乘上每次交互，与 P0 的 100ms 口径直接冲突。
- 与 §5.9 的分工一致：`lsp/` 只做协议适配与进程管理，`compiler/` 与 `anx/` 被它 import 复用，
  而不是被它调用成外部命令。

直接后果（写进 P2/P3）：

- **stdout 必须纯净**：stdio 传输下 stdout 就是协议通道，任何 `print()` 都会破坏 JSON-RPC 帧。
  因此 §5.8.2 第 8 条（去掉 `__print_source_error` 的 stdout 输出）不是整洁性问题而是**功能性
  前提**：`compiler/` 与 `anx/` 的分析路径不得向 stdout 写任何东西。日志走 stderr 或文件，
  stderr 由扩展收进 Output 通道。
- **进程内库调用**：`lsp/` import `compiler` 的分析会话与 `anx` 的项目模型；P2 只负责把会话做成
  可在长驻进程里反复调用的形态，不需要为跨进程调用设计序列化协议。
- **`yian-lsp` 是进程入口**：`pyproject.toml` 的 `[project.scripts]` 注册 `yian-lsp`（§5.7），
  扩展的 `ServerOptions` 用 stdio transport 启动它；命令名与查找路径（PATH 上的 `yian-lsp`，
  还是显式配置 Python 解释器 + 模块名）属于 §6.1 的分发问题。
- **文档同步用全量**：既然第一版是全量重分析（§5.3），`textDocumentSync` 用 `Full`，客户端每次
  直接送整份文本，服务器不需要实现增量文本 diff 的应用。
- **取消与生命周期**：支持 `$/cancelRequest`，映射到 §5.8.2 第 9 条的取消机制；服务器异常退出时
  扩展只记日志、不崩（P8 验收项）；扩展关闭时服务器随 stdin 结束退出，不留孤儿进程。

### 5.11 错误恢复与多诊断：三层，恢复粒度定在顶层定义

**决定：P2 只做"捕获并结构化"+"文件在构造中途结束"这一种容错；多诊断的正确粒度是顶层定义（item）
级而不是语句级；类型检查器的多诊断推到 P4，且以先引入错误类型为前提。**

**第一层（P2，必须做）：把"中止进程"改成"中止分析"。** 这一层**不需要动解析器**——`compiler/`
里的 224 个抛出点已经带 span，在会话边界 `try/except` 接住、转成结构化 `Diagnostic` 返回即可。
P2 的"不崩、返回至少一条诊断、位置正确"主要靠**接住**而不是靠**恢复**，这是投入产出最高的一步。

**第二层：容错范围限定为"输入到一半"的几种形状，不是"任意错误"。** 打字时坏代码的分布很集中：
构造中途文件结束、半个标识符、缺 `;` 或换行、悬空运算符（`x.`、`x +`）、空块。其中**"文件在构造
中途结束"单独就占大头**，因为每写一个新函数体都会触发。所以：

- P2 只切这一刀：解析器在构造内部遇到 EOF 时隐式闭合该构造并记一条错误，而不是抛异常；
- P3/P4 按一份**固定的清单**逐条扩，每条在"实际使用清单"里有对应操作项。这样"错误恢复"从无底洞
  变成一张可勾选的表。

两个先例支撑"在适配层打补丁而不是在前端做大手术"：clangd 官方明确说 "Clang doesn't do a great job
of preserving the AST around incomplete code"，于是补全时**另起一次 parse**；gopls 则加 `parsego`
做 "tree repair to work around error-recovery shortcomings of the Go parser"（§9.2）。

**第三层（P4）：多诊断的粒度定在顶层定义级。** 某个顶层定义解析或检查失败 → 记错误、替换成错误
节点、继续下一个顶层定义。这能在 Problems 面板里给出"这个文件有 5 处问题"，而**不需要**在函数体
内部做语句级恢复。Kotlin / rust-analyzer 的 FOLLOW 集回退也是这个粒度（跳过 token 直到能开始下
一个 item 的位置）。语句级恢复留到有实测证据说明需要时再做。

**两个明确不做的事**：

1. **不把 `never` / `never_id` 复用成错误类型**。类型检查器要能继续就必须有 poison 值压住级联，
   但那应是**新增一个显式错误类型**，而不是拿已有的发散/底类型顶替。这是 P4 的前置条件：先有
   错误类型，再谈类型检查器的多诊断。
2. **不做"退回上一次有效快照"**。它对导航很诱人，但直接违反 §5.3 的"缓存不能返回过期的定义、类型
   或诊断"，还会引入一类很难复现的陈旧结果 bug。更干净的不变量是：把容错做到"半成品也能出可用树"。

**另有一条与恢复无关但更早影响可用性的要求**：`lsp/` 建起来后（P3），**每个 LSP 请求都要单独兜住
异常**（rust-analyzer 的做法：每个请求外面套 `catch_unwind`）。服务器是长驻进程，一次内部异常不能
让整个编辑器会话失效。

### 5.12 快照、惰性与增量阶梯

**决定：快照边界定在"类型检查结束"；失效永远是整快照重建；性能先靠惰性与防抖，再按成本阶梯升级；
细粒度增量（salsa 式）在没有实测数据前不进入计划。**

**为什么边界正好在那里**：`TypeCtx` 的注释已经说明"类型与 impl 注册在 GlobalResolve 之后完成，
这些缓存保持关闭直到 `finalize()`，这样半成品类型空间不会产生陈旧的否定结果"——也就是说
**`finalize()` 之后 `TypeCtx` 天然是"注册已封闭、查询缓存已启用"的干净状态**，正是可冻结的边界。
而且分析只需要编译流水线的一个**前缀**：lex → parse → desugar → prelude → 受限操作检查 →
全局解析 → 类型终结 → 类型检查；后面的 comptime 特化、闭包下降、确定赋值、CFG、LLVM **一律不跑**。
这条"流水线前缀"是 YIAN 相对 clangd 的结构性优势。

**惰性优先于增量**（rust-analyzer 作者："it's not the incrementality that makes an IDE fast. Rather,
it's laziness"，§9.2）。落到 YIAN 上是三条：

1. **不按键触发分析，而是空闲 + 按需**：clangd 的写操作去抖是"重建直到收到一个读请求或者一个很短的
   空闲期限到期才开始"。照此执行——否则每敲一个字符就会出现一条"未定义标识符 f"。这是**必需项**，
   不是优化项。
2. **按功能跳过后半段**：只要语法诊断时连类型检查都不跑；只有悬停/补全/跳转这类语义请求才跑完整
   前缀，结果缓存到下次文本变化。这与 §5.3 的全量重分析不矛盾——重的是"全量程序"，不是"每次都跑
   完整流水线"。
3. **失效规则做成结构上不可能陈旧**：快照以「全部输入文本的哈希集合 + 编译器版本 + 编译旗标」为键，
   快照不可变，查询只读一个快照。这样"陈旧结果"不是靠纪律避免，而是没有表达方式。

**实测不达标时按这条成本递增的阶梯升级，不跳级**：

| 级别 | 做法 | 成本 / 风险 |
| --- | --- | --- |
| a | 去抖 + 取消 + 每次空闲最多分析一次 | 极低（本来就是必需项） |
| b | 按功能跳过 pass（上面第 2 条） | 低，只是编排 |
| c | **stdlib 结果跨快照复用**（按 stdlib 内容哈希 + 编译器版本缓存） | 中。`lib/src` 是最大且几乎不变的输入，收益最高——相当于 clangd 的 preamble，但要绕开 §5.3 说的"pass 原地改 AST" |
| d | **parse 结果按 (path, 内容哈希) 缓存** | 中低。parse 是纯函数（文本 → AST），只要缓存**变更之前**的产物就安全，后面的 pass 照旧跑 |
| e | 细粒度、带依赖追踪的增量（salsa 式） | 高。要求把 pass 重构成纯查询；rust-analyzer 自陈代价是 "extra complexity, slower performance"。**没有实测数据不进入** |

c 和 d 单独列出的理由：它们**不需要重构成查询引擎**，只是"给纯函数的输出加缓存"，风险与收益比远好
于 e。

**测量与判定**：不给"性能指标口径"单独设一个定案环节（用户已明确不做）。判定标准直接用 §5.8.1 调研
里两个项目独立采用的阈值——**跟手打字的反馈 <100ms，>200ms 不可接受**；P2 起用现成的
`--profile` / `timings` 机制给分析前缀加分段，P8 记录首次打开项目、修改后重分析、悬停、补全四个数。
**判断顺序写死：如果"首次打开"就超过 200ms，问题几乎一定在 stdlib，该做的是 c 而不是 e**——避免
一上手就往增量引擎走。

## 6. 暂不锁定的决策节点

以下决策在拿到原型和实测数据后再确定。分析层、接入方式、进程形态、错误恢复与增量策略都已定案
（§5.2、§5.8–§5.12），这里只剩分发。

### 6.1 语言服务器的分发方式

进程形态已定（§5.10），位置已定（§5.7），这里只剩下**分发**：

- 候选：① 随 VSIX 打包 Python 运行时；② 依赖本机 Python 环境（复用已有的 editable 安装）；
  ③ 提供独立可执行文件。
- 与之绑定的小决策：扩展启动服务器时用 PATH 上的 `yian-lsp`，还是显式配置解释器 + `python -m lsp`；
  后者对"本机多环境"更可控，前者对用户更省事。
- **P2/P3 一律按候选 ② 执行**（依赖本机 Python），先让功能跑通；打包形式在 P8 决定并补齐安装
  步骤与版本匹配规则（扩展与服务器版本要能对上，§7 P8）。

## 7. 各阶段详细计划与验收标准

每个阶段的"实际使用清单"由维护者在 VS Code 中手工执行；自动化部分只有编译器三套件不回归。

### P0：范围、决策与示例工程

**目标**：在写代码之前固定支持范围、决策项和最低质量标准。

**状态**：已完成。交付物是 `ide-support/sample/`（绿色工程）、`ide-support/sample-errors/`（诊断
验收工程），以及本节写下的三份定案（功能支持矩阵、诊断结构与错误码方案、位置模型 §5.1）。

#### P0.1 示例工程

放**两个**工程，而不是一个工程里塞一个"错误文件"：示例工程必须能 `anx build` 通过，所以故意写坏的
代码不能待在根包的 `src/` 里（`anx` 会分析包内所有文件）；拆成两个工程还顺带覆盖"同一工作区里
发现多个项目"这条路径。

| 工程 | 用途 | 内容 | 验收命令 |
| --- | --- | --- | --- |
| `ide-support/sample/` | 编辑器默认打开、功能验收 | `kind = "hybrid"`；`src/types.an`（类型别名）、`src/geometry.an`（结构体 + 泛型结构体 + 方法 + 自由函数）、`src/shapes.an`（枚举 + `match` + 泛型函数）、`src/main.an`（入口，`std` 导入 + 串联各模块）、`tests/`（2 个测试，其中一个 import 根包） | `anx build` / `anx run` / `anx test` / `anx check` 全部通过 |
| `ide-support/sample-errors/` | 诊断与多诊断验收 | `src/main.an`、`src/helper.an`、`src/types.an` 正确；`src/errors.an` 在**五个独立的顶层定义**里各含一个错误 | `anx build` 预期失败（`anx` 退出码 1） |

为什么用 `kind = "hybrid"`：bin 根不向测试暴露库接口，测试就无法 import 项目自己的模块；hybrid 才能
让 `tests/library.an` 引用 `sample.geometry`。

`src/errors.an` 的五个错误都已逐个单独验证过（每条都能让构建失败），分别是：类型不匹配
（`Expected type 'i32' but got 'str'`）、未知标识符、结构体未知字段、函数参数个数不匹配、泛型推断失败。
它们被刻意放在不同的顶层定义里，正是为了让 §5.11 第三层的"按顶层定义恢复"能一次报出多条。
当前编译器在第一个错误处中止，所以 P4 之前这里只会显示一条诊断——这是预期行为，不是缺陷。

#### P0.2 功能支持矩阵

"第一版"指 P4–P7 交付的范围；"不做"表示需要时再单独立项。

| 阶段 | 功能 | 第一版 |
| --- | --- | --- |
| P4 | 词法 / 语法诊断 | 做 |
| P4 | 名称、导入、可见性、包诊断 | 做 |
| P4 | 类型诊断（不匹配、成员、trait、泛型推断） | 做 |
| P4 | 多诊断，粒度 = 顶层定义（§5.11） | 做 |
| P4 | 错误码、相关位置、诊断来源标记 | 做 |
| P5 | 跳转定义：局部、参数、函数、类型、字段、方法、枚举成员、导入符号 | 做 |
| P5 | 文档符号 | 做 |
| P5 | 悬停：符号种类、完整名、解析后的类型 / 函数签名 | 做 |
| P5 | 标准库符号的来源标记 | 做 |
| P5 | 工作区符号搜索（`workspace/symbol`） | **不做** |
| P5 | `declaration` / `typeDefinition` / `implementation` | **不做** |
| P6 | 标识符补全：当前作用域、外层作用域、模块公开符号、预定义类型 | 做 |
| P6 | 成员补全：按接收者静态类型列字段与方法 | 做 |
| P6 | 类型位置补全、导入包 / 模块补全 | 做 |
| P6 | 签名帮助（参数信息） | 做 |
| P6 | 补全项的插入文本与参数占位符 | 做 |
| P6 | 语义 token 分类着色 | 做 |
| P6 | 模糊匹配、snippet 补全、补全项文档（doc 注释） | **不做** |
| P7 | 查找引用、当前文件引用高亮 | 做 |
| P7 | 重命名（跨文件 + 导入别名） | 做 |
| P7 | 代码操作：删除无效导入、展示相关诊断 | 做 |
| P7 | 补导入（`codeAction` + 自动 import） | **不做**（依赖包索引与模糊匹配，与 P6 的"不做"一致） |
| P7 | 格式化（`formatting`） | **不做**（先只做评估，见 §7 P7） |
| P7 | 折叠范围、选择范围、文档链接、内联提示 | **不做** |

#### P0.3 诊断结构与源码级错误码方案

**结构**（内部表示与 LSP 载荷一一对应）：

| 字段 | 说明 |
| --- | --- |
| `range` | `{start:{line,character}, end:{...}}`，**0 基行、UTF-16 列**，由 §5.1 的转换模块从 `SrcSpan` 得到 |
| `severity` | 硬错误 `Error`；由错误恢复产生的诊断（缺失 token、按定义边界恢复）`Warning`；标准库提示 `Information`；纯提示 `Hint` |
| `code` | 源码级错误码，见下 |
| `source` | 固定 `"yian"` |
| `message` | 面向人的文本，不带位置前缀（位置在 `range` 里） |
| `relatedInformation` | 可选；用于别名定义处、导入处、trait 声明处等次要位置 |
| `tags` | 可选：`Unnecessary`（无效导入）、`Deprecated` |
| 内部标记 | `recovered: bool`——**不发给 LSP**，只用于把恢复性诊断与硬错误区分开（§5.11） |

**错误码方案**：源码级诊断使用 **`E` + 组位 + 两位序号**，形如 `E301`；一个码对应一个**诊断条件**
（不是一条消息文本），同一条件由多处抛出时共用同一个码。

| 码段 | 归属 | 例子（P0 已确认存在的条件） |
| --- | --- | --- |
| `E1xx` | 词法（`LexError`） | `E101` 未闭合的块注释；`E102` f-string 中的非法 `}`；`E103` 文件结束时转义未闭合 |
| `E2xx` | 语法（`ParseError`） | `E201` 期望 token 缺失 / 构造中途结束（`End of token stream reached`，§5.11 第二层）；`E202` 无法开始一个声明；`E203` 表达式中的意外 token；`E204` 保留关键字误用 |
| `E3xx` | 名称、导入与可见性（`GlobalResolve`） | `E301` 未知标识符；`E302` 未知类型；`E304` 导入的符号不存在；`E305` 导入的符号不是 `pub`；`E307` 导入入口模块 |
| `E4xx` | 类型（类型终结与 `TypeCheck`） | `E401` 类型不匹配；`E402` 泛型推断失败；`E403` 结构体未知字段；`E404` 未知方法；`E405` 参数个数不匹配；`E407` 类型别名循环 |
| `E5xx` | 其它编译期检查 | `E501` 受限操作；`E502` 确定赋值；`E503` comptime 条件不是编译期常量 |

规则：

- **码段与项目级 `AX…`、运行时 `S…`/`R…` 不重叠**：`AX` 描述*工程/包*层面的问题（清单、依赖、
  循环、可见性配置），`E` 描述*源码*层面的问题，`S`/`R` 是运行期安全与资源错误。
- 一个码只增不改：发布后不得改号、不得复用；条件合并时保留旧码并标注别名。
- 组内序号从 `01` 起按需分配，不预留空号；P2/P4 落码时按组追加，并同步 `docs/grammar/16` 的
  错误码清单一节。
- **同一条诊断在不同严重级别下沿用同一个码**（严重级别是字段，不是命名空间），因此错误恢复产生的
  诊断不需要第二套码。

#### P0.4 本阶段发现并处理的编译器缺陷

按"问题分类"（编译器前端问题 / 分析模型问题 / 协议适配问题 / 扩展打包问题 / 性能问题）中的
**编译器前端问题**记录。

**缺陷 1（已修复）：类型别名作为声明签名类型时依赖声明顺序，顺序不利时崩溃。**
`typedef` 用在参数、返回值、结构体字段或枚举 payload 上，而该别名定义在**后面的 unit**（跨模块）
或**同一 unit 的后面**时，编译器抛未捕获的 `CompilerError: Type ID -1 does not exist in the type
context`，输出 Python traceback 并退出（直接 `yianc` 退出码 255；经 `anx` 包装后为 1）。

- 最小复现一（同一 unit 内逆序）：`typedef A = B;` 在前、`typedef B = f64;` 在后，再写
  `fn twice(v: A) -> A`。
- 最小复现二（跨 unit）：`src/types.an` 里 `pub typedef Meters = f64;`，`src/main.an` 里
  `from demo.types import Meters;` 后用 `fn twice(v: Meters) -> f64`——只要使用者的 unit 排在定义者
  之前就崩溃。
- 根因：`GlobalResolve` 原先对每个 unit 依次执行"导入 → 定义"，而签名解析发生在这个单遍内并通过
  `resolve_type` 立即折叠别名链；此时后定义别名的 `AliasDef.aliased_type` 仍是哨兵值 `-1`。
  局部变量注解不受影响，因为它在更晚的 `TypeCheck` 阶段解析。
- 修复：`GlobalResolve` 增加一个只解析别名体的前置循环，别名依赖未就绪时**重试**而不是放弃；
  `TypeCtx.resolve_aliases` 遇到未填充的别名抛 `UnfilledAliasError` 供该循环重试（不再静默返回
  别名 id——静默返回会把未折叠的别名烘进泛型实参，留下更难查的错误）。
- 验证：14 个最小复现全部通过；`basic` / `safety` / `package` 三套件全绿。
- 遗留：真正的**循环别名**（`typedef A = B; typedef B = A;`）目前以未捕获的 `UnfilledAliasError`
  结束——不再挂起，但仍没有位置和 `E407` 码。P2 的"捕获并结构化"（§5.11 第一层）应当把它变成
  一条正常诊断。

**缺陷 2（已记录，未修，非别名相关）：泛型结构体的静态方法必须写显式类型实参。**
`Pair.of(1, 2)` 报 `Unknown static method call 'of' on Pair<T>`；`Pair<i32>.of(1, 2)` 正常。
这是既有局限，与本次修复无关（在没有任何别名的控制组里同样复现）。示例工程因此统一写
`Pair<Meters>.of(...)`。是否需要支持从期望类型反推接收者泛型，留待后续评估。

**缺陷 3（已知，P2 处理）：错误路径打印 traceback 到 stdout 并用 `sys.exit(-1)`。**
即 §2.3 第 5 条与 §5.8.2 第 8 条；上面缺陷 1 的报错现场也走了同一条路径。它是 §5.10 所指的
"stdout 必须是协议通道"的直接障碍，属 P2 范围。

#### P0.5 位置模型与文档标识

沿用 §5.1：内部 `SrcPosition` / `SrcSpan` 不动（row 0 基、col 1 基、携带 `Path`），编辑器侧新增唯一
的转换模块，负责 1 基列 → 0 基列、码点 → UTF-16 代码单元、`Path` ↔ uri + 文档版本号。文档标识为
`(uri, version)`，版本号来自 LSP 的 `didChange`，是 §5.12 快照键的一部分。

#### P0.6 实际使用清单

- `ide-support/sample/` 能 `anx build` / `anx run` / `anx test` / `anx check` 通过（`anx run` 打印
  `sample ok`，`anx test` 为 `2 passed, 0 failed`）；
- `ide-support/sample-errors/` 的 `anx build` 失败，且五个错误各自单独验证过；
- 功能矩阵里每一项都能指出在示例工程中的对应文件与位置（"不做"项在计划里已注明理由）；
- 位置模型（§5.1）、诊断结构与错误码方案（P0.3）都有书面记录，即本节；
- 两个工程都能被 `anx graph --json` 描述，供编辑器发现项目使用。

### P1：扩展工程

**目标**：建立一个干净、可构建、可调试、可打包的扩展源码树。

**P1a 源码树与构建（后续所有前端工作的前提）**：

- 新建 **`ide-support/vscode/`**（命名理由见 §5.7），包含 `package.json`、`tsconfig.json`、
  `src/extension.ts`、`language-configuration.json`、`syntaxes/yian.tmLanguage.json`、
  `readme.md`、`.gitignore`；
- **对外契约**：语言 id 用 `yian`，`.an` 文件关联 `yian`，激活事件 `onLanguage:yian`；
- **grammar 与 language-configuration 按当前语言编写**：关键字、scope 与注释/括号规则以
  `compiler/frontend/lex/` 的实际词法和 `docs/grammar/` 的关键字清单为准；
- **删除 `ide-support/yian-language-support-0.0.10.vsix`**；git 历史里仍可追溯到它；
- readme 写清构建、调试（F5）、打包与安装步骤；
- **`.gitignore`**：仓库根增加 `ide-support/vscode/node_modules/`，并把 `*.vsix` 改为按模式忽略；
  扩展目录内再放一份局部 `.gitignore`（`node_modules/`、`out/`、`*.vsix`）；
- 构建并生成 VSIX（版本号从 `0.1.0` 起），安装到本地 VS Code。

**P1b 编辑行为完善**：

- 最小 snippets、括号匹配、注释切换与折叠配置；
- 语法高亮样例与 scope 检查方法（用 VS Code 的 token/scope 检查工具人工确认）。

**实际使用清单**：

- 干净环境按 readme 能构建出 VSIX，安装后打开 `.an` 文件语言模式自动为 YIAN；
- `code --list-extensions --show-versions` 显示版本 ≥ 0.1.0；
- 注释切换、括号匹配、自动闭合、基础高亮、折叠可用；
- 高亮覆盖当前语言的关键字集合（对照 `docs/grammar/` 人工抽查）；
- 修改 grammar 后可重复构建，不依赖开发者机器上的隐藏文件；
- `git status` 不出现 `node_modules/`、`out/` 或 `*.vsix`；
- 编译器三套件结果不变。

### P2：编译器分析接口

**目标**：把 `compiler/` 改造成 IDE 友好（§5.8.2 的第 1、2、4、8 条），并抽出可被编辑器重复调用的
分析会话。**不新写前端，不引入增量引擎。**

**先做技术验证（spike）**：给 `CharStream` / `Lexer` 加文本输入，打通
"未保存文本 → 结构化诊断"的最短路径，量清需要改动哪些数据模型（`Symbol` 的 span、位置与
uri 转换、解析器错误处理），产出一份结论再定 P2 的完整范围。

**工作内容**（括号内为 §5.8.2 清单编号）：

- 设计与协议无关的分析会话：`DocumentStore`、`AnalysisSession`、`AnalysisSnapshot`、诊断；
  **落在 `compiler/` 内**（§5.2），不新建顶层包；会话要在**长驻进程里可反复调用**，因为 `lsp/`
  会在自己的进程内经库调用使用它，而不是为每个请求起子进程（§5.10）（第 7 条的开头）；
- 输入为「文档标识 + 文本 + 版本号」，不要求先保存到磁盘（第 1、6 条）；
- 只运行 §5.12 定义的**流水线前缀**：Lexer、Parser、去糖、导入解析、名称解析与类型分析，
  **不启动 LLVM / clang**，也不跑 comptime 特化/闭包下降/确定赋值/CFG；
- 位置与 URI 转换层（§5.1，第 6 条）；
- 结构化诊断：严重级别、消息、范围、错误码；错误返回结构化结果，不再依赖终端输出（第 8 条）；
- 给命令行加一个结构化出口（如 `yianc --analyze --json`），作为分析会话的第一个消费者与调试、
  验收手段（接入方式已定为 LSP，见 §5.9，所以它不再承担接入职责）；
- **去掉 `__print_source_error` 的 traceback + stdout + `sys.exit(-1)` 路径**，让 CLI 与分析
  接口共用同一套诊断渲染，并让渲染接受内存文本而不是再读盘，并对齐 `docs/grammar/16` 的约定
  （stderr、退出码只区分成功/失败）（第 1、8 条）；
- **建立“分析路径不写 stdout”的硬约束**（§5.10）：stdio 传输下 stdout 是 JSON-RPC 通道，
  `compiler/` 与 `anx/` 里任何 `print()` 都会破坏协议。日志改走 stderr（由扩展收进 Output 通道）
  或文件；在 P2 就要把这条查干净，不能留到 P3 接服务器时才发现；
- 错误恢复按 §5.11 执行：P2 只做**第一层（捕获并结构化）**与**第二层的"文件在构造中途结束"**
  这一种容错；多诊断与其余容错形状留给 P3/P4 的固定清单。224 个抛出点的改造允许分批完成、
  分阶段验收，但每一批都要保持三套件全绿（第 2 条）。

**实际使用清单**：

- 对一段未保存文本能在不生成可执行文件的前提下得到分析结果；
- 输入缺少括号、缺少分号、正在输入标识符等半成品代码时不会退出或崩溃，且返回可用的部分结果；
- 同一个错误稳定映射到编辑器正确的行和列；含非 ASCII 注释或字符串时后续位置不偏移；
- 分析失败时得到结构化结果，而不是终端文本；
- 分析路径不向 stdout 写任何内容（stdout 上只有被显式请求的结构化出口）；
- 编译器三套件全绿；`yianc` 直接报错时不输出 Python traceback。

### P3：工作区、符号索引与快照

**目标**：让编辑器理解"当前打开的是一个项目"，而不是孤立地分析单个文件；并把 `lsp/` 建起来
（LSP：`initialize`、`didOpen`/`didChange`/`didClose`、`workspace/didChangeWatchedFiles`）。

**工作内容**：

- 项目发现与依赖解析**直接调用 `anx`**：`Project.discover` / `load` / `files` /
  `dependencies` / `resolve_import` / `describe`，标准库根用 `resolve_stdlib_root`；
- 建立文件、模块、导入边、声明、作用域、符号之间的索引；为函数、结构体、枚举、trait、
  类型别名、变量、字段、方法、枚举成员记录**声明位置**（`Symbol` 补 span 是这里的核心改动，
  §5.8.2 第 5 条）；
- 记录导入别名，使"引用 → 符号 → 声明位置"在别名场景下也成立；
- **不可变快照 + 失效规则**（§5.8.2 第 7 条）：分析结果封装为带版本号的快照，编辑后重建快照、
  丢弃旧快照，而不是在原对象上打补丁。失效粒度至少区分当前文件、直接依赖者
  （用 `Project.dependencies` 反查）两类；
- 请求取消与旧结果丢弃机制；
- 索引同样落在 Python 侧（`compiler/`，复用 §5.2 的会话），**不在扩展里建索引**；
- 不做 clangd 那样的 stub/index 式跨文件补丁（§5.8.3）：一次分析全量文件即可；
- **创建顶层 `lsp/` 包**（§5.7、§5.9）并同步 `pyproject.toml` 的三处配置；`lsp/` 只做协议适配
  与进程管理，索引与项目模型仍从 `compiler/` / `anx/` 复用（§5.10 的进程内库调用）；
- **按 §5.10 的进程形态接线**：`yian-lsp` 作为 stdio 服务器入口；`initialize` 时声明
  `textDocumentSync = Full`（配合 §5.3 的全量重分析）、以及本阶段实际实现的能力；stdout 只走
  JSON-RPC，日志走 stderr；支持 `$/cancelRequest`；
- 扩展侧接入 `vscode-languageclient`，用 stdio transport 启动 `yian-lsp`，把"启动/停止语言服务器
  + 转发文档事件 + 把 stderr 收进 Output 通道"跑通；服务器启动命令与分发方式按 §6.1（P2/P3 先
  依赖本机 Python）。

**实际使用清单**：

- 一个文件能引用另一个文件中的公开函数、类型与结构体字段；
- 标准库与路径依赖能被定位；
- 导入不存在、导入私有符号、循环依赖能给出可定位诊断；
- 修改被依赖文件后，依赖它的文件重新分析，无需重启服务；
- 未被 `main` 调用的库函数也在索引中；
- 重复打开同一项目不产生重复符号或诊断；
- VS Code 里日志能看到语言服务器已启动，关闭窗口后进程退出。

### P4：实时诊断

**目标**：实现编辑器中最先可感知的语言服务功能（LSP：`textDocument/publishDiagnostics`）。

**工作内容**：

- 接收打开、修改、保存、关闭文档事件；
- 对当前内存版本执行分析，并把诊断发布到对应文档；
- 覆盖词法、语法、未定义符号、重复定义、导入错误、类型不匹配和成员不存在等类别；
- 新版本分析完成后清理旧版本诊断；加入防抖、版本号检查与旧请求取消（§5.8.2 第 9 条）；
- 诊断保留错误码与可选的相关位置，为后续代码操作做准备；
- **按 §5.11 第三层引入多诊断**：粒度定在顶层定义级，先引入显式错误类型压级联，再让类型检查器
  继续（§5.8.2 第 3 条）。

**实际使用清单**：

- 打开含错误的文件出现波浪线与 Problems 面板条目，修复后消失；
- 示例工程里故意写坏的那个文件上**至少给出 3 条诊断**（说明多诊断确实生效），且**不超过约 20 条**
  （说明显式错误类型确实压住了级联刷屏）；
- 边打字边出现的诊断不闪烁：构造中途的输入（未闭合括号、半个标识符、悬空运算符）不产生整片红；
- 连续快速输入时不会出现旧诊断覆盖新诊断；
- 关闭或删除文件后不保留孤立诊断；
- 诊断过程不调用 LLVM、clang 或链接器；
- 示例工程上的响应时间达到 §5.12 的 100ms / 200ms 阈值。

### P5：导航与类型查看

**目标**：使符号能够在项目中被可靠地查找和解释（LSP：`definition`、`documentSymbol`、`hover`，
以及可选的 `workspace/symbol`）。

**工作内容**：

- 实现跳转定义：局部变量、参数、函数、类型、字段、方法、枚举成员和导入符号；
- 实现文档符号：列出当前文件的顶层定义和嵌套成员；
- 视性能与项目规模决定是否实现工作区符号搜索；
- 实现悬停信息：至少显示符号种类、完整名称和解析后的类型/函数签名；
- 对标准库符号标记来源，必要时提供源文件位置；
- 解析失败、重载歧义、不完整输入时返回空结果或降级结果，不误跳到无关位置。

**实际使用清单**：

- 声明与引用两侧"跳转到定义"得到同一个位置；
- 跨文件、跨包、标准库符号的跳转正确；
- 方法调用跳到方法本身，而不是只跳到接收者类型；
- 文档符号树与源码结构一致；
- 悬停类型来自分析结果而非字符串猜测；
- 删除或重命名定义后，旧位置不再作为有效结果返回。

### P6：代码补全与语义高亮

**目标**：在可靠符号表与类型分析的基础上提供上下文补全与准确着色（LSP：`completion`、
`signatureHelp`、`semanticTokens`）。

**工作内容**：

- 普通标识符补全：当前作用域、外层作用域、模块公开符号和预定义类型；
- 成员补全：根据 `.` 左侧表达式的类型列出字段与方法；
- 类型位置补全、函数调用参数提示、导入路径/名称补全；
- 补全项提供种类、详细类型、文档、插入文本与必要的参数占位符；
- 去除同名重复项，处理局部符号遮蔽；
- 根据符号索引产生语义 token，区分类型、函数、方法、变量、参数、字段、枚举成员、
  声明和标准库符号；语义高亮失败时保留 P1 的基础高亮。

**实际使用清单**：

- 文件开头、函数体内、类型位置、导入语句和成员访问位置分别得到预期候选；
- 补全不包含不可见的私有符号；成员补全与接收者静态类型一致；
- 泛型、指针、切片与别名等场景至少覆盖 P0 功能矩阵中标注支持的类型；
- 未完成表达式时仍返回合理的降级结果；
- 语义 token 范围不越过标识符，也不覆盖字符串与注释；
- 关闭语义高亮后基础 TextMate 高亮仍可用。

### P7：高级编辑能力

**目标**：在符号身份与源码范围可靠之后，提供会修改代码的能力（LSP：`references`、`rename`、
`codeAction`，格式化视评估结论决定是否加 `formatting`）。

**工作内容**：

- 查找引用与当前文件引用高亮；
- 基于符号身份的安全重命名，覆盖跨文件引用和导入别名；
- 格式化策略评估：独立 formatter、复用解析树，或暂不提供；
- 代码操作与快速修复，例如补充导入、删除无效导入、展示相关诊断；
- 无法确认符号身份时拒绝批量修改，而不是按字符串全局替换。

**实际使用清单**：

- 查找引用不会把同名但不同作用域的符号混在一起；
- 重命名后项目内预期引用同步变化，字符串、注释和无关同名变量不被修改；
- 重命名可撤销，修改范围可在编辑器中预览；
- 格式化至少满足幂等性：连续执行两次结果相同；
- 任何代码操作都有对应的正例、歧义例和拒绝例（人工确认）。

### P8：性能、打包与发布

**目标**：把实验性功能整理为其他开发者可以安装和使用的版本。

**工作内容**：

- 为不同规模项目记录初始化、修改后重分析、补全与跳转的耗时和内存；
- **对 §5.12 的四个耗时数下结论**：达标则关闭"是否需要增量"这个问题（§5.12），不达标则按 §5.12 的
  成本阶梯从 a 往 e 升，不跳级；
- 增加日志级别、日志文件与故障排查说明，但不把调试信息显示为普通诊断；
- **确定 `lsp/` 的分发方式**（随 VSIX 打包 Python 运行时、依赖本机 Python、或独立可执行文件，
  按 §6.1 的结论），并据此补齐 `pygls` 与 Python 运行时的安装步骤；
- 增加版本号、变更日志、安装说明、兼容性说明、卸载与回滚说明；
- 发布前做安装冒烟检查（人工）：装 VSIX + 服务器侧依赖，打开示例工程确认功能可用；扩展与服务器
  的版本要能互相匹配（扩展启动的是哪个 `yian-lsp`）。

**实际使用清单**：

- 干净环境按 readme 完成安装并打开示例工程；
- 安装后语言识别、基础高亮、诊断、导航与已承诺的补全功能全部可用；
- 语言服务器异常退出时扩展能记录可读日志，且不会让 VS Code 崩溃；
- 编译器三套件通过；
- 发布版本、源码版本与功能矩阵一致；
- 已知限制与未实现功能在文档中明确列出。

## 8. 阶段完成的统一定义

一个阶段只有同时满足以下条件才算完成：

- 该阶段列出的功能已经实现，或明确记录为不支持；
- §7 中该阶段的"实际使用清单"已由维护者逐条手工确认；
- 没有已知的高优先级数据错误，例如跳转到错误定义、旧诊断残留或重命名误改文本；
- 编译器 `basic` / `safety` / `package` 三套件保持全绿；
- 相关文档、日志与故障排查方式已经补齐（分析接口与 `lsp/` 写进 `docs/manual/`，扩展用法写进
  `ide-support/vscode/readme.md`）；
- 下一阶段所依赖的接口和限制已经记录；
- Git diff 只包含本阶段范围内的修改；`node_modules/`、VSIX 产物、日志与缓存文件不进入提交。

## 9. 参考资料

### 9.1 VS Code / LSP

- [VS Code Syntax Highlight Guide](https://code.visualstudio.com/api/language-extensions/syntax-highlight-guide)
- [VS Code Semantic Highlight Guide](https://code.visualstudio.com/api/language-extensions/semantic-highlight-guide)
- [VS Code Language Server Extension Guide](https://code.visualstudio.com/api/language-extensions/language-server-extension-guide)
- [VS Code Programmatic Language Features](https://code.visualstudio.com/api/language-extensions/programmatic-language-features)
- [Language Server Protocol Specification](https://microsoft.github.io/language-server-protocol/specifications/lsp/3.17/specification/)

### 9.2 语言服务器如何复用编译器（§5.8 的依据）

按路线分组，全部为官方文档或项目仓库内文档：

- 路线 A（编译器即平台）
  - Roslyn：[.NET Compiler Platform Overview](https://github.com/dotnet/roslyn/blob/main/docs/wiki/Roslyn-Overview.md)
    —— 错误恢复（`IsMissing` / `SkippedTokens`）、语法树的三条属性、`Span`/`FullSpan`、
    Workspace/Solution/Document 快照，以及"语言服务是**用公开编译器 API 重写**的"这一声明。
  - TypeScript：[Using the Compiler API](https://github.com/microsoft/TypeScript/wiki/Using-the-Compiler-API)
    —— `Program`/`TypeChecker` 与 `LanguageServiceHost`（`ScriptSnapshot`、`version`、`isOpen`）、
    `DocumentRegistry`/`BuilderProgram`。
  - Pyright：[Internals](https://github.com/microsoft/pyright/blob/main/docs/internals.md)
    —— CLI / LSP / TSP 三个前端共用一个 `parser` + `analyzer`。
  - gopls：[Design](https://github.com/golang/tools/blob/master/gopls/doc/design/design.md)、
    [Implementation](https://github.com/golang/tools/blob/master/gopls/doc/design/implementation.md)
    —— 复用 `go/parser`、`go/types`、`go/packages`；`cache`/`Snapshot`；`parsego` 的 tree repair；
    `protocol.Mapper` 的坐标换算；"标准库不为错误与增量设计"的自陈；100ms/200ms 延迟阈值。
  - HLS / ghcide：[ghcide 组件文档](https://haskell-language-server.readthedocs.io/en/latest/components/ghcide.html)
    —— GHC 被改成"接受 string buffer 而不是文件"。
- 路线 B（批编译器 + 快照）
  - clangd：[Design](https://clangd.llvm.org/design/)、[Code walkthrough](https://clangd.llvm.org/design/code)、
    [Threads & requests](https://clangd.llvm.org/design/threads)、
    [ParsedAST.h](https://github.com/llvm/llvm-project/blob/main/clang-tools-extra/clangd/ParsedAST.h)
    —— preamble 快照、ASTWorker、写去抖、补全另走 completion API、index 的存在理由。
  - Merlin：[A Language Server for OCaml (Experience Report)](https://arxiv.org/abs/1807.06702)
    —— 改造 OCamllex/Menhir 以支持增量化、不完整与错误处理（即 §5.8.1 的路线 E）。
- 路线 D（独立前端）与其代价
  - rust-analyzer：[Architecture](https://github.com/rust-lang/rust-analyzer/blob/master/docs/book/src/contributing/architecture.md)
    —— "parsing never fails"、语法树是值类型且不存语义信息、`hir-*` 永不是 API 边界、
    只有 `rust-analyzer` crate 知道 LSP、取消与 `catch_unwind`。
  - [Three Architectures for a Responsive IDE](https://rust-analyzer.github.io/blog/2020/07/20/three-architectures-for-responsive-ide.html)
    —— 三条路线综述；"it's not the incrementality that makes an IDE fast. Rather, it's laziness"；
    路线 B"允许复用既有批编译器，另外两条通常导致编译器重写"。
  - [cfe-dev: [RFC] A C++ pseudo parser for tooling（Sam McCall 回复）](https://lists.llvm.org/pipermail/cfe-dev/2021-November/069325.html)
    —— rust-analyzer 不复用 rustc 的两个原因；**RLS 复用 rustc 却被判定不够快**。
- 同类语言的改造作业单
  - KCL：[#420 A better KCL compiler frontend technology architecture for the LSP tool and IDE extensions](https://github.com/kcl-lang/kcl/issues/420)
    —— 明确要避免"像 rustc 和 rust-analyzer 那样维护两套前端"；错误恢复策略、`(ast, Vec<Error>)`、
    lossless syntax tree、`Symbol` 的 span、100ms 目标。

### 9.3 仓库内文档

- `docs/grammar/16.runtime_errors.md`：诊断与退出码约定
- `docs/anx/index.md`：项目 / 清单 / 依赖 / 导入规则
- `docs/manual/20.anx_overview.md`、`docs/manual/22.anx_project.md`：`Project` 模型与加载器
