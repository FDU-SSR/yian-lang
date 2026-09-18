# YIAN 格式化器计划

## 1. 目标与范围

### 1.1 目标

给 `.an` 源码一个**确定性**的排版器：同一份输入在任何时候、任何入口下都产生同样的输出。三个入口共用
同一个引擎：

- `yianc --format`：单文件或目录，打到 stdout 或原地写回；
- `anx fmt`：按包的 `Project.files` 格式化项目自己的源码；
- 语言服务器的 `textDocument/formatting`：格式化编辑器里的当前缓冲区（含未保存内容）。

确定性意味着两件事：**幂等**（对已格式化的代码再跑一次不产生变化）与**保守**（只改空白与注释的位置，
不改任何 token）。

### 1.2 第一版不做

- **自动折行**：语料 p99 行长 83 列、超过 100 列的只有 25 行（见 §2），折行规则复杂而收益小。
- **列对齐**（结构体字段、行尾注释）：会把无关行的改动绑在一起，diff 噪音大。
- **导入排序、增删括号**：属于改写语义层面的风格决策，第一版只做保内容的排版。
- **range formatting / on-type formatting**：整文件格式化稳定之后再谈。
- **格式化语法错误的文件**：lex 或 parse 失败时拒绝（见 §3.3 第 5 条），不在残缺树上冒险。

## 2. 现状与实测

以下数字来自 `lib/src`、`tests/basic`、`ide-support/sample`（579 个 `.an` 文件、19607 行），后续验收
以同一批语料为准。

| 事实 | 数值 | 对计划的影响 |
| --- | --- | --- |
| 行长 | 中位 22 / p90 61 / p99 83 / 最长 147；>100 列 25 行，>120 列 4 行 | 第一版不折行（§1.2） |
| 注释 | 行注释约 1900 处，独立成行与行尾各约一半；块注释 0 处（语言允许） | 注释守恒是主要风险点；语料里没有块注释，但仍须支持 |
| 缩进 | 除 7 行故意怪缩进的测试用例外全是 4 空格倍数；无制表符 | 缩进规范直接取 4 空格 |
| 块 | 1929 行以 `}` 收尾；335 行是单行 `{ ... }`（如 `impl Clone for i8  { fn clone() -> Self { *self } }`） | 单行块保持单行，只规范内部空格 |
| 条件括号 | `if` 170 处无括号、28 处带括号；`while` 全部无括号 | 风格不统一 → 保留作者写的括号，不增删 |
| AST | `ast.py` 59 个节点类，`ast_type.py` 18 个；不保留注释与空白 | 不写 AST 打印器（§3.1） |
| 词法 | lexer 直接跳过空白与注释，不产出 token；字符串整体是一个 token | 注释可从 token 间隙恢复（§3.2） |
| f-string | `FStrStart`/`FStrLiteral`/`FStrExprBegin`/`FStrExprEnd`/`FStrEnd`；`FStrExprBegin` 零宽，`{` 落在 token 间隙 | f-string 内部第一版按原文保留（§3.2） |
| 编辑器侧 | pygls 从注册的 feature 自动广告 `documentFormattingProvider`；扩展已给 `[yian]` 设 4 空格 | 接入只需服务器注册 handler，扩展无需改动 |

## 3. 已确定的决策

### 3.1 架构：token 布局重排 + AST 角色表

**决定：以词法 token 流为唯一内容来源，重排空白与注释位置。**

（实现时确认：不需要 AST 角色表。词法器已按"前有无空白"把 `<`/`>` 分成 `LAngle`/`RAngle` 与
`Less`/`Greater`，后置指针、字段初始化与闭包 `|` 都能由邻接 token 判定，因此格式化器不读 AST；
"语法树不变"由 token 守恒间接保证——语法分析是 token 的函数。）

不写 AST 打印器的理由：AST 有 77 个节点类，打印器要自己决定括号保留、表达式结合顺序、类型写法等
语义层面的取舍，任何一处判断错都会静默改程序。token 重排的保证更强也更简单——**内容守恒**：除空白与
注释位置外，非 trivia token 的序列逐字不变。代价是排版质量依赖规则表而非语法树（例如无法做"按语法
单位折行"），这正是第一版不需要的。

AST 的角色表只回答几个问题，由节点 span → token 位置映射得到：

- 某个 `{` 是**块**（函数体、控制流、match 臂）还是**载荷**（结构体、枚举变体、结构体构造）；
- 顶层项（`fn`/`struct`/`enum`/`trait`/`impl`/`typedef`/`import`）的边界，用于空行与注释归属；
- 语句边界，用于保证一条语句一行。

