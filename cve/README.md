# CVE 机制见证项目

数据集包含 38 个编号，每个编号是一对程序，位于 `cve/<编号>/`：`c/` 是带缺陷的
参考程序，`yian/` 是对应的 YIAN 改写。
翻译、表示差异和语义对应的审查统一采用
[C → YIAN 改写规范](../docs/c-to-yian.md)；本文只记录数据集现状和运行器行为。
规范为 v0.2 初稿。当前源码已按明确条款改写并作静态复核；
[审查报告](../docs/c-to-yian-review.md) 区分未解决差异、构建检查与实际运行。

38 个案例都有 `README.md`，记录 C 与 YIAN 的函数对应、表示差异和第一次非法操作。
其中 15 个按 note.md 的 P0/P1 名单和上游记录重写（Linux cleanup 只重写 YIAN 一侧），
另 23 个保留较早的最小化见证，并已按同一契约审计：输入由同一 `args` 或同一字节序列驱动，
硬编码的触发值改为从输入派生。当前仍有 8 个案例的存储期差异和 HyperKit 的
状态/部分输出表示问题，未据此宣称 38 对完全通过规范。项目与 `tests/` 下的编译器回归测试独立；
目录完整或 guard 命中不单独证明与上游缺陷的对应关系。

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
  CVE-2020-15999/
    README.md               # source-mapped witness notes
    case.json
    c/
    yian/
```

YIAN 的 `main.an` 是进程入口，`scenario.an` 组织触发输入和执行过程，`support.an` 保存
原见证的类型与辅助函数；没有辅助定义的案例不包含 `support.an`。包名为 `witness`，
导入使用 `witness.scenario` 和 `witness.support`。项目不使用 FFI。

C 的进程入口与复现实现分别位于 `src/main.c` 和 `src/reproducer.c`，接口位于
`include/reproducer.h`。CMake 编译翻译单元并链接为 `build/reproducer`。
YIAN 经 `anx build` 生成 `build/app`。

## 见证与来源

每个编号只保留当前使用的一份 C 项目和一份 YIAN 项目；`case.json` 记录该见证的参数、
ASan 类别、YIAN 退出码和安全诊断，不记录旧副本或逐例哈希。

每个案例的 `README.md` 记录 C 与 YIAN 的函数对应、输入与第一次非法操作、表示差异和
运行命令；15 个上游重写案例另有版本/修复与替代设施的记录。所有案例都是缩减模型，
不声称是上游项目的完整移植；部分上游项目不是 C 项目，C 文件是模型而不是原始源码。

### 语言设施与失败语义

YIAN 提供 `@print`/`@println`/`@eprint`/`@eprintln` 以及 `std.core.io`，但见证不使用
标准输出。C 的 `printf` 语句已移除，因为它们不参与触发；唯一保留的是 CVE-2022-31003 的
`printf("option o, value: %s", rest)`，它本身就是第一次越界读取的载体。运行器只比较退出码
和安全诊断，不比较 stdout。

`dyn` 分配失败是运行时资源错误 `R002`（`yian: runtime error [R002]: memory allocation
failed`）：写入标准错误后立即 `_exit(1)`，不展开调用栈，也不返回 NULL。因此 C 的
`malloc` 失败分支没有可返回的值与之对应；触发路径上的分配不会失败。

较早的 C 见证来自历史数据集提交
`70db192f4299eb63765f561999ef87ac5362704f`，原路径为
`tests/fat_cve/cve/<编号>/<编号>.c`。当前 C 项目在该形状上做了本地入口适配：引入本地
头文件，把 `main` 改名为 `cve_reproduce`，触发逻辑不变。这一条只说明文件来源，不证明
与上游缺陷的逐行对应。

## 运行

从仓库根目录执行；本机需有匹配源码的 native `yianc` 和 `anx`。
C 对照还需要 CMake 和支持 AddressSanitizer 的 C 编译器，默认使用 clang。
运行器不安装、刷新或回退工具链。

```sh
python scripts/run_cve.py --list
python scripts/run_cve.py --check-layout
python scripts/run_cve.py --case CVE-2021-33304
python scripts/run_cve.py --case CVE-2021-33304 --language both
python scripts/run_cve.py --case CVE-2023-45664 --language both
python scripts/run_cve.py --case CVE-2020-15999 --language c --build-only
```

`--case` 可重复，按完整编号精确选择；缺少选择时退出 2，不隐式运行整组。
`--list` 和 `--check-layout` 不启动编译器。布局检查验证项目文件、`expected.txt`
与 `case.json` 的预期结果一致。`--build-only` 只验证构建，结果为 `BUILD_ONLY`，
不表示漏洞已触发。

`--language yian|c|both` 默认 `yian`。`--anx`、`--yianc` 和 `--cc` 指定工具，
分别默认读取 `ANX`、`YIANC`、`CC`，缺省使用 PATH。`--timeout` 默认每个子进程 60 秒。
YIAN 构建采用 `-O0`、默认受检 fat 指针。CMake 启用 ASan，并关闭泄漏检查以聚焦内存
访问和释放错误。Clang 构建显式启用 use-after-return 检测。两侧使用 `case.json` 的
同一组 `args`。

整组命令为 `python scripts/run_cve.py --all`，选择 38 个 YIAN 项目；
增加 `--language both` 则选择 38 个 C 项目和 38 个 YIAN 项目。
在 Agent 工作中，这些属于需要明确授权的批量运行；文档示例不构成执行授权。
`scripts/rerun_paper_experiments.sh` 的 CVE 阶段只调用默认 YIAN 选择，不包含编译器
safety 套件或 C 对照。

## 判定与报告

- YIAN：构建必须成功，运行退出码必须为 1，且输出包含该案例声明的完整安全诊断。
- C：构建必须成功并完成运行。原缺陷可以静默存在，ASan 是否命中只作为观测记录
  （报告的 `asan` 字段），不作为失败条件。`c.expected_asan` 描述期望的观测类别。
  目录完整不表示所有 C 项目都能触发相应错误。
- 构建失败、超时、普通崩溃或诊断不匹配均为失败，不能算作安全检查成功。
- 成功退出 0，选择错误退出 2，工具、构建或运行验证失败退出 1。

输出日志与 `report.json` 位于 `build/cve/`；报告记录实际选中的执行、工具路径、
工具二进制哈希及当前项目源码哈希。每次运行覆盖汇总报告，只能用报告中的选中范围解释结果。
先前的正反例结果不等价于此布局的一次完整运行。
