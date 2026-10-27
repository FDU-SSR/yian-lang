# CVE 机制见证项目

数据集包含 38 个编号，每个编号有一个 C 项目和一个 YIAN 项目，均保留触发缺陷的版本。
它与 `tests/` 下的编译器回归测试独立。项目拆分提供多文件构建边界，不代表复现保真度提高；
归档来源和缩减范围需要逐例核实，不能仅凭 guard 命中确认与原始缺陷的对应关系。

## 目录

```text
cve/
  index.json
  CVE-2021-33304/
    case.json
    c/
      CMakeLists.txt
      include/reproducer.h
      src/main.c
      src/reproducer.c
    yian/
      package.anx
      expected.txt
      src/main.an
      src/scenario.an
      src/support.an
```

YIAN 的 `main.an` 是进程入口，`scenario.an` 组织触发输入和执行过程，`support.an` 保存
原见证的类型与辅助函数；没有辅助定义的案例不包含 `support.an`。包名为 `witness`，
导入使用 `witness.scenario` 和 `witness.support`。项目不使用 FFI。

C 的进程入口与复现实现分别位于 `src/main.c` 和 `src/reproducer.c`，接口位于
`include/reproducer.h`。CMake 编译两个翻译单元并链接为 `build/reproducer`。
YIAN 经 `anx build` 生成 `build/app`。

## 来源与边界

C 文件取自 `archive/secl-paper-20260909` 分支的提交
`70db192f4299eb63765f561999ef87ac5362704f`，原路径为
`tests/fat_cve/cve/<编号>/<编号>.c`。这些文件本身是归档的最小化机制见证，
不能统一称为上游原始源码或完整项目复现；部分上游项目也不是 C 项目。

每个 `case.json` 保存归档提交、原路径、原文件 SHA-256、去除行尾空白及多余末尾空行后的
SHA-256，以及入口适配信息。C 实现的适配包括引入本地头文件、将 `main` 重命名为
`cve_reproduce`，并在需要时补上原 C `main` 隐含的末尾 `return 0`。
触发逻辑保持归档内容。归档文件与原始缺陷之间的对应仍需逐例核实。

YIAN 保存现有见证的触发输入、主函数体、辅助定义与预期安全诊断。跨模块定义及字段使用
显式 `pub`；局部参数、浅拷贝别名、分配和释放语义保持原见证的约定。
`case.json` 的 YIAN 来源哈希对应迁移前的单文件见证，不是当前多文件项目哈希。

## 运行

从仓库根目录执行；本机需有匹配源码的 native `yianc` 和 `anx`。
C 对照还需要 CMake 和支持 AddressSanitizer 的 C 编译器，默认使用 clang。
运行器不安装、刷新或回退工具链。

```sh
python scripts/run_cve.py --list
python scripts/run_cve.py --check-layout
python scripts/run_cve.py --case CVE-2021-33304
python scripts/run_cve.py --case CVE-2021-33304 --language both
python scripts/run_cve.py --case CVE-2020-15999 --language c --build-only
```

`--case` 可重复，按完整编号精确选择；缺少选择时退出 2，不隐式运行整组。
`--list` 和 `--check-layout` 不启动编译器。布局检查验证文件、预期结果与 C 来源适配哈希。
`--build-only` 只验证构建，结果为 `BUILD_ONLY`，不表示漏洞已触发。

`--language yian|c|both` 默认 `yian`。`--anx`、`--yianc` 和 `--cc` 指定工具，
分别默认读取 `ANX`、`YIANC`、`CC`，缺省使用 PATH。`--timeout` 默认每个子进程 60 秒。
YIAN 构建采用 `-O0`、默认受检 fat 指针；CMake 启用 ASan，并关闭泄漏检查以聚焦内存访问
和释放错误。Clang 构建显式启用 use-after-return 检测。C 参数由各 `case.json` 的 `args` 决定。

整组命令为 `python scripts/run_cve.py --all`，选择 38 个 YIAN 项目；
增加 `--language both` 则选择 38 个 C 项目和 38 个 YIAN 项目。
在 Agent 工作中，这些属于需要明确授权的批量运行；文档示例不构成执行授权。
`scripts/rerun_paper_experiments.sh` 的 CVE 阶段只调用默认 YIAN 选择，不包含编译器 safety 套件或 C 对照。

## 判定与报告

- YIAN：构建必须成功，运行退出码必须为 1，且输出包含该案例原有的完整预期安全诊断。
- C：构建必须成功，运行必须非零退出，且 ASan 报告匹配该案例声明的错误类别。
  错误类别是待运行验证的断言；目录完整不表示所有归档 C 文件都能触发相应错误。
- 构建失败、超时、普通崩溃或诊断不匹配均为失败，不能算作安全检查成功。
- 成功退出 0，选择错误退出 2，工具、构建或运行验证失败退出 1。

输出日志与 `report.json` 位于 `build/cve/`；报告记录实际选中的执行、工具路径、工具二进制
哈希及当前项目源码哈希。每次运行覆盖汇总报告，只能用报告中的选中范围解释结果。
历史论文的正反例结果不等价于此布局的一次完整运行。
