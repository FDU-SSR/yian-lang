const PREC = {
  COMMENT: -1,         // //  /*  */
  RANGE: 1,            // ..
  ASSIGN: 2,          // =  += -=  *=  /=  %=  &=  ^=  |=  <<=  >>=  >>>=
  DECL: 4,
  ELEMENT_VAL: 6,
  OR: 8,              // ||
  AND: 12,             // &&
  NOT: 14,
  GENERIC: 15,
  BIT_OR: 16,          // |
  BIT_XOR: 18,         // ^
  BIT_AND: 20,         // &
  EQUALITY: 22,        // ==  !=
  REL: 24,            // <  <=  >  >=  instanceof
  SHIFT: 26,          // <<  >>  >>>
  ADD: 28,            // +  -
  MULT: 30,           // *  /  %
  CAST: 32,           // (Type)
  OBJ_INST: 34,       // new1
  UNARY: 36,          // +  -  !  ~
  SLICE: 37,
  ARRAY: 38,          // [Index]
  OBJ_ACCESS: 38,     // .
  PARENS: 38,         // (Expression)
  CALL: 40,
  TUPLE: 42,
}

const terminator = choice(/\n/, ';', "\0", /\/\/[^\n]*/);
const non_semicolon_terminator = choice(/\n/, "\0", /\/\/[^\n]*/);

