"""
Text serialisation for CFG IR (cfg/ir.py).
"""

from __future__ import annotations

from compiler.codegen.cfg import ir as IR
from compiler.frontend.parse.operator import BinaryOperator, UnaryOperator


def dump(func: IR.Function) -> str:
    """Return a human-readable string representation of *func*."""
    out: list[str] = []

    # signature
    param_strs = [f"%{p.name}: {__type_str(p.type_id)}" for p in func.params]
    ret = __type_str(func.return_type) if func.return_type >= 0 else "void"
    out.append(f"function @{func.name}({', '.join(param_strs)}) -> {ret} {{")

    for block in func.blocks:
        out.append(f"  {block.label}:")
        for stmt in block.stmts:
            out.append(f"    {__dump_stmt(stmt)}")
        if block.terminator:
            out.append(f"    {__dump_terminator(block.terminator)}")
        out.append("")

    out.append("}")
    return "\n".join(out)


def __dump_stmt(stmt: IR.Stmt) -> str:
    match stmt:
        case IR.LocalPtr(result=result, var_name=name, type_id=tid):
            return f"%{result} = localptr {name}  [{__type_str(tid)}]"

        case IR.FieldPtr(result=result, base=base, field_name=field, struct_type=st):
            return f"%{result} = fieldptr %{base}.{field}  [struct {__type_str(st)}]"

        case IR.ElementPtr(result=result, base=base, index=idx, element_type=et):
            return f"%{result} = elementptr %{base}[{idx}]  [{__type_str(et)}]"

        case IR.Lit(result=result, value=value, type_id=tid):
            return f"%{result} = Lit {__repr_value(value)}  [{__type_str(tid)}]"

        case IR.Load(result=result, ptr=ptr, type_id=tid):
            return f"%{result} = Load %{ptr}  [{__type_str(tid)}]"

        case IR.Binary(result=result, op=op, lhs=lhs, rhs=rhs, type_id=tid):
            return f"%{result} = Binary {__op_str(op)} %{lhs}, %{rhs}  [{__type_str(tid)}]"

        case IR.Unary(result=result, op=op, operand=operand, type_id=tid):
            return f"%{result} = Unary {__op_str(op)} %{operand}  [{__type_str(tid)}]"

        case IR.Call(result=result, callee=callee, args=args, type_id=tid):
            arg_str = ", ".join(f"%{a}" for a in args)
            res = f"%{result}" if result is not None else "void"
            return f"{res} = call @{callee}({arg_str})  [{__type_str(tid)}]"

        case IR.StructConstruct(result=result, struct_type=st, fields=fields):
            field_str = ", ".join(f"{n}: %{v}" for n, v in fields)
            return f"%{result} = Struct {__type_str(st)} {{{field_str}}}"

        case IR.VariantConstruct(result=result, variant_name=name, payload=payload):
            if payload is not None:
                pl = ", ".join(f"{n}: %{v}" for n, v in payload)
                return f"%{result} = Variant {name} {{{pl}}}"
            return f"%{result} = Variant {name}"

        case IR.Cast(result=result, value=value, target_type=tt, source_type=st):
            return f"%{result} = Cast %{value}  [{__type_str(st)} -> {__type_str(tt)}]"

        case IR.Phi(result=result, incoming=incoming, type_id=tid):
            inc_str = ", ".join(f"[%{v}, {b}]" for v, b in incoming)
            return f"%{result} = Phi [{inc_str}]  [{__type_str(tid)}]"

        case IR.Store(ptr=ptr, value=value):
            return f"Store %{ptr}, %{value}"


def __dump_terminator(term: IR.Terminator) -> str:
    match term:
        case IR.Ret(value=value):
            return f"Ret %{value}" if value is not None else "Ret"
        case IR.Br(target=target):
            return f"Br {target}"
        case IR.CondBr(cond=cond, then_block=then_, else_block=else_):
            return f"CondBr %{cond}, {then_}, {else_}"
        case IR.Switch(value=value, default_block=default, cases=cases):
            case_strs = [f"case {v}: {b}" for v, b in cases]
            if default is not None:
                case_strs.append(f"default: {default}")
            return f"Switch %{value} [{', '.join(case_strs)}]"
        case IR.Unreachable():
            return "Unreachable"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def __type_str(type_id: int) -> str:
    if type_id < 0:
        return "void"
    return f"T{type_id}"


def __op_str(op: BinaryOperator | UnaryOperator) -> str:
    return str(op)


def __repr_value(value: int | float | bool) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return repr(value)
