from compiler.frontend.lex.token import Literal as LexLiteral
from compiler.frontend.parse.ast_type import ASTType
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator
from compiler.utils.IR.position import SrcSpan


class ASTNode:
    def __init__(self, span: SrcSpan):
        self.span = span


class Program(ASTNode):
    def __init__(self, funcs: list["ProgramItem"]):
        # use the span of the first program item as the span of the whole program
        assert len(funcs) > 0, "Program must have at least one item"
        super().__init__(funcs[0].span)
        self.funcs = funcs


class ProgramItem(ASTNode):
    pass


class Import(ProgramItem):
    def __init__(self, span: SrcSpan, paths: list[str], target: str, alias: str | None):
        super().__init__(span)
        self.paths = paths
        self.target = target
        self.alias = alias


class Alias(ProgramItem):
    def __init__(self, span: SrcSpan, name: str, generics: list[str], target: ASTType):
        super().__init__(span)
        self.name = name
        self.generics = generics
        self.target = target


class VarInfo(ASTNode):
    def __init__(self, span: SrcSpan, var_type: ASTType, name: str):
        super().__init__(span)
        self.var_type = var_type
        self.name = name


class FuncDef(ProgramItem):
    def __init__(self, span: SrcSpan, attrs: list[str], name: str, params: list[VarInfo], ret_type: ASTType | None, body: "Block"):
        super().__init__(span)
        self.attrs = attrs
        self.name = name
        self.params = params
        self.ret_type = ret_type
        self.body = body


class FieldInfo(ASTNode):
    def __init__(self, span: SrcSpan, is_pub: bool, field_type: ASTType, name: str):
        super().__init__(span)
        self.is_pub = is_pub
        self.field_type = field_type
        self.name = name


class StructDef(ProgramItem):
    def __init__(self, span: SrcSpan, attrs: list[str], name: str, generics: list[str], fields: list[FieldInfo]):
        super().__init__(span)
        self.attrs = attrs
        self.name = name
        self.generics = generics
        self.fields = fields


class VariantInfo(ASTNode):
    def __init__(self, span: SrcSpan, name: str, fields: list[VarInfo]):
        super().__init__(span)
        self.name = name
        self.fields = fields


class EnumDef(ProgramItem):
    def __init__(self, span: SrcSpan, attrs: list[str], name: str, generics: list[str], variants: list[VariantInfo]):
        super().__init__(span)
        self.attrs = attrs
        self.name = name
        self.generics = generics
        self.variants = variants


class Impl(ProgramItem):
    def __init__(self, span: SrcSpan, generics: list[str], target: ASTType, trait: ASTType | None, items: list["MethodDef"]):
        super().__init__(span)
        self.generics = generics
        self.target = target
        self.trait = trait
        self.items = items


class TraitDef(ProgramItem):
    def __init__(self, span: SrcSpan, attrs: list[str], name: str, generics: list[str], items: list["MethodDecl | MethodDef"]):
        super().__init__(span)
        self.attrs = attrs
        self.name = name
        self.generics = generics
        self.items = items


class MethodDef(ASTNode):
    def __init__(self, span: SrcSpan, attrs: list[str], name: str, generics: list[str], params: list[VarInfo], ret_type: ASTType | None, body: "Block"):
        super().__init__(span)
        self.attrs = attrs
        self.name = name
        self.generics = generics
        self.params = params
        self.ret_type = ret_type
        self.body = body


class MethodDecl(ASTNode):
    def __init__(self, span: SrcSpan, attrs: list[str], name: str, generics: list[str], params: list[VarInfo], ret_type: ASTType | None):
        super().__init__(span)
        self.attrs = attrs
        self.name = name
        self.generics = generics
        self.params = params
        self.ret_type = ret_type


class Stmt(ASTNode):
    pass


class Block(Stmt):
    def __init__(self, span: SrcSpan, stmts: list["Stmt"]):
        super().__init__(span)
        self.stmts = stmts


class VarDecl(Stmt):
    def __init__(self, span: SrcSpan, name: str, var_type: ASTType, init_expr: "Expr | None"):
        super().__init__(span)
        self.name = name
        self.var_type = var_type
        self.init_expr = init_expr


class Return(Stmt):
    def __init__(self, span: SrcSpan, expr: "Expr | None"):
        super().__init__(span)
        self.expr = expr


