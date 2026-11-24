# yianc 编码规范

本规范适用于此项目中的 YIAN 代码, 它补充命名、模块边界、所有权和错误处理约定.

## 1. 文件与模块

### 1.1 按职责放置代码

`yianc/src/` 的目录边界对应编译阶段和依赖方向：

| 目录           | 职责                                                  |
| -------------- | ----------------------------------------------------- |
| `frontend/`    | 源码会话、词法、语法、AST、去糖、遍历与转储 |
| `analysis/`    | 权限分类、解析、类型、符号、HIR、常量求值、lowering、检查与转储 |
| `codegen/`     | CFG、LLVM lowering、指针安全检查、原生链接与转储      |
| `telemetry/`   | 日志、计时与 profile 记录                             |
| `common/`      | 会话内 ID、源码位置等不依赖分析或 codegen 的基础类型  |
| `diagnostics/` | 诊断模型、渲染和 stderr 输出                          |
| `package/`     | 包图校验与来源信任，不依赖 AST 或 HIR                 |
| `host.an`、`roots.an` | 主机资源定位、标准库源码根与运行时资源根       |
| `preprocess.an`、`input_error.an` | 输入加载/收集与共享输入诊断            |

依赖方向从底层到上层：

```text
common → frontend → analysis → codegen
host/roots、telemetry、diagnostics 与 package 可被需要它的阶段使用
```

`common` 不依赖 `analysis`，`package/` 不依赖 AST/HIR，诊断模型不依赖渲染。
每个阶段的 `driver.an` 是阶段组合根，阶段内部模块不导入它；`main.an` 是唯一组合三个阶段驱动的入口。
新增模块先确定它属于哪个阶段；不要为了复用把阶段专属类型放进 `common`。

### 1.2 一个文件一个主要职责

文件名描述职责，不用 `utils.an`、`helpers.an` 或 `misc.an` 收纳无关逻辑。
同一目录的文件按编译顺序和概念分组，例如 `analysis/lowering/` 按表达式类别拆分。

一个文件中先放 import，再放常量和类型，再放实现：

```yian
from yianc.analysis.session.session import AnalysisSession;
from yianc.common.ids import HirId, TypeId;
from std.core.iter import Iterator, IntoIterator;

const INITIAL_CAPACITY: u64 = 16;

pub struct AssignmentChecker { ... }

impl AssignmentChecker { ... }

impl Drop for AssignmentChecker { ... }
```

- 所有 `from ... import ...;` 放在文件顶部，每个 import 语句指定一个来源路径；过长的符号列表可以续行。
- import 按概念分组，不要求字母序；新增代码通常先列 `yianc.*`，再列 `std.*` 和 `bootstrap` 包，同一文件内保持顺序一致。格式化器不排序 import。
- 不使用的 import 立即删除，不保留“以后可能用到”的导入。
- 不使用统一的文件头模板；需要说明模块契约时直接写注释，不写 `Expected output` 一类的测试头。

### 1.3 公开边界

- 只有跨模块使用的声明加 `pub`；模块内部实现保持默认可见性。
- 导出类型时同时导出调用者需要的构造函数和查询方法；不要让调用者直接修改内部字段来绕过不变量。
- 修改 `pub` 签名、字段或语义时，同步更新 `yianc/docs/` 中的对应章节和调用者。
- 一个类型通常只有一个 inherent `impl` 块；按“构造 → 查询 → 修改”分组。确有清晰边界时可以拆成多个
  `impl Type` 块，例如 `package/model.an` 把根信息读取和包查询分开。
- `impl Drop for T` 放在该类型的 inherent `impl` 之后、下一个类型之前；trait impl 与 inherent impl
  分开，不混在同一块。
- 不使用横幅注释（`// ====`）分隔 impl 或方法组；类型声明、`impl` 块和空行已经表达分组。

## 2. 命名

| 类别                       | 风格                          | 示例                                             |
| -------------------------- | ----------------------------- | ------------------------------------------------ |
| struct、enum、trait        | `PascalCase`                  | `TypeStore`、`AnalysisSession`、`UserDiagnostic` |
| enum 变体                  | `PascalCase`                  | `Source`、`NotAType`、`TypeMismatch`             |
| 函数、方法、字段、局部变量 | `snake_case`                  | `parse_program`、`source_root`、`deferred`       |
| 常量                       | `UPPER_SNAKE_CASE`            | `VOID_TYPE_ID`、`EXIT_FAILURE`                   |
| 类型参数                   | 单个大写字母或短 `PascalCase` | `T`、`E`、`K`、`V`                               |
| 会话内 ID                  | `PascalCase` + `Id` 后缀      | `UnitId`、`TypeId`、`HirId`、`BlockId`           |