### 3.2 trivia 恢复：不动 lexer

**决定：注释与空行从 token 间隙扫描得到，词法器与语法器完全不改。**

两个相邻 token 之间只可能出现空白与注释（字符串与字符字面量各自是一个 token，其内容不会出现在间隙
里），所以扫描间隙就能恢复全部注释：`//` 到行尾、`/* */` 到闭合。空行数由前后 token 的行号差得到，
不需要额外的换行 token。

唯一例外是 **f-string 内部**：`FStrExprBegin` 是零宽的，插值的 `{` 落在间隙里，按"间隙只有空白与注释"
的假设会丢字符。因此第一版把整段 f-string（`FStrStart` 到 `FStrEnd`）当作**不透明区域逐字保留**，内部
的表达式排版留到后续阶段。

这样做的直接好处：编译器流水线（lexer/parser/类型检查/代码生成）一行不改，格式化器是纯粹的消费者，
零回归风险。

### 3.3 保证（每条都有对应验收）

1. **token 守恒**：格式化前后的非 trivia token 序列逐字相同（含括号与分号）。
2. **幂等**：`format(format(x)) == format(x)`。
3. **AST 等价**：格式化后的文本重新分析得到同一棵树（忽略 span）且不产生新诊断。
4. **注释守恒**：注释条数与文本逐条相同，独占一行/行尾的归属不变。
5. **失败即不改**：lex 或 parse 失败时返回"无格式化结果"，不输出半成品；编辑器侧表现为不做任何修改。

### 3.4 风格规范

规范以语料为准，逐条对应 §2 的实测：

| 规则 | 内容 |
| --- | --- |
| 缩进 | 4 空格一层，不用制表符 |
| 换行 | 语句/声明结束换行；`{` 与所属行同行；`}` 单独一行（单行块除外） |
| 单行块 | 源里写在一行的 `{ ... }` 保持单行，只规范内部空格 |
| 分号 | `;` 紧贴前一 token；其后换行（同一行的下一 token 前不断行） |
| 逗号 | 前无空格、后一个空格；作者的尾随逗号保留 |
| 冒号 | 类型标注 `name: T`：前无空格、后一个空格 |
| 调用/声明括号 | 函数名与 `(` 之间无空格；括号内两侧无空格 |
| 运算符 | 二元运算符与 `->`、`=` 两侧各一个空格；一元运算符与操作数紧贴 |
| 泛型 | `<` `>` 内侧不空格，`, ` 分隔参数；`Pair<Meters>` |
| 字段初始化 | 构造式里 `name=value` 紧贴；`Point(x=1.0, y=2.0)` |
| 闭包 | `|` 与参数列表紧贴；`|x_in = x| (y: i32) -> i32 { … }` |
| 属性 | `@Name` 后一个空格；`@sizeof(x)` 括号内不空格 |
| 空行 | 最多保留 1 个连续空行；不新增空行 |
| 注释 | 文本原样保留，只去掉每行行尾空白；独占一行的注释按当前缩进对齐；行尾注释前至少一个空格 |
| 条件括号 | 保留作者写法（`if (x)` 与 `if x` 都不动） |

### 3.5 落位与接入

- **引擎**：`compiler/format/`
  - `trivia.py`：间隙扫描（注释与空行）；
  - `layout.py`：token 布局（空格表、缩进、换行、空行、注释放置）；
  - `roles.py`：AST → token 角色表；
  - `formatter.py`：公开入口 `format_text(text, *, path=None) -> str | None`（失败返回 `None`）；
  - `cases.py` 与 `fixtures/`：格式化器**自带的固定用例与运行入口**（见下）。
- **固定用例由格式化器自己管理**，不放进 `tests/`、不挂 `scripts/run_tests.py`：`compiler/format/fixtures/`
  下按主题分目录（缩进与块、注释归属、f-string、泛型、单行块、空行），每个用例是 `case.an` 加期望的
  `case.expected.an`；`compiler/format/__main__.py` 提供三个自管理入口——
  `python3 -m compiler.format --check <路径…>`（只报告是否需要格式化）、
  `python3 -m compiler.format --cases`（跑自带用例）、
  `python3 -m compiler.format --bless`（用当前实现重新生成期望输出，人工 review 后提交）。
  用例的增删改都在格式化器目录内完成，与编译器的测试约定无关。用例是开发期资产，editable 安装下
  直接从仓库读取；将来若要随发行版分发，再在 `pyproject.toml` 的 `package-data` 里加一条。
