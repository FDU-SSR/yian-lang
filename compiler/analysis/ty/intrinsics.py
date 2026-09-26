"""Stable type IDs reserved for intrinsic types and traits."""


class IntrinsicIds:
    never_id: int = 9
    void_id: int = 10
    bool_id: int = 11
    char_id: int = 12
    str_id: int = 13

    i8_id: int = 14
    i16_id: int = 15
    i32_id: int = 16
    i64_id: int = 17
    u8_id: int = 18
    u16_id: int = 19
    u32_id: int = 20
    u64_id: int = 21
    f16_id: int = 22
    f32_id: int = 23
    f64_id: int = 24
    int_literal_id: int = 25
    float_literal_id: int = 26
    error_id: int = 27

    add_id: int = 50
    sub_id: int = 51
    mul_id: int = 52
    div_id: int = 53
    rem_id: int = 54
    neg_id: int = 55
    bitand_id: int = 56
    bitor_id: int = 57
    bitxor_id: int = 58
    bitnot_id: int = 59
    shl_id: int = 60
    shr_id: int = 61
    partial_eq_id: int = 70
    partial_ord_id: int = 71
    index_id: int = 80
    contains_id: int = 81
    deref_id: int = 82
    delete_id: int = 83
    add_assign_id: int = 90
    sub_assign_id: int = 91
    mul_assign_id: int = 92
    div_assign_id: int = 93
    rem_assign_id: int = 94
    bitand_assign_id: int = 95
    bitor_assign_id: int = 96
    bitxor_assign_id: int = 97
    shl_assign_id: int = 98
    shr_assign_id: int = 99

    Range_id: int = 100
    Option_id: int = 101
    Result_id: int = 102
