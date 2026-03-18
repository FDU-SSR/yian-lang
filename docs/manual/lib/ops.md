# ops 模块

`ops` 模块包含了一些与操作符重载相关的类型和函数, 这些类型和函数定义了如何实现自定义类型的各种操作符行为.

## 算数运算

```text
// 二元加(`+`)
trait Add<Rhs, Output> {
    Output add(Rhs rhs);
}

// 二元减(`-`)
trait Sub<Rhs, Output> {
    Output sub(Rhs rhs);
}

// 二元乘(`*`)
trait Mul<Rhs, Output> {
    Output mul(Rhs rhs);
}

// 二元除(`/`)
trait Div<Rhs, Output> {
    Output div(Rhs rhs);
}

// 二元取模(`%`)
trait Rem<Rhs, Output> {
    Output rem(Rhs rhs);
}

// 一元减(`-`)
trait Neg<Output> {
    Output neg();
}
```

## 位运算

```text
// 二元按位与(`&`)
trait BitAnd<Rhs, Output> {
    Output bit_and(Rhs rhs);
}

// 二元按位或(`|`)
trait BitOr<Rhs, Output> {
    Output bit_or(Rhs rhs);
}

// 二元按位异或(`^`)
trait BitXor<Rhs, Output> {
    Output bit_xor(Rhs rhs);
}

// 一元按位非(`~`)
trait Not<Output> {
    Output not();
}

// 左移(`<<`)
trait Shl<Rhs, Output> {
    Output shl(Rhs rhs);
}

// 右移(`>>`)
trait Shr<Rhs, Output> {
    Output shr(Rhs rhs);
}
```

## 比较运算

```text
// 等于(`==`)/不等于(`!=`)
trait PartialEq<Rhs> {
    bool eq(Rhs rhs);
    bool ne(Rhs rhs) {
        return not self.eq(rhs)
    }
}

// 小于(`<`)/小于等于(`<=`)/大于等于(`>=`)/大于(`>`)
enum Ordering {
    Less,
    Equal,
    Greater,
}

trait PartialOrd<Rhs> {
    Ordering partial_cmp(Rhs rhs);
    bool lt(Rhs rhs) {
        match self.partial_cmp(rhs) {
            Less { return true }
            _ { return false }
        }
    }
    bool le(Rhs rhs) {
        match self.partial_cmp(rhs) {
            Less, Equal { return true }
            _ { return false }
        }
    }
    bool ge(Rhs rhs) {
        match self.partial_cmp(rhs) {
            Greater, Equal { return true }
            _ { return false }
        }
    }
    bool gt(Rhs rhs) {
        match self.partial_cmp(rhs) {
            Greater { return true }
            _ { return false }
        }
    }
}
```

## 复合赋值

```text
trait AddAssign<Rhs> {
    add_assign(Rhs rhs);
}

trait SubAssign<Rhs> {
    sub_assign(Rhs rhs);
}

trait MulAssign<Rhs> {
    mul_assign(Rhs rhs);
}

trait DivAssign<Rhs> {
    div_assign(Rhs rhs);
}

trait RemAssign<Rhs> {
    rem_assign(Rhs rhs);
}

trait BitAndAssign<Rhs> {
    bit_and_assign(Rhs rhs);
}

trait BitOrAssign<Rhs> {
    bit_or_assign(Rhs rhs);
}

trait BitXorAssign<Rhs> {
    bit_xor_assign(Rhs rhs);
}

trait ShlAssign<Rhs> {
    shl_assign(Rhs rhs);
}

trait ShrAssign<Rhs> {
    shr_assign(Rhs rhs);
}
```

## 索引运算

```text
trait Index<Idx, Output> {
    Output* index(Idx index);
}
```

## 解引用运算

```text
trait Deref<Target> {
    Target* deref();
}
```
