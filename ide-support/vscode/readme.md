# YIAN Language Support（VS Code 扩展）

YIAN（`.an`）的编辑器支持：声明式的语言注册与编辑体验、一个语言服务器客户端（`yian-lsp`，stdio
传输）、实时诊断、导航与类型查看、补全/参数提示/语义高亮、查找引用/重命名/快速修复。服务器不随扩展
打包运行时，而是启动本机 Python 环境里的 `yian-lsp`（安装见下文）。

- 语言 id `yian`，文件关联 `.an`
- TextMate 语法高亮（关键字、类型、字面量、f-string 内插、属性与内建、注释）
- 注释切换（`//`、`/* */`）、括号匹配与自动闭合、缩进规则
- `[yian]` 的默认编辑器设置：4 空格缩进、关闭自动检测
- 语言服务器客户端：随窗口启动 `yian-lsp`，同步文档、监听工作区文件变化，
  并把服务器日志收进 **YIAN Language Server** 输出通道
- 实时诊断（波浪线 + Problems 面板）：词法、语法、名称/导入/可见性、类型与成员错误，
  每个错误带稳定错误码（`E1xx`–`E5xx`）、`source: yian` 与文档版本号
- 跳转定义：局部、参数、函数、方法（跳到方法本身而不是接收者类型）、结构体字段、
  枚举成员、类型与别名、导入符号；跨文件、跨包、标准库都能跳（标准库目标直接落在 `lib/src`）
- 悬停：符号种类、完整名、按当前实例化渲染的类型或函数签名，并标出"声明于标准库"
- 文档符号（大纲）：当前文件的顶层定义与嵌套成员（结构体的字段、impl 的方法、枚举成员）
- 补全：按上下文区分**值位置**（局部/参数/模块公开符号/预定义类型）、**成员位置**
  （按接收者静态类型列字段与方法，`Shape.` 这类静态访问列枚举成员）、**类型位置**
  与**导入位置**（包名、模块路径、该模块的公开符号）；函数项带 `fn(x: T)` 签名与
  `${1:x}` 参数占位符；同名项去重且局部符号优先
- 参数提示（`signatureHelp`）：调用处显示签名并标出正在写的参数
- 语义高亮：按符号索引给标识符分类（类型/函数/方法/变量/参数/字段/枚举成员），
  声明处加 `declaration`、标准库符号加 `defaultLibrary`；字符串、注释与数字仍由文本语法负责，
  语义 token 只覆盖标识符本身
- 查找引用（`references`）与当前文件引用高亮（`documentHighlight`）：按**符号身份**
  匹配，同名但不同作用域的符号不会混在一起；声明处标为写入、使用处标为读取
- 重命名（`prepareRename` + `rename`）：跨文件、跨包，覆盖声明、使用与别名导入里的
  原名；**只在解析出的名字 span 上改**，字符串、注释和无关同名符号不受影响；整体是
  一个 `WorkspaceEdit`，可一次撤销。无法保证完整时会**拒绝**并给出原因：重命名标准库
  符号、非法/关键字名字、以及"分析没检查到的定义里还用到这个名字"（未实例化的泛型体）
- 快速修复（`codeAction`）：删掉无法解析的导入。`from X import a, b;` 只坏一项时只删那一项
  和它的分隔符，单独一项坏掉或路径本身坏掉时删整条语句；动作回带它针对的诊断

格式化由语言服务器提供：`Shift+Alt+F` 或 `editor.formatOnSave` 都会请求 `textDocument/formatting`，
服务器返回一个覆盖全文的编辑，可一次撤销。格式化只改空白与注释的位置，token 序列逐字不变；语法有错的
文件不产生任何修改。内联提示、折叠范围、选择范围与文档链接不做。

引用高亮/重命名/代码操作都基于解析结果，因此**分析没跑到的代码不会被连带修改**；
这也意味着重命名在"项目里存在未实例化的泛型体用到该名字"时会拒绝而不是只改一半。

编辑事件重置两级分析定时器：空闲 200 ms 后运行词法、语法与去糖分析，空闲 600 ms 后运行完整前缀
（包括名称解析与类型检查）。持续输入会重新开始计时。打开文件、保存或语义请求会立即请求完整分析。
因此短暂停顿时 Problems 面板可先显示语法诊断，随后补上语义诊断；保存和语义请求也会保证当前文本有
完整结果。语义高亮只读取当前修订的完整快照，分析期间返回空数组，由 TextMate 着色兜底。

