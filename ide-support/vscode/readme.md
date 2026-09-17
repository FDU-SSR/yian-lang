# YIAN Language Support（VS Code 扩展）

YIAN（`.an`）的编辑器支持。当前是 **P1** 阶段：只提供声明式的语言注册与编辑体验，没有语言服务器。

- 语言 id `yian`，文件关联 `.an`
- TextMate 语法高亮（关键字、类型、字面量、f-string 内插、属性与内建、注释）
- 注释切换（`//`、`/* */`）、括号匹配与自动闭合、缩进规则
- `[yian]` 的默认编辑器设置：4 空格缩进、关闭自动检测

语义诊断、跳转、补全、语义高亮在后续阶段实现（见 `docs/plan/ide-support-plan.md`）。
`src/extension.ts` 目前是空的 `activate()`：上面的能力全部由 VS Code 依据本扩展的声明式贡献注册，
不需要运行时代码；语言服务器客户端在 **P3** 接入（stdio 传输，启动 `yian-lsp`）。

## 构建

```bash
cd ide-support/vscode
npm install
npm run compile          # 编译 src/ → out/；也可用 npm run watch 持续编译
```

## 调试

用 VS Code 打开本目录，按 `F5` 启动 Extension Development Host，在新窗口里打开任意 `.an` 文件
（例如 `ide-support/sample/src/main.an`）验证高亮与编辑行为。

## 打包与安装

```bash
cd ide-support/vscode
npx @vscode/vsce package          # 需要联网；也可全局安装 @vscode/vsce 后用 vsce package
code --install-extension yian-language-support-0.1.1.vsix
code --list-extensions --show-versions | grep -i yian   # 核对版本 ≥ 0.1.0
```

打包产物（`*.vsix`）、`node_modules/`、`out/` 都不进版本库。

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
