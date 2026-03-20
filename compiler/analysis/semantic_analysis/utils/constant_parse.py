from compiler.utils import IR
from compiler.utils.errors import YianSyntaxError

__ESCAPE_LETTERS = {
    "n": "\n",
    "t": "\t",
    "r": "\r",
    "\\": "\\",
    "'": "'",
    "\"": "\"",
    "0": "\0",
}

__BASE_DIGITS = {
    2: "01",
    8: "01234567",
    10: "0123456789",
    16: "0123456789abcdefABCDEF",
}

__TYPE_LIMITS = {
    'i8': (-2**7, 2**7 - 1),
    'u8': (0, 2**8 - 1),
    'i16': (-2**15, 2**15 - 1),
    'u16': (0, 2**16 - 1),
    'i32': (-2**31, 2**31 - 1),
    'u32': (0, 2**32 - 1),
    'i64': (-2**63, 2**63 - 1),
    'u64': (0, 2**64 - 1),
    'f32': (float('-inf'), float('inf')),
    'f64': (float('-inf'), float('inf')),
}


def __consume_prefix(s: str, prefix: str) -> str:
    """
    Consumes the given prefix from the string s.
    """
    if not s.startswith(prefix):
        raise YianSyntaxError(f"Expected prefix '{prefix}' in '{s}'")
    return s[len(prefix):]


def __consume_chars(s: str, num_chars: int) -> tuple[str, str]:
    """
    Consumes the given number of characters from the string s and returns the consumed characters and the remaining string.
    """
    if len(s) < num_chars:
        raise YianSyntaxError(f"Expected at least {num_chars} characters in '{s}'")
    return s[:num_chars], s[num_chars:]


def __consume_one(s: str) -> tuple[str, str]:
    """
    Consume one character from the string s and return the character and the remaining string.
    """
    if len(s) == 0:
        raise YianSyntaxError("Expected at least one character")
    return s[0], s[1:]


def __consume_escape_sequence(s: str) -> tuple[str, str]:
    r"""
    Consumes an escape sequence from the string s and returns the corresponding character and the remaining string.

    1. Standard escape sequences: \n, \t, \r, \\, \', \", \0
    2. Hexadecimal escape sequences: \xHH (where HH are two hex digits)
    3. Unicode escape sequences: \u{H...} (where H... is one or more hex digits)
    """
    if not s.startswith("\\"):
        raise YianSyntaxError("Expected escape sequence starting with '\\'")

    s = __consume_prefix(s, "\\")

    escape_char, s = __consume_one(s)

    if escape_char in __ESCAPE_LETTERS:
        return __ESCAPE_LETTERS[escape_char], s
    elif escape_char == "x" or escape_char == "X":
        # hex character
        hex_str, s = __consume_chars(s, 2)
        if not all(c in __BASE_DIGITS[16] for c in hex_str):
            raise YianSyntaxError("Invalid hex string")
        hex_val = int(hex_str, 16)
        if not (0 <= hex_val <= 0xFF):
            raise YianSyntaxError("Hex value out of range")
        return chr(hex_val), s
    elif escape_char == "u":
        # unicode character
        s = __consume_prefix(s, "{")
        unicode_str = ""
        while not s.startswith("}"):
            c, s = __consume_one(s)
            unicode_str += c
        s = __consume_prefix(s, "}")
        if not all(c in __BASE_DIGITS[16] for c in unicode_str):
            raise YianSyntaxError("Invalid unicode string")
        unicode_val = int(unicode_str, 16)
        if not (0 <= unicode_val <= 0x10FFFF):
            raise YianSyntaxError("Unicode value out of range")
        return chr(unicode_val), s
    else:
        raise YianSyntaxError(f"Unknown escape character: \\{escape_char}")


def __consume_integer(s: str, base: int) -> tuple[int, str]:
    """
    Consumes an integer in the given base from the string s and returns the integer and the remaining string.

    Supports bases 2, 8, 10, and 16.

    Ignores underscores in the number.
    """
    digits = __BASE_DIGITS[base] + "_"
    number_str = ""
    while len(s) > 0 and s[0] in digits:
        if s[0] != '_':
            number_str += s[0]
        s = s[1:]
    if len(number_str) == 0:
        raise YianSyntaxError(f"Expected number in base {base}")
    return int(number_str, base), s