const newline_block_start = /\s*\{/;
const string_constant = /[^"\\]+/;
const DIGITS = token(/[0-9](?:_?[0-9])*(?:_*)/);
const DECIMAL_DIGITS = token(sep1(/[0-9]+/, '_'));
const HEX_DIGITS = token(sep1(/[A-Fa-f0-9]+/, '_'));
const _DEC_DIGITS_RE = '[0-9](?:_?[0-9])*(?:_*)';
const _DEC_EXP_RE = `(?:[eE][+-]?${_DEC_DIGITS_RE})`;
const _HEX_DIGITS_RE = '[0-9a-fA-F](?:_?[0-9a-fA-F])*(?:_*)';
const _HEX_EXP_RE = `(?:[pP][+-]?${_DEC_DIGITS_RE})`;
const _FLOAT_SUFFIX_RE = '(?:f(?:16|32|64)|[fFdD])';
const _INT_SUFFIX_RE = '(?:_*(?:[iu](?:8|16|32|64)))';
const _BIN_DIGITS_RE = '[01](?:_?[01])*(?:_*)';
const _OCT_DIGITS_RE = '[0-7](?:_?[0-7])*(?:_*)';
module.exports = grammar({
  name: 'yian',

  extras: $ => [
    /\s/,
    $.line_comment,
    $.block_comment,
  ],

  supertypes: $ => [
    $.expression,
    $.declaration,
    $.statement,
    $.primary_expression,
    $.constant,
    $._type,
  ],

  conflicts: $ => [
    // [$._multiline_string_literal, $._string_literal],
    // [$.multiline_string_fragment, $.string_fragment],
    [$.type_arguments, $.type_parameters],
    // [$.block_without_brace],
    [$.if_expression],
    [$.primary_expression, $.local_variable_declaration],
    [$.primary_expression, $.switch_label],
    [$.primary_expression, $.array_type],
    [$.argument_list, $.formal_parameters],
    [$.primary_expression, $.formal_parameter],
  ],

  inline: $ => [
    $._variable_declarator_id,
    $._type,
    $._statement_list,
    $._field_identifier
  ],

  word: $ => $.identifier,

  rules: {
    program: $ =>
      repeat(
        seq($.statement, terminator)
      ),

    comment: $ => seq(
      choice(
        $.line_comment,
        $.block_comment,
      ),
    ),

    line_comment: _ => token(prec(PREC.COMMENT, /\/\/[^\n]*/)),
    block_comment: _ => token(prec(PREC.COMMENT,
      seq(
        '/*',
        /[^*]*\*+([^/*][^*]*\*+)*/,
        '/',
      ),
    )),


    // expression_statement: $ => seq($.expression, terminator),
    expression_statement: $ => $.expression,

    expression: $ => choice(
      $.binary_expression,
      $.not_expression,
      $.boolean_expression,
      $.primary_expression,
      $.unary_expression,
      $.switch_expression,
      $.if_expression,
      $.dyn_expression,
      $.array_initializer,
      $.tuple_initializer,
      $.anonymous_method,
      $.slice_access,
    ),

    primary_expression: $ => choice(
      $.parenthesized_expression,
      // $.object_creation_expression,
      $.field_access,
      $.array_access,
      $.mem_access,
      $.addr_of,
      $.call_expression,
      alias($.constant, $.constant),
      // $.constant,
      $.identifier,
    ),

    call_expression: $ => prec.left(PREC.CALL,
      // choice(
      //   seq(
      //     field('function_name', $.primary_expression),
      //     field("argument_list", $.argument_list),
      //     optional(terminator),
      //   ),
      //   seq(
      //     field('function_name', choice(
      //       $.identifier,
      //       $.mem_access,
      //       $.field_access,
      //       $.array_access
      //     )),
      //     field('type_arguments', $.type_arguments),
      //     field("argument_list", $.argument_list),
      //     optional(terminator),
      //   )
      // )
      choice(
        seq(
          field('function_name', choice(
            $.identifier,
            $.mem_access,
            // $.field_access,
            $.array_access,
            $.parenthesized_expression,
            $.call_expression,
            $.generic_type,
          )),
          optional(field('type_arguments', $.type_arguments)),
          field("argument_list", $.argument_list),
          optional(terminator),
        ),
        seq(
          field('receiver', choice(
            $.identifier,
            $.mem_access,
            $.field_access,
            $.array_access,
            $.parenthesized_expression,
            $.call_expression,
            $.generic_type,
            $.constant,
          )),
          // field("pointer_stars", repeat(choice('*', '@', 'full@'))),
          optional(field('receiver_type_arguments', $.type_arguments)),
          '.',
          field('function_name', $.identifier),
          optional(field('type_arguments', $.type_arguments)),
          field("argument_list", $.argument_list),
          optional(terminator),
        ),
      ),
    ),

    argument_list: $ => seq(
      '(',
      commaSep(
        choice(
          $.expression,
          $.named_arg,
        ),
      ),
      ')'
    ),

    named_arg: $ => seq(
      field('name', $.identifier),
      "=",
      field('value', $.expression),
    ),

    array_initializer: $ => seq(
      '[',
      commaSep($.expression),
      ']',
    ),
    tuple_initializer: $ => prec.left(PREC.TUPLE, seq(
      '(',
      field('element_type', $.expression),
      ',',
      commaSep(field('element_type', $.expression)),
      ')'
    )),

    mem_access: $ => prec.left(PREC.CAST, seq(
      field('operator', '*'),
      field('argument', $.primary_expression),
    )),

    addr_of: $ => prec.left(PREC.CAST, seq(
      field('operator', '&'),
      field('argument', $.primary_expression),
    )),

    array_access: $ => prec.left(PREC.ARRAY, seq(
      field('array', choice(
        $.primary_expression,
        $.slice_access,
      )),
      $.dimensions_expr,
    )),

    dimensions_expr: $ => seq('[', commaSep($.expression), ']'),

    slice_access: $ => prec.left(PREC.SLICE, seq(
      field('array', $.primary_expression),
      '[',
      field('start', optional($.expression)),
      ':',
      field('end', optional($.expression)),
      optional(seq(
        ':',
        field('step', optional($.expression))
      )),
      ']'
    )),

    field_access: $ => prec.left(PREC.OBJ_ACCESS, seq(
      field('object', choice(
        $.parenthesized_expression,
        // $.object_creation_expression,
        $.field_access,
        $.array_access,
        $.mem_access,
        $.addr_of,
        $.identifier,
        $.call_expression,
        $.generic_type
      )
      ),
      optional(field('type_arguments', $.type_arguments)),
      '.',
      field('field', choice($.identifier, $.int_literal)),
    )),



    // call_chain_expression: $ => prec.right(100，
    //   seq($.call_expression,
    //   repeat1(seq(
    //     choice('.', seq(terminator, '.')),
    //     $.call_expression
    //   )))
    // ),
    dot_call_statement: $ => seq(
      '.',
      field('function_name', $.identifier),
      field("argument_list", $.argument_list),
      terminator
    ),

    anonymous_method: $ => prec.left(PREC.OBJ_INST, seq(
      field("parameters", $.anonymous_formal_parameters),
      field('body', choice($.expression, $.block)),
    )),

    anonymous_formal_parameters: $ => prec.left(seq(
      '|',
      commaSep($.formal_parameter),
      '|',
    )),

    parenthesized_expression: $ => prec(PREC.PARENS, seq(
      '(', $.expression, ')'
    )),

    dyn_expression: $ => prec.right(PREC.OBJ_INST, seq(
      "dyn",
      choice(
        // b = dyn f64 不进行初始化
        //seq('dyn', field('type', $._type),
        // c = dyn i32(5) 进行初始化
        seq(field('type', $._type), field("pointer_stars", repeat(choice('*', '@', 'full@'))), optional($.argument_list)),
        // d = dyn expr 支持添加常量
        // p = dyn Person(name = "abc", age = 18)
        field('expr', $.expression),
        // e = dyn (i32)5 支持类型转换
        // seq('dyn', '(', field('cast_type', $._type), ')', field('cast_value', $.expression),
        // f = dyn 5i32 支持声明一个 i32 类型的常量 5
        field('constant', $.constant),
        // g = dyn i32[5] 支持声明定长数组
        seq(field('array_type', $._type), field("pointer_stars", repeat(choice('*', '@', 'full@'))), field('array_size', $.dimensions_expr)),
        // h = dyn [1, 2, 3, 4, 5] 支持声明数组常量
        field('array_constant', $.array_initializer),
      )
    )),

    not_expression: $ => prec(PREC.NOT, seq(
      'not',
      field('argument', $.expression),
    )),

    boolean_expression: $ => choice(
      prec.left(PREC.AND, seq(
        field('left', $.expression),
        field('operator', 'and'),
        field('right', $.expression),
      )),
      prec.left(PREC.OR, seq(
        field('left', $.expression),
        field('operator', 'or'),
        field('right', $.expression),
      )),
    ),
    binary_expression: $ => choice(
      ...[
        ['..', PREC.RANGE],
        ['>', PREC.REL],
        ['<', PREC.REL],
        ['>=', PREC.REL],
        ['<=', PREC.REL],
        ['==', PREC.EQUALITY],
        ['typeof', PREC.EQUALITY],
        ['!=', PREC.EQUALITY],
        ['&&', PREC.AND],
        ['||', PREC.OR],
        ['+', PREC.ADD],
        ['-', PREC.ADD],
        ['*', PREC.MULT],
        ['/', PREC.MULT],
        ['&', PREC.BIT_AND],
        ['|', PREC.BIT_OR],
        ['^', PREC.BIT_XOR],
        ['%', PREC.MULT],
        ['<<', PREC.SHIFT],
        ['>>', PREC.SHIFT],
        ['in', PREC.REL],
        ['not in', PREC.REL],
      ].map(([operator, precedence]) =>
        prec.left(precedence, seq(
          field('left', $.expression),
          // @ts-ignore
          field('operator', operator),
          field('right', $.expression),
        )),
      )),

    unary_expression: $ => choice(...[
      ['+', PREC.UNARY],
      ['-', PREC.UNARY],
      ['!', PREC.UNARY],
      ['~', PREC.UNARY],
    ].map(([operator, precedence]) =>
      prec.left(precedence, seq(
        // @ts-ignore
        field('operator', operator),
        field('operand', $.expression),
      )),
    )),

    statement: $ => choice(
      $.expression_statement,
      $.assignment_statement,
      $.type_definition,
      $.declaration,
      $.while_statement,
      $.loop_statement,
      $.for_statement,
      $.for_in_statement,
      $.standalone_block,
      $.break_statement,
      $.continue_statement,
      $.return_statement,
      $.with_statement,
      $.local_variable_declaration,
      $.method_declaration,
      $.import_statement,
      $.from_import_statement,
      $.yield_statement,
      $.assert_statement,
      $.del_statement,
      $.empty_statement,
      $.dot_call_statement,
      $.constant_declaration,
    ),

    empty_statement: _ => ';',

    del_statement: $ => seq(
      'del',
      field('target', $.expression),
    ),

    assignment_statement: $ => prec.right(PREC.ASSIGN, seq(
      field('left', choice(
        $.identifier,
        $.field_access,
        $.array_access,
        $.mem_access,
      )),
      field('operator', choice('=', '+=', '-=', '*=', '/=', '&=', '|=', '^=', '%=', '<<=', '>>=')),
      field('right', $.expression),
    )),

    import_statement: $ => seq(
      'import',
      $.import_list,
    ),

    from_import_statement: $ => seq(
      'from',
      field('module', $.dotted_name),
      'import',
      choice(
        $.import_list,
        seq('(', $.import_list, ')'),
      ),
    ),

    import_list: $ => (
      commaSep1(choice(
        $.dotted_name,
        $.aliased_import,
      ))
    ),

    dotted_name: $ => sep1($.identifier, '.'),

    aliased_import: $ => seq(
      field('name', $.dotted_name),
      'as',
      field('alias', $.identifier),
    ),

    constant_declaration: $ => seq(
      'const',
      $._variable_declarator_id,
      '=',
      field('value', $.expression),
    ),

    local_variable_declaration: $ => seq(
      optional($.modifiers),
      field('type', $._type),

      field("pointer_stars", repeat(choice('*', '@', 'full@'))),
      commaSep1(
        field(
          'declarator',
          $.variable_declarator
        )
      ),
    ),

    variable_declarator: $ => seq(
      $._variable_declarator_id,
      optional(
        seq(
          '=',
          field('value', $.expression),
        )
      ),
    ),

    _variable_declarator_id: $ => seq(
      field('name', $.identifier),
    ),

    switch_expression: $ => seq(
      'match',
      field('condition', $.expression),
      field('body', $.switch_block),
    ),

    switch_block: $ => seq(
      '{',
      repeat1(seq($.switch_block_statement_group)),
      '}',
    ),

    switch_block_statement_group: $ => prec.left(seq(
      $.switch_label,
      // ":",
      // $.block_without_brace,
      $.block,
    )),

    switch_label: $ => commaSep1(choice(
      $._type,
      $.expression,
      $.enum_case,
      '_',
    )),

    enum_case: $ => seq(
      field('condition', $.identifier),
      'as',
      field('target', $.identifier),
    ),

    with_statement: $ => seq(
      'with',
      field('receiver', $.primary_expression),
      field('body', $.block),
    ),

    break_statement: $ => "break",

    continue_statement: $ => "continue",

    return_statement: $ => prec.left(seq(
      'return',
      optional($.expression)
    )),

    for_statement: $ => seq(
      'for',
      choice(
        seq("(", $.for_spec, ")"),
        $.for_spec
      ),
      field('body', $.block),
    ),

    for_spec: $ => seq(
      field('init', optional(
        choice(
          $.local_variable_declaration,
          $.assignment_statement,
          // commaSep($.expression),
        ))),
      ';',
      field('condition', optional($.expression)),
      ';',
      field('update', commaSep(choice($.assignment_statement, $.expression))),
    ),

    for_in_statement: $ => seq(
      'for',
      // choice(
      //     // 支持 index, value 形式
      //     seq(
      //         field('index', choice($.identifier)),
      //         ',',
      //         field('value', choice($.identifier))
      //     ),
      //     // 支持 index 形式
      //     field('index', choice($.identifier))
      // ),
      field('value', choice($.identifier)),
      'in',
      field('receiver', $.expression), // 允许任意表达式作为可迭代对象
      field('body', $.block),
    ),

    while_statement: $ => seq(
      'while',
      field('condition', $.expression),
      field('body', $.block),
    ),

    loop_statement: $ => seq(
      'loop',
      field('body', $.block),
    ),

    yield_statement: $ => seq(
      'yield',
      field('expression', $.expression),
    ),

    assert_statement: $ => seq(
      'assert',
      field('condition', $.expression),
      optional(seq(
        ':',
        field('message', $.string_literal)
      )),
    ),

    if_expression: $ => seq(
      'if',
      field('condition', $.expression),
      field('consequence', $.block),
      repeat(field('alternative', $.elif_clause)),
      optional(field('alternative', $.else_clause)),
    ),

    elif_clause: $ => seq(
      optional(terminator),
      'elif',
      field('condition', $.expression),
      field('consequence', $.block),
    ),

    else_clause: $ => seq(
      optional(terminator),
      'else',
      field('consequence', $.block),
    ),

    block: $ => seq(
      '{',
      optional($._statement_list),
      '}',
    ),

    standalone_block: $ => seq(
      '{',
      optional($._statement_list),
      '}',
    ),

    _statement_list: $ => seq(
      $.statement,
      repeat(seq(terminator, $.statement)),
      optional(terminator),
    ),

    // block_without_brace: $ => repeat1(
    //   seq($.statement, terminator)
    // ),

    declaration: $ => prec(PREC.DECL, choice(
      $.struct_declaration,
      $.enum_declaration,
      $.implement_declaration,
      $.trait_declaration,
      $.ffi_declaration,
    )),

    implement_declaration: $ => seq(
      'impl',
      optional(field('type_parameters', $.type_parameters)),
      // optional(
      //     seq(
      //         field('trait', seq(
      //             field('name', $.identifier),
      //             optional(field('type_parameters', $.type_parameters))
      //         )),
      //         'for'
      //     )
      // ),
      optional($.trait_in_impl),
      // field('struct', seq(
      //     field('name', $.identifier),
      //     optional(field('type_parameters', $.type_parameters))
      // )),
      // $.type_in_impl,
      seq(field('type', $._type), field("pointer_stars", repeat(choice('*', '@', 'full@')))),
      optional(field('where_clause', $.where_clause)),
      field('body', $.implement_body)
    ),
    trait_in_impl: $ => seq(
      field('trait', seq(
        field('name', $.identifier),
        optional(field('type_parameters', $.type_parameters))
      )),
      'for'
    ),

    where_clause: $ => seq(
      'where',
      commaSep1($.type_constraint)
    ),

    type_constraint: $ => seq(
      field('type', $._type),
      ':',
      field('bound', seq(
        $._type,
        repeat(
          seq(
            choice('&', '|'),
            $._type
          )
        )
      ))
    ),

    implement_body: $ => seq(
      '{',
      repeat($.method_declaration),
      '}'
    ),

    // trait_list: $ => commaSep1(alias($.identifier, $.type_identifier)),

    ffi_declaration: $ => seq(
      'extern',
      field('name', $.identifier),
      field('body', $.ffi_body)
    ),

    ffi_body: $ => seq(
      '{',
      optional(commaOrNewlineSep($, $.ffi_item)),
      '}'
    ),

    ffi_item: $ => seq(
      optional($.modifiers),
      optional(field('type', $._type)),
      field("pointer_stars", repeat(choice('*', '@', 'full@'))),
      field('name', $.identifier),
      optional(field('type_parameters', $.type_parameters)),
      field('parameters', $.formal_parameters),
      ';'
    ),

    trait_declaration: $ => prec.right(seq(
      optional($.modifiers),
      'trait',
      field('name', $.identifier),
      optional(field("type_parameters", $.type_parameters)),

      optional(terminator),
      optional(seq(
        '{',
        repeat($.method_declaration),
        '}',
      )),
    )),

    enum_declaration: $ => seq(
      optional($.modifiers),
      'enum',
      field('name', $.identifier),
      optional(field("type_parameters", $.type_parameters)),
      '{',
      optional(commaOrNewlineSep($, $.enum_variant)),
      '}'
    ),

    enum_variant: $ => choice(
      $.simple_enum_variant,
      $.enum_variant_with_value,
      $.enum_variant_struct,
    ),

    simple_enum_variant: $ => field('name', $.identifier),

    enum_variant_with_value: $ => seq(
      field('name', $.identifier),
      '=',
      field('value', $.expression)
    ),

    enum_variant_struct: $ => seq(
      field('name', $.identifier),
      '{',
      optional(commaOrNewlineSep($, $.enum_field)),
      '}'
    ),

    enum_field: $ => seq(
      field('type', $._type),

      field("pointer_stars", repeat(choice('*', '@', 'full@'))),
      field('name', $.identifier)
    ),

    dyn: $ => 'dyn',

    struct_declaration: $ => seq(
      optional(field('heap_flag', $.dyn)),
      optional($.modifiers),
      'struct',
      field('name', $.identifier),
      optional(field("type_parameters", $.type_parameters)),
      $.struct_body,
    ),

    struct_body: $ => seq(
      '{',
      // 使用 commaOrNewlineSep 函数来支持逗号或换行符分隔
      optional(commaOrNewlineSep($, $.field_declaration)),
      '}'
    ),

    field_declaration: $ => seq(
      optional($.modifiers),
      field('type', $._type),

      field("pointer_stars", repeat(choice('*', '@', 'full@'))),
      field('name', choice(
        $._field_identifier,
        $.mem_access,
      )),
    ),

    method_declaration: $ => prec.left(PREC.DECL, choice(
      seq(
        optional($.modifiers),
        optional(field('type', $._type)),

        field("pointer_stars", repeat(choice('*', '@', 'full@'))),
        field('name', $.identifier),
        optional(field('type_parameters', $.type_parameters)),
        field('parameters', $.formal_parameters),
        ';'
      ),
      seq(
        optional($.modifiers),
        optional(field('type', $._type)),

        field("pointer_stars", repeat(choice('*', '@', 'full@'))),
        field('name', $.identifier),
        optional(field('type_parameters', $.type_parameters)),
        field('parameters', $.formal_parameters),
        optional(terminator),
        field('body', $.block),
      ),
    )),

    formal_parameters: $ => seq(
      '(',
      commaSep($.formal_parameter),
      ')'
    ),

    formal_parameter: $ => seq(
      optional($.modifiers),
      field('type', $._type),

      field("pointer_stars", repeat(choice('*', '@', 'full@'))),
      $._variable_declarator_id,
    ),

    type_definition: $ => seq(
      'typedef',
      field('alias', $.identifier),
      field("pointer_stars", repeat(choice('*', '@', 'full@'))),
      '=',
      field('type', $._type),
    ),

    _type: $ => choice(
      $.array_type,
      $.tuple_type,
      $.generic_type,
      $.primitive_type,
      $.record_type,
      $.method_pointer,
      alias($.identifier, $.type_identifier),
    ),

    record_type: $ => seq(
      '{',
      field('key_type', $._type),
      ':',
      field('value_type', $._type),
      '}'
    ),

    tuple_type: $ => prec.left(PREC.TUPLE, seq(
      '(',
      field('element_type', $._type),
      ',',
      optional(field('element_type', $._type)),
      commaSep(field('element_type', $._type)),
      ')'
    )),

    array_type: $ => seq(
      field('data_type', $._type),
      field('dimensions', $.dimensions_expr),
    ),

    primitive_type: $ => choice(
      'i8', 'u8', 'i16', 'u16', 'i32', 'u32', 'i64', 'u64', 'f16', 'f32', 'f64', 'bool', 'char', 'string'
    ),



    generic_type: $ => prec.right(PREC.GENERIC, seq(
      alias($.identifier, $.type_identifier),
      $.type_arguments,
    )),

    type_arguments: $ => prec.right(PREC.GENERIC, seq(
      token.immediate('<'),
      commaSep1(seq($._type, optional('*'))),
      token.immediate('>'),
    )),

    type_parameters: $ => prec.right(PREC.GENERIC, seq(
      token.immediate('<'),
      commaSep1(choice(
        seq($.identifier, $.type_bound),
        $._type
      )),
      token.immediate('>'),
    )),

    type_bound: $ => seq(
      ':',
      $.identifier,
      repeat(
        seq(
          choice('&', '|'),
          $.identifier
        )
      )
    ),

    method_pointer: $ => prec.left(PREC.GENERIC, seq(
      'fn<',
      optional(choice(
        // $.tuple_type,
        $.generic_type,
        $.primitive_type,
        $.record_type,
        alias($.identifier, $.type_identifier),
      ),),
      // $.formal_parameters,
      $.formal_types,
      '>',
    )),

    formal_types: $ => seq(
      '(',
      commaSep(alias($._type, $.formal_type)),
      ')'
    ),

    modifiers: $ => repeat1(choice(
      'static',
      'inline',
      'pub',
      'intrinsic',
    )),

    _field_identifier: $ => alias($.identifier, $.field_identifier),

    constant: $ => choice(
      $.decimal_floating_point_literal,
      $.float_literal,
      $.int_literal,
      $.bool_literal,
      $.bytes_literal,
      $.char_literal,
      //$.constant_group,
      //$.static_string,
      $.string_literal,
    ),

    // Define a new rule for grouped constants
    //constant_group: $ => seq('(', commaSep($.constant), ')'),

    // Data Types
    identifier: $ => /[_a-zA-Z][_a-zA-Z0-9]*/,
    int_literal: _ => token(new RegExp(
      `(?:` +
      `0[bB]${_BIN_DIGITS_RE}(?:${_INT_SUFFIX_RE})?` +
      `|0[oO]${_OCT_DIGITS_RE}(?:${_INT_SUFFIX_RE})?` +
      `|0[xX]${_HEX_DIGITS_RE}(?:${_INT_SUFFIX_RE})?` +
      `|${_DEC_DIGITS_RE}(?:${_INT_SUFFIX_RE})?` +
      `)`
    )),

    float_literal: _ => token(new RegExp(
      `(?:` +
      `(?:${_DEC_DIGITS_RE}\\.${_DEC_DIGITS_RE}|\\.${_DEC_DIGITS_RE})` +
      `(?:${_DEC_EXP_RE})?` +
      `(?:${_FLOAT_SUFFIX_RE})?` +
      `)`
    )),
    bool_literal: $ => choice('true', 'false'),
    bytes_literal: _ => /b'[^'\\]*(?:\\.[^'\\]*)*'/,

    char_literal: _ => token(seq(
      '\'',
      repeat1(choice(
        /[^\\'\n]/,
        /\\./,
        /\\\n/,
      )),
      '\'',
    )),
    decimal_floating_point_literal: _ => token(new RegExp(
      `(?:` +
      `0[xX](?:` +
      `(?:${_HEX_DIGITS_RE}\\.${_HEX_DIGITS_RE}|\\.${_HEX_DIGITS_RE}|${_HEX_DIGITS_RE})` +
      `${_HEX_EXP_RE}` +
      `(?:${_FLOAT_SUFFIX_RE})?` +
      `)` +
      `|(?:${_DEC_DIGITS_RE}\\.${_DEC_DIGITS_RE}|\\.${_DEC_DIGITS_RE})${_DEC_EXP_RE}(?:${_FLOAT_SUFFIX_RE})?` +
      `|${_DEC_DIGITS_RE}${_DEC_EXP_RE}(?:${_FLOAT_SUFFIX_RE})?` +
      `|${_DEC_DIGITS_RE}(?:${_FLOAT_SUFFIX_RE})` +
      `|(?:${_DEC_DIGITS_RE}\\.${_DEC_DIGITS_RE}|\\.${_DEC_DIGITS_RE})(?:${_FLOAT_SUFFIX_RE})` +
      `)`
    )),

    string_literal: $ => choice($._string_literal, $._multiline_string_literal),
    // string_literal: $ =>  $._string_literal,
    _string_literal: $ => seq(
      '"',
      repeat(choice(
        $.string_fragment,
        $.escape_sequence,
        $.string_interpolation,
        $.string_fragment_with_brace,
      )),
      '"',
    ),
    _multiline_string_literal: $ => seq(
      '"""',
      repeat(choice(
        alias($._multiline_string_fragment, $.multiline_string_fragment),
        $._escape_sequence,
        $.string_interpolation,
      )),
      '"""',
    ),
    // Workaround to https://github.com/tree-sitter/tree-sitter/issues/1156
    // We give names to the token() constructs containing a regexp
    // so as to obtain a node in the CST.

    string_fragment: _ => token.immediate(prec(1, /[^"\\]+/)),
    _multiline_string_fragment: _ => choice(
      /[^"\\]+/,
      /"([^"\\]|\\")*/,
    ),

    string_interpolation: $ => seq(
      '{',
      $.expression,
      '}',
    ),
    string_fragment_with_brace: $ => seq(
      '\\{',
      $.string_fragment,
      '\\}',
    ),

    _escape_sequence: $ => choice(
      prec(2, token.immediate(seq('\\', /[^bfnrts'\"\\{}]/))),
      prec(1, $.escape_sequence),
    ),
    escape_sequence: _ => token.immediate(seq(
      '\\',
      choice(
        /[^xu0-7{}]/,
        /[0-7]{1,3}/,
        /x[0-9a-fA-F]{2}/,
        /u[0-9a-fA-F]{4}/,
        /u\{[0-9a-fA-F]+\}/,
      ))),
  }
})

/**
 * Creates a rule to match one or more of the rules separated by separator
 *
 * @param {RuleOrLiteral} rule
 *
 * @param {RuleOrLiteral} separator
 *
 * @return {SeqRule}
 *
 */
function sep1(rule, separator) {
  return seq(rule, repeat(seq(separator, rule)));
}

/**
 * Creates a rule to match one or more of the rules separated by a comma
 *
 * @param {RuleOrLiteral} rule
 *
 * @return {SeqRule}
 *
 */
function commaSep1(rule) {
  return sep1(rule, ',');
}

/**
 * Creates a rule to optionally match one or more of the rules separated by a comma
 *
 * @param {RuleOrLiteral} rule
 *
 * @return {ChoiceRule}
 *
 */
function commaSep(rule) {
  return optional(commaSep1(rule));
}

function commaOrNewlineSep($, rule) {
  const separator = choice(
    seq(',', optional(terminator)),  // 逗号后可选换行
    terminator                            // 独立换行
  );

  return optional(seq(
    rule,
    repeat(seq(
      separator,
      rule
    )),
    optional(separator) // 允许尾部逗号
  ));
}