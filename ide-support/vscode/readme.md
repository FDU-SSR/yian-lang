# YIAN Language Support（VS Code 扩展）

YIAN（`.an`）的编辑器支持。当前是 **P5** 阶段：声明式的语言注册与编辑体验、一个语言服务器客户端
（`yian-lsp`，stdio 传输，见 `docs/plan/ide-support-plan.md` §5.9、§5.10）、实时诊断，
以及导航与类型查看。

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

补全与语义高亮（P6）、引用/重命名/代码操作（P7）在后续阶段实现。

诊断的策略：文件改动后等 200ms 空闲再分析（防抖），保存时立即分析；分析结果按**整个项目**
计算，但只发布给**已打开**的文档；每个诊断带被分析时的文档版本号，客户端据此丢弃过期结果；
文件修好、关闭或不再被分析时，该文档的诊断会被清空。分析只跑编译器流水线的前半段
（词法 → 类型检查），不生成代码，也不调用 LLVM/clang/链接器。

## 语言服务器

`src/extension.ts` 只做 LSP 客户端：打开 `.an` 文件时通过 stdio 启动 `yian-lsp`，把文档事件转发过去，
并把服务器的 stderr 收进输出通道。语言规则、索引与分析都在服务器侧（`lsp/` + `compiler/`），
扩展里没有任何 YIAN 语法知识。

前提：`yian-lsp` 在 `PATH` 上，且其 Python 环境能 `import pygls`：

```bash
scripts/install.sh --with-deps              # 仓库内：editable 安装三个命令
python3 -m pip install '.[lsp]'             # 或者只补语言服务器的依赖
yian-lsp --version                          # 确认入口点可用
```

设置：

| 设置 | 默认值 | 说明 |
| --- | --- | --- |
| `yian.languageServer.command` | `yian-lsp` | 服务器启动命令；不在 `PATH` 上时填绝对路径 |
| `yian.languageServer.args` | `[]` | 追加参数，例如 `--compiler-root /path/to/yian`、`--log-level DEBUG` |

分析模式由**工作区**决定，与之后打开哪个文件无关：

- 工作区本身（或其上层）有 `package.anx` → **package 模式**：加载项目模型，分析该包的整个文件索引；
  日志里出现 `project <名字> at <路径>: N packages, M files` 与
  `analysis #1 (startup): N files, K declarations, D diagnostics in X ms`。
- 工作区不是 YIAN 包 → **standalone 模式**：只分析打开的文档加标准库，包名导入（如
  `from sample.geometry import …`）会报无法解析；日志里出现
  `<工作区> is not a YIAN package; standalone mode` 与 `standalone mode: waiting for a document`，
  之后每打开/关闭一个文档都会出现一条 `analysis #N (didOpen|didClose): …`。

分析在「被分析的文件集合变化」时重跑（standalone 模式下打开/关闭文档就是这种变化，
package 模式下只有打开包外文件才会）；纯文本修改只作废快照，重新分析属于 **P4**。

验证：

1. 打开 `.an` 文件，`Ctrl+Shift+P` → `Output: Focus on Output View`，通道选 **YIAN Language Server**
2. 对照上面两种模式，日志里应出现对应的那几行
3. 打开/修改/关闭文件时出现 `textDocument/didOpen|didChange|didClose` 行
4. 关闭窗口后 `yian-lsp` 进程退出（`pgrep -f yian-lsp` 无结果），不留孤儿进程

服务器启动失败（命令不存在、缺少 `pygls`）会在输出通道里给出原因，并弹出一条提示。
要人工验收诊断，可把工作区设为 `ide-support/sample-errors`（它每个错误都放在独立的顶层定义里）：
打开 `src/errors.an` 应看到 **5 条**波浪线/Problems 条目，修好某一条后它立刻消失。

要验收导航，把工作区设为 `ide-support/sample`，打开 `src/main.an`：在 `corner.scaled(2.0)` 的
`scaled` 上 `F12` 应跳到 `src/geometry.an` 的方法定义；在 `flipped.first` 的 `first` 上悬停应显示
`field first: Meters`；在 `print(` 上 `F12` 应跳到 `lib/src/core/io.an`；大纲（`Ctrl+Shift+O`）
应显示 `Point` 及其字段与方法。

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

## 打包与安装

```bash
cd ide-support/vscode
npx @vscode/vsce package          # 需要联网；也可全局安装 @vscode/vsce 后用 vsce package
code --install-extension yian-language-support-0.3.0.vsix
code --list-extensions --show-versions | grep -i yian   # 核对版本 ≥ 0.3.0
```

打包产物（`*.vsix`）、`node_modules/`、`out/` 都不进版本库。VSIX 里必须带上
`node_modules/vscode-languageclient/`：不打包 `node_modules` 而又没有 bundler 时，
扩展运行时会 `Cannot find module 'vscode-languageclient/node'`。

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
同时是比较运算符，TextMate 无法可靠区分，真正的类型/函数/变量区分留给 P6 的语义 token（计划 §5.4）。

## 说明

- 本扩展是 **重建** 的源码树，不继承仓库里那个已弃用的 `yian-language-support-0.0.10.vsix`。
- 关键字、类型名与标点以 `compiler/frontend/lex/token.py` 的 `KeywordKind` / `PunctuatorKind`
  为准，改动词法后要同步 `syntaxes/yian.tmLanguage.json`。
- TextMate 只是近似：真正的类型/函数/变量区分由后续的语义 token 提供（计划 §5.4）。
