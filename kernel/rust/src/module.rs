//! Module decoder and static validator.
//!
//! The wire format is normative in `docs/spec/module.md`; this is the Rust
//! implementation of it, byte for byte the inverse of the Python reference's
//! `to_bytes`.

use std::collections::HashSet;

use crate::errors::{Error, Result};
use crate::isa::{
    buffer_typecode, BufferKind, FieldKind, Ins, Op, Typecode, INSTRUCTION_SIZE, ISA_MAJOR,
    ISA_MINOR, MAGIC,
};

/// One declared output buffer.
#[derive(Debug, Clone)]
pub struct BufferDecl {
    pub name: String,
    pub dtype: String,
    pub kind: BufferKind,
    pub init: Vec<i64>,
}

/// One declared state slot.
#[derive(Debug, Clone)]
pub struct StateDecl {
    pub name: String,
    pub init: i64,
    pub is_string: bool,
}

/// One field of a flat layout.
#[derive(Debug, Clone, Copy)]
pub struct LayoutField {
    pub kind: FieldKind,
    pub typecode: Typecode,
    pub count: i32,
    pub buffer_id: i32,
}

/// One class-version layout.
#[derive(Debug, Clone)]
pub struct Layout {
    pub id: u32,
    pub fields: Vec<LayoutField>,
}

/// One function: a name, a register budget and a slice of the code pool.
#[derive(Debug, Clone)]
pub struct FuncDecl {
    pub id: u32,
    pub name: String,
    pub arity: u8,
    pub reg_count: u16,
    pub code_off: u32,
    pub code_len: u32,
}

impl FuncDecl {
    /// One past the last instruction index this function owns.
    pub fn end(&self) -> usize {
        self.code_off as usize + self.code_len as usize
    }
}

/// A decoded module: the whole of what a front end hands a machine.
#[derive(Debug, Clone)]
pub struct Module {
    pub isa_major: u16,
    pub isa_minor: u16,
    pub entry: u16,
    pub regs_max: u16,
    pub buffers: Vec<BufferDecl>,
    pub states: Vec<StateDecl>,
    pub strings: Vec<String>,
    pub layouts: Vec<Layout>,
    pub funcs: Vec<FuncDecl>,
    pub code: Vec<Ins>,
}

struct Decoder<'a> {
    raw: &'a [u8],
    pos: usize,
}

impl<'a> Decoder<'a> {
    fn new(raw: &'a [u8]) -> Decoder<'a> {
        Decoder { raw, pos: 0 }
    }

    fn remaining(&self) -> usize {
        self.raw.len() - self.pos
    }

    fn take(&mut self, size: usize, what: &str) -> Result<&'a [u8]> {
        if size > self.remaining() {
            return Err(Error::module(format!(
                "module is truncated: wanted {size} bytes for {what} at {}, only {} left",
                self.pos,
                self.remaining()
            )));
        }
        let chunk = &self.raw[self.pos..self.pos + size];
        self.pos += size;
        Ok(chunk)
    }

    fn u8(&mut self, what: &str) -> Result<u8> {
        Ok(self.take(1, what)?[0])
    }

    fn u16(&mut self, what: &str) -> Result<u16> {
        Ok(u16::from_le_bytes(self.take(2, what)?.try_into().unwrap()))
    }

    fn u32(&mut self, what: &str) -> Result<u32> {
        Ok(u32::from_le_bytes(self.take(4, what)?.try_into().unwrap()))
    }

    fn i32(&mut self, what: &str) -> Result<i32> {
        Ok(i32::from_le_bytes(self.take(4, what)?.try_into().unwrap()))
    }

    fn i64(&mut self, what: &str) -> Result<i64> {
        Ok(i64::from_le_bytes(self.take(8, what)?.try_into().unwrap()))
    }

    fn text(&mut self, what: &str) -> Result<String> {
        let size = self.u16(what)? as usize;
        let bytes = self.take(size, what)?;
        String::from_utf8(bytes.to_vec())
            .map_err(|_| Error::module(format!("{what} is not valid UTF-8")))
    }
}

/// Refuse a count the remaining bytes cannot possibly satisfy, before reserving.
fn check_count(count: u32, minimum_each: usize, remaining: usize, what: &str) -> Result<()> {
    if count as usize > remaining / minimum_each {
        return Err(Error::module(format!(
            "module declares {count} {what}, which does not fit in {remaining} remaining bytes"
        )));
    }
    Ok(())
}