def __consume_float(s: str, base: int) -> tuple[float, str]:
    """
    Consumes a floating-point number from the string s and returns the float and the remaining string.

    Supports decimal floats with optional fractional and exponent parts.

    Ignores underscores in the number.
    """
    if base == 10:
        digits = __BASE_DIGITS[10] + "_" + "." + "eE" + "+-"
    elif base == 16:
        digits = __BASE_DIGITS[16] + "_" + "." + "pP" + "+-"
    else:
        raise YianSyntaxError("Unsupported base for float")

    number_str = ""
    while len(s) > 0 and s[0] in digits:
        if s[0] != '_':
            number_str += s[0]
        s = s[1:]
    if len(number_str) == 0:
        raise YianSyntaxError(f"Expected float in base {base}")

    if base == 16:
        return float.fromhex(number_str), s
    else:
        return float(number_str), s


def parse_char_literal(char_literal: str) -> tuple[IR.LiteralValue, str]:
    """
    Given a character literal (with single quotes), return the corresponding char LiteralValue and remaining suffix.
    """
    # 1. starting single quote
    char_literal = __consume_prefix(char_literal, "'")

    # 2. parse char content
    if char_literal.startswith("\\"):
        # consume escape character
        char_value, char_literal = __consume_escape_sequence(char_literal)
    else:
        char_value, char_literal = __consume_one(char_literal)

    # 3. ending single quote
    char_literal = __consume_prefix(char_literal, "'")

    return IR.CharLiteral(char_value), char_literal.strip()


def parse_string_literal(string_literal: str) -> tuple[IR.LiteralValue, str]:
    """
    Given a string literal (with double quotes), return the corresponding string LiteralValue and remaining suffix.
    """
    # 1. starting double quote
    string_literal = __consume_prefix(string_literal, '"')

    # 2. parse string content
    content = bytearray()
    while not string_literal.startswith('"'):
        if string_literal.startswith("\\"):
            # escape character
            char, string_literal = __consume_escape_sequence(string_literal)
        else:
            char, string_literal = __consume_one(string_literal)
        content.extend(char.encode('utf-8'))

    # 3. ending double quote
    string_literal = __consume_prefix(string_literal, '"')

    return IR.StringLiteral(bytes(content)), string_literal.strip()


def parse_number_literal(number_literal: str) -> tuple[IR.LiteralValue, str]:
    """
    Given a number literal string, return the corresponding LiteralValue and remaining suffix.
    """
    # 1. determine base
    if number_literal.startswith(("0x", "0X")):
        # hexadecimal
        _, number_literal = __consume_chars(number_literal, 2)
        base = 16
    elif number_literal.startswith(("0b", "0B")):
        # binary
        _, number_literal = __consume_chars(number_literal, 2)
        base = 2
    elif number_literal.startswith(("0o", "0O")):
        # octal
        _, number_literal = __consume_chars(number_literal, 2)
        base = 8
    else:
        # decimal
        base = 10

    # 2. try to parse in both float and integer
    try:
        float_val, float_suffix = __consume_float(number_literal, base)
    except Exception:
        float_val, float_suffix = None, number_literal

    try:
        int_val, int_suffix = __consume_integer(number_literal, base)
    except Exception:
        int_val, int_suffix = None, number_literal

    if float_val is not None and int_val is not None:
        # both parsed successfully, choose the longer consumed one
        if len(float_suffix) < len(int_suffix):
            number_value, number_literal = float_val, float_suffix
        elif len(int_suffix) < len(float_suffix):
            number_value, number_literal = int_val, int_suffix
        else:
            # same length, prefer integer
            number_value, number_literal = int_val, int_suffix
    elif float_val is not None:
        number_value, number_literal = float_val, float_suffix
    elif int_val is not None:
        number_value, number_literal = int_val, int_suffix
    else:
        raise YianSyntaxError("Invalid number literal")

    # 3. handle suffix
    number_suffix = None
    for suffix, (min_val, max_val) in __TYPE_LIMITS.items():
        if number_literal.startswith(suffix):
            number_literal = __consume_prefix(number_literal, suffix)

            # int to float conversion
            if isinstance(number_value, int) and suffix in {'f32', 'f64'}:
                number_value = float(number_value)

            # range check
            if not (min_val <= number_value <= max_val):
                raise YianSyntaxError(f"Value {number_value} out of range for type {suffix}")

            number_suffix = suffix

            break

    # 4. return LiteralValue
    if isinstance(number_value, int):
        return IR.IntegerLiteral(number_value, number_suffix), number_literal.strip()
    else:
        return IR.FloatLiteral(number_value, number_suffix), number_literal.strip()