分析结果按**整个项目**计算，但只发布给**已打开**的文档；每个诊断带被分析时的文档版本号，客户端据此
丢弃过期结果；文件修好、关闭或不再被分析时，该文档的诊断会被清空。分析不生成代码，也不调用
LLVM/clang/链接器。

## 语言服务器

`src/extension.ts` 只做 LSP 客户端：打开 `.an` 文件时通过 stdio 启动 `yian-lsp`，把文档事件转发过去，
并把服务器的 stderr 收进输出通道。语言规则、索引与分析都在服务器侧（`lsp/` + `compiler/`），
扩展里没有任何 YIAN 语法知识。

### 安装（依赖本机 Python）

服务器不随扩展分发：扩展启动的是本机已有的 `yian-lsp`，所以**装在哪个解释器里，
就要让扩展用那个解释器**。在仓库根目录：

```bash
scripts/install.sh --with-deps              # editable 安装 yianc / anx / yian-lsp
python3 -m pip install '.[lsp]'             # 只补语言服务器依赖（pygls；llvmlite 是基础依赖）
python3 -m lsp --version                    # 用同一个解释器确认入口点可用
```

默认配置直接用 `PATH` 上的 `yian-lsp`。多环境机器（多个 conda 环境、系统 Python 与虚拟环境并存）
推荐显式写解释器，否则很容易出现"VS Code 里的 Python"与"PATH 上的 Python"不是同一个：

```jsonc
{
    "yian.languageServer.command": "/home/<用户>/miniconda3/envs/yian-env/bin/python",
    "yian.languageServer.args": ["-m", "lsp"]
}
```

设置：

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `yian.languageServer.command` | `yian-lsp` | 服务器启动命令；多环境时填解释器的绝对路径 |
| `yian.languageServer.args` | `[]` | 追加参数；配合上面的写法是 `["-m", "lsp"]`，也可加 `--compiler-root`（可用 `--help` 看全部） |
| `yian.languageServer.logLevel` | `""` | 传给服务器的 `--log-level`（`DEBUG`/`INFO`/`WARNING`/`ERROR`）；空时由 YIAN_LSP_LOG 决定，未设置则为 INFO |
| `yian.languageServer.logFile` | `""` | 服务器额外写入的日志文件（`--log-file`），`${workspaceFolder}` 会被展开 |

### 版本匹配

扩展与服务器一起发布，版本号相同（当前为 **0.7.0**：package.json 中的 version 与
lsp/server.py 中的 SERVER_VERSION）。服务器在 initialize 中回报 serverInfo；版本不一致时扩展
在输出通道记录警告并显示提示。可在启动服务器的 Python 环境中执行
pip install -e '.[lsp]'，或把扩展设置指向正确的环境。实际可用能力由所连接的服务器决定。

分析模式由**工作区**决定，打开文档不会改变模式：

- 工作区本身（或其上层）有 `package.anx` → **package 模式**：加载项目模型，分析该包的整个文件索引；
  日志里出现 `project <名字> at <路径>: N packages, M files` 与
  `analysis #1 (startup, full): N files, K declarations, D diagnostics in X ms`。
- 工作区不是 YIAN 包 → **standalone 模式**：只分析打开的文档加标准库，包名导入（如
  `from sample.geometry import …`）会报无法解析；日志里出现
  `<工作区> is not a YIAN package; standalone mode` 与 `standalone mode: waiting for a document`；
  打开文档后会执行完整分析。

文件或文本变化会作废快照并调度语法与完整分析。完整分析在后台线程构建声明索引，因而成功完成时日志
可同时包含声明数量；语法分析日志不包含该字段。分析时长随机器和输入项目变化。

验证：

1. 打开 `.an` 文件，`Ctrl+Shift+P` → `Output: Focus on Output View`，通道选 **YIAN Language Server**
2. 对照上面两种模式，日志里应出现对应的那几行
3. 打开/修改/关闭文件时出现 `textDocument/didOpen|didChange|didClose` 行
4. 关闭窗口后 `yian-lsp` 进程退出（`pgrep -f yian-lsp` 无结果），不留孤儿进程

