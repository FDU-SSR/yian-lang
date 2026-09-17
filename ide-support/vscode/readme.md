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
code --install-extension yian-language-support-0.1.0.vsix
code --list-extensions --show-versions | grep -i yian   # 核对版本 ≥ 0.1.0
```

打包产物（`*.vsix`）、`node_modules/`、`out/` 都不进版本库。

## 说明

- 本扩展是 **重建** 的源码树，不继承仓库里那个已弃用的 `yian-language-support-0.0.10.vsix`。
- 关键字、类型名与标点以 `compiler/frontend/lex/token.py` 的 `KeywordKind` / `PunctuatorKind`
  为准，改动词法后要同步 `syntaxes/yian.tmLanguage.json`。
- TextMate 只是近似：真正的类型/函数/变量区分由后续的语义 token 提供（计划 §5.4）。