impl Module {
    /// Decode a serialized module.
    pub fn from_bytes(raw: &[u8]) -> Result<Module> {
        let mut decoder = Decoder::new(raw);

        let magic = decoder.take(MAGIC.len(), "magic")?;
        if magic != MAGIC {
            return Err(Error::module("bad magic: not a detree module"));
        }

        let isa_major = decoder.u16("isa_major")?;
        let isa_minor = decoder.u16("isa_minor")?;
        let entry = decoder.u16("entry")?;
        let regs_max = decoder.u16("regs_max")?;

        let n_buffers = decoder.u32("buffer count")?;
        let n_states = decoder.u32("state count")?;
        let n_strings = decoder.u32("string count")?;
        let n_layouts = decoder.u32("layout count")?;
        let n_funcs = decoder.u32("function count")?;

        check_count(n_buffers, 7, decoder.remaining(), "buffers")?;
        check_count(n_states, 11, decoder.remaining(), "states")?;
        check_count(n_strings, 2, decoder.remaining(), "strings")?;
        check_count(n_layouts, 4, decoder.remaining(), "layouts")?;
        check_count(n_funcs, 15, decoder.remaining(), "functions")?;

        let mut buffers = Vec::with_capacity(n_buffers as usize);
        for _ in 0..n_buffers {
            let name = decoder.text("buffer name")?;
            let dtype = decoder.text("buffer dtype")?;
            let kind = BufferKind::from_u8(decoder.u8("buffer kind")?)
                .ok_or_else(|| Error::module(format!("buffer {name}: unknown kind")))?;
            let n_init = decoder.u32("buffer init count")?;
            check_count(n_init, 8, decoder.remaining(), "buffer init values")?;
            let mut init = Vec::with_capacity(n_init as usize);
            for _ in 0..n_init {
                init.push(decoder.i64("buffer init value")?);
            }
            buffers.push(BufferDecl {
                name,
                dtype,
                kind,
                init,
            });
        }

        let mut states = Vec::with_capacity(n_states as usize);
        for _ in 0..n_states {
            let name = decoder.text("state name")?;
            let init = decoder.i64("state init")?;
            let flags = decoder.u8("state flags")?;
            if flags & !0x01 != 0 {
                return Err(Error::module(format!(
                    "state {name}: unknown flags {flags:#x}"
                )));
            }
            states.push(StateDecl {
                name,
                init,
                is_string: flags & 0x01 != 0,
            });
        }

        let mut strings = Vec::with_capacity(n_strings as usize);
        for _ in 0..n_strings {
            strings.push(decoder.text("string")?);
        }

        let mut layouts = Vec::with_capacity(n_layouts as usize);
        for _ in 0..n_layouts {
            let id = decoder.u32("layout id")?;
            let n_fields = decoder.u32("layout field count")?;
            check_count(n_fields, 10, decoder.remaining(), "layout fields")?;
            let mut fields = Vec::with_capacity(n_fields as usize);
            for _ in 0..n_fields {
                let kind = FieldKind::from_u8(decoder.u8("field kind")?)
                    .ok_or_else(|| Error::module("unknown layout field kind"))?;
                let typecode = Typecode::from_u8(decoder.u8("field typecode")?)
                    .ok_or_else(|| Error::module("unknown layout typecode"))?;
                let count = decoder.i32("field count")?;
                let buffer_id = decoder.i32("field buffer")?;
                fields.push(LayoutField {
                    kind,
                    typecode,
                    count,
                    buffer_id,
                });
            }
            layouts.push(Layout { id, fields });
        }

        let mut funcs = Vec::with_capacity(n_funcs as usize);
        for _ in 0..n_funcs {
            let id = decoder.u32("function id")?;
            let name = decoder.text("function name")?;
            let arity = decoder.u8("function arity")?;
            let reg_count = decoder.u16("function reg_count")?;
            let _reserved = decoder.u16("function reserved")?;
            let code_off = decoder.u32("function code offset")?;
            let code_len = decoder.u32("function code length")?;
            funcs.push(FuncDecl {
                id,
                name,
                arity,
                reg_count,
                code_off,
                code_len,
            });
        }

        let n_code = decoder.u32("code length")?;
        check_count(
            n_code,
            INSTRUCTION_SIZE,
            decoder.remaining(),
            "instructions",
        )?;
        let mut code = Vec::with_capacity(n_code as usize);
        for index in 0..n_code {
            let raw = decoder.take(INSTRUCTION_SIZE, "instruction")?;
            let op = Op::from_u8(raw[0]).ok_or_else(|| {
                Error::module(format!(
                    "instruction {index} has unknown opcode {:#04x}",
                    raw[0]
                ))
            })?;
            let imm1 = i64::from_le_bytes(raw[4..12].try_into().unwrap());
            let imm2 = i64::from_le_bytes(raw[12..20].try_into().unwrap());
            code.push(Ins {
                op,
                rd: raw[1],
                ra: raw[2],
                rb: raw[3],
                imm1,
                imm2,
            });
        }

        if decoder.remaining() != 0 {
            return Err(Error::module(format!(
                "module has {} trailing bytes after the instructions",
                decoder.remaining()
            )));
        }

        Ok(Module {
            isa_major,
            isa_minor,
            entry,
            regs_max,
            buffers,
            states,
            strings,
            layouts,
            funcs,
            code,
        })
    }
}

