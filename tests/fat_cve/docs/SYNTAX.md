# 语法迁移指南：旧语法（C 风格）→ 胖指针语法

本文件供 CVE 移植使用：把 bak/ 中老实验的 C 风格 YIAN 源码（`char*`、`u32 my_strlen(...)`、
`i32 main(i32 argc, char** argv)` 等旧形态）迁移为当前 YIAN + 胖指针语法。

> 当前语言形态参照：`tests/fat/`（胖指针正/负例）、`tests/std/`（标准库用法）、
> `lib/`（标准库自身）、`docs/grammar/`（语言手册）。

## 旧语法 → 胖指针语法对照表

| 旧语法（老实验/bak） | 胖指针语法（当前） | 说明 |
|---|---|---|
| `char*` | `u8*` | 字节指针；C 的 `char` 即 1 字节，YIAN 中字节类型为 `u8` |
| `dyn char[n]` | `dyn[n] u8` | 动态字节数组：`let p: u8* = dyn[n] u8;` |
| `dyn char` | `dyn u8` | 单元素动态分配：`let p: u8* = dyn u8;` |
| `.is_valid()`（手动检查指针再解引用） | 删除或改 `!= nullptr` 显式比较 | 胖指针 null 检查内建：`lock_ptr == 0` 时 `live` 短路为假（security.md 定义 8 null 短路），解引用自动 trap，无需手写守卫；如需显式判空用 `p != nullptr` |
| `Ptr<T>` | `T*` | 指针一律写 `T*`（如 `i32*`、`u8*`、`Person*`）；数组退化 `T[m] → T*` 由编译器完成 |
| `str`/切片旧 API | 当前 stdlib 公开 API | 切片：`len()`、`ptr()`、`get(i)`、`first()`、`last()`、`to_vec()`（`lib/core/slice.an`）；str：`as_slice()`、`chars()`、`len()`、`starts_with()`、`contains_char()`、`trim()`、`parse<T>()` 等（`lib/core/str.an`）。不要使用 `from_raw_parts` 等受限原语（security-code.md §8.3） |
| `u32 my_strlen(char* s)`（返回值类型前缀旧签名） | `fn my_strlen(s: u8*) -> u64 { ... }` | 函数签名改为 `fn 名(参数: 类型) -> 返回类型`，返回值类型后置 |
| `i32 main(i32 argc, char** argv)` | `fn main()` | 入口统一 `fn main()`，无参数、无返回值（CLI 参数/环境经 `tests/input/<name>.args` 提供，见 run_tests.py `_find_input`） |
| `fn name() { }`（旧函数体形态） | `fn name() { }`（当前形态） | 函数体为表达式块；`fn main()` 后 `{` 可与函数名同行或换行（见 tests/fat/positive/dyn.an） |
| `impl Type { fn m(self) { ... } }` | `impl Type { pub fn m(self) { ... } }` / `static fn` | 当前 impl 语法参照 `lib/core/str.an`（`impl str { ... }`）、`tests/std/core/convert.an`（`impl From<i32> for FakeI32 { static fn ... }`）；实例方法 `pub fn m(self)`、静态方法 `static fn`/`pub static fn` |
| 手动 `free`/释放后继续用 | `del p;`（删除） | 释放 `delete p` 前置检查 `is_heap ∧ live ∧ is_raw`（规则 3.6.2），不满足即 trap |

### 指针字面量与比较

- 空指针字面量：`null`（规则 8.1.4，无条件隐转 `null : T*`）。
- 相等比较 `p == q`：按 `(data, index)` 字段化（规则 3.4.2），允许跨对象。
- 序比较 `p < q`：跨对象 trap（规则 3.4.1 前提 `data` 相等）。

## 分号语义（关键，引用源码）

YIAN 的块内语句是**表达式**，分号决定语句的「丢弃」性质：

- `compiler/frontend/parse/parser_expr.py:435-453`（`__parse_expr_stmt`）：解析一个表达式，
  若其后跟 `;` 则包装为 `AST.Semi`，否则作为裸表达式返回。块的类型由最后一项决定：
  `Semi` → void，裸表达式 → 该表达式类型（即块返回值）。
- `compiler/frontend/parse/stream.py:150-155`（`consume_semicolon`）：分号是**可选消费**——
  不存在时静默跳过、返回合成的 `Semicolon` 记号。
- `docs/grammar/01.lexicon.md:168`：`;` — 语句终止符（「Yian 2026 语法中每句必须以 `;` 结尾」）。

**结论：每句加分号。** 因为分号可选消费，缺分号本身不报错，但下一行若以运算符起始
（如解引用 `*p`、取址 `&x`、算术 `p + 1`），表达式会与上一行**粘连**解析成同一表达式
（上一轮 fat_field_reanchor 的教训）。C 风格源码每句末尾补 `;` 可完全规避该风险。

示例：

```yian
// 错误：缺分号，`*q = 5` 与上一行粘连
let p: i32* = dyn i32
*q = 5

// 正确：每句加分号
let p: i32* = dyn i32;
*q = 5;
```

## 最小可编译骨架

```yian
// 负例骨架（漏洞形态 → 应 trap → Exit code -4）
fn main() {
    let p: i32* = dyn[2] i32;
    p[0] = 1;
    del p;
    p[0] = 99;  // UAF: live 失败 → trap
}

// 修复版骨架（等价正确实现 → 通过）
fn main() {
    let p: i32* = dyn[2] i32;
    p[0] = 1;
    assert p[0] == 1;
}
```