- 缩写按词处理，写 `HirId`、`CfgModule`、`LlvmLowering`、`AstArena`；已有名称如 `NativeLLVM`
  保持原样，不为统一大写而批量重命名。
- 错误类型参数通常用 `E`，值类型参数用 `T`，容器键值用 `K`、`V`。
- 布尔字段和查询使用肯定式名称，例如 `reachable`、`is_entry`、`has_generic`；避免 `not_ready` 一类
  需要双重否定的名称。
- 主构造函数命名为 `new`；其他构造入口使用 `from_*` 或明确的语义动词（如 `target`、`unsupported`、
  `with_max_depth`），不要用无意义的 `create` 或 `make`。
- 诊断码和消息属于用户可见契约，不按命名风格改写；诊断码定义集中在 `DiagnosticCode`。

## 3. 类型声明

### 3.1 struct

多字段 struct 通常每行一个字段，字段按构造/语义顺序排列：

```yian
pub struct AnalysisOutput {
    pub checked: Vec<DefId>,
    pub procedures: Vec<AnalyzedProcedure>,
    pub entry: Option<TypeId>,
}
```

字段少且结构紧凑时允许单行；格式化器会保留作者写下的单行形式：

```yian
struct Place {
    parent: Option<u64>, type_id: TypeId
}
```

- `Drop` 按资源依赖顺序释放：先释放字段指向的载荷和嵌套记录，再释放容器存储。
- 非拥有引用在字段上方用注释说明有效期，例如：

```yian
pub struct AnalysisOptions {
    // The caller keeps this provider alive for the entire synchronous analysis call.
    pub sizes: Option<TypeSizeProvider&>,
    pub trace: Option<CompilerTrace&>,
}
```

### 3.2 enum 与 trait

- enum 变体按语义顺序排列；短小且无载荷的变体可以放在一行，带字段或需要注释的变体每行一个。
- 模式匹配覆盖所有变体；确实只关心部分变体时显式写 `_ => {}`，不要依赖隐式忽略。
- trait 的方法先声明必需方法，再提供默认方法。默认实现只在语义确实通用时添加。
- 阶段错误按变体携带足够的位置和消息；需要分类时增加枚举变体，不用消息字符串保存错误类别。

## 4. 函数与控制流

### 4.1 函数形状

- 函数参数和返回类型写完整类型，不用推断替代公开签名。
- 前置条件和快速失败放在函数开头，使用 guard clause 减少嵌套：

```yian
pub fn runtime_root(config: CompilerConfig&) -> Result<PathBuf, InputError> {
    if config.root_explicit { return normalize_path(config.compiler_root.as_str()); }
    let root = associated_root()?;
    defer root.drop();
    normalize_path(root.as_str())
}
```

- 小函数和单语句分支允许写在同一行：

```yian
fn finish_profile(trace: CompilerTrace&, pass_id: str, success: bool) {
    if success { trace.finish(pass_id); } else { trace.fail(pass_id); }
}
```

- 逻辑块之间留一个空行；紧密相关的连续语句不插空行。
- 条件不写 C 风格括号：`if ready { ... }`，不是 `if (ready) { ... }`。
- `match` 分支按变体顺序排列，短分支写在同一行，长分支使用块：

```yian
match result {
    Ok(value) => value,
    Err(problem) => {
        report(&problem, sources);
        problem.drop();
        return EXIT_FAILURE;
    },
}
```

- 不使用 `return` 隐藏多个出口；需要提前退出时用 guard clause，正常路径保留尾表达式或单一 `return`。

### 4.2 函数长度

- 一个函数只完成一个阶段职责。解析、类型检查、诊断构造和资源释放通常不应混在同一函数中。
- 超过一屏仍难以命名的函数，按阶段边界拆成私有函数；拆分点使用有意义的名称，而不是 `part1`、`helper`。

### 4.3 变量与表达式

- 变量在首次使用附近声明，并尽量用有意义的初始值；不要先声明一大组变量再逐个赋值。
- 类型不能由上下文确定、或字面量需要固定位宽时写显式类型，例如 `let index: u64 = 0;`。
- 需要查看而不转移所有权时使用 `&value`；需要复制标量或已知安全的值时使用 `*value`。不要把借用
  临时提升为长期字段，除非其所有者保证生命周期。
- 长表达式按子表达式绑定局部名称，名称说明中间结果的含义；不要用 `a`、`b`、`tmp` 堆积难以审查的链式调用。