def parse_array_literal(array_literal: str) -> tuple[IR.LiteralValue, str]:
    """
    Given an array literal string (with square brackets), return the corresponding Array LiteralValue and remaining suffix.
    """
    # 1. starting square bracket
    array_literal = __consume_prefix(array_literal, "[")

    # 2. parse elements
    elements: list[IR.LiteralValue] = []
    while not array_literal.startswith("]"):
        # parse element
        element, array_literal = parse_constant(array_literal)
        elements.append(element)

        # consume comma if present
        array_literal = array_literal.lstrip()
        if array_literal.startswith(","):
            array_literal = __consume_prefix(array_literal, ",")
        array_literal = array_literal.lstrip()

    # 3. ending square bracket
    array_literal = __consume_prefix(array_literal, "]")

    # 4. TODO: make sure all elements are of the same type
    if len(elements) == 0:
        raise YianSyntaxError("Array literal cannot be empty")

    # 5. return Array LiteralValue
    return IR.ArrayLiteral(elements), array_literal.strip()


def parse_tuple_literal(tuple_literal: str) -> tuple[IR.LiteralValue, str]:
    """
    Given a tuple literal string (with parentheses), return the corresponding Tuple LiteralValue and remaining suffix.
    """
    # 1. starting parenthesis
    tuple_literal = __consume_prefix(tuple_literal, "(")

    # 2. parse elements
    elements: list[IR.LiteralValue] = []
    while not tuple_literal.startswith(")"):
        # parse element
        element, tuple_literal = parse_constant(tuple_literal)
        elements.append(element)

        # consume comma if present
        tuple_literal = tuple_literal.lstrip()
        if tuple_literal.startswith(","):
            tuple_literal = __consume_prefix(tuple_literal, ",")
        tuple_literal = tuple_literal.lstrip()

    # 3. ending parenthesis
    tuple_literal = __consume_prefix(tuple_literal, ")")

    # 4. return Tuple LiteralValue
    return IR.TupleLiteral(elements), tuple_literal.strip()


def parse_byte_literal(byte_literal: str) -> tuple[IR.LiteralValue, str]:
    """
    Given a byte literal (with b'...'), return the corresponding Byte LiteralValue and remaining suffix.
    """
    # 1. starting b'
    byte_literal = __consume_prefix(byte_literal, "b'")

    # 2. parse byte content
    if byte_literal.startswith("\\x"):
        # hex byte
        byte_value, byte_literal = __consume_escape_sequence(byte_literal)
        # convert hex string to byte value
        byte_value = ord(byte_value)
    elif byte_literal.startswith("\\"):
        # other escape byte
        byte_value, byte_literal = __consume_escape_sequence(byte_literal)
        byte_value = ord(byte_value)
    else:
        byte_char, byte_literal = __consume_one(byte_literal)
        # byte must be in range 0-255
        if not (0 <= ord(byte_char) <= 255):
            raise YianSyntaxError(f"Byte char {byte_char} out of range")
        byte_value = ord(byte_char)

    # 3. ending single quote
    byte_literal = __consume_prefix(byte_literal, "'")

    return IR.IntegerLiteral(byte_value), byte_literal.strip()


def parse_constant(literal: str) -> tuple[IR.LiteralValue, str]:
    """
    Given a literal string, parse it into a LiteralValue.

    Returns the corresponding LiteralValue object and the remaining unparsed suffix.
    """
    # bool literal
    if literal.startswith("true"):
        return IR.BooleanLiteral(True), __consume_prefix(literal, "true").strip()
    if literal.startswith("false"):
        return IR.BooleanLiteral(False), __consume_prefix(literal, "false").strip()

    # char literal
    if literal.startswith("'"):
        return parse_char_literal(literal)

    # string literal
    if literal.startswith('"'):
        return parse_string_literal(literal)

    # array literal
    if literal.startswith("["):
        return parse_array_literal(literal)

    # tuple literal
    if literal.startswith("("):
        return parse_tuple_literal(literal)

    # byte literal
    if literal.startswith("b'"):
        return parse_byte_literal(literal)

    # remaining: number
    return parse_number_literal(literal)