class If(Stmt):
    def __init__(self, span: SrcSpan, condition: "Expr", then_branch: "Block", else_branch: "Block | None"):
        super().__init__(span)
        self.condition = condition
        self.then_branch = then_branch
        self.else_branch = else_branch


class For(Stmt):
    def __init__(self, span: SrcSpan, var_name: str, iterable: "Expr", body: "Block"):
        super().__init__(span)
        self.var_name = var_name
        self.iterable = iterable
        self.body = body


class While(Stmt):
    def __init__(self, span: SrcSpan, condition: "Expr", body: "Block"):
        super().__init__(span)
        self.condition = condition
        self.body = body


class Loop(Stmt):
    def __init__(self, span: SrcSpan, body: "Block"):
        super().__init__(span)
        self.body = body


class Match(Stmt):
    def __init__(self, span: SrcSpan, expr: "Expr", arms: list[tuple["Pattern", "Block"]]):
        super().__init__(span)
        self.expr = expr
        self.arms = arms


class Break(Stmt):
    pass


class Continue(Stmt):
    pass


class Assert(Stmt):
    def __init__(self, span: SrcSpan, condition: "Expr", message: str | None):
        super().__init__(span)
        self.condition = condition
        self.message = message


class Delete(Stmt):
    def __init__(self, span: SrcSpan, target: "Expr"):
        super().__init__(span)
        self.target = target


class Pattern(ASTNode):
    pass


class IntPattern(Pattern):
    def __init__(self, span: SrcSpan, values: list[int], block: "Block"):
        super().__init__(span)
        self.values = values
        self.block = block


class CharPattern(Pattern):
    def __init__(self, span: SrcSpan, values: list[str], block: "Block"):
        super().__init__(span)
        self.values = values
        self.block = block


class StrPattern(Pattern):
    def __init__(self, span: SrcSpan, values: list[str], block: "Block"):
        super().__init__(span)
        self.values = values
        self.block = block


class EnumPattern(Pattern):
    def __init__(self, span: SrcSpan, variants: list[str], block: "Block"):
        super().__init__(span)
        self.variants = variants
        self.block = block


class PayloadPattern(Pattern):
    def __init__(self, span: SrcSpan, variant: str, fields: list[str], block: "Block"):
        super().__init__(span)
        self.variant = variant
        self.block = block


class WildcardPattern(Pattern):
    def __init__(self, span: SrcSpan, block: "Block"):
        super().__init__(span)
        self.block = block


class Expr(ASTNode):
    pass


class Binary(Expr):
    def __init__(self, span: SrcSpan, op: BinaryOperator, left: "Expr", right: "Expr"):
        super().__init__(span)
        self.op = op
        self.left = left
        self.right = right


class Unary(Expr):
    def __init__(self, span: SrcSpan, op: UnaryOperator, operand: "Expr"):
        super().__init__(span)
        self.op = op
        self.operand = operand


class Call(Expr):
    def __init__(self, span: SrcSpan, callee: "Expr", positional_args: list["Expr"], named_args: dict[str, "Expr"]):
        super().__init__(span)
        self.callee = callee
        self.positional_args = positional_args
        self.named_args = named_args


class MethodCall(Expr):
    def __init__(self, span: SrcSpan, receiver: "Expr", method_name: str, generics: list[ASTType], args: list["Expr"]):
        super().__init__(span)
        self.receiver = receiver
        self.method_name = method_name
        self.generics = generics
        self.args = args


class FieldAccess(Expr):
    def __init__(self, span: SrcSpan, receiver: "Expr", field_name: str):
        super().__init__(span)
        self.receiver = receiver
        self.field_name = field_name


class DynValue(Expr):
    def __init__(self, span: SrcSpan, value: "Expr"):
        super().__init__(span)
        self.value = value


class DynBuffer(Expr):
    def __init__(self, span: SrcSpan, target_type: ASTType, size: "Expr"):
        super().__init__(span)
        self.target_type = target_type
        self.size = size


class Atom(Expr):
    def __init__(self, span: SrcSpan, name: str, generics: list[ASTType]):
        super().__init__(span)
        self.name = name
        self.generics = generics


class Literal(Expr):
    def __init__(self, span: SrcSpan, literal: LexLiteral):
        super().__init__(span)
        self.literal = literal