/// Everything about `module` that can be wrong by construction.
pub fn validate(module: &Module) -> Result<()> {
    if module.isa_major != ISA_MAJOR {
        return Err(Error::module(format!(
            "module isa_major {} != machine {ISA_MAJOR}",
            module.isa_major
        )));
    }
    if module.isa_minor > ISA_MINOR {
        return Err(Error::module(format!(
            "module isa_minor {} is newer than machine {ISA_MINOR}",
            module.isa_minor
        )));
    }
    if module.funcs.is_empty() {
        return Err(Error::module("module has no functions"));
    }
    if module.entry as usize >= module.funcs.len() {
        return Err(Error::module(format!(
            "entry function {} does not exist",
            module.entry
        )));
    }

    // -- functions: ids, arity, ranges, non-overlapping code slices ---------
    let mut spans = Vec::with_capacity(module.funcs.len());
    for (index, func) in module.funcs.iter().enumerate() {
        let name = display_name(&func.name);
        if func.id as usize != index {
            return Err(Error::module(format!(
                "function {index} has id {}",
                func.id
            )));
        }
        if func.arity > 1 {
            return Err(Error::module(format!("{name}: arity {}", func.arity)));
        }
        if func.arity == 1 && func.reg_count == 0 {
            return Err(Error::module(format!(
                "{name}: arity 1 needs a register for the argument"
            )));
        }
        if func.code_len == 0 {
            return Err(Error::module(format!("{name}: empty body")));
        }
        if func.code_off as usize > module.code.len() || func.end() > module.code.len() {
            return Err(Error::module(format!("{name}: code range out of bounds")));
        }
        if module.regs_max < func.reg_count {
            return Err(Error::module(format!(
                "regs_max {} below {name} reg_count {}",
                module.regs_max, func.reg_count
            )));
        }
        spans.push((func.code_off as usize, func.end(), name));
    }
    spans.sort_by_key(|span| span.0);
    for pair in spans.windows(2) {
        if pair[0].1 > pair[1].0 {
            return Err(Error::module(format!(
                "{} and {} have overlapping code slices",
                pair[0].2, pair[1].2
            )));
        }
    }

    // -- buffers ------------------------------------------------------------
    let mut names = HashSet::new();
    for decl in &module.buffers {
        let name = display_name(&decl.name);
        if !names.insert(decl.name.as_str()) {
            return Err(Error::module(format!(
                "duplicate buffer name {:?}",
                decl.name
            )));
        }
        if buffer_typecode(&decl.dtype).is_none() {
            return Err(Error::module(format!(
                "buffer {name}: dtype {:?}",
                decl.dtype
            )));
        }
        if decl.kind != BufferKind::Data && decl.dtype != "int64" {
            return Err(Error::module(format!(
                "buffer {name}: OFFSETS/COUNT buffers must be int64"
            )));
        }
    }

    // -- layouts --------------------------------------------------------------
    for layout in &module.layouts {
        for field in &layout.fields {
            let at = format!("layout {}", layout.id);
            if field.kind == FieldKind::TobjectSkip {
                return Err(Error::module(format!("{at}: TOBJECT_SKIP unsupported")));
            }
            if field.count < 0 {
                return Err(Error::module(format!("{at}: negative field count")));
            }
            if field.kind != FieldKind::Skip {
                if field.buffer_id < 0 || field.buffer_id as usize >= module.buffers.len() {
                    return Err(Error::module(format!(
                        "{at}: buffer {} does not exist",
                        field.buffer_id
                    )));
                }
            }
        }
    }

    // -- code ------------------------------------------------------------------
    let n_buffers = module.buffers.len();
    let n_states = module.states.len();
    let n_layouts = module.layouts.len();
    let n_strings = module.strings.len();

    for func in &module.funcs {
        for offset in 0..func.code_len as usize {
            let ins = module.code[func.code_off as usize + offset];
            let at = format!("{}#{offset}", display_name(&func.name));

            for (used, register) in [
                (uses_rd(ins.op), ins.rd),
                (uses_ra(ins.op), ins.ra),
                (uses_rb(ins.op), ins.rb),
            ] {
                if used && register as u16 >= func.reg_count {
                    return Err(Error::module(format!(
                        "{at}: register {register} >= reg_count {}",
                        func.reg_count
                    )));
                }
            }

            if ins.op.is_jump() && (ins.imm1 < 0 || ins.imm1 as usize >= func.code_len as usize) {
                return Err(Error::module(format!(
                    "{at}: jump target {} outside the function",
                    ins.imm1
                )));
            }

            if ins.op == Op::Skip && ins.imm1 < 0 {
                return Err(Error::module(format!("{at}: negative skip {}", ins.imm1)));
            }

            if ins.op == Op::Call && (ins.imm1 < 0 || ins.imm1 as usize >= module.funcs.len()) {
                return Err(Error::module(format!(
                    "{at}: function {} does not exist",
                    ins.imm1
                )));
            }

            if ins.op.is_buffer() && (ins.imm1 < 0 || ins.imm1 as usize >= n_buffers) {
                return Err(Error::module(format!(
                    "{at}: buffer {} does not exist",
                    ins.imm1
                )));
            }

            if ins.op == Op::BufAppendLen {
                for operand in [ins.imm1, ins.imm2] {
                    if operand < 0 || operand as usize >= n_buffers {
                        return Err(Error::module(format!(
                            "{at}: buffer {operand} does not exist"
                        )));
                    }
                }
            }

            if matches!(ins.op, Op::ReadBulk | Op::ReadStrided) {
                if ins.imm1 < 0 || ins.imm1 as usize >= n_buffers {
                    return Err(Error::module(format!(
                        "{at}: buffer {} does not exist",
                        ins.imm1
                    )));
                }
                if !is_typecode(ins.imm2) {
                    return Err(Error::module(format!(
                        "{at}: unknown typecode {}",
                        ins.imm2
                    )));
                }
            }

            if matches!(ins.op, Op::Sread | Op::ReadAppend) {
                let typecode = if ins.op == Op::Sread {
                    ins.imm1
                } else {
                    ins.imm2
                };
                if !is_typecode(typecode) {
                    return Err(Error::module(format!("{at}: unknown typecode {typecode}")));
                }
            }

            if ins.op.is_state() && (ins.imm1 < 0 || ins.imm1 as usize >= n_states) {
                return Err(Error::module(format!(
                    "{at}: state {} does not exist",
                    ins.imm1
                )));
            }

            if ins.op == Op::StrConst && (ins.imm1 < 0 || ins.imm1 as usize >= n_strings) {
                return Err(Error::module(format!(
                    "{at}: string {} does not exist",
                    ins.imm1
                )));
            }

            if ins.op == Op::ReadFields && (ins.imm1 < 0 || ins.imm1 as usize >= n_layouts) {
                return Err(Error::module(format!(
                    "{at}: layout {} does not exist",
                    ins.imm1
                )));
            }

            if ins.op.is_message() && (ins.imm1 < 0 || ins.imm1 as usize >= n_strings) {
                return Err(Error::module(format!(
                    "{at}: message {} does not exist",
                    ins.imm1
                )));
            }
        }
    }

    Ok(())
}

