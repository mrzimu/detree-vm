"""
Small helper for hand-built modules in the tests.
"""

from __future__ import annotations

from detree.spec.isa import BufferKind, Typecode
from detree.spec.module import (
    BufferDecl,
    FuncDecl,
    Ins,
    Layout,
    LayoutField,
    Module,
    StateDecl,
)


def ins(op, rd=0, ra=0, rb=0, imm1=0, imm2=0):
    return Ins(op=op, rd=rd, ra=ra, rb=rb, imm1=imm1, imm2=imm2)


class Builder:
    """
    Build one module from hand-written declarations and code.
    """

    def __init__(self):
        self.buffers = []
        self.states = []
        self.strings = []
        self.layouts = []
        self.funcs = []
        self.code = []

    def buffer(self, name, dtype, kind=BufferKind.DATA, init=()):
        index = len(self.buffers)
        self.buffers.append(BufferDecl(name=name, dtype=dtype, kind=kind, init=tuple(init)))
        return index

    def state(self, name, init=0, is_string=False):
        index = len(self.states)
        self.states.append(StateDecl(name=name, init=init, is_string=is_string))
        return index

    def string(self, text):
        self.strings.append(text)
        return len(self.strings) - 1

    def layout(self, fields):
        layout_id = len(self.layouts)
        self.layouts.append(Layout(id=layout_id, fields=tuple(fields)))
        return layout_id

    def field(self, kind, typecode=Typecode.UINT8, count=1, buffer_id=-1):
        return LayoutField(kind=kind, typecode=typecode, count=count, buffer_id=buffer_id)

    def function(self, name, code, arity=0, reg_count=1):
        func_id = len(self.funcs)
        code_off = len(self.code)
        self.code.extend(code)
        self.funcs.append(
            FuncDecl(
                id=func_id,
                name=name,
                arity=arity,
                reg_count=reg_count,
                code_off=code_off,
                code_len=len(code),
            )
        )
        return func_id

    def build(self, entry=0):
        regs_max = max((func.reg_count for func in self.funcs), default=0)
        return Module(
            entry=entry,
            regs_max=regs_max,
            buffers=list(self.buffers),
            states=list(self.states),
            strings=list(self.strings),
            layouts=list(self.layouts),
            funcs=list(self.funcs),
            code=list(self.code),
        )


__all__ = ["Builder", "ins"]
