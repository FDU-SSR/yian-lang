from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence


def __is_option(text: str) -> bool:
    if len(text) < 2 or text[0] != "-":
        return False
    if text[1] in "0123456789":
        return False
    if len(text) > 2 and text[1] == "." and text[2] in "0123456789":
        return False
    return True


def parse_arguments(
    parser: argparse.ArgumentParser,
    options: Mapping[str, argparse.Action],
    argv: Sequence[str],
) -> argparse.Namespace:
    """按顺序扫描单值选项和标志，输入路径可与选项混排。"""
    option_args: list[str] = []
    paths: list[str] = []
    index = 0

    def __consume(name: str, attached: str | None) -> None:
        nonlocal index
        action = options.get(name)
        if action is None:
            parser.error(f"unrecognized argument: {name}")
        if action.nargs == 0:
            if attached is not None:
                parser.error(f"argument {name}: does not accept a value")
            option_args.append(name)
            return
        if attached is None:
            if index == len(argv) or __is_option(argv[index]):
                parser.error(f"argument {name}: expected one argument")
            attached = argv[index]
            index += 1
        # 附加值保留以横杠开头的文本；argparse 校验类型、choices 和 action。
        option_args.append(f"{action.option_strings[-1]}={attached}")

    while index < len(argv):
        text = argv[index]
        index += 1
        if text == "--":
            paths.extend(argv[index:])
            break
        if not __is_option(text):
            paths.append(text)
            continue
        if text.startswith("--"):
            name, separator, value = text.partition("=")
            attached = value if separator else None
            __consume(name, attached)
            if name == "--help":
                return parser.parse_args(option_args)
            continue
        offset = 1
        while offset < len(text):
            name = f"-{text[offset]}"
            if name == "-h":
                option_args.append(name)
                return parser.parse_args(option_args)
            action = options.get(name)
            if action is None:
                parser.error(f"unrecognized argument: {name}")
            takes_value = action.nargs != 0
            attached = text[offset + 1:] if takes_value and offset + 1 < len(text) else None
            if attached is not None and attached.startswith("="):
                attached = attached[1:]
            __consume(name, attached)
            if takes_value:
                break
            offset += 1

    # 一个结束标记保护所有输入路径，并保留其在命令行中的顺序。
    return parser.parse_args([*option_args, "--", *paths])
