---
name: yian-writing
description: Write, edit, explain, or debug YIAN language code in .an files. Use for YIAN syntax, standard-library APIs, package imports, explicit resource cleanup, and compiler diagnostics in YIAN programs.
---

# 编写 YIAN 代码

以下链接指向当前 checkout 的文档和源码。先读适用的 [AGENTS.md](../../../AGENTS.md)，再按任务查找相关章节或现有示例。

## 语法与接口

- 查语法时，从[语言参考](../../../docs/grammar/index.md)定位章节。独立程序参考[快速入门](../../../docs/grammar/00.quick_start.md)，具体用法可对照 [tests/basic](../../../tests/basic) 中最近似的用例。循环用 `while`、`loop` 或 `for ... in`，变量声明用 `let`。
- 类型标注或上下文已确定数值类型时省略字面量后缀，例如 `const ID: u64 = 0;`。类型宽度、转换、推断或测试语义依赖后缀时保留它。
- 调用标准库时，在 [lib/src](../../../lib/src) 查实际签名、导入路径、返回类型和资源约定。涉及包内或跨包导入时，先看项目的 `package.anx` 和[模块规则](../../../docs/grammar/13.modules.md)。
- 遇到编译诊断，先定位报错对应的源表达式，按当前语法和类型修正。使用默认的指针检查模式。涉及外部 C 接口时，查阅 [FFI](../../../docs/grammar/17.ffi.md) 中的权限、类型和资源边界。

## 复制与资源清理

仅在用户询问相关语义，或具体操作会影响当前问题的结果时使用本节。解释时指出哪一步会发生什么，例如“这次追加可能触发扩容，使之前保存的元素地址失效”。容器、哈希与缓冲 I/O 的接口见[标准库参考](../../../docs/grammar/14.standard_library.md)。

- 赋值、传参和返回都是浅复制，来源仍可使用。`T&` 没有借用检查或生命周期推断。对资源指定一个清理责任方，标明责任转交，并用 `defer` 覆盖提前返回路径。`Clone` 和 `Drop` 是显式调用的标准库 trait，作用域结束不会自动清理，`del` 只释放当前分配块。见[赋值与内存管理](../../../docs/grammar/15.assign_semantics.md)。
- 浅复制 `String`、容器或编译器 session 会共享底层资源。`String` 拥有缓冲区，`str` 引用现有数据。需要独立副本时，核对具体类型的 `clone()` 深度。`ptr.clone()` 会解引用并克隆指向的值。
- `Vec`、`HashMap`、`HashSet` 和 `Arena` 的清理只释放容器存储，元素持有的资源由调用方负责。`Vec`、`HashMap` 和 `HashSet` 的 `clone()` 复制存储，元素依然是浅复制。`Vec` 扩容和哈希表结构修改可能使引用或迭代器失效。需要跨追加保留稳定引用时用 `Arena`，ID、队列和临时缓冲区可用 `Vec`。迭代哈希表时，将结构修改留到迭代结束后。
- 处理 `insert_or_replace` 和 `remove_entry` 返回的旧资源，以及 `get_or_insert_with` 命中时退回的输入键。普通 `insert` 和 `remove` 丢弃的资源也由调用方负责。实现哈希时，`Hash<T>.hash` 通过 `T&` 更新同一个累加器。相等的值应有相同哈希，复合类型用同一组字段判断相等和计算哈希，浮点哈希将正负零归一。
- 需要提交缓冲输出并得到写入错误时，显式调用 `flush()`。`BufWriter.drop()` 只释放缓冲区。

## 局部验证

默认系统已安装完整的 YIAN 工具链，直接使用 PATH 中的原生 `anx` 和 `yianc`。

- **包项目**：在含 `package.anx` 的项目目录执行 `anx build` 编译可执行包。需要观察运行结果时，执行 `anx run`。库包使用 `anx check` 做语义检查。见 [anx CLI](../../../docs/anx/03.cli.md)。
- **独立程序**：使用 `yianc` 编译，显式传入标准库源码目录和程序依赖的本地源码。下面的示例从 YIAN 仓库根目录编译并运行一个现有程序。见[编译器 CLI](../../../yianc/docs/compile_script.md)。

```bash
yianc --compiler-root . -o build/access lib/src tests/basic/array/access.an
./build/access
```

仅需检查独立源码时，使用 `yianc --check --compiler-root . lib/src tests/basic/array/access.an`。按仓库局部验证规则选择最小用例，核对工具与源码的匹配情况，并说明实际完成了语义检查、编译或运行中的哪些步骤。