fn display_name(name: &str) -> &str {
    if name.is_empty() {
        "<anonymous>"
    } else {
        name
    }
}

/// True when an immediate is one of the ten type codes.
fn is_typecode(value: i64) -> bool {
    (0x01..=0x0A).contains(&value)
}

/// True when `op` writes `rd`.
fn uses_rd(op: Op) -> bool {
    matches!(
        op,
        Op::Const
            | Op::ConstF
            | Op::Mov
            | Op::Zext32
            | Op::Zext16
            | Op::Zext8
            | Op::Sext32
            | Op::Sext16
            | Op::Sext8
            | Op::Add
            | Op::Sub
            | Op::Mul
            | Op::DivS
            | Op::DivU
            | Op::ModS
            | Op::ModU
            | Op::Neg
            | Op::Shl
            | Op::ShrS
            | Op::ShrU
            | Op::And
            | Op::Or
            | Op::Xor
            | Op::Not
            | Op::NotB
            | Op::CmpEq
            | Op::CmpNe
            | Op::CmpLtS
            | Op::CmpLeS
            | Op::CmpGtS
            | Op::CmpGeS
            | Op::CmpLtU
            | Op::CmpLeU
            | Op::CmpGtU
            | Op::CmpGeU
            | Op::Call
            | Op::Sread
            | Op::SreadFnb
            | Op::SreadVersion
            | Op::SreadCstr
            | Op::Cursor
            | Op::Entries
            | Op::InitOffset
            | Op::DataSize
            | Op::OffsetAt
            | Op::OffsetSeek
            | Op::EventEnd
            | Op::BufLast
            | Op::BufLen
            | Op::StateGet
            | Op::RefHas
            | Op::RefGetObj
            | Op::RefGetClass
            | Op::RefCount
            | Op::StrConst
            | Op::StrEq
            | Op::StrLoad
    )
}

