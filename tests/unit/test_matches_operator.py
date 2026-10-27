"""Focused parser and desugar tests for the `matches` operator."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "python"))

from yianc.analysis.passes.desugar import Desugar
from yianc.frontend.lex import token as Tok
from yianc.frontend.lex.lexer import Lexer
from yianc.frontend.parse import ast as AST
from yianc.frontend.parse.error import ParseError
from yianc.frontend.parse.operator import BinaryOperator
from yianc.frontend.parse.parser import Parser


class MatchesOperatorTests(unittest.TestCase):
    def parse(self, text: str) -> AST.Program:
        lexer = Lexer(Path("<test>.an"), text)
        lexer.lex()
        return Parser(lexer.export()).parse()

    def first_init(self, text: str) -> AST.Expr:
        program = self.parse(text)
        function = program.items[0]
        self.assertIsInstance(function, AST.FuncDef)
        assert isinstance(function, AST.FuncDef)
        statement = function.body.stmts[0]
        if isinstance(statement, AST.Semi):
            statement = statement.expr
        self.assertIsInstance(statement, AST.VarDecl)
        assert isinstance(statement, AST.VarDecl)
        assert statement.init_expr is not None
        return statement.init_expr

    def test_lexer_reserves_matches(self) -> None:
        lexer = Lexer(Path("<test>.an"), "matches")
        lexer.lex()
        token = lexer.export()[0]
        self.assertIsInstance(token, Tok.Keyword)
        assert isinstance(token, Tok.Keyword)
        self.assertEqual(token.kind, Tok.KeywordKind.Matches)

    def test_parser_builds_matches_node(self) -> None:
        init = self.first_init("fn main() { let result = 1 matches 1; }")
        self.assertIsInstance(init, AST.Matches)
        assert isinstance(init, AST.Matches)
        self.assertIsInstance(init.pattern, AST.LiteralPattern)

    def test_pattern_alternatives_and_range(self) -> None:
        alternatives = self.first_init("fn main() { let result = 1 matches 1 | 2; }")
        self.assertIsInstance(alternatives, AST.Matches)
        assert isinstance(alternatives, AST.Matches)
        self.assertIsInstance(alternatives.pattern, AST.OrPattern)

        ranged = self.first_init("fn main() { let result = 1 matches 0..10; }")
        self.assertIsInstance(ranged, AST.Matches)
        assert isinstance(ranged, AST.Matches)
        self.assertIsInstance(ranged.pattern, AST.RangePattern)

    def test_equality_binds_outside_matches(self) -> None:
        init = self.first_init("fn main() { let result = 1 == 1 matches true; }")
        self.assertIsInstance(init, AST.Matches)
        assert isinstance(init, AST.Matches)
        self.assertIsInstance(init.value, AST.Binary)
        assert isinstance(init.value, AST.Binary)
        self.assertEqual(init.value.op, BinaryOperator.Eq)

    def test_desugar_builds_condition_match(self) -> None:
        program = self.parse("fn main() { let result = 1 matches 1; }")
        Desugar(program).run()
        function = program.items[0]
        self.assertIsInstance(function, AST.FuncDef)
        assert isinstance(function, AST.FuncDef)
        statement = function.body.stmts[0]
        if isinstance(statement, AST.Semi):
            statement = statement.expr
        self.assertIsInstance(statement, AST.VarDecl)
        assert isinstance(statement, AST.VarDecl)
        self.assertIsInstance(statement.init_expr, AST.Match)
        assert isinstance(statement.init_expr, AST.Match)
        arms = statement.init_expr.arms
        self.assertEqual(len(arms), 2)
        self.assertEqual(arms[0].origin, AST.MatchArmOrigin.CONDITION)
        self.assertEqual(arms[1].origin, AST.MatchArmOrigin.SYNTHETIC)
        self.assertIsInstance(arms[0].body.stmts[0], AST.Literal)
        self.assertIsInstance(arms[1].body.stmts[0], AST.Literal)

    def test_matches_is_reserved_identifier(self) -> None:
        with self.assertRaises(ParseError) as caught:
            self.parse("fn main() { let matches = 1; }")
        self.assertIn("reserved", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
