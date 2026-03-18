# prelude

此文档描述 `yian` 标准库的 `prelude` 模块, 其中定义的所有类型和机制都会被默认导入到每个模块的作用域中.

## 内存管理

`Clone` 与 `Copy`. 详见 [mem 模块文档](./mem.md).

## 比较与排序

`PartialEq`, `PartialOrd`. 详见 [ops 模块文档](./ops.md).

## 迭代体系

`Iterator`, `IntoIterator`. 详见 [iter 模块文档](./iter.md).

## 类型转换

`From`, `Into`. 详见 [convert 模块文档](./convert.md).

## 类型

- `Option<T>`: 详见 [Option 类型文档](./core/option.md).
- `Result<T, E>`: 详见 [Result 类型文档](./core/result.md).
- `String`: 详见 [String 类型文档](./core/string.md).
- `Range<T>`: 详见 [Range 类型文档](./core/range.md).
