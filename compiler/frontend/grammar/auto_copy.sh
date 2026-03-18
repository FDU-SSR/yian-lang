#!/bin/bash
tree-sitter generate
tree-sitter build
# 定义源文件夹和目标文件夹
SOURCE_DIR="/home/puppy/tree-sitter-java"  # 替换为实际的源文件夹路径
TARGET_DIR="../"  # 替换为实际的目标文件夹路径

# 检查源文件是否存在
if [ ! -f "$SOURCE_DIR/java.so" ]; then
    echo "错误: 源文件 $SOURCE_DIR/java.so 不存在!"
    exit 1
fi

# 检查目标文件夹是否存在，如果不存在则创建
if [ ! -d "$TARGET_DIR" ]; then
    mkdir -p "$TARGET_DIR"
    echo "注意: 目标文件夹 $TARGET_DIR 不存在，已自动创建"
fi

# 复制并重命名文件，-f 选项强制覆盖已存在的文件
cp -f "$SOURCE_DIR/java.so" "$TARGET_DIR/yian_lang_linux.so"

# 检查操作是否成功
if [ $? -eq 0 ]; then
    echo "文件已成功复制并重命名为 $TARGET_DIR/yian_lang_linux.so"
else
    echo "错误: 文件复制失败!"
    exit 1
fi