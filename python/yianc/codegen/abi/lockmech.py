"""Fat-pointer lifetime mechanisms: lock table, keys, and frame locks. LLVM lowers their values.

This module defines the runtime concepts used by fat-pointer memory safety, as described in
docs/security.md and implemented by the CFG and LLVM layers:

- **Second pointer word** `word = <key:32 | lock:32>` (lock occupies the low bits; see `WORD_KEY_SHIFT`):
  `key` identifies an allocation lifetime; `lock` indexes the global lock table.
- **Lock table** `__secl_lock_table`: each 8 B entry is `{key:u32 @0, anchor_lo32:u32 @4}`
  (see `LockEntry`). Index ranges encode kind: `[0, FRAME_LOCK_SLOTS)` contains frame shadow-stack
  slots (indexed by depth), followed by literal/environment constant slots and allocator-issued heap slots.
  The hot path needs no kind dispatch: the word's lock field determines the slot address.
- **Predicates** (emitted inline by LLVM with the semantics defined here):
  `live` = `word != 0 and load32(lock_table[lock].key) == key`;
  `is_heap` tests the heap index range without reading the table;
  `is_raw` checks the payload anchor and index fields.
- **Block header** `BlockHeader`: 8 B = `{extent:u32 @0, pad:u32 @4}`; payload = block base + 8.
  The lock entry stores the identity key, and the pointer carries the same key.
- **Frame locks**: one active slot per frame, re-keyed on entry (`k_f <- Gen()`, a 32-bit key),
  set to `SENTINEL` on every return path, in the stable-address `FrameLockArena` shadow stack.
- **Heap lifetime**: LLVM writes the entry's `key`/`anchor_lo32` on allocation; `del` increments key
  (immediately invalidating dangling pointers) and pushes the index onto the free list.

This module defines constants, layouts, protocols, and predicate semantics. CFG inserts checks; LLVM
lowers values and runtime failures. `runtime/include/yian_rt.h` defines the C ABI constants, and
`runtime/build.py --check` verifies frame slots, slot width, header bytes, size classes, and failure messages.
"""

from __future__ import annotations

from typing import ClassVar

# ---------------------------------------------------------------------------
# Word encoding, keys, and sentinel.
# ---------------------------------------------------------------------------

WORD_LOCK_SHIFT = 0
"""Shift of the lock field in word (low bits)."""

LOCK_BITS = 32
"""Lock field width: the table has 2^26 entries, leaving the upper index bits unused."""

LOCK_MASK = (1 << LOCK_BITS) - 1
"""Lock field mask (the low 32 bits of word)."""

WORD_KEY_SHIFT = 32
"""Shift of the key field in word (high bits)."""

KEY_MASK = 0xFFFF_FFFF
"""Key field mask: 32 bits, matching the lock entry's u32 key."""

FRAME_KEY_LIMIT = KEY_MASK - 1
"""Frame-key limit; reserve 0xFFFFFFFF for the exit SENTINEL and never wrap to reuse old identities."""

SENTINEL = 0xFFFF_FFFF_FFFF_FFFF
"""Sentinel value: an all-ones word.

Frame exit writes it to the frame lock slot with an LLVM volatile store. The 32-bit key comparison
reads 0xFFFFFFFF, while active keys never exceed `FRAME_KEY_LIMIT`, so active pointers cannot match.
Heap release increments the lock entry's key (see `LockEntry`), likewise invalidating dangling pointers.
"""

# View length width and limit: PointerType stores index/size as 32-bit element counts.
# Malloc verifies allocation counts <= MAX_VIEW_COUNT (R001 on overflow). View lengths derive from
# allocation capacities, so the length component carried by a pointer is representable.
VIEW_COUNT_BITS = 32
MAX_VIEW_COUNT = (1 << VIEW_COUNT_BITS) - 1

# 4 GiB windows: reconstruct the payload anchor from the data address's high 32 bits and a low-32-bit field,
# so [block base, block base + header + payload) must lie in one window (guaranteed by the allocator).
WINDOW_MASK = ((1 << 64) - 1) ^ LOCK_MASK

# Lock-table index ranges (their order determines kind).
FRAME_LOCK_SLOTS = 1 << 20
LITERAL_LOCK_INDEX = FRAME_LOCK_SLOTS
ENV_LOCK_INDEX = FRAME_LOCK_SLOTS + 1
HEAP_LOCK_BASE = FRAME_LOCK_SLOTS + 2
LOCK_ENTRY_BYTES = 8
LOCK_TABLE_SLOTS = 1 << 26

# Literal/environment constant slots: key is always zero and never changes, so these pointers stay live.
# The word's lock field uses the constant indices above.
LITERAL_KEY = 0
ENV_KEY = 0
LITERAL_WORD = (LITERAL_KEY << WORD_KEY_SHIFT) | LITERAL_LOCK_INDEX
ENV_WORD = (ENV_KEY << WORD_KEY_SHIFT) | ENV_LOCK_INDEX

