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
    """Scan single-value options and flags in order, allowing input paths between options."""
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
        # Attached values retain leading hyphens; argparse validates types, choices, and actions.
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

    # One end-of-options marker protects all input paths while preserving their command-line order.
    return parser.parse_args([*option_args, "--", *paths])