## 5. 所有权与资源

### 5.1 借用与拥有

- 参数和字段需要借用时写 `T&`；返回借用值时在注释中说明它依赖哪个所有者。
- `Option<T&>` 表示可选的借用；不要用空指针或哨兵值表达“没有值”。
- `str` 表示借用文本，`String` 表示拥有文本；需要保存或跨调用返回时创建 `String.from(...)`，不要返回
  指向临时值的 `str`。
- 原始指针只用于 C ABI、运行时存储或底层容器实现；普通编译器逻辑使用引用、数组、切片和显式 ID。

### 5.2 显式释放

YIAN 的赋值、传参和返回都是浅复制，不自动释放来源值。每个资源只有一个显式所有者：

```yian
impl Drop for AnalysisOutput {
    pub fn drop() {
        for procedure in self.procedures {
            procedure.drop();
        }

        self.checked.drop();
        self.procedures.drop();
    }
}
```

- 先释放容器中的载荷，再释放容器存储；不要依赖容器递归释放元素。
- 不要把拥有资源的 struct 浅复制后释放两份；需要共享时明确借用，需要独立副本时显式 clone。
- 错误路径使用 `defer` 释放已取得的资源：

```yian
let values = Vec<Attr&>.new();
defer values.drop();

while ... {
    values.push(...);
}

Option<Vec<Attr&>>.Some(val=values.take())
```

`take()` 把存储转交给返回值；延迟执行的 `drop()` 随后只释放空容器。

- 需要“成功后转移、失败时释放”的模式时，用布尔标记配合 `defer`，并在成功点更新标记：

```yian
let accepted = false;
defer { if !accepted { root.drop(); } };
...
accepted = true;
```

- 通过 `exit`、`@panic` 等非自然方式结束程序时不执行任何资源释放：不要在这些调用前手动 `drop()`；进程终止后由操作系统回收资源。正常返回和可恢复错误仍按本节规则释放。

### 5.3 稳定地址与容器

- `Arena<T>` 用于需要稳定地址、按 ID 索引的记录；分配后地址不变。
- `Vec`、`HashMap`、`HashSet` 的扩容或结构修改会使旧引用/迭代器失效。跨扩容保存的记录使用 arena，
  队列、临时缓冲和纯 ID 集合使用容器。
- 不要在遍历 `HashMap`/`HashSet` 时做结构修改。
- 结构类型的驻留键在插入后不可变；不要用已插入的键继续修改值。

## 6. 错误、诊断与 panic

### 6.1 用户错误

- 可恢复的用户错误通过 `Result<T, E>` 返回，使用 `?` 传播；不要用 panic 处理输入错误、类型错误或 I/O 错误。
- 每个阶段定义具体的错误类型，并实现 `UserDiagnostic` 与 `Drop`：

```yian
pub enum CodegenErrorKind {
    User,
    LLVM,
    Target,
}

impl UserDiagnostic for CodegenError {
    pub fn code() -> DiagnosticCode { self.code }
    pub fn location() -> DiagnosticLocation { self.location.copy() }
    pub fn message() -> String { String.from(&self.message.as_str()) }
}

impl Drop for CodegenError {
    pub fn drop() {
        self.location.drop();
        self.message.drop();
    }
}
```

- 错误联合类型按变体转发 `code`、`location`、`message` 和 `drop`，不要复制下层诊断逻辑。
- 用户可见诊断码由 `DiagnosticCode` 统一定义；不要在消息文本里临时发明新的 `E`/`W` 编号。
- 可恢复警告通过同步的 `DiagnosticReporter` 报告；阶段致命错误沿 `Result` 返回 `main`，命令行参数和日志配置错误分别由 `parse_args`、`CompilerTrace.new` 直接输出并以进程状态退出。
- 不要根据诊断消息文本判断错误类别；需要分类时增加枚举变体或诊断码。

### 6.2 内部不变量

- `@panic` 只用于调用者无法恢复的内部不变量违反，例如会话状态错误或无效的会话内 ID。
- panic 消息说明被破坏的不变量，不写用户操作建议：

```yian
if self.attempted { @panic("frontend driver has already run"); }
...
None => @panic("invalid type ID")
```

- `.unwrap()` 只用于构造后必然成功的查询或测试夹具；用户输入路径使用 `match`、`?` 或显式错误转换。
- 不可达分支使用 `@panic` 或明确的 `Result` 错误，不用返回默认值掩盖问题。

## 7. 注释与文档

- 代码注释和诊断消息使用英文；用户文档与 `yianc/docs/` 使用中文。
- 注释解释所有权、前置条件、生命周期和跨模块边界，不复述代码本身：