# Field indices for the four-field fat pointer (PointerType maps to
# the 24 B aggregate {data: ptr, word: u64, index: u32, size: u32}).
FAT_DATA = 0
FAT_WORD = 1
FAT_INDEX = 2
FAT_SIZE = 3

# Field indices for a three-field slice: {data: ptr, word: u64, size: u64}, 24 B.
SLICE_DATA = 0
SLICE_WORD = 1
SLICE_SIZE = 2

# Field indices for a two-field reference: {data: ptr, word: u64}, 16 B.
REF_DATA = 0
REF_WORD = 1


def is_heap(lock: int) -> bool:
    """`is_heap(p)` tests only the lock-table index: it must be >= HEAP_LOCK_BASE."""
    return lock >= HEAP_LOCK_BASE


def live(word: int, slot_key: int) -> bool:
    """`live(p)` = word != 0 and lock-entry key equals the key carried by the pointer.

    LLVM loads the entry indexed by word's low 32 bits, compares its 32-bit key, and combines
    the result with word != 0 (word = 0 reads entry zero but cannot make the predicate true).
    """
    if word == 0:
        return False
    return slot_key == ((word >> WORD_KEY_SHIFT) & KEY_MASK)


def is_raw(data: int, anchor: int, index: int) -> bool:
    """`is_raw` checks fields: `(data & LOCK_MASK) == anchor and index == 0`.

    `anchor` is the entry's `anchor_lo32`, the payload address's low 32 bits. Comparison relies on
    the allocator keeping the whole block in one 4 GiB window (see `WINDOW_MASK`).
    Re-anchored subobject pointers (`&s.field`) and offset pointers (`p +/- n`, `&arr[i != 0]`)
    differ from the allocation anchor and are rejected by `del`.
    """
    return (data & LOCK_MASK) == anchor and index == 0


class BlockHeader:
    """Block-header layout for the single-threaded heap pool.

    `{extent:u32 @0, pad:u32 @4}` occupies 8 B; payload = block base + 8. `extent` is the allocation's
    element count (u32; deletion/view checks convert it to bytes using the element size). Identity is
    in the lock entry and pointer; pad keeps the payload 8-byte aligned, the language's maximum alignment.
    """

    BYTES: ClassVar[int] = 8
    EXTENT_OFFSET: ClassVar[int] = 0  # Active payload element count (u32).

    @staticmethod
    def data_addr(block_base: int) -> int:
        """Payload anchor address: `data = b + H` (allocation anchor)."""
        return block_base + BlockHeader.BYTES


class LockEntry:
    """Lock-entry layout: `{key:u32 @0, anchor_lo32:u32 @4}`, 8 B.

    Key identifies the lifetime and increments on allocation and release, invalidating dangling pointers.
    anchor_lo32 holds the payload address's low 32 bits; deletion/views combine it with data's high bits.
    Free entries store the next free index + 1 in anchor; the runtime's
    `__secl_lock_free_head` stores the head. Frame and constant slots use only key.
    """

    BYTES: ClassVar[int] = LOCK_ENTRY_BYTES
    KEY_OFFSET: ClassVar[int] = 0
    ANCHOR_OFFSET: ClassVar[int] = 4


class FrameLockArena:
    """Stable frame-lock shadow-stack layout for a single thread.

    Slots are fixed-address u64 array elements for the process lifetime, accessed only by trusted
    frame entry/exit protocols. Active frames occupy slots in LIFO order; exit writes SENTINEL and pops.
    Reuse installs a new key before user code runs. Capacity and key exhaustion fail deterministically,
    without wrapping or falling back to ordinary stack storage.

    2^20 slots reserve 8 MiB of virtual memory; resident pages scale with peak active addressed frames.
    """

    SLOTS: ClassVar[int] = 1 << 20
    SLOT_BYTES: ClassVar[int] = 8


# ---------------------------------------------------------------------------
# Fat-value synthesis at standard-library boundaries (mechanism; implemented by CFG/LLVM).
#
# SliceStruct<T> in lib/core/slice.an is a source-level wrapper: its T* field is 24 B in fat mode,
# plus a length field; in raw mode the fields occupy 8 B + 8 B.
# SliceType/StrType are separate built-in view types: fat mode uses {data, word, size}, 24 B,
# and raw mode uses {T*, u64}, 16 B. Constructors synthesize <data, word, size>,
# where data is the decayed/addressed payload and word is inherited from the source pointer's frame or heap lock,
# and size is the slice or string length. See SLICE_* for field indices.
#
# Metadata is injected at construction (VarPtr / Malloc CFG/LLVM lowering) and flows with the value.
# This module exposes no separate fat-construction primitive to YIAN source code.
# ---------------------------------------------------------------------------
