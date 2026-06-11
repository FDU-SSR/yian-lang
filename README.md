# YIAN编译器

## 运行环境

- Linux(like ubuntu)
- python3.10+
- clang

## 安装依赖库

```bash
pip install -r ./requirements.txt
```

## 编译yian代码

使用下来命令进行基础编译

```bash
./scripts/yian_compiler.py <文件或者目录路径>
```

编译脚本的详细用法见[编译脚本文档](docs/compile_script.md)

## For github users

### Environment

- Linux(like ubuntu)
- python3.10+
- clang

Install required packages:

```bash
pip install -r ./requirements.txt
```

### Compiling an example file

```bash
./scripts/yian_compiler.py tests/array/access.an
```
