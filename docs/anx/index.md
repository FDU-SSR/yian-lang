# anx 包管理器

`anx` 是 YIAN 的项目与包管理工具：创建项目、按包名解析导入、构建/运行/检查、跑项目测试、
格式化源码、输出依赖图。

- 语言语法与模块系统见[语言参考](../grammar/index.md)（其中 `13.modules.md` 讲 `import` 语法）
- 编译器内部实现见[开发手册](../manual/index.md)（编译器 01–19 章）
- anx 自己的内部实现见[开发手册 · anx 部分](../manual/index.md)（20–25 章：
  项目模型、清单与诊断、依赖图与包图契约、CLI、测试运行器）
- 编译器的命令行与 `--packages` 契约见[编译脚本文档](../compile_script.md) 2.6 节

## 目录

| 章节 | 标题 | 说明 |
| --- | --- | --- |
| [01](01.project.md) | 项目与清单 | 目录规范、`package.anx` 字段、`kind` 三档语义 |
| [02](02.dependencies.md) | 依赖 | 路径依赖、dev 依赖、可见性、依赖诊断 |
| [03](03.cli.md) | 命令行 | 七个子命令、选项、退出码、构建产物 |
| [04](04.testing.md) | 项目测试 | `tests/` 布局、期望文件、用例怎么编译 |
| [05](05.diagnostics.md) | 诊断目录 | `AX001`–`AX015` 的含义与产出方 |
| [06](06.install.md) | 安装与环境 | `install.sh`、标准库定位、非 editable 安装 |

## 快速上手

```bash
scripts/install.sh        # 一次安装：yianc、anx 与 yian-lsp 进入 PATH

anx new demo              # 生成 bin 项目骨架
cd demo

anx run                   # 构建到 build/app 并执行
anx test                  # 跑 tests/ 下的项目测试（骨架里已有一个通过的用例）
anx graph                 # 看依赖图与文件归属
```

`anx new` 生成的结构：

```text
demo/
├── package.anx           # 清单：包名、类型、依赖
├── src/
│   └── main.an           # 入口（默认 src/main.an）
├── tests/
│   ├── smoke.an          # 项目测试
│   └── output/
│       └── smoke.an.ans  # 期望 stdout
├── .gitignore            # 忽略 build/
└── build/                # 构建后才出现：pkg.json、app、test/
```

## 常用命令速查

| 目的 | 命令 |
| --- | --- |
| 新建可执行项目 | `anx new demo` |
| 新建库 / 库+CLI | `anx new mylib --kind lib` / `anx new tool --kind hybrid` |
| 构建到 `build/app` | `anx build` |
| 构建并运行（转发参数与 stdin） | `anx run -- alpha beta` |
| 只做类型检查（不产出可执行文件） | `anx check` |
| 跑项目自己的测试 | `anx test` |
| 格式化根包源码 | `anx fmt` / `anx fmt --check` |
| 打印依赖图 | `anx graph` / `anx graph --json` |
| 提高优化级别 | `anx build --release` 或 `anx build -O 3` |

命令可以在项目的任意子目录里执行：`anx` 会向上查找最近的 `package.anx`（找不到报
`AX001`），也可以显式传项目根目录，例如 `anx build path/to/demo`。

## 一个最小项目

```toml
# demo/package.anx
[package]
name = "demo"
kind = "bin"
version = "0.1.0"

[dependencies]
mathlib = { path = "vendor/mathlib" }
```

```yian
// demo/src/greet.an
from std.core.io import print;

pub fn greet() {
    print("hello from demo\n");
}
```

```yian
// demo/src/main.an
from demo.greet import greet;        // 本包模块：第一段是包名，不是目录
from mathlib.ops import add;         // 依赖包的模块

fn main() {
    greet();
    assert add(2, 3) == 5: "add(2, 3) must be 5";
}
```

要点：`import` 的第一段永远是**包名**；一个包只能导入**自己、自己直接声明的依赖、`std`**；
导入必须落在一个存在的 `.an` 文件上。细节见[依赖](02.dependencies.md)与
[语言参考 · 模块系统](../grammar/13.modules.md)。
