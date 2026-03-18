from collections import defaultdict
from typing import TypeVar

from compiler import utils
from compiler.utils.errors import CompilerError

from .gir import (BlockStmt, CaseStmt, DefaultStmt, EnumDeclStmt, FunctionDeclStmt, GIRStmt, ImplementDeclStmt,
                  MethodDeclStmt, MethodHeaderStmt, SrcPosition, StmtId, StmtMetadata, StructDeclStmt, SwitchStmt,
                  TraitDeclStmt)


def map_stmts(stmts: list) -> dict[StmtId, GIRStmt]:
    """
    Maps raw statement data to Stmt objects.

    Args:
        stmts: Raw statement data.

    Returns:
        A tuple containing
        - A dictionary mapping statement IDs to Stmt objects.
        - A list of Stmt objects at the root level.
    """
    # 1. identify all blocks and map their stmt ids to indexes
    stmt_index_stack: list[list[int]] = []
    block_id_to_stmt_indexes: dict[StmtId, list[int]] = defaultdict(list)

    # push the root block
    stmt_index_stack.append([0])

    # traverse stmts to find blocks
    for index, stmt in enumerate(stmts):
        if stmt.operation == "block_start":
            stmt_index_stack.append([index])
        elif stmt.operation == "block_end":
            block_indexes = stmt_index_stack.pop()

            block_id = stmt.stmt_id
            block_id_to_stmt_indexes[block_id] = block_indexes[1:]  # exclude the block_start stmt itself
        else:
            stmt_index_stack[-1].append(index)

    # handle the root block
    root_block_indexes = stmt_index_stack.pop()
    block_id_to_stmt_indexes[0] = root_block_indexes[1:]  # exclude the block_start stmt itself

    # 2. convert indexes to stmt ids
    block_info: dict[StmtId, list[StmtId]] = {
        block_id: [stmts[i].stmt_id for i in stmt_indexes]
        for block_id, stmt_indexes in block_id_to_stmt_indexes.items()
    }

    # 3. map stmts
    stmt_map: dict[StmtId, GIRStmt] = {}
    for stmt in stmts:
        if stmt.operation == "block_end":
            continue
        stmt_id = stmt.stmt_id
        # it is confusing that position info is stored as float in the DB
        pos = SrcPosition(
            start_row=int(stmt.start_row) if utils.is_available(stmt.start_row) else -1,
            start_col=int(stmt.start_col) if utils.is_available(stmt.start_col) else -1,
            end_row=int(stmt.end_row) if utils.is_available(stmt.end_row) else -1,
            end_col=int(stmt.end_col) if utils.is_available(stmt.end_col) else -1,
        )
        metadata = StmtMetadata(
            parent_stmt_id=stmt.parent_stmt_id,
            stmt_id=stmt.stmt_id,
            unit_id=stmt.unit_id,
        )
        stmt_obj = GIRStmt.from_raw(stmt, metadata, pos, block_info)
        stmt_map[stmt_id] = stmt_obj

    # 4. validate some nested block stmts
    for stmt_id, stmt_obj in stmt_map.items():
        if isinstance(stmt_obj, FunctionDeclStmt) and stmt_obj.parameters is not None:
            for param_stmt_id in stmt_map[stmt_obj.parameters].expect_block().body:
                stmt_map[param_stmt_id].expect_parameter_decl()
        elif isinstance(stmt_obj, StructDeclStmt) and stmt_obj.fields is not None:
            for field_stmt_id in stmt_map[stmt_obj.fields].expect_block().body:
                stmt_map[field_stmt_id].expect_variable_decl()
        elif isinstance(stmt_obj, ImplementDeclStmt) and stmt_obj.methods is not None:
            for method_stmt_id in stmt_map[stmt_obj.methods].expect_block().body:
                stmt_map[method_stmt_id].expect_method_decl()
        elif isinstance(stmt_obj, SwitchStmt):
            for case_stmt_id in stmt_map[stmt_obj.body].expect_block().body:
                case_stmt = stmt_map[case_stmt_id]
                if not isinstance(case_stmt, (CaseStmt, DefaultStmt)):
                    raise CompilerError(f"Switch body must contain CaseStmt or DefaultStmt, got {type(case_stmt).__name__} at {case_stmt.pos}")
        elif isinstance(stmt_obj, TraitDeclStmt) and stmt_obj.methods is not None:
            for method_stmt_id in stmt_map[stmt_obj.methods].expect_block().body:
                # stmt_map[method_stmt_id].expect_method_header()
                if not isinstance(stmt_map[method_stmt_id], (MethodHeaderStmt, MethodDeclStmt)):
                    raise CompilerError(f"Expected MethodHeaderStmt or MethodDeclStmt, got {type(stmt_map[method_stmt_id]).__name__} at {stmt_map[method_stmt_id].pos}")
        elif isinstance(stmt_obj, EnumDeclStmt):
            for variant_stmt_id in stmt_map[stmt_obj.variants].expect_block().body:
                stmt_map[variant_stmt_id].expect_variant_decl()
        elif isinstance(stmt_obj, MethodHeaderStmt) and stmt_obj.parameters is not None:
            for param_stmt_id in stmt_map[stmt_obj.parameters].expect_block().body:
                stmt_map[param_stmt_id].expect_parameter_decl()

    # 4. collect root stmts into one block
    root_block = BlockStmt(
        StmtMetadata(-1, 0, stmts[0].unit_id),
        SrcPosition(0, 0, 0, 0),
        block_info[0],
    )
    stmt_map[0] = root_block
    return stmt_map


T_FuncOrMethod = TypeVar("T_FuncOrMethod", FunctionDeclStmt, MethodDeclStmt)