/// True when `op` reads `ra`.
fn uses_ra(op: Op) -> bool {
    matches!(
        op,
        Op::Mov
            | Op::Zext32
            | Op::Zext16
            | Op::Zext8
            | Op::Sext32
            | Op::Sext16
            | Op::Sext8
            | Op::Neg
            | Op::Not
            | Op::NotB
            | Op::Jz
            | Op::Jnz
            | Op::BrEq
            | Op::BrNe
            | Op::BrLtS
            | Op::BrLeS
            | Op::BrGtS
            | Op::BrGeS
            | Op::BrLtU
            | Op::BrLeU
            | Op::BrGtU
            | Op::BrGeU
            | Op::Call
            | Op::Ret
            | Op::SkipR
            | Op::SetCursor
            | Op::OffsetAt
            | Op::OffsetSeek
            | Op::ReadBulk
            | Op::ReadStrided
            | Op::ReadFields
            | Op::BufAppend
            | Op::BufAppendOffset
            | Op::BufReserve
            | Op::StateSet
            | Op::RefHas
            | Op::RefGetObj
            | Op::RefGetClass
            | Op::RefSetObj
            | Op::RefSetClass
            | Op::StrEq
            | Op::StrStore
            | Op::Assert
            | Op::AssertCursorEq
            | Op::AssertStrEq
            | Op::DebugPrint
    )
}

/// True when `op` reads `rb`.
fn uses_rb(op: Op) -> bool {
    matches!(
        op,
        Op::Add
            | Op::Sub
            | Op::Mul
            | Op::DivS
            | Op::DivU
            | Op::ModS
            | Op::ModU
            | Op::Shl
            | Op::ShrS
            | Op::ShrU
            | Op::And
            | Op::Or
            | Op::Xor
            | Op::CmpEq
            | Op::CmpNe
            | Op::CmpLtS
            | Op::CmpLeS
            | Op::CmpGtS
            | Op::CmpGeS
            | Op::CmpLtU
            | Op::CmpLeU
            | Op::CmpGtU
            | Op::CmpGeU
            | Op::BrEq
            | Op::BrNe
            | Op::BrLtS
            | Op::BrLeS
            | Op::BrGtS
            | Op::BrGeS
            | Op::BrLtU
            | Op::BrLeU
            | Op::BrGtU
            | Op::BrGeU
            | Op::ReadStrided
            | Op::RefSetObj
            | Op::RefSetClass
            | Op::StrEq
            | Op::AssertStrEq
    )
}
