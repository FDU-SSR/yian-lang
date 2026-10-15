# YIAN Language Support

VS Code 扩展提供 `.an` 语法高亮、语言配置和原生 YIAN 语言服务器客户端。语言服务器源码位于仓库根目录 `lsp/`，通过独立安装脚本构建和安装。

## 安装工具链与服务器

先安装 `yianc`、`anx` 和标准库/运行时资源，再使用原生 `anx` 构建服务器：

```bash
conda run --no-capture-output -n yian-env bash scripts/install.sh \
  --prefix "$HOME/.local" --python python
PATH="$HOME/.local/bin:$PATH" \
  scripts/install_lsp.sh --prefix "$HOME/.local"
"$HOME/.local/bin/yian-lsp" --version
```

LSP 安装需要 PATH 上的原生 `anx` 与 `yianc`、LLVM 22、clang 22，以及安装前缀下 `share/yian` 的资源。安装后的语言服务器是 native binary；它复用同一工具链和标准库，不复制第二份资源。二进制以 raw 模式和 `-O2` 构建，分析 YIAN 源码时默认使用 fat 指针。

服务器安装与卸载：

```bash
scripts/install_lsp.sh --prefix "$HOME/.local"
scripts/uninstall_lsp.sh --prefix "$HOME/.local"
```

## 扩展设置

扩展默认执行 PATH 上的 `yian-lsp`。多套安装并存时，将命令设置为安装二进制的绝对路径：

```json
{
  "yian.languageServer.command": "/home/you/.local/bin/yian-lsp",
  "yian.languageServer.args": [],
  "yian.languageServer.logLevel": "INFO",
  "yian.languageServer.logFile": ""
}
```

如果需要指定 checkout 中的编译器资源，可将 `--compiler-root` 和 checkout 根目录放入 `yian.languageServer.args`。安装后的标准配置无需该选项。要以 raw 模式分析当前工作区，将参数设置为 `["--raw-pointers"]`。

服务器支持 `--stdio`、`--compiler-root PATH`、`--raw-pointers`、`--log-level LEVEL`、`--log-file PATH` 和 `--version`。扩展启动失败、服务器版本不匹配和 stderr 日志会显示在 `YIAN Language Server` 输出通道。

## 功能

- 诊断、定义、悬停、文档符号和语义 token。
- 补全、签名帮助、引用、文档高亮、重命名和导入快速修复。
- 文档格式化、工作区文档同步和文件变更更新。
- 未保存文本优先参与分析；过期分析结果不会覆盖新版本。
- 不完整或有错误的缓冲区仍可提供可靠范围内的只读查询；引用信息不完整时不执行重命名。

## 打包扩展

```bash
cd ide-support/vscode
npm ci
npm run compile
npm run package
```

生成文件为 `ide-support/vscode/yian-language-support-0.7.0.vsix`。可以将其安装到 VS Code 手动验收；仓库自动检查不会启动 VS Code。

## 手动验收

1. 安装上面的 VSIX，并把 `yian.languageServer.command` 配置为 `yian-lsp` 的绝对路径。
2. 打开单文件工作区和带 `package.anx` 的多文件项目，检查版本信息和诊断。
3. 修改但不保存文件，检查补全、悬停、定义、签名帮助和语义高亮是否反映当前缓冲区。
4. 在多文件项目中检查引用与重命名，应用导入快速修复并格式化文件，确认编辑结果。
5. 连续输入、保存、新增/删除源码并修复无效清单，确认诊断更新且旧结果不回写。
6. 将 `yian.languageServer.args` 改为 `["--raw-pointers"]`，重启服务并重复代表性操作。
7. 关闭窗口后运行 `ps -eo pid,ppid,args | grep '[y]ian-lsp'`，确认该工作区的服务进程和 worker 已退出。

## 自动检查

从仓库根目录运行：

```bash
PATH="$HOME/.local/bin:$PATH" \
  conda run --no-capture-output -n yian-env python scripts/check_lsp.py --mode check
```

`check_lsp.py` 是协议客户端和测量工具，只启动原生 `yian-lsp`。`--mode benchmark` 运行性能矩阵，`--mode profile` 保存原生阶段 trace，`--mode infrastructure` 刷新原生编译工具、独立安装服务器并运行协议与资源检查。