服务器启动失败（命令不存在、缺少 `pygls`）会在输出通道里给出原因，并弹出一条提示。
诊断样例工程 ide-support/sample-errors 的 src/errors.an 为五种错误各提供一个独立函数。
完整分析会为这些函数发布对应诊断；编辑后诊断随新的分析结果更新。

示例工程 `ide-support/sample` 的语法覆盖：`types.an`（类型别名）、`geometry.an`（结构体、泛型结构体、
方法、静态方法）、`shapes.an`（带载荷的枚举、`match`、泛型函数）、`measure.an`（trait、默认方法、
条件实现 `impl<T> Trait for T if T: Trait`）、`text.an`（`Vec`/`String`/`Option`、`for`、闭包、
f-string、`dyn` 数组与 `del`）、`handles.an`（`T&` 引用、指针参数、`Drop`、`defer`、`comptime if`），
以及入口 `main.an`；`tests/tour.an` 跑一遍新增模块。

要验收导航，把工作区设为 `ide-support/sample`，打开 `src/main.an`：在 `corner.scaled(2.0)` 的
`scaled` 上 `F12` 应跳到 `src/geometry.an` 的方法定义；在 `flipped.first` 的 `first` 上悬停应显示
`field first: Meters`；在 `print(` 上 `F12` 应跳到 `lib/src/core/io.an`；大纲（`Ctrl+Shift+O`）
应显示 `Point` 及其字段与方法。

要验收补全与语义高亮，在 `src/main.an` 里输入 `corner.` 应只列出 `Point` 的字段与方法（不应出现
`Vec`/`Pair` 的成员），`Shape.` 应只列出三个枚举成员；输入 `from sample.` 应列出模块路径，
`from sample.geometry import ` 应只列出该模块的 `pub` 项；把光标放进 `Pair<Meters>.of(` 应出现
签名提示。语义高亮可以在设置里关掉（`editor.semanticHighlighting.enabled`），关掉后 TextMate
的着色不受影响。

要验收引用、重命名与快速修复：在 `src/main.an` 的局部变量 `corner` 上按 `Shift+F12`（查找引用）
只应列出本文件里的三处；`F2`（重命名）成 `cornerstone` 应只改这三处，字符串与注释不动。在 `src/shapes.an` 的
枚举成员 `Point` 上 `F2` 改名会同时改到 `main.an` 里的使用。把 `print` 改名会被拒绝并提示它是
标准库符号。把某条导入写坏（例如 `from sample.types import Meters, Nope;`）后，问题处会出现
"Remove this import: …" 的快速修复，执行后只剩 `import Meters;`。

要检查分析路径与发布行为，把工作区设为 `ide-support/sample`，`yian.languageServer.logLevel`
设为 `DEBUG`，然后：

1. 打开 `src/main.an`：日志里出现完整分析行，包含文件数、声明数、诊断数与耗时；示例工程无诊断时
   Problems 面板为空；
2. 修改一个字符并暂停：约 200 ms 后可见语法分析日志；若继续空闲，约 600 ms 后会运行完整分析；
   文本领先于完整快照期间，语义 token 返回空数组；
3. `Ctrl+S` 会立即触发完整分析。把某处类型写错（例如
   `let n: i32 = "x";`）保存后应看到 `E4xx` 波浪线，改回后再保存消失；
   改回并保存后，完整分析结果会清除该诊断；
4. 在完整快照尚未生成时发起悬停等语义请求，服务器会为当前修订执行完整分析；快照就绪时，语义请求复用它，
   不会再记一条分析日志；
5. 输出通道里能看到 `connected to yian-lsp 0.7.0`；版本不匹配时会提示连接到的服务器版本；
6. 打开 `ide-support/sample-errors/src/errors.an` 可查看五类错误对应的诊断。

## 构建

```bash
cd ide-support/vscode
npm install
npm run compile          # 编译 src/ → out/；也可用 npm run watch 持续编译
```

## 调试

用 VS Code 打开本目录，按 `F5` 启动 Extension Development Host，在新窗口里打开任意 `.an` 文件
（例如 `ide-support/sample/src/main.an`）验证高亮、编辑行为与语言服务器日志
（Development Host 继承启动者的 `PATH`，所以 `yian-lsp` 要能在该 `PATH` 上找到）。

## 打包、安装、卸载与回滚