```yian
// References survive allocation. Treat interned records as immutable, including their buffers.
pub fn get(id: TypeId) -> Option<TypeData&> { ... }
```

```yian
// The primitive input is consumed on both success and failure.
pub fn primitive(data: TypeData) -> Result<TypeId, AnalysisError> { ... }
```

- 注释放在被说明的声明上方；行尾注释只用于简短的局部说明。
- 不使用 `// ====` 横幅；用空行和声明边界组织文件。
- 不写变更历史、缺陷故事、计划、TODO 或“以后会支持”的承诺。需要记录未完成工作时使用外部计划，
  不把临时说明留在产品代码中。
- 文档和注释描述当前事实；链接使用仓库内稳定路径，不依赖临时计划或提案编号。

## 8. 排版

`yian-fmt` 是排版的唯一权威。以下规则概括它对本仓库代码的稳定输出：

- 缩进 4 个空格；不使用 tab。
- `{` 与所属语句同行，`}` 单独一行；源文件写成单行的块保持单行。
- 语句以 `;` 结尾；`else`、`elif` 与前一个 `}` 同行。
- `:` 前无空格、后一个空格；`,` 前无空格、后一个空格；`.` 紧贴。
- 二元运算符、`->`、`=>` 两侧各一个空格；一元运算符与操作数紧贴。
- 泛型括号紧贴类型名（`Vec<i32>`），比较运算符两侧有空格（`a < b`）；后缀指针/引用紧贴
  （`i32*`、`T&`）。
- 空行最多保留一个；文件以单个换行结束；行尾不留空白。
- 格式化器不按列宽折行，也不排序 import。断行位置按声明和表达式边界选择，不为了满足某个人为列宽
  把简单表达式拆开。
- 需要格式化时优先运行工具，不手工对齐：

```bash
yian-fmt --check yianc/src/main.an
yian-fmt -w yianc/src/main.an
```

## 9. 字面量与数值

- 类型标注或上下文已经决定类型时省略数字后缀：

```yian
pub const VOID_TYPE_ID: u64 = 0;
const EXIT_FAILURE: i32 = 1;
```

- 需要位宽、转换、推断或测试语义时保留后缀，例如 `0u64`、`32u32` 和 `0xFFFFFFFFFFFFFFFFu64`。
- 位掩码和 ABI 常量使用十六进制；计数和索引使用十进制。
- 不为了去掉后缀而改变类型或运算结果。

## 10. 测试代码

测试位于 `yianc/tests/`，按被测模块分目录：

```text
yianc/tests/unit/lex/           词法与 token
yianc/tests/unit/parse/         语法分析
yianc/tests/unit/desugar/       去糖
yianc/tests/unit/analysis/      类型存储与 HIR 验证
yianc/tests/unit/codegen/       CFG/表示
yianc/tests/unit/constant/      常量求值
yianc/tests/integration/        前端会话、源码、生命周期和端到端行为
```

- 入口使用 `fn main()`；一个文件聚焦一个模块或一组紧密相关的行为。
- 断言带说明失败行为的消息：

```yian
assert generic != another_generic: "generic binders have distinct declaration identities";
```

- 用 `defer` 释放测试夹具；提前释放用于验证“资源释放后仍可用的借用”时，注释说明测试意图。
- 解析器、词法器和构造已知有效的输入可以使用 `.unwrap()`；测试用户错误路径时使用 `match` 并断言具体
  错误类别或消息。
- 预期 stdout 放在 `yianc/tests/output/` 下与测试同名的 `.ans` 文件；没有期望文件时只要求退出码为 0。
- 不写“测试测试框架”的元测试；测试运行器、用例选择和期望文件处理由人工审查。
- 测试代码也遵循本规范的命名、所有权和注释要求；测试数据可以包含中文或其他语言，以覆盖真实输入。

## 11. 文档同步

- 修改编译阶段、公开类型、命令行选项、诊断码或资源契约时，更新 `yianc/docs/` 中的对应章节。
- 编译器文档只描述原生 `yianc`；不把 Python 恢复编译器的 CLI、API 或实现写进产品文档。
- 文档中的路径、类型名、函数名和命令必须能在当前源码或清单中找到；不要凭计划或旧版本描述行为。
- 交叉链接使用相对路径。例如从 `yianc/docs/` 引用根目录文档：

```markdown
[语言参考](../../docs/grammar/index.md)
[anx 包管理器](../../docs/anx/index.md)
```

- 用户可见的文档使用中文；代码注释使用英文。
