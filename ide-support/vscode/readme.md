# YIAN Language Support

VS Code 扩展提供 `.an` 语法高亮、语言配置和原生 YIAN 语言服务器客户端。语言服务器源码位于仓库根目录 `lsp/`，由完整 native toolchain warm refresh 构建和安装。

## 安装工具链与服务器

先 cold bootstrap `yianc`、`anx`、`yian-fmt` 和标准库/运行时资源，再用这些原生 seeds warm refresh 完整工具链与服务器：

```bash
conda run --no-capture-output -n yian-env bash scripts/bootstrap_toolchain.sh \
  --prefix "$HOME/.local" --python python
PATH="$HOME/.local/bin:$PATH" \
  scripts/refresh_toolchain.sh --anx "$HOME/.local/bin/anx" \
    --yianc "$HOME/.local/bin/yianc" --prefix "$HOME/.local"
"$HOME/.local/bin/yian-lsp" --version
```

warm refresh 需要可用的原生 `anx` 与 `yianc`、LLVM 22、clang 22，以及已安装的共享资源。发布的完整工具链是 native binaries；语言服务器与 `anx fmt` 复用同一 `yian-fmt` 命令和标准库，不复制第二份格式化实现。服务器以 raw 模式和 `-O2` 构建，分析 YIAN 源码时默认使用 fat 指针。

服务器卸载（保留 `yianc`、`anx`、`yian-fmt` 与共享资源）：

```bash
scripts/uninstall.sh --lsp-only --prefix "$HOME/.local"
```

已有原生 `anx` 和 `yianc` 时，也可以直接用 warm 入口重建完整工具链和服务器；它会校验 staging 上的 LSP 握手后再发布：

```bash
scripts/refresh_toolchain.sh --anx "$HOME/.local/bin/anx" --yianc "$HOME/.local/bin/yianc" --prefix "$HOME/.local"
```

该入口不调用 Python，也不退回 cold 构建。若没有可用的原生 seed，先执行上面的 cold 安装，再显式运行 warm 刷新。

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

- 诊断、定义、悬停、文档符号、语义 token、引用、文档高亮、无注解变量的类型提示（inlay hints）和整文件格式化。
- 类型提示覆盖简单 `let`、解构 `let`、`for`、`match`、`if let`/`while let`、闭包捕获和闭包变量；显式注解与形参不重复提示，不可展示的字面量类型不提示。提示只读，不修改源码。
- 提示使用 VS Code 内建的 `editor.inlayHints.enabled`；可按语言设置 `"[yian]": {"editor.inlayHints.enabled": "off"}` 关闭。
- 工作区文档同步和文件变更更新。
- 未保存文本优先参与一次严格前端与语义分析；过期分析结果不会覆盖新版本。
- Format Document 使用当前未保存文本，通过已安装的 `yian-fmt` 子进程返回整文档编辑；已格式化、无法解析或格式化工具不可用的文本不产生修改。
- 不声明补全、签名帮助、重命名或代码操作；这些请求由服务器以 `MethodNotFound` 拒绝。
- 任一输入错误使当前分析单元不可导航并发布首错；修复后重新分析自动恢复，不返回旧导航。

## 打包扩展

```bash
cd ide-support/vscode
npm ci
npm run compile
npm run package
```

生成文件为 `ide-support/vscode/yian-language-support-0.8.0.vsix`。可以将其安装到 VS Code 手动验收；仓库自动检查不会启动 VS Code。

## 手动验收

1. 安装上面的 VSIX，并把 `yian.languageServer.command` 配置为 `yian-lsp` 的绝对路径。
2. 打开单文件工作区和带 `package.anx` 的多文件项目，检查版本信息和诊断。
3. 修改但不保存文件，检查悬停、定义、引用、文档高亮和语义高亮是否反映当前缓冲区；确认无注解变量显示 `: 类型` 提示、注解变量不重复，并用 `editor.inlayHints.enabled` 验证开关。
4. 在多文件项目中检查跨文件定义与引用；确认补全、重命名和代码操作请求被拒绝，Format Document 反映当前未保存的缓冲区。
5. 连续输入、保存、新增/删除源码并修复无效清单，确认诊断更新、失败期间没有旧导航，且修复后导航恢复。
6. 将 `yian.languageServer.args` 改为 `["--raw-pointers"]`，重启服务并重复代表性操作。
7. 关闭窗口后运行 `ps -eo pid,ppid,args | grep '[y]ian-lsp'`，确认该工作区的服务进程和 worker 已退出。

## 自动检查

从仓库根目录运行：

```bash
PATH="$HOME/.local/bin:$PATH" \
  conda run --no-capture-output -n yian-env python scripts/check_lsp.py --mode check
```

`check_lsp.py` 是协议客户端和测量工具，只启动已安装的原生 `yian-lsp`。`--mode benchmark` 运行性能矩阵，`--mode profile` 保存原生阶段 trace，`--mode infrastructure` 要求完整工具链及共享资源已安装后运行基础设施检查，不安装或刷新工具，也不运行协议场景。

`scripts/probe_lsp_navigation.py` 覆盖 inlay hints 的各绑定形态、注解与形参抑制、range 过滤、失败态和 `workspace/inlayHint/refresh`；`scripts/probe_lsp_cache.py --case restart` 比较严格分析与缓存恢复后的提示数据。

`--mode check` 默认运行 standalone、JSON project 和 compiler project 三种协议场景，并分别使用 fat/raw 两种模式，共六种组合。仓库验证应选择准确的场景和 pointer mode；完整矩阵需要明确授权。`--mode infrastructure` 还会运行 bootstrap package tests、全部 Python unit tests、formatter checks 以及 runtime 普通和 sanitizer checks；这是广泛验证入口，仓库任务执行前需先审查其间接范围并取得明确授权。