```bash
cd ide-support/vscode
npm install
npm run compile
npx @vscode/vsce package          # 需要联网；也可全局安装 @vscode/vsce 后用 vsce package
code --install-extension yian-language-support-0.7.0.vsix --force
code --list-extensions --show-versions | grep -i yian   # 核对版本 ≥ 0.7.0
```

在 WSL / 远程窗口里 `code` 是远端 CLI，安装、卸载、`--list-extensions` 都作用于**远端**扩展目录，
要在该窗口的终端里执行（本机 profile 名与 CLI 的 `--profile` 不一定一致，`--force` 重装最省事）。

- 卸载扩展：`code --uninstall-extension yian.yian-language-support`。
- 选择扩展版本：安装指定版本的 VSIX 并加 `--force`；也可以检出对应源码版本后运行
  `vsce package` 构建安装包。
- 选择服务器版本：检出目标源码版本并运行 `scripts/install.sh --with-deps`。editable 安装使用当前源码；
  可在输出通道核对 `connected to yian-lsp <版本>`。
- 兼容性：扩展要求 VS Code `^1.90.0`；服务器要求 Python ≥ 3.11 与 `pygls` ≥ 2.1，`llvmlite` 由基础
  依赖提供；扩展与服务器的版本必须同号（见上文"版本匹配"）。

打包产物（`*.vsix`）、`node_modules/`、`out/` 都不进版本库。VSIX 里必须带上
`node_modules/vscode-languageclient/`：不打包 `node_modules` 而又没有 bundler 时，
扩展运行时会 `Cannot find module 'vscode-languageclient/node'`。

## 故障排查

先看 **YIAN Language Server** 输出通道（`Ctrl+Shift+P` → `Output: Focus on Output View`，通道选
YIAN Language Server）。要把日志留档或开到更详细：

1. `yian.languageServer.logLevel` 设为 `DEBUG`（等价于在 `args` 里加 `--log-level DEBUG`）；
2. `yian.languageServer.logFile` 设为例如 `${workspaceFolder}/build/yian-lsp.log`，日志同时写文件与
   输出通道；也可以直接用 `--log-file` 或环境变量 `YIAN_LSP_LOG_FILE`；
3. `DEBUG` 会记录 JSON-RPC 载荷，载荷包含源码文本；分析摘要在 `INFO` 级别输出。完整分析成功时摘要
   可包含声明数量，语法分析摘要不包含该字段。外发日志前应检查源码内容。

| 症状 | 原因 | 处理 |
| --- | --- | --- |
| 弹 `could not start '…'`，输出通道同名报错 | 命令不在 `PATH` 上，或那个解释器里没有 `pygls` | 按"安装"配好 `command`/`args`，在该解释器里 `pip install -e '.[lsp]'` |
| 弹 `language server X does not match extension Y` | 启动的解释器中安装的服务器版本与扩展版本不一致 | 在那个解释器里重新 `pip install -e '.[lsp]'` |
| 完全没有诊断 | 服务器没连上，或当前文件不属于被分析的文件集合 | 输出通道里应有 `project … N files` 或 `standalone mode`；没有就先解决启动问题 |
| 打字时类型错误消失 | 打字路径只跑前端 | 保存一次，或做一次悬停/补全；要确认可看 `(…, syntax)` 与 `(…, full)` 两类日志 |
| 语义高亮没颜色 | 文本已改动、完整分析还没落地，或客户端关了语义高亮 | 保存一次；检查 `editor.semanticHighlighting.enabled` 与 `yian` 语言的 token 主题色 |
| 跳转不到标准库 | 找不到 `lib/src` | 加 `--compiler-root <仓库根>`，或设 `YIAN_LIB` |
| 服务器异常退出 | 服务器遇到未处理异常 | 输出通道里有 Python traceback；扩展只记录并提示，不会让 VS Code 崩溃，可把日志附到问题报告 |

## 代码片段

`snippets/yian.json` 提供最小片段集：`fn` `main` `struct` `structg` `enum` `trait` `impl`
`implfor` `typedef` `from` `let` `if` `match` `for` `while` `assert` `defer` `comptime`。
补全由 VS Code 内置的 snippet 机制提供，不需要运行时代码。

## 折叠

按大括号缩进折叠（VS Code 默认的 indentation 折叠策略）。此外支持注释形式的区域标记：

```yian
// #region 名字
...
// #endregion
```

## 检查 scope（人工）

