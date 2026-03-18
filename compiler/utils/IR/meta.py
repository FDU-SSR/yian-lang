from dataclasses import dataclass

from compiler.config.defs import StmtId, UnitId


@dataclass
class SrcPosition:
    start_row: int
    start_col: int
    end_row: int
    end_col: int

    def __repr__(self) -> str:
        return f"(Line {self.start_row}, Col {self.start_col}) to (Line {self.end_row}, Col {self.end_col})"


@dataclass
class StmtMetadata:
    parent_stmt_id: StmtId
    stmt_id: StmtId
    unit_id: UnitId

    def __repr__(self) -> str:
        return f"Stmt ID: {self.stmt_id}, Parent Stmt ID: {self.parent_stmt_id}, Unit ID: {self.unit_id}"
