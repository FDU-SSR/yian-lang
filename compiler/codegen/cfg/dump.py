"""
Text serialisation for CFG IR (cfg/ir.py).
"""

from __future__ import annotations

from compiler.codegen.cfg import ir as IR
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


def dump(func: IR.Function) -> str:
    """Return a human-readable string representation of *func*."""
    out: list[str] = []

    # signature — Function no longer carries params/return_type directly;
    # those are encoded in the type system via func.type_id.
    out.append(f"function @{func.name} (type_id={func.type_id}) {{")

    for block in func.blocks:
        out.append(f"  {block.label}:")
        for stmt in block.stmts:
            out.append(f"    {__dump_stmt(stmt)}")
        if block.terminator:
            out.append(f"    {__dump_terminator(block.terminator)}")
        out.append("")

    out.append("}")
    return "\n".join(out)


# ---------------------------------------------------------------------------
# statement dumpers
# ---------------------------------------------------------------------------

# pylint: disable=too-many-return-statements
def __dump_stmt(stmt: IR.Stmt) -> str:
    match stmt:
        case IR.VarPtr(result=result, var_ref=var_ref):
            return (
                f"%{result.name} = varptr {var_ref.name}"
                f"  [{__type_str(var_ref.type_id)}]"
            )

        case IR.FieldPtr(result=result, base=base, field_index=idx):
            return f"%{result.name} = fieldptr {__dump_value(base)}[{idx}]"

        case IR.Alloca(result=result, value=value):
            return (
                f"%{result.name} = alloca"
                f" {__dump_value(value)}"
            )

        case IR.Malloc(result=result, type_id=type_id, size=size):
            return (
                f"%{result.name} = malloc"
                f" {__type_str(type_id)}, {__dump_value(size)}"
            )

        case IR.FuncPtr(result=result, func_type_id=func_type_id):
            return (
                f"%{result.name} = funcptr {__type_str(func_type_id)}"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.Load(result=result, ptr=ptr):
            return (
                f"%{result.name} = load {__dump_value(ptr)}"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.Store(ptr=ptr, value=value):
            return f"store {__dump_value(ptr)}, {__dump_value(value)}"

        case IR.Binary(result=result, op=op, lhs=lhs, rhs=rhs):
            return (
                f"%{result.name} = binary {__op_str(op)}"
                f" {__dump_value(lhs)}, {__dump_value(rhs)}"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.Unary(result=result, op=op, operand=operand):
            return (
                f"%{result.name} = unary {__op_str(op)}"
                f" {__dump_value(operand)}"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.ExtractValue(result=result, base=base, field_index=idx):
            return (
                f"%{result.name} = extractvalue"
                f" {__dump_value(base)}[{idx}]"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.Delete(ptr=ptr):
            return f"delete {__dump_value(ptr)}"

        case IR.Call(result=result, callee_type=callee_type, args=args):
            arg_str = ", ".join(__dump_value(a) for a in args)
            return (
                f"%{result.name} = call @{__type_str(callee_type)}({arg_str})"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.Invoke(result=result, callee=callee, args=args):
            arg_str = ", ".join(__dump_value(a) for a in args)
            return (
                f"%{result.name} = invoke"
                f" {__dump_value(callee)}({arg_str})"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.Cast(result=result, value=value, to_type=to_type):
            return (
                f"%{result.name} = cast {__dump_value(value)}"
                f" to {__type_str(to_type)}"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.SizeOf(result=result, type_id=type_id):
            return (
                f"%{result.name} = sizeof"
                f" {__type_str(type_id)}"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.AggregateConstruct(
            result=result, type_id=type_id, fields=fields
        ):
            field_str = ", ".join(__dump_value(f) for f in fields)
            return (
                f"%{result.name} = aggregate"
                f" {__type_str(type_id)} {{{field_str}}}"
            )

        case IR.ArrayConstruct(
            result=result, type_id=type_id, elements=elements
        ):
            elem_str = ", ".join(__dump_value(e) for e in elements)
            return (
                f"%{result.name} = array"
                f" {__type_str(type_id)} [{elem_str}]"
            )

        case IR.VariantConstruct(
            result=result,
            enum_type=_,
            variant=variant,
            payload_fields=payload_fields,
        ):
            if payload_fields is not None:
                pl = ", ".join(__dump_value(f) for f in payload_fields)
                return f"%{result.name} = variant {variant.name} {{{pl}}}"
            return f"%{result.name} = variant {variant.name}"

        case IR.SysWrite(
            fd=fd,
            buf=buf,
        ):
            return f"sys_write {__dump_value(fd)} {__dump_value(buf)}"

        case IR.SysRead(
            result=result,
            fd=fd,
            buf=buf,
        ):
            return (
                f"%{result.name} = sys_read"
                f" {__dump_value(fd)}, {__dump_value(buf)}"
                f"  [{__type_str(result.type_id)}]"
            )

        case IR.Phi(result=result, incoming=incoming):
            inc_str = ", ".join(
                f"[{__dump_value(v)}, {b.label}]" for b, v in incoming
            )
            return (
                f"%{result.name} = phi [{inc_str}]"
                f"  [{__type_str(result.type_id)}]"
            )


# ---------------------------------------------------------------------------
# terminator dumpers
# ---------------------------------------------------------------------------

def __dump_terminator(term: IR.Terminator) -> str:
    match term:
        case IR.Ret(value=value):
            return f"ret {__dump_value(value)}"

        case IR.RetVoid():
            return "ret"

        case IR.Br(target=target):
            return f"br {target.label}"

        case IR.CondBr(
            cond=cond, then_block=then_block, else_block=else_block
        ):
            return (
                f"condbr {__dump_value(cond)},"
                f" {then_block.label}, {else_block.label}"
            )

        case IR.Match(value=value, arms=arms, default=default):
            arm_strs = [__dump_match_arm(arm) for arm in arms]
            if default is not None:
                arm_strs.append(f"default: {default.label}")
            return f"match {__dump_value(value)} {{{', '.join(arm_strs)}}}"

        case IR.Panic(message=message):
            return f"panic {__dump_value(message)}"


def __dump_match_arm(arm: IR.MatchArm) -> str:
    pattern_str = __dump_pattern(arm.pattern)
    return f"{pattern_str} => {arm.body.label}"


# pylint: disable=too-many-return-statements
def __dump_pattern(pattern: IR.Pattern) -> str:
    match pattern:
        case IR.IntPattern(value=value):
            return str(value.value)

        case IR.CharPattern(value=value):
            return repr(value.value)

        case IR.EnumPattern(variant=variant, fields=fields):
            if fields is not None:
                field_strs = [f.name for f in fields]
                return f"{variant.name}({', '.join(field_strs)})"
            return variant.name


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

# pylint: disable=too-many-return-statements
def __dump_value(val: IR.Value) -> str:
    """Format a Value (Reg or Literal) for display."""
    match val:
        case IR.Reg(name=name):
            return f"%{name}"

        case IR.IntLiteral(value=v):
            return f"{v}"

        case IR.FloatLiteral(value=v):
            return f"{v}"

        case IR.BoolLiteral(value=v):
            return "true" if v else "false"

        case IR.CharLiteral(value=v):
            return repr(v)

        case IR.StringLiteral(value=v):
            return repr(v)


def __type_str(type_id: int) -> str:
    if type_id < 0:
        return "void"
    return f"T{type_id}"


def __op_str(op: BinaryOperator | UnaryOperator) -> str:
    return str(op)
