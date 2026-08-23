"""Boundary tests for the monotonic temporal-key generator."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from compiler.codegen.cfg.lockmech import (
    BODY_MASK,
    FLAG_MASK,
    MAX_HEAP_BODY,
    MAX_STACK_BODY,
    SENTINEL,
    KeyGen,
)


def expect_overflow(action: object) -> None:
    try:
        action()  # type: ignore[operator]
    except OverflowError:
        return
    raise AssertionError("expected key exhaustion to raise OverflowError")


def main() -> None:
    heap = KeyGen()
    heap._KeyGen__heap_counter = MAX_HEAP_BODY - 1  # type: ignore[attr-defined]
    final_heap = heap.heap_key()
    assert final_heap == FLAG_MASK | (BODY_MASK - 1)
    assert final_heap != SENTINEL
    expect_overflow(heap.heap_key)

    stack = KeyGen()
    stack._KeyGen__stack_counter = MAX_STACK_BODY - 1  # type: ignore[attr-defined]
    final_stack = stack.stack_key()
    assert final_stack == BODY_MASK
    assert final_stack != SENTINEL
    expect_overflow(stack.stack_key)


if __name__ == "__main__":
    main()