- **`yianc --format [-w] [-t none] <路径…>`**：默认把结果打到 stdout（单文件）；`-w` 原地写回；
  同时给 `--check`（只报告"是否需要格式化"与退出码，给脚本用）。
- **`anx fmt [--check]`**：走 `Project.files`，只格式化包自己的源码，不动 `lib/src`。
- **LSP**：`lsp/formatting.py` + `textDocument/formatting` handler，用打开文档的覆盖层文本；返回一个
  覆盖全文的 `TextEdit`。不注册 range/on-type。
- **扩展**：无需改动；readme 说明可用 `editor.formatOnSave`，并写明"格式化只改空白与注释位置"。
- **分层**：格式化器是语言工具、与协议无关，住在 `compiler/format/`；`lsp/` 只做协议形状转换——与
  `compiler/analysis/` 同一条依赖方向（`lsp/ → compiler/`）。

## 4. 阶段与验收

### P0：规范、骨架与语料扫描

**工作内容**：落定 §3 的决策与风格表；建 `compiler/format/` 骨架、`format_text` 接口与 `__main__.py`
的三个入口（`--check` / `--cases` / `--bless`）；写一个**语料扫描脚本**（统计行长、注释归属、缩进、
单行块等分布），作为后续阶段对比"改动是否只发生在预期位置"的工具。

**验收**：`format_text` 对已格式化文本返回原文（占位实现）；`--cases` 能跑空用例集并返回成功；
扫描脚本能复现 §2 的数字。

### P1：trivia 与布局引擎

**工作内容**：间隙扫描（行注释、块注释、空行）；空格表；缩进与换行规则；单行块保持。

**验收**：语料 579 个文件全部满足 token 守恒与幂等；块注释与行尾注释的人工样例通过。

### P2：角色表与结构规则

**工作内容**：AST → token 角色表（块/载荷、顶层项、语句）；空行策略与注释放置；f-string 段落逐字保留；
`match` 臂与 `impl`/`trait` 成员的规则；用 `--bless` 建起第一批自带用例（缩进与块、注释归属、
f-string、单行块、空行）。

**验收**：`--cases` 全部通过；token 守恒、幂等、AST 等价、注释守恒四条全部通过，另加一条
**邻接空格**检查（词法器不敏感的位置，token 守恒看不见）；`lib/src` 与 `tests` 的格式化 diff 人工过一遍，
确认只有空白与注释位置变化；编译器三套件（basic/safety/package）保持全绿。

### P3：命令行与编辑器接入

**工作内容**：`yianc --format [-w] [--check]`、`anx fmt [--check]`、LSP `textDocument/formatting`；
readme 与手册补章节。

**验收**：三个入口对同一文件产生字节相同的结果；编辑器里格式化未保存缓冲区生效且可撤销（一次
`TextEdit`）；示例工程 `ide-support/sample` 实测；`ide-support/sample-errors`（有语法错误）被拒绝且不做修改。

### P4：收尾

**工作内容**：已知限制写进手册（不折行、不对齐、不格式化坏文件、f-string 内部不动）；`--check` 的退出码
约定；性能数字（579 文件的批量耗时与单文件延迟）记录。

### 需要时再单独立项

折行与列对齐、range formatting、on-type formatting、f-string 内部重排、导入排序。

## 5. 验收方式

- **语料级（主要）**：对 579 个 `.an` 文件逐个检查 §3.3 的四条保证；批量格式化后再编译，三套件全绿。
- **自带用例**：`python3 -m compiler.format --cases` 跑 `compiler/format/fixtures/` 下的输入/期望对，
  覆盖语料里少见或没有的形态（块注释、f-string、条件括号、单行块、空行与注释归属）。
- **人工**：`ide-support/sample`（含 f-string、泛型、条件实现、指针）的观感；注释归属是否正确；
  `git diff` 通读一遍，确认没有非空白的改动。

## 6. 参考

- [gofmt](https://pkg.go.dev/cmd/gofmt)：AST + 注释、幂等、不折行的取舍依据。
- [clang-format](https://clang.llvm.org/docs/ClangFormat.html)：token 布局与可配置项的分类方式。
- [rustfmt](https://rust-lang.github.io/rustfmt/)：折行与默认风格的关系（本计划把它排除在第一版之外）。
- [EditorConfig](https://editorconfig.org/)：缩进类约定的通用表述。
- 仓库内相关代码：`compiler/frontend/lex/`、`compiler/frontend/parse/`、`compiler/analysis/positions.py`、
  `compiler/main.py`、`anx/main.py`、`lsp/server.py`。
