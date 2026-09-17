# YIAN IDE / VS Code 支持开发计划

> 跨阶段政策（详见 §5）：**不为 IDE / LSP 建立自动化测试**，功能由实际使用验收，编译器
> `basic` / `safety` / `package` 三套件是不回归底线；**VS Code 扩展与语言服务器分层存放**
> （`ide-support/vscode/` 与顶层 `lsp/`）；**分析能力继续留在 `compiler/`，把它改造成 IDE 友好，
> 不重写语言前端**（§5.8）。

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

`ide-support/` 下只有一个打包产物 `yian-language-support-0.0.10.vsix`，**没有扩展源码树，也没有
构建脚本**。该产物内容陈旧：`activate()` 是空的，没有语言服务器，也没有补全、跳转、悬停或诊断
逻辑，grammar 的关键字集合与当前语言不一致。它**不作为开发基线**，P1 直接重建源码树（§7 P1）。

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
   按"先做到不崩、能返回部分结果"分阶段做，不能一步改成多诊断（§6.3）。
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

# ── P3 之后，且 §6.1 选择 LSP 路线时才需要
# lsp/ 是 Python 包，语言服务器若依赖 pygls：
/home/zhx/miniconda3/bin/python3 -m pip install pygls
# 扩展侧需要 LSP 客户端库：
cd /home/zhx/workspace/yian/ide-support/vscode && npm install vscode-languageclient
```

这些安装都需要联网：`npx --no-install @vscode/vsce` 在本地没有 `@vscode/vsce` 时会失败，
离线环境需要预先准备 `@vscode/vsce` 与扩展的 `node_modules`（内网镜像或离线安装包）。

## 4. 功能分层与阶段总览

下面的阶段是能力依赖关系，不是固定工期。每个阶段完成后才能进入下一阶段；如果实际使用中发现
性能或架构问题，应允许在当前阶段回退或调整技术路线。

| 阶段 | 名称 | 主要交付物 | 阶段门槛 |
| --- | --- | --- | --- |
| P0 | 范围、决策与示例工程 | 功能矩阵、示例工程、位置模型与诊断结构定案、性能指标口径 | "支持什么""怎么算做完"不再有歧义 |
| P1 | 扩展工程 | 扩展源码树；语言注册、基础高亮和编辑行为 | 新环境能构建 VSIX，安装后 `.an` 文件识别与编辑正常 |
| P2 | 编译器前端改造与分析接口 | 文本输入与解析容错（§5.8.2 第 1/2/4/8 条）、面向内存文本的分析会话、位置与 URI 转换、结构化诊断 | 不生成 LLVM/可执行文件也能对未保存文本得到稳定分析结果 |
| P3 | 工作区、符号索引与快照 | 项目发现、依赖解析、符号声明位置、不可变快照与失效规则 | 多文件项目中能定位符号并跟上依赖变化 |
| P4 | 实时诊断 | 词法、语法、导入、名称、类型诊断 | 修改未保存文本后诊断及时更新且不残留旧结果 |
| P5 | 导航与类型查看 | 跳转定义、文档符号、悬停信息 | 本地、跨文件、标准库和成员符号都能验证 |
| P6 | 补全与语义高亮 | 作用域补全、成员补全、导入补全、语义 token | 补全结果与当前作用域和类型一致 |
| P7 | 高级编辑能力 | 查找引用、重命名、格式化、代码操作 | 修改范围准确，不误改无关文本 |
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

打开文件后对当前内存文本做一次完整分析，先保证正确性。只有在 §6.4 的指标无法满足时才考虑
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
- **`ide-support/` 只放某个具体编辑器的专属产物**：客户端目录用 `vscode/`，第二个客户端作为
  并列子目录（`ide-support/neovim/` 等）。P0 的示例工程属于编辑器支持资产，放
  `ide-support/sample/`。
- **`lsp/` 按需创建**：如果 §6.1 最终选择"扩展直接调用 `yianc --analyze --json`"这条路线，就
  不创建 `lsp/`，此时上面的分层照旧成立（客户端依然薄，语言逻辑依然在 Python 侧）。
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

#### 5.8.3 明确不采用的替代方案

- **不重写前端**（路线 D）：见 5.8.1。若将来发现 P2 的改造无法收敛，再重新评估，而不是现在先分叉。
- **不新建 stub/index 式的跨文件索引**（路线 B 的 clangd 部分）：那是 C++ 无法从单个 TU 看到全局
  信息的补丁；YIAN 一次分析整个程序，直接分析全量文件即可（P3 只做索引与失效，不做 stub）。
- **暂不引入 salsa 式细粒度增量**：先按 5.8.2 第 7 条做"不可变快照 + 变更即清缓存"（gopls 的做法），
  只有实测不达标才升级（§6.4）。

## 6. 暂不锁定的技术路线与决策节点

以下决策在拿到原型和实测数据后再确定。

### 6.1 接入方式：VS Code 直接 API 还是语言服务器

分析层已经定案在 `compiler/`（§5.8），这里只决定**接入形态**：

- 直接使用 VS Code 的 `languages.*` API：只支持 VS Code、功能规模小、调试简单；
- 使用 Language Server：复用 Python 编译器、为其他编辑器保留可能性，但需要进程与协议层。

**目录分层不依赖这个选择**（§5.7）：客户端固定在 `ide-support/vscode/`，`lsp/` 是否创建取决于
本节的结论。P1 只要求扩展可维护；P2 先定义与 VS Code 无关的分析接口；P3 之后根据复用成本、
调试难度和实测性能决定最终接入方式。

### 6.2 语言服务器的实现方式与分发

位置已经定在顶层 `lsp/`（§5.7），这里要定的是**进程形态与分发**：

- 进程形态候选：① `lsp/` 作为独立进程，扩展通过 stdio 启动它；② 先用外部命令
  （`yianc` / `anx`）验证功能，再切换为结构化库调用；③ 分析逻辑与协议适配拆成两个模块。
- 分发方式：随 VSIX 打包 Python 运行时、依赖本机 Python 环境（复用已有的 editable 安装）、或提供
  独立可执行文件。这一条会反向影响 P2 的接口形态（进程内 vs 子进程），建议在 P2 开始前先给倾向。

### 6.3 错误恢复与多诊断的时机

编译器每个 pass 都在第一个错误处抛异常（224 个抛出点）。P2 只要求"半成品代码不崩、至少返回一条
结构化诊断、位置正确"，对应 §5.8.2 的第 1、2、4、8 条；"一份文档多个诊断"（第 3 条）留给 P4，
和解析器的错误恢复策略一起做。若过早要求多诊断，P2 会膨胀成重写解析器——那正是 §5.8.1 里
否掉的路线 D。

### 6.4 增量分析与缓存

先做 §5.8.2 第 7 条：不可变快照 + 变更即清缓存（gopls 的做法），此时"失效规则"就是快照边界。
只有当 P8 的实测数据显示全量重分析在小/中型项目上不达标时，才考虑细粒度增量；引入前必须权衡
rust-analyzer 自己承认的代价（"extra complexity, slower performance"），并同时定义失效粒度
（当前文件 / 直接依赖者 / 整个项目）。

## 7. 各阶段详细计划与验收标准

每个阶段的"实际使用清单"由维护者在 VS Code 中手工执行；自动化部分只有编译器三套件不回归。

### P0：范围、决策与示例工程

**目标**：在写代码之前固定支持范围、决策项和最低质量标准。

**工作内容**：

- 功能支持矩阵：P4/P5/P6/P7 的每项功能标注"第一版做 / 明确不做"；
- 建立最小示例工程（`ide-support/sample/`）：主文件 + 辅助模块 + `std` 导入 + 结构体 + 枚举 +
  类型别名 + 泛型 + 方法 + 一个错误文件，且能直接 `anx build` / `anx test` 跑通；
- 定案位置模型（§5.1）与文档标识（uri + 版本号）；
- 定案诊断结构：严重级别、消息、范围、**错误码方案**。目前的码段有三套：项目级
  `AX001`–`AX015`（`anx/diagnostics.py`）、运行时 `S001…`/`R00x`（`docs/grammar/16`）、
  源码级（词法/语法/分析）**没有码**——需要在 P0 给出源码级码段与归属，并写进本节；
- 定义性能指标口径，起点直接采用已被两个项目独立采用的阈值：**跟手打字的反馈 <100ms 才不可
  感知，>200ms 不可接受**（gopls 设计文档；KCL 的 LSP 改造目标同为 100ms，引用见 §9）。
  具体到示例工程上首次分析、修改后重分析、补全与跳转四个数字，P2 完成后按实测冻结；
- 建立问题分类：编译器前端问题、分析模型问题、协议适配问题、扩展打包问题、性能问题。

**实际使用清单**：

- 示例工程能打开、能 `anx build`、能被编辑器识别为 `yian` 语言；
- 功能矩阵里每一项都能指出在示例工程中的对应文件与位置；
- 位置模型、诊断结构、错误码方案三份决定都有书面记录（写进本文件）。

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
  **落在 `compiler/` 内**（§5.2），不新建顶层包，命令行与将来的 `lsp/` 都调它（第 7 条的开头）；
- 输入为「文档标识 + 文本 + 版本号」，不要求先保存到磁盘（第 1、6 条）；
- 只运行 Lexer、Parser、去糖、导入解析、名称解析与类型分析，**不启动 LLVM / clang**；
- 位置与 URI 转换层（§5.1，第 6 条）；
- 结构化诊断：严重级别、消息、范围、错误码；错误返回结构化结果，不再依赖终端输出（第 8 条）；
- 给命令行加一个结构化出口（如 `yianc --analyze --json`），使 §6.1 选"扩展直连 CLI"路线时
  不依赖 `lsp/` 也能工作，同时作为分析会话的第一个消费者与手工验证手段；
- **去掉 `__print_source_error` 的 traceback + stdout + `sys.exit(-1)` 路径**，让 CLI 与分析
  接口共用同一套诊断渲染，并让渲染接受内存文本而不是再读盘，并对齐 `docs/grammar/16` 的约定
  （stderr、退出码只区分成功/失败）（第 1、8 条）；
- 解析器容错改造：先保证"遇到坏输入不抛异常、能返回部分结果 + 错误列表"，具体恢复策略
  （局部恢复、占位节点、保留上一次有效结果或组合）在选型后记入 §6.3（第 2 条）。
  这一步是 224 个抛出点的渐进改造，允许在 P2 期间分批完成、分阶段验收。

**实际使用清单**：

- 对一段未保存文本能在不生成可执行文件的前提下得到分析结果；
- 输入缺少括号、缺少分号、正在输入标识符等半成品代码时不会退出或崩溃，且返回可用的部分结果；
- 同一个错误稳定映射到编辑器正确的行和列；含非 ASCII 注释或字符串时后续位置不偏移；
- 分析失败时得到结构化结果，而不是终端文本；
- 编译器三套件全绿；`yianc` 直接报错时不输出 Python traceback。

### P3：工作区、符号索引与快照

**目标**：让编辑器理解"当前打开的是一个项目"，而不是孤立地分析单个文件。

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
- 若此时已确定 §6.1 走 LSP：创建顶层 `lsp/` 包并同步 `pyproject.toml` 的三处配置（§5.7），
  `lsp/` 只做协议适配与进程管理，索引与项目模型仍从 `compiler/` / `anx/` 复用。

**实际使用清单**：

- 一个文件能引用另一个文件中的公开函数、类型与结构体字段；
- 标准库与路径依赖能被定位；
- 导入不存在、导入私有符号、循环依赖能给出可定位诊断；
- 修改被依赖文件后，依赖它的文件重新分析，无需重启服务；
- 未被 `main` 调用的库函数也在索引中；
- 重复打开同一项目不产生重复符号或诊断。

### P4：实时诊断

**目标**：实现编辑器中最先可感知的语言服务功能。

**工作内容**：

- 接收打开、修改、保存、关闭文档事件；
- 对当前内存版本执行分析，并把诊断发布到对应文档；
- 覆盖词法、语法、未定义符号、重复定义、导入错误、类型不匹配和成员不存在等类别；
- 新版本分析完成后清理旧版本诊断；加入防抖、版本号检查与旧请求取消（§5.8.2 第 9 条）；
- 诊断保留错误码与可选的相关位置，为后续代码操作做准备；
- 按 §6.3 的结论决定是否在本阶段引入多诊断（§5.8.2 第 3 条）。

**实际使用清单**：

- 打开含错误的文件出现波浪线与 Problems 面板条目，修复后消失；
- 连续快速输入时不会出现旧诊断覆盖新诊断；
- 关闭或删除文件后不保留孤立诊断；
- 诊断过程不调用 LLVM、clang 或链接器；
- 示例工程上的响应时间达到 P0 冻结的目标指标。

### P5：导航与类型查看

**目标**：使符号能够在项目中被可靠地查找和解释。

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

**目标**：在可靠符号表与类型分析的基础上提供上下文补全与准确着色。

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

**目标**：在符号身份与源码范围可靠之后，提供会修改代码的能力。

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
- **对 P0 冻结的指标下结论**：达标则关闭"是否需要增量"这个问题（§6.4），不达标则先做快照粒度
  的优化，再评估细粒度增量；
- 增加日志级别、日志文件与故障排查说明，但不把调试信息显示为普通诊断；
- 若已创建 `lsp/`：确定其分发方式（随 VSIX 打包、依赖本机 Python、或独立可执行文件），
  并据此补齐安装步骤；若未创建，则说明扩展依赖的 `yianc` / `anx` 版本要求；
- 增加版本号、变更日志、安装说明、兼容性说明、卸载与回滚说明；
- 发布前运行编译器三套件与 VSIX 安装冒烟检查（人工）。

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
