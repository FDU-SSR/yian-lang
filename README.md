

## 运行环境

- Linux(like ubuntu) 
- python3.10+
- clang

## 安装依赖库
```
$ pip install -r ./requirements.txt
```

## 配置仓库

使用下列命令配置依赖子模块（莲花系统：用于代码分析）

```
$ ./scripts/config-submodules.sh
```

## 编译yian代码

使用下来命令进行编译
```
$ ./scripts/yian_compiler.py <文件或者目录路径>
```

也可以添加优化等级设置（-o0, -o1, -o2, -o3, or -os），例如
```
$ ./scripts/yian_compiler.py -o2 tests/core_function/test1.an
```

## 如何合并代码

- 使用 git commit -m "message" 命令将自己的代码先保存

- 使用 git pull 命令从服务器上拉取最新代码，这个命令会提示哪些代码需要合并

将需要合并的文件逐一打开，进行手动合并
手动合并的方法是：打开带有合并冲突的代码文件，搜索 <<<<<<< ，找到冲突代码段（这段代码实际上是一个diff），然后手动消除diff部分

- 合并完成后，运行 git add <合并的文件> 命令将合并后的文件添加到暂存区

- 最后，运行 git rebase --continue 命令结束操作


## For github users

### Environment

- Linux(like ubuntu) 
- python3.10+
- clang
- Install required packages
```
$ cd yian-lang
$ pip install -r ./requirements.txt
```

### LIAN is missing

LIAN is a program analysis platform developed by ourselves.  It provides middle-end for YIAN. 

It may be difficult to import the LIAN package. To mitigate this problem, download LIAN:

```
$ cd yian-lang
$ rm -r lian
$ git clone git@github.com:yang-guangliang/lian.git
```

### Compiling an example into llvm code

```
$ ./scripts/compiler.sh tests/syntax/202412-1/func.an 
```

### Compiling an example into executable program
Add -o0, -o1, -o2, -o3, or -os to set the optimization level

```
$ ./scripts/yian_compiler.py tests/core_function/test1.an 

$ ./scripts/yian_compiler.py -o2 tests/core_function/test1.an
```

## Configure submodules

To configure submodules, run the following command:

```
$ ./scripts/config-submodules.sh
```

## How to merge code

1. Commit your local changes:
```
git commit -m "message"
```

2. Pull the latest code from the remote repository:
```
git pull
```
This command may prompt you to resolve conflicts if there are any overlapping changes.

3. Resolve any conflicts manually:
Seach for <<<<<<< which marks the beginning of the conflict block.
Manually resolve the conflict by editing the file to keep the correct code and remove conflict markers

4. Add the resolved files to the staging area:
```
git add <file>
```

5. Complete the merge or rebase process:
```
git rebase --continue
```




