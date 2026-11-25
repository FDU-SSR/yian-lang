# yianc 编码规范

本规范适用于原生编译器的实现和测试。通用的命名、类型声明、控制流、资源清理、注释和排版约定见 [YIAN 写作技能](../../.agents/skills/yian-writing/SKILL.md)。

## 模块与公开边界

`yianc/src/` 的目录边界对应编译阶段和依赖方向：

| 目录           | 职责                                                  |
| -------------- | ----------------------------------------------------- |
| `frontend/`    | 源码/单元表、词法、语法、AST、去糖、遍历与转储 |
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

同一目录按编译阶段和概念分组，例如 `analysis/lowering/` 按表达式类别拆分。导入通常先列 `yianc.*`，再列 `std.*` 和 bootstrap 包，同一文件内保持一致。

会话内 ID 使用带 `Id` 后缀的类型名，如 `UnitId`、`HirId` 和 `BlockId`。普通编译器逻辑使用引用、数组、切片和显式 ID。结构类型驻留后，记录及其缓冲区应与驻留键保持一致。LSP 专用的适配由 LSP 实现。

## 阶段错误与诊断

阶段错误按变体携带足以生成诊断的位置和消息。阶段错误类型实现 `UserDiagnostic` 和 `Drop`。

- 错误联合类型按变体转发 `code`、`location`、`message` 和 `drop`，不要复制下层诊断逻辑。
- 用户可见诊断码由 `DiagnosticCode` 统一定义；不要在消息文本里临时发明新的 `E`/`W` 编号。
- warning 通过同步的 `DiagnosticReporter` 立即报告；阶段用户错误沿 `Result` 返回 `main`，命令行参数和日志配置错误分别由 `parse_args`、`CompilerTrace.new` 直接输出并以进程状态退出。
- 不要根据诊断消息文本判断错误类别；需要分类时增加枚举变体或诊断码。

诊断消息使用英文。诊断码和消息属于用户可见契约。会话状态错误、无效会话内 ID 等内部不变量在发现处触发 `@panic`。

## 编译器测试

测试位于 `yianc/tests/`，按被测模块分目录：

```text
yianc/tests/unit/lex/           词法与 token
yianc/tests/unit/parse/         语法分析
yianc/tests/unit/desugar/       去糖
yianc/tests/unit/analysis/      类型存储与 HIR 验证
yianc/tests/unit/codegen/       CFG/表示
yianc/tests/unit/constant/      常量求值
yianc/tests/integration/        前端单元表、源码、生命周期和端到端行为
```

可执行测试以 `fn main()` 为入口。预期 stdout 放在 `yianc/tests/output/` 下与用例同名的 `.ans` 文件中；没有期望文件时只要求退出码为 0。测试运行器、用例选择和期望文件处理按仓库指南人工审查。

## 文档同步

修改编译阶段、公开签名或字段、命令行选项、诊断码或资源契约时，同步调用者和 `yianc/docs/` 中的对应章节。编译器文档使用中文，描述原生 `yianc` 的当前行为。
