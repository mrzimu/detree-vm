"""
Programmatic construction of IR programs.

A compiler uses this builder instead of constructing node objects by hand. The
builder assigns ids by declaration order, which is what makes modules
deterministic.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..spec.isa import BufferKind
from . import nodes


class FunctionBuilder:
    """
    Collects the body of one function.

    Every method returns `self`, so a body can be written as one call chain.
    """

    def __init__(self, function: nodes.Function) -> None:
        """
        Wrap *function* so statements can be appended to it.

        Args:
            function: the declaration this builder fills.
        """
        self.function = function
        self.last = None

    def local(self, name: str) -> nodes.Local:
        """
        Declare a function-local value.

        Args:
            name: the local's name; a repeated name reuses the declaration.

        Returns:
            A reference to the local.
        """
        if name not in self.function.params and name not in self.function.locals:
            self.function.locals.append(name)
        return nodes.Local(name)

    def emit(self, stmt: nodes.Stmt) -> FunctionBuilder:
        """
        Append *stmt*.

        Args:
            stmt: the statement to append.

        Returns:
            This builder, so calls can be chained.
        """
        self.function.body.append(stmt)
        self.last = stmt
        return self

    def assign(self, target: nodes.Target, value: nodes.Expr) -> FunctionBuilder:
        """
        Emit `target = value`.

        Args:
            target: the slot to write.
            value: the expression to store.

        Returns:
            This builder.
        """
        return self.emit(nodes.Assign(target, value))

    def append(self, buf: int, value: nodes.Expr) -> FunctionBuilder:
        """
        Append the expression *value* to buffer *buf*.

        Args:
            buf: the buffer id.
            value: the expression to append.

        Returns:
            This builder.
        """
        return self.emit(nodes.BufferAppend(buf, value))

    def append_const(self, buf: int, value: int) -> FunctionBuilder:
        """
        Append the constant *value* to buffer *buf*.

        Args:
            buf: the buffer id.
            value: the integer to append.

        Returns:
            This builder.
        """
        return self.emit(nodes.BufferAppendConst(buf, value))

    def ret(self, value: nodes.Expr | None = None) -> FunctionBuilder:
        """
        Return from the function.

        Args:
            value: the result to yield, or `None` for a void function.

        Returns:
            This builder.
        """
        return self.emit(nodes.Return(value))

    def if_(
        self,
        cond: nodes.Expr,
        then_body: Sequence[nodes.Stmt],
        else_body: Sequence[nodes.Stmt] = (),
    ) -> FunctionBuilder:
        """
        Emit a two-way branch over *cond*.

        Args:
            cond: the condition.
            then_body: statements run when *cond* holds.
            else_body: statements run otherwise.

        Returns:
            This builder.
        """
        return self.emit(nodes.If(cond, list(then_body), list(else_body)))

    def while_(
        self,
        cond: nodes.Expr,
        body: Sequence[nodes.Stmt],
    ) -> FunctionBuilder:
        """
        Emit a loop that runs *body* while *cond* holds.

        Args:
            cond: the loop condition, re-evaluated every iteration.
            body: statements run per iteration.

        Returns:
            This builder.
        """
        return self.emit(nodes.While(cond, list(body)))

    def for_range(
        self,
        var: str,
        start: nodes.Expr,
        stop: nodes.Expr,
        step: int,
        body: Sequence[nodes.Stmt],
    ) -> FunctionBuilder:
        """
        Emit a counted loop; *var* becomes a function local.

        Args:
            var: the loop variable's name.
            start: the first value.
            stop: the exclusive upper bound.
            step: the increment.
            body: statements run per iteration.

        Returns:
            This builder.
        """
        if var not in self.function.params and var not in self.function.locals:
            self.function.locals.append(var)
        return self.emit(nodes.ForRange(var, start, stop, step, list(body)))


class ProgramBuilder:
    """
    Build one `nodes.Program` deterministically.

    Ids follow declaration order, which is what makes lowering byte-identical
    across runs.
    """

    def __init__(self) -> None:
        """
        Start an empty program.

        Declarations append to `self.program` in call order, which is what
        assigns their ids.
        """
        self.program = nodes.Program()

    # -- declarations ------------------------------------------------------
    def buffer(
        self,
        name: str,
        dtype: str,
        kind: BufferKind = BufferKind.DATA,
        init: Sequence[int] = (),
    ) -> int:
        """
        Declare an output buffer.

        Args:
            name: the buffer name reported with the result.
            dtype: the NumPy dtype name, such as `"int32"`.
            kind: the storage class; `DATA` unless the buffer holds offsets.
            init: declared initial content, restored at every reset.

        Returns:
            The buffer id used in expressions.
        """
        index = len(self.program.buffers)
        self.program.buffers.append(nodes.Buffer(name, dtype, kind, tuple(init)))
        return index

    def state(self, name: str, init: int = 0, type: str = "i64") -> int:
        """
        Declare a state slot.

        Args:
            name: the state name reported by `state_values`.
            init: the initial value.
            type: `"i64"` for an integer slot, `"name"` for a string slot.

        Returns:
            The state id used in expressions.
        """
        index = len(self.program.states)
        self.program.states.append(nodes.State(name, init, type))
        return index

    def string(self, text: str) -> int:
        """
        Intern *text* in the arena.

        Args:
            text: the string to intern; a duplicate returns the same id.

        Returns:
            The arena id.
        """
        try:
            return self.program.strings.index(text)
        except ValueError:
            self.program.strings.append(text)
            return len(self.program.strings) - 1

    def constant(self, text: str) -> int:
        """
        Intern a string constant.

        Args:
            text: the constant text.

        Returns:
            The `StrConst` id.
        """
        for index, constant in enumerate(self.program.constants):
            if constant.text == text:
                self.string(text)
                return index
        self.program.constants.append(nodes.Constant(text))
        self.string(text)
        return len(self.program.constants) - 1

    def layout(self, fields: Sequence[nodes.LayoutField]) -> int:
        """
        Declare a flat layout.

        Args:
            fields: the layout fields, in stream order.

        Returns:
            The layout id.
        """
        index = len(self.program.layouts)
        self.program.layouts.append(nodes.Layout(index, tuple(fields)))
        return index

    def function(
        self,
        name: str,
        params: Sequence[str] = (),
        result: str = nodes.VOID,
    ) -> FunctionBuilder:
        """
        Declare a function.

        Args:
            name: the function name.
            params: parameter names, in call order.
            result: the result type, or `nodes.VOID`.

        Returns:
            A builder for the function body.
        """
        function = nodes.Function(
            name=name,
            arity=len(params),
            params=tuple(params),
            body=[],
            result=result,
        )
        self.program.functions.append(function)
        return FunctionBuilder(function)

    def build(self, entry: int = 0) -> nodes.Program:
        """
        Finish the program.

        Args:
            entry: the entry function id.

        Returns:
            The completed program, ready for the optimizer.
        """
        self.program.entry = entry
        return self.program


__all__ = ["FunctionBuilder", "ProgramBuilder"]
