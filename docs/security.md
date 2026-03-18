# Key Lock

指针变量持有 address 和 key，内存管理块保存 lock 和指向实际数据的指针。使用指针访问内存时，key 和 lock 需要匹配。

同一时间只允许一个线程访问同一块内存。

```rust
enum Ptr<T> {
    Stack{
        T* base                 // address
        u64 offset              // 64 bit offset
        u64 len                 // 64 bit len, offset < len
    }
    Heap{
        u64 key                 // 64 bit key
        MemoryBlock<T>* base    // memory block address
        u64 offset              // 64 bit offset
    }
}

struct MemoryBlock<T> {
    u64 lock        // 64 bit lock
    u64 size        // 64 bit size
    T* data         // to buffer
}
```

申请内存, 构造堆指针

```rust
impl Ptr<T> {
    static Ptr<T> new_object() {
        // 生成 key 和 lock
        u64 key_lock = generate_key_lock()
        // 首先构造 MemoryBlock
        T* data = dyn T
        u64 size = sizeof(T)
        MemoryBlock<T>* base = dyn MemoryBlock<T>(key_lock, size, data)
        // 然后构造指针
        return Ptr<T>.Heap(key_lock, base, 0)
    }
    static Ptr<T> new_array(u64 len) {
        // 生成 key 和 lock
        u64 key_lock = generate_key_lock()
        // 首先构造 MemoryBlock
        T* data = dyn T[len]
        u64 size = sizeof(T) * len
        MemoryBlock<T>* base = dyn MemoryBlock<T>(key_lock, size, data)
        // 然后构造指针
        return Ptr<T>.Heap(key_lock, base, 0)
    }
}
```

对栈变量取地址, 构造栈指针. 或者将数组赋值给指针, 使其转换为栈指针

```rust
impl Ptr<T> {
    static Ptr<T> address(T* raw_ptr) {
        return Ptr<T>.Stack(raw_ptr, 0, sizeof(T))
    }

    static Ptr<T> from_array(T* array, u64 len) {
        return Ptr<T>.Stack(array, 0, len * sizeof(T))
    }
}
```

指针的读写

```rust
impl Ptr<T> {
    T read() {
        match self {
            Stack as ptr:
                return ptr.base[ptr.offset]
            Heap as ptr:
                // 检查 key 与 lock 匹配
                assert(ptr.key == ptr.base.lock)
                // 检查越界
                assert(ptr.offset != ptr.base.size)
                return ptr.base.data[ptr.offset]
        }
    }

    write(T value) {
        match self {
            Stack as ptr:
                ptr.base[ptr.offset] = value
            Heap as ptr:
                // 检查 key 与 lock 匹配
                assert(ptr.key == ptr.base.lock)
                // 检查越界
                assert(ptr.offset != ptr.base.size)
                ptr.base.data[ptr.offset] = value
        }
    }
}
```

释放内存

```rust
impl Ptr<T> {
    delete() {
        match self {
            Stack: panic("Delete stack pointer")
            Heap as ptr:
                // 检查 key 和 lock 匹配
                assert(ptr.key == ptr.base.lock)
                // 检查是否有偏移
                assert(ptr.offset == 0)
                // 释放内存
                del ptr.base.data
                // 清空内存管理快
                ptr.base.lock = 0
                ptr.base.size = 0
        }
    }
}
```

指针运算：

```rust
// 指针 + 整数
impl Add<u64, Ptr<T>> for Ptr<T> {
    Self add(u64 rhs) {
        match self {
            Stack as ptr:
                ptr.offset += rhs * sizeof(T)
                assert(ptr.offset <= ptr.len)
            Heap as ptr:
                // 检查 key 和 lock
                assert(ptr.key == ptr.base.lock)
                ptr.offset += rhs * sizeof(T)
                assert(ptr.offset <= ptr.base.size)
        }
    }
}

// 指针 - 整数
impl Sub<u64, Ptr<T>> for Ptr<T> {
    Self sub(u64 rhs) {
        match self {
            Stack as ptr:
                assert(ptr.offset >= rhs * sizeof(T))
                ptr.offset -= rhs * sizeof(T)
            Heap as ptr:
                // 检查 key 和 lock
                assert(ptr.key == ptr.base.lock)
                assert(ptr.offset >= rhs * sizeof(T))
                ptr.offset -= rhs * sizeof(T)
        }
    }
}

// 指针 - 指针
impl Sub<Ptr<T>, i64> for Ptr<T> {
    i64 sub(Ptr<T> rhs) {
        match self {
            Stack as lptr:
                match rhs {
                    Heap: panic("Stack ptr - Heap ptr")
                    Stack as rptr:
                        assert(lptr.base == rptr.base)
                        return (lptr.offset - rptr.offset) / sizeof(T)
                }
            Heap as lptr:
                match rhs {
                    Stack: panic("Heap ptr - Stack ptr")
                    Heap as rptr:
                        assert(lptr.base == rptr.base)
                        assert(lptr.key == rptr.key)
                        return (lptr.offset - rptr.offset) / sizeof(T)
                }
        }
    }
}
```

## 通过指针进行遍历

为了使用 `for...in` 遍历一个类型, 该类型必须满足下面的条件:

必须实现 `GetIterator` trait, 返回一个实现了 `Iterator` trait 的迭代器

```rust
struct PtrIter<T> {
    T* cur
    T* end
}

impl Iterator<T> for PtrIter<T> {
    Option<T> next() {
        if self.cur == self.end {
            return Option<T>.None
        } else {
            T res = *self.cur
            self.cur += sizeof(T)
            return Option<T>.Some(res)
        }
    }
}

impl GetIterator<PtrIter<T>> for Ptr<T> {
    PtrIter<T> get_iterator() {
        match self {
            Stack as ptr:
                T* begin = ptr.base + ptr.offset
                T* end = ptr.base + ptr.len
                return PtrIter<T>(begin, end)
            Heap as ptr:
                T* begin = ptr.base.data + ptr.offset
                T* end = ptr.base.data + ptr.base.size
                return PtrIter<T>(begin, end)
        }
    }
}
```