1. 用 VS Code 打开本目录（`ide-support/vscode`）作为工作区根，按 `F5` 启动 Extension Development Host
2. 在新窗口打开 `highlight-sample.an`（该文件每个 grammar 规则至少覆盖一次）
3. `Ctrl+Shift+P` → `Developer: Inspect Editor Tokens and Scopes`，把光标放到 token 上
4. 对照下表

| token | 期望 scope |
| --- | --- |
| `let` `fn` `struct` `enum` `trait` `impl` `typedef` `pub` `static` `const` `comptime` `inline` `intrinsic` `dyn` | `keyword.declaration.yian` |
| `if` `elif` `else` `match` `for` `while` `loop` `in` `break` `continue` `return` `defer` `assert` `del` | `keyword.control.yian` |
| `import` `from` `as` | `keyword.control.import.yian` |
| `typeof` | `keyword.operator.typeof.yian` |
| `i8`…`u64` `f16`…`f64` `bool` `char` `str` `void` `int` `uint` `float` | `storage.type.primitive.yian` |
| `Shape` `Point`（PascalCase） | `entity.name.type.yian` |
| `@BitCopy` 等 PascalCase 属性 | `entity.other.attribute-name.yian` |
| `@sizeof` `@str_get_len` 等小写内建 | `support.function.builtin.yian` |
| `self` | `variable.language.self.yian` |
| `_` | `variable.language.wildcard.yian` |
| `true` `false` | `constant.language.boolean.yian` |
| `IS_RAW_MODE` | `constant.language.configuration.yian` |
| `// 行注释` / `/* 块注释 */` | `comment.line.double-slash.yian` / `comment.block.yian` |
| `"字符串"` | `string.quoted.double.yian`（转义为 `constant.character.escape.yian`） |
| `'a'` 与 `b'x'` | `string.quoted.single.yian` |
| `f"…"` | `string.interpolated.yian`；`{…}` 内插为 `meta.embedded.expression.yian`，其中内容按本表递归 |
| `123` `0xFF` `0b1010` `0o755` `12i8` | `constant.numeric.integer.yian` |
| `3.5` `1e3` `3.5f64` | `constant.numeric.float.yian` |
| `+` `->` `=>` `==` `&&` `<<=` … | `keyword.operator.yian` |
| `(` `)` `{` `}` `[` `]` | `punctuation.section.yian` |
| `,` `;` | `punctuation.separator.yian` |
| `.` | `punctuation.accessor.yian` |
| `:` | `punctuation.separator.type-annotation.yian` |
| `fn 名字` | `entity.name.function.yian` |
| `名字(` 调用 | `entity.name.function.call.yian` |

已知近似：`Pair<Meters>` 的 `<` `>` 会按 `keyword.operator.yian` 高亮，而不是泛型括号。`<`/`>`
同时是比较运算符，TextMate 无法可靠区分，真正的类型/函数/变量区分由语义 token 提供。

## 已知限制

- 不提供：内联提示（inlay hints）、折叠范围、选择范围、文档链接、工作区符号搜索
  （`workspace/symbol`）、`declaration` / `typeDefinition` / `implementation`、模糊匹配补全、
  doc 注释补全、自动补 import。语言服务器不生成 snippet 项；静态代码片段由 VS Code 扩展提供。
- 编辑后的语法分析与完整分析分别在 200 ms、600 ms 空闲后运行；保存或语义请求会立即请求完整分析。
- 语义高亮在文本领先于分析时返回空数组，客户端保留文本语法着色。
- 重命名在无法确认符号集合完整时拒绝执行（标准库符号、非法名字、分析没检查过的定义里用到该名字）。
- 格式化只支持整文件：`editor.formatOnSaveMode` 需保持默认 `file`，设成 `modifications`（只格式化
  改动行）时不会格式化；行尾统一为 LF，CRLF 文件格式化后变成 LF。
- 多根工作区只分析第一个工作区文件夹。
- 分析工作在单个后台 worker 上串行执行；大型项目分析期间，需要完整语义快照的请求会等待分析完成。

## 说明

- 关键字、类型名与标点以 `compiler/frontend/lex/token.py` 的 `KeywordKind` / `PunctuatorKind`
  为准，改动词法后要同步 `syntaxes/yian.tmLanguage.json`。
- TextMate 只是近似：真正的类型/函数/变量区分由语义 token 提供。
