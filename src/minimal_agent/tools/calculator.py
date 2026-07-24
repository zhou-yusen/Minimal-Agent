"""A deliberately small arithmetic tool implemented with an AST whitelist."""

from __future__ import annotations

import ast
import math
from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, StringConstraints

from minimal_agent.errors import ToolExecutionError
from minimal_agent.models import ToolContext


Expression = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=256),
]


class CalculatorArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    expression: Expression


class CalculatorTool:
    name = "calculator"
    description = (
        "Evaluate a basic arithmetic expression containing numbers, parentheses, "
        "addition, subtraction, multiplication, division, and unary plus or minus."
    )
    arguments_model = CalculatorArguments

    _MAX_AST_NODES = 64
    _MAX_AST_DEPTH = 12
    _MAX_ABS_LITERAL = 1_000_000_000_000
    _MAX_ABS_RESULT = 1_000_000_000_000_000

    @property
    def parameters_schema(self) -> dict[str, Any]:
        return self.arguments_model.model_json_schema()

    async def execute(
        self,
        arguments: CalculatorArguments,
        context: ToolContext,
    ) -> dict[str, int | float | str]:
        del context
        tree = self._parse(arguments.expression)
        result = self._evaluate(tree.body)
        self._check_result(result)
        return {"expression": arguments.expression, "result": result}

    def _parse(self, expression: str) -> ast.Expression:
        try:
            tree = ast.parse(expression, mode="eval")
        except (SyntaxError, ValueError) as exc:
            raise ToolExecutionError("invalid arithmetic expression") from exc

        if sum(1 for _ in ast.walk(tree)) > self._MAX_AST_NODES:
            raise ToolExecutionError("arithmetic expression is too complex")
        if self._depth(tree) > self._MAX_AST_DEPTH:
            raise ToolExecutionError("arithmetic expression is nested too deeply")
        return tree

    def _evaluate(self, node: ast.AST) -> int | float:
        if isinstance(node, ast.Constant):
            if isinstance(node.value, bool) or not isinstance(
                node.value, (int, float)
            ):
                raise ToolExecutionError("only finite numeric literals are allowed")
            if isinstance(node.value, float) and not math.isfinite(node.value):
                raise ToolExecutionError("only finite numeric literals are allowed")
            if abs(node.value) > self._MAX_ABS_LITERAL:
                raise ToolExecutionError("numeric literal is too large")
            return node.value

        if isinstance(node, ast.UnaryOp) and isinstance(
            node.op, (ast.UAdd, ast.USub)
        ):
            operand = self._evaluate(node.operand)
            result = operand if isinstance(node.op, ast.UAdd) else -operand
            self._check_result(result)
            return result

        if isinstance(node, ast.BinOp) and isinstance(
            node.op, (ast.Add, ast.Sub, ast.Mult, ast.Div)
        ):
            left = self._evaluate(node.left)
            right = self._evaluate(node.right)
            try:
                if isinstance(node.op, ast.Add):
                    result = left + right
                elif isinstance(node.op, ast.Sub):
                    result = left - right
                elif isinstance(node.op, ast.Mult):
                    result = left * right
                else:
                    result = left / right
            except ZeroDivisionError as exc:
                raise ToolExecutionError("division by zero") from exc
            except OverflowError as exc:
                raise ToolExecutionError("numeric result overflowed") from exc
            self._check_result(result)
            return result

        raise ToolExecutionError(
            f"unsupported arithmetic syntax: {type(node).__name__}"
        )

    def _check_result(self, value: int | float) -> None:
        try:
            finite = math.isfinite(float(value))
        except OverflowError as exc:
            raise ToolExecutionError("numeric result overflowed") from exc
        if not finite or abs(value) > self._MAX_ABS_RESULT:
            raise ToolExecutionError("numeric result is too large")

    def _depth(self, node: ast.AST) -> int:
        children = list(ast.iter_child_nodes(node))
        if not children:
            return 1
        return 1 + max(self._depth(child) for child in children)
