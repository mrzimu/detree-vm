//! The machine: event loop, frames, and instruction dispatch.
//!
//! Semantics are normative in `docs/spec/isa.md`; the Python reference is the oracle
//! this port is tested against.

use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};
use std::slice;

use crate::buffer::Buffer;
use crate::errors::{Error, Kind, Result};
use crate::isa::{FieldKind, Ins, Op, Typecode};
use crate::module::{validate, BufferDecl, Module, StateDecl};

/// Call depth limit, matching the Python machine's `MAX_DEPTH`.
pub const MAX_DEPTH: usize = 256;

const MASK64: u64 = u64::MAX;
const BYTE_COUNT_MASK: u64 = 0x4000_0000;
const NEW_CLASS_TAG: u64 = 0xFFFF_FFFF;
const IS_REFERENCED: u64 = 1 << 4;

/// FNV-1a: the string table is small and looked up once per C string.
struct FnvHasher(u64);

impl Default for FnvHasher {
    fn default() -> Self {
        FnvHasher(0xcbf2_9ce4_8422_2325)
    }
}

impl Hasher for FnvHasher {
    fn finish(&self) -> u64 {
        self.0
    }

    fn write(&mut self, bytes: &[u8]) {
        for byte in bytes {
            self.0 ^= *byte as u64;
            self.0 = self.0.wrapping_mul(0x0000_0100_0000_01b3);
        }
    }
}

type FnvBuild = BuildHasherDefault<FnvHasher>;

/// What a reference tag points at: an object, or a class.
#[derive(Debug, Clone, Copy)]
struct RefEntry {
    object: i64,
    class_id: i64,
}

impl Default for RefEntry {
    fn default() -> Self {
        RefEntry {
            object: -1,
            class_id: -1,
        }
    }
}

/// One saved caller.
#[derive(Debug, Clone, Copy)]
struct Frame {
    func: usize,
    ip: usize,
    dst: u8,
    /// Byte-count stack depth the callee must hand back on `RET`.
    nb_depth: usize,
}

/// A state slot as the Python machine reports it.
pub struct StateValue<'a> {
    pub raw: u64,
    pub text: Option<&'a str>,
}

/// A Detree machine for one module, reused basket after basket.
pub struct Machine {
    pub module: Module,
    pub buffers: Vec<Buffer>,
    pub states: Vec<u64>,
    strings: Vec<String>,
    str_ids: HashMap<String, u64, FnvBuild>,
    refs: HashMap<u64, RefEntry, FnvBuild>,

    data: *const u8,
    data_len: usize,
    offsets: *const i64,
    n_offsets: usize,
    cursor: usize,
    cursor_offset: i64,
    event: usize,
    entry_end: usize,

    func_index: usize,
    ip: usize,
    halted: bool,
    /// End positions of the open `FNB_BEGIN` regions, innermost last.
    nb_stack: Vec<u64>,

    reg_pool: Vec<u64>,
    max_regs: usize,
    reg_count: usize,
    depth: usize,
    code_off: usize,
    code_len: usize,
    frames: Vec<Frame>,
}

impl Machine {
    /// Decode and validate a module.
    pub fn new(raw: &[u8]) -> Result<Machine> {
        let module = Module::from_bytes(raw)?;
        validate(&module)?;

        let max_regs = module
            .funcs
            .iter()
            .map(|func| func.reg_count as usize)
            .max()
            .unwrap_or(1)
            .max(1);
        let mut machine = Machine {
            buffers: Vec::new(),
            states: Vec::new(),
            strings: Vec::new(),
            str_ids: HashMap::with_hasher(FnvBuild::default()),
            refs: HashMap::with_hasher(FnvBuild::default()),
            data: std::ptr::null(),
            data_len: 0,
            offsets: std::ptr::null(),
            n_offsets: 0,
            cursor: 0,
            cursor_offset: 0,
            event: 0,
            entry_end: 0,
            func_index: module.entry as usize,
            ip: 0,
            halted: true,
            nb_stack: Vec::new(),
            reg_pool: vec![0; (MAX_DEPTH + 1) * max_regs],
            max_regs,
            reg_count: 0,
            depth: 0,
            code_off: 0,
            code_len: 0,
            frames: Vec::new(),
            module,
        };
        machine.reset();
        Ok(machine)
    }

    /// Return to the state just after construction: nothing read yet.
    ///
    /// Fresh storage is allocated rather than the old buffers cleared, because
    /// a caller may still hold a view of a previous run's bytes.
    pub fn reset(&mut self) {
        self.data = std::ptr::null();
        self.data_len = 0;
        self.offsets = std::ptr::null();
        self.n_offsets = 0;
        self.cursor = 0;
        self.cursor_offset = 0;
        self.event = 0;
        self.entry_end = 0;
        self.frames.clear();
        self.nb_stack.clear();
        self.depth = 0;
        self.func_index = self.module.entry as usize;
        self.ip = 0;
        self.halted = true;

        let entry = &self.module.funcs[self.module.entry as usize];
        self.reg_count = entry.reg_count as usize;
        self.code_off = entry.code_off as usize;
        self.code_len = entry.code_len as usize;

        self.buffers.clear();
        for decl in &self.module.buffers {
            self.buffers
                .push(Buffer::new(decl.clone()).expect("dtype validated"));
        }

        self.states = self
            .module
            .states
            .iter()
            .map(|decl| decl.init as u64)
            .collect();

        self.strings.clear();
        self.str_ids.clear();
        let constants: Vec<String> = self.module.strings.clone();
        for text in constants {
            self.intern(&text);
        }
        self.refs.clear();
    }

    /// Read every event of one basket.
    pub fn run(&mut self, data: &[u8], offsets: &[i64], cursor_offset: usize) -> Result<()> {
        self.reset();
        if offsets.len() < 2 {
            return Err(Error::plain(
                Kind::ReadLengthMismatch,
                "a basket needs at least two entry offsets",
            ));
        }

        self.data = data.as_ptr();
        self.data_len = data.len();
        self.offsets = offsets.as_ptr();
        self.n_offsets = offsets.len();
        self.cursor_offset = cursor_offset as i64;
        self.cursor = 0;

        let result = self.run_events();
        self.data = std::ptr::null();
        self.data_len = 0;
        self.offsets = std::ptr::null();
        self.n_offsets = 0;
        result
    }

    fn run_events(&mut self) -> Result<()> {
        let entry_index = self.module.entry as usize;
        // The entry function cannot change between events, so its window and
        // register count are read once instead of once per event.
        let entry = &self.module.funcs[entry_index];
        let reg_count = entry.reg_count as usize;
        let code_off = entry.code_off as usize;
        let code_len = entry.code_len as usize;
        for event in 0..self.n_offsets - 1 {
            self.event = event;
            // SAFETY: the offsets outlive `run` and `event + 1 < n_offsets`.
            self.entry_end = unsafe { *self.offsets.add(event + 1) } as usize;
            self.frames.clear();
            self.nb_stack.clear();
            self.depth = 0;
            self.func_index = entry_index;
            self.reg_count = reg_count;
            self.code_off = code_off;
            self.code_len = code_len;
            self.ip = 0;
            self.halted = false;
            let base = self.depth * self.max_regs;
            for slot in &mut self.reg_pool[base..base + reg_count] {
                *slot = 0;
            }
            self.loop_until_halt()?;
            if self.cursor != self.entry_end {
                let entry = &self.module.funcs[entry_index];
                return Err(Error::at(
                    Kind::ReadLengthMismatch,
                    format!(
                        "event {event}: expected {} bytes, read {}",
                        self.entry_end, self.cursor
                    ),
                    entry.name.clone(),
                    -1,
                    self.cursor,
                ));
            }
        }
        Ok(())
    }

    // -- public accessors --------------------------------------------------
    /// The declared state at `index`.
    pub fn state_decl(&self, index: usize) -> &StateDecl {
        &self.module.states[index]
    }

    /// The current value of the state at `index`.
    pub fn state_value(&self, index: usize) -> StateValue<'_> {
        let raw = self.states[index];
        StateValue {
            raw,
            text: if self.module.states[index].is_string {
                self.string_of(raw)
            } else {
                None
            },
        }
    }

    // -- machinery ---------------------------------------------------------
    #[inline]
    fn loop_until_halt(&mut self) -> Result<()> {
        while !self.halted {
            if self.ip >= self.code_len {
                let func = &self.module.funcs[self.func_index];
                return Err(Error::at(
                    Kind::Other,
                    format!("{}: execution ran past the end of the function", func.name),
                    func.name.clone(),
                    self.ip as i64,
                    self.cursor,
                ));
            }
            // SAFETY: `ip < code_len` and the validator bounds the slice.
            let ins = unsafe { *self.module.code.get_unchecked(self.code_off + self.ip) };
            self.ip += 1;
            self.step(ins)?;
        }
        Ok(())
    }

    fn err(&self, kind: Kind, message: impl Into<String>) -> Error {
        let func = &self.module.funcs[self.func_index];
        Error::at(
            kind,
            message,
            func.name.clone(),
            self.ip as i64 - 1,
            self.cursor,
        )
    }

    fn need(&self, nbytes: usize) -> Result<()> {
        if nbytes > self.data_len || self.cursor > self.data_len - nbytes {
            return Err(self.err(
                Kind::StreamUnderflow,
                format!(
                    "need {nbytes} bytes, {} left",
                    self.data_len.saturating_sub(self.cursor)
                ),
            ));
        }
        Ok(())
    }

    /// Fail when a return hands back a different byte-count depth.
    fn require_nb_depth(&self, expected: usize) -> Result<()> {
        if self.nb_stack.len() != expected {
            return Err(self.err(
                Kind::Other,
                format!(
                    "return with byte-count depth {}, expected {expected}",
                    self.nb_stack.len()
                ),
            ));
        }
        Ok(())
    }

    fn read_bits(&mut self, size: usize) -> Result<u64> {
        self.need(size)?;
        // SAFETY: `need` checked the range; the pointer is valid for the run.
        let raw = unsafe { slice::from_raw_parts(self.data.add(self.cursor), size) };
        let value = match size {
            1 => raw[0] as u64,
            2 => u16::from_be_bytes([raw[0], raw[1]]) as u64,
            4 => u32::from_be_bytes([raw[0], raw[1], raw[2], raw[3]]) as u64,
            8 => u64::from_be_bytes([
                raw[0], raw[1], raw[2], raw[3], raw[4], raw[5], raw[6], raw[7],
            ]),
            _ => raw
                .iter()
                .fold(0u64, |value, byte| (value << 8) | *byte as u64),
        };
        self.cursor += size;
        Ok(value)
    }

    fn read_register(&mut self, typecode: Typecode) -> Result<u64> {
        let raw = self.read_bits(typecode.size())?;
        Ok(match typecode {
            Typecode::Int8 => sign_extend(raw, 8),
            Typecode::Int16 => sign_extend(raw, 16),
            Typecode::Int32 => sign_extend(raw, 32),
            _ => raw,
        })
    }

    #[inline]
    fn reg_base(&self) -> usize {
        self.depth * self.max_regs
    }

    #[inline]
    fn reg(&self, index: u8) -> u64 {
        // SAFETY: validation bounds every register operand by `reg_count`, and
        // every call-depth window is `max_regs` wide.
        unsafe {
            *self
                .reg_pool
                .get_unchecked(self.reg_base() + index as usize)
        }
    }

    #[inline]
    fn set_reg(&mut self, index: u8, value: u64) {
        let slot = self.reg_base() + index as usize;
        // SAFETY: same invariant as `reg`.
        unsafe { *self.reg_pool.get_unchecked_mut(slot) = value };
    }

    #[inline]
    fn buffer(&self, index: usize) -> &Buffer {
        // SAFETY: validation bounds every buffer operand by the buffer count.
        unsafe { self.buffers.get_unchecked(index) }
    }

    #[inline]
    fn buffer_mut(&mut self, index: usize) -> &mut Buffer {
        // SAFETY: same invariant as `buffer`.
        unsafe { self.buffers.get_unchecked_mut(index) }
    }

    #[inline]
    fn state(&self, index: usize) -> u64 {
        // SAFETY: validation bounds every state operand by the state count.
        unsafe { *self.states.get_unchecked(index) }
    }

    #[inline]
    fn set_state(&mut self, index: usize, value: u64) {
        // SAFETY: same invariant as `state`.
        unsafe { *self.states.get_unchecked_mut(index) = value };
    }

    fn message_of(&self, ident: i64) -> String {
        if ident >= 0 && (ident as usize) < self.module.strings.len() {
            self.module.strings[ident as usize].clone()
        } else {
            format!("<message {ident}>")
        }
    }

    fn intern(&mut self, text: &str) -> u64 {
        if let Some(id) = self.str_ids.get(text) {
            return *id;
        }
        let id = self.strings.len() as u64;
        self.strings.push(text.to_owned());
        self.str_ids.insert(text.to_owned(), id);
        id
    }

    fn string_of(&self, ident: u64) -> Option<&str> {
        if ident == MASK64 {
            return None;
        }
        self.strings.get(ident as usize).map(String::as_str)
    }

    fn skip_cstr(&mut self) -> Result<()> {
        let start = self.cursor;
        let data_len = self.data_len;
        // SAFETY: the pointer is set only for the duration of `run`; the slice
        // is used only while `run` holds it.
        let data = unsafe { slice::from_raw_parts(self.data, data_len) };
        while self.cursor < data_len && data[self.cursor] != 0 {
            self.cursor += 1;
        }
        if self.cursor >= data_len {
            self.cursor = start;
            return Err(self.err(Kind::StreamUnderflow, "unterminated string"));
        }
        self.cursor += 1;
        Ok(())
    }

    #[inline(always)]
    fn step(&mut self, ins: Ins) -> Result<()> {
        match ins.op {
            // -- control and constants --------------------------------------
            Op::Halt => self.halted = true,
            Op::Nop | Op::Line | Op::DebugPrint => {}
            Op::BufReserve => {
                // A hint, not an observable effect: ignore a size no real basket
                // can justify instead of inviting an allocation failure.
                let extra = self.reg(ins.ra);
                if extra <= (1 << 24) {
                    self.buffer_mut(ins.imm1 as usize)
                        .reserve_more(extra as usize);
                }
            }
            Op::Const | Op::ConstF => self.set_reg(ins.rd, ins.imm1 as u64),
            Op::Mov => self.set_reg(ins.rd, self.reg(ins.ra)),
            Op::Zext8 => self.set_reg(ins.rd, self.reg(ins.ra) & 0xFF),
            Op::Zext16 => self.set_reg(ins.rd, self.reg(ins.ra) & 0xFFFF),
            Op::Zext32 => self.set_reg(ins.rd, self.reg(ins.ra) & 0xFFFF_FFFF),
            Op::Sext8 => self.set_reg(ins.rd, sign_extend(self.reg(ins.ra), 8)),
            Op::Sext16 => self.set_reg(ins.rd, sign_extend(self.reg(ins.ra), 16)),
            Op::Sext32 => self.set_reg(ins.rd, sign_extend(self.reg(ins.ra), 32)),

            // -- integer arithmetic -----------------------------------------
            Op::Add => {
                let value = self.reg(ins.ra).wrapping_add(self.reg(ins.rb));
                self.set_reg(ins.rd, value);
            }
            Op::Sub => {
                let value = self.reg(ins.ra).wrapping_sub(self.reg(ins.rb));
                self.set_reg(ins.rd, value);
            }
            Op::Mul => {
                let value = self.reg(ins.ra).wrapping_mul(self.reg(ins.rb));
                self.set_reg(ins.rd, value);
            }
            Op::DivS => {
                let a = self.reg(ins.ra) as i64;
                let b = self.reg(ins.rb) as i64;
                self.set_reg(ins.rd, trunc_div(a, b) as u64);
            }
            Op::DivU => {
                let b = self.reg(ins.rb);
                let value = if b == 0 { 0 } else { self.reg(ins.ra) / b };
                self.set_reg(ins.rd, value);
            }
            Op::ModS => {
                let a = self.reg(ins.ra) as i64;
                let b = self.reg(ins.rb) as i64;
                let value = if b == 0 {
                    0
                } else {
                    (a.wrapping_sub(trunc_div(a, b).wrapping_mul(b))) as u64
                };
                self.set_reg(ins.rd, value);
            }
            Op::ModU => {
                let b = self.reg(ins.rb);
                let value = if b == 0 { 0 } else { self.reg(ins.ra) % b };
                self.set_reg(ins.rd, value);
            }
            Op::Neg => self.set_reg(ins.rd, self.reg(ins.ra).wrapping_neg()),
            Op::Shl => {
                let shift = (self.reg(ins.rb) & 63) as u32;
                let value = self.reg(ins.ra).wrapping_shl(shift);
                self.set_reg(ins.rd, value);
            }
            Op::ShrS => {
                let shift = (self.reg(ins.rb) & 63) as u32;
                let value = ((self.reg(ins.ra) as i64) >> shift) as u64;
                self.set_reg(ins.rd, value);
            }
            Op::ShrU => {
                let shift = (self.reg(ins.rb) & 63) as u32;
                let value = self.reg(ins.ra).wrapping_shr(shift);
                self.set_reg(ins.rd, value);
            }
            Op::And => self.set_reg(ins.rd, self.reg(ins.ra) & self.reg(ins.rb)),
            Op::Or => self.set_reg(ins.rd, self.reg(ins.ra) | self.reg(ins.rb)),
            Op::Xor => self.set_reg(ins.rd, self.reg(ins.ra) ^ self.reg(ins.rb)),
            Op::Not => self.set_reg(ins.rd, !self.reg(ins.ra)),
            Op::NotB => {
                let value = if self.reg(ins.ra) == 0 { 1 } else { 0 };
                self.set_reg(ins.rd, value);
            }

            // -- comparison ---------------------------------------------------
            Op::CmpEq => self.set_reg(ins.rd, (self.reg(ins.ra) == self.reg(ins.rb)) as u64),
            Op::CmpNe => self.set_reg(ins.rd, (self.reg(ins.ra) != self.reg(ins.rb)) as u64),
            Op::CmpLtS => {
                let value = ((self.reg(ins.ra) as i64) < (self.reg(ins.rb) as i64)) as u64;
                self.set_reg(ins.rd, value);
            }
            Op::CmpLeS => {
                let value = ((self.reg(ins.ra) as i64) <= (self.reg(ins.rb) as i64)) as u64;
                self.set_reg(ins.rd, value);
            }
            Op::CmpGtS => {
                let value = ((self.reg(ins.ra) as i64) > (self.reg(ins.rb) as i64)) as u64;
                self.set_reg(ins.rd, value);
            }
            Op::CmpGeS => {
                let value = ((self.reg(ins.ra) as i64) >= (self.reg(ins.rb) as i64)) as u64;
                self.set_reg(ins.rd, value);
            }
            Op::CmpLtU => self.set_reg(ins.rd, (self.reg(ins.ra) < self.reg(ins.rb)) as u64),
            Op::CmpLeU => self.set_reg(ins.rd, (self.reg(ins.ra) <= self.reg(ins.rb)) as u64),
            Op::CmpGtU => self.set_reg(ins.rd, (self.reg(ins.ra) > self.reg(ins.rb)) as u64),
            Op::CmpGeU => self.set_reg(ins.rd, (self.reg(ins.ra) >= self.reg(ins.rb)) as u64),

            // -- control flow -------------------------------------------------
            Op::Jmp => self.ip = ins.imm1 as usize,
            Op::Jz => {
                if self.reg(ins.ra) == 0 {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::Jnz => {
                if self.reg(ins.ra) != 0 {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrEq => {
                if self.reg(ins.ra) == self.reg(ins.rb) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrNe => {
                if self.reg(ins.ra) != self.reg(ins.rb) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrLtS => {
                if (self.reg(ins.ra) as i64) < (self.reg(ins.rb) as i64) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrLeS => {
                if (self.reg(ins.ra) as i64) <= (self.reg(ins.rb) as i64) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrGtS => {
                if (self.reg(ins.ra) as i64) > (self.reg(ins.rb) as i64) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrGeS => {
                if (self.reg(ins.ra) as i64) >= (self.reg(ins.rb) as i64) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrLtU => {
                if self.reg(ins.ra) < self.reg(ins.rb) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrLeU => {
                if self.reg(ins.ra) <= self.reg(ins.rb) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrGtU => {
                if self.reg(ins.ra) > self.reg(ins.rb) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::BrGeU => {
                if self.reg(ins.ra) >= self.reg(ins.rb) {
                    self.ip = ins.imm1 as usize;
                }
            }
            Op::Call => {
                if self.frames.len() >= MAX_DEPTH {
                    return Err(
                        self.err(Kind::StackOverflow, format!("call depth above {MAX_DEPTH}"))
                    );
                }
                let callee_id = ins.imm1 as usize;
                let argument = if (ins.ra as usize) < self.reg_count {
                    self.reg(ins.ra)
                } else {
                    0
                };
                self.frames.push(Frame {
                    func: self.func_index,
                    ip: self.ip,
                    dst: ins.rd,
                    nb_depth: self.nb_stack.len(),
                });
                self.depth += 1;
                let callee = &self.module.funcs[callee_id];
                self.func_index = callee_id;
                self.ip = 0;
                self.reg_count = callee.reg_count as usize;
                self.code_off = callee.code_off as usize;
                self.code_len = callee.code_len as usize;
                let base = self.reg_base();
                for slot in &mut self.reg_pool[base..base + self.reg_count] {
                    *slot = 0;
                }
                if callee.arity != 0 && self.reg_count != 0 {
                    self.set_reg(0, argument);
                }
            }
            Op::Ret => {
                let value = if (ins.ra as usize) < self.reg_count {
                    self.reg(ins.ra)
                } else {
                    0
                };
                if let Some(frame) = self.frames.pop() {
                    self.require_nb_depth(frame.nb_depth)?;
                    self.depth -= 1;
                    self.func_index = frame.func;
                    self.ip = frame.ip;
                    let caller = &self.module.funcs[self.func_index];
                    self.reg_count = caller.reg_count as usize;
                    self.code_off = caller.code_off as usize;
                    self.code_len = caller.code_len as usize;
                    self.set_reg(frame.dst, value);
                } else {
                    self.require_nb_depth(0)?;
                    self.halted = true;
                }
            }

            // -- stream access -------------------------------------------------
            Op::Sread => {
                let typecode = typecode(ins.imm1);
                let value = self.read_register(typecode)?;
                self.set_reg(ins.rd, value);
            }
            Op::SreadFnb => {
                let value = self.read_bits(4)?;
                if value & BYTE_COUNT_MASK == 0 {
                    return Err(self.err(
                        Kind::InvalidByteCount,
                        format!("fNBytes={value} lacks kByteCountMask"),
                    ));
                }
                self.set_reg(ins.rd, value & !BYTE_COUNT_MASK);
            }
            Op::SreadVersion => {
                let value = sign_extend(self.read_bits(2)?, 16);
                self.set_reg(ins.rd, value);
            }
            Op::SreadCstr => {
                let start = self.cursor;
                self.skip_cstr()?;
                let length = self.cursor - 1 - start;
                // SAFETY: `skip_cstr` found the terminator inside the run's data.
                let bytes = unsafe { slice::from_raw_parts(self.data.add(start), length) };
                let text = String::from_utf8_lossy(bytes);
                let id = self.intern(&text);
                self.set_reg(ins.rd, id);
            }
            Op::Skip => {
                let nbytes = ins.imm1 as usize;
                self.need(nbytes)?;
                self.cursor += nbytes;
            }
            Op::SkipR => {
                let nbytes = self.reg(ins.ra) as usize;
                self.need(nbytes)?;
                self.cursor += nbytes;
            }
            Op::SkipVersion => {
                self.need(2)?;
                self.cursor += 2;
            }
            Op::SkipFnb => {
                let value = self.read_bits(4)?;
                if value & BYTE_COUNT_MASK == 0 {
                    return Err(self.err(
                        Kind::InvalidByteCount,
                        format!("fNBytes={value} lacks kByteCountMask"),
                    ));
                }
            }
            Op::FnbBegin => {
                let value = self.read_bits(4)?;
                if value & BYTE_COUNT_MASK == 0 {
                    return Err(self.err(
                        Kind::InvalidByteCount,
                        format!("fNBytes={value} lacks kByteCountMask"),
                    ));
                }
                if self.nb_stack.len() >= MAX_DEPTH {
                    return Err(self.err(
                        Kind::StackOverflow,
                        format!("byte-count depth above {MAX_DEPTH}"),
                    ));
                }
                let end = self.cursor as u64 + (value & !BYTE_COUNT_MASK);
                self.nb_stack.push(end);
            }
            Op::FnbEnd => {
                let Some(expected) = self.nb_stack.pop() else {
                    return Err(self.err(Kind::Other, "FNB_END with no open byte-count frame"));
                };
                if self.cursor as u64 != expected {
                    return Err(self.err(
                        Kind::AssertionFailed,
                        format!(
                            "{} (expected {expected}, got {})",
                            self.message_of(ins.imm1),
                            self.cursor
                        ),
                    ));
                }
            }
            Op::SkipCstr => self.skip_cstr()?,
            Op::SkipTobject => {
                self.need(6)?;
                self.cursor += 6;
                let bits = self.read_bits(4)?;
                if bits & IS_REFERENCED != 0 {
                    self.need(2)?;
                    self.cursor += 2;
                }
            }
            Op::SkipObjhdr => {
                let value = self.read_bits(4)?;
                if value & BYTE_COUNT_MASK == 0 {
                    return Err(self.err(
                        Kind::InvalidByteCount,
                        format!("fNBytes={value} lacks kByteCountMask"),
                    ));
                }
                let tag = self.read_bits(4)?;
                if tag == NEW_CLASS_TAG {
                    self.skip_cstr()?;
                }
            }
            Op::Cursor => self.set_reg(ins.rd, self.cursor as u64),
            Op::SetCursor => {
                let value = self.reg(ins.ra) as usize;
                if value > self.data_len {
                    return Err(self.err(
                        Kind::StreamUnderflow,
                        format!("cursor {value} outside the basket"),
                    ));
                }
                self.cursor = value;
            }
            Op::Entries => self.set_reg(ins.rd, (self.n_offsets - 1) as u64),
            Op::InitOffset => self.set_reg(ins.rd, self.cursor_offset as u64),
            Op::DataSize => {
                // SAFETY: the event loop keeps `event + 1 < n_offsets`.
                let value =
                    unsafe { *self.offsets.add(self.event + 1) - *self.offsets.add(self.event) };
                self.set_reg(ins.rd, value as u64);
            }
            Op::OffsetAt => {
                let index = self.reg(ins.ra) as usize;
                if index >= self.n_offsets {
                    return Err(self.err(
                        Kind::ReadLengthMismatch,
                        format!("entry offset {index} does not exist"),
                    ));
                }
                // SAFETY: the index was checked against the live array.
                self.set_reg(ins.rd, unsafe { *self.offsets.add(index) } as u64);
            }
            Op::OffsetSeek => {
                let value = self.reg(ins.ra) as i64;
                // SAFETY: the offsets outlive `run`.
                let offsets = unsafe { slice::from_raw_parts(self.offsets, self.n_offsets) };
                let index = offsets.partition_point(|offset| *offset <= value);
                if index >= self.n_offsets {
                    return Err(self.err(
                        Kind::ReadLengthMismatch,
                        format!("no entry offset after {value}"),
                    ));
                }
                self.set_reg(ins.rd, offsets[index] as u64);
            }
            Op::EventEnd => {
                // SAFETY: the event loop keeps `event + 1 < n_offsets`.
                self.set_reg(ins.rd, unsafe { *self.offsets.add(self.event + 1) } as u64);
            }

            // -- coarse-grained reads ------------------------------------------
            Op::ReadBulk => {
                let count = self.reg(ins.ra) as i64;
                if count <= 0 {
                    return Ok(());
                }
                let typecode = typecode(ins.imm2);
                let size = typecode.size();
                let count = count as usize;
                let span = count
                    .checked_mul(size)
                    .ok_or_else(|| self.err(Kind::StreamUnderflow, "bulk count is too large"))?;
                self.need(span)?;
                // SAFETY: `need` checked the range; the slice does not borrow self.
                let raw = unsafe { slice::from_raw_parts(self.data.add(self.cursor), span) };
                self.buffer_mut(ins.imm1 as usize)
                    .append_stream(raw, count, typecode);
                self.cursor += span;
            }
            Op::ReadBulkUntil => {
                let end = self.reg(ins.ra) as usize;
                let typecode = typecode(ins.imm2);
                let size = typecode.size();
                let cursor = self.cursor;
                if end < cursor {
                    return Err(self.err(
                        Kind::ReadLengthMismatch,
                        format!("bulk end {end} is behind the cursor {cursor}"),
                    ));
                }
                let span = end - cursor;
                if size == 0 || span % size != 0 {
                    return Err(self.err(
                        Kind::ReadLengthMismatch,
                        format!("{span} bytes are not a whole number of {size}-byte elements"),
                    ));
                }
                if span != 0 {
                    self.need(span)?;
                    let data = self.data;
                    // SAFETY: `need` checked the range; the slice does not borrow self.
                    let raw = unsafe { slice::from_raw_parts(data.add(cursor), span) };
                    self.buffer_mut(ins.imm1 as usize)
                        .append_stream(raw, span / size, typecode);
                    self.cursor = end;
                }
            }
            Op::ReadString => {
                let mut size = self.read_bits(1)? as usize;
                if size == 255 {
                    size = self.read_bits(4)? as usize;
                }
                self.need(size)?;
                let chars = ins.imm1 as usize;
                let offsets = ins.imm2 as usize;
                let data = self.data;
                let cursor = self.cursor;
                // SAFETY: `need` checked the range; the slice does not borrow self.
                let raw = unsafe { slice::from_raw_parts(data.add(cursor), size) };
                self.buffer_mut(chars)
                    .append_stream(raw, size, Typecode::Uint8);
                self.cursor += size;
                let end = if ins.rb == 0 {
                    self.buffer(chars).count()
                } else {
                    self.buffer(offsets).last_bits().wrapping_add(size as u64)
                };
                self.buffer_mut(offsets).append_bits(end);
            }
            Op::ReadStrided => {
                let count = self.reg(ins.ra) as i64;
                let stride = self.reg(ins.rb) as i64;
                if count <= 0 {
                    return Ok(());
                }
                let typecode = typecode(ins.imm2);
                let size = typecode.size();
                if stride < size as i64 {
                    return Err(self.err(
                        Kind::StreamUnderflow,
                        format!("stride {stride} below element size {size}"),
                    ));
                }
                let stride = stride as usize;
                let count = count as usize;
                let span = (count - 1)
                    .checked_mul(stride)
                    .and_then(|value| value.checked_add(size))
                    .ok_or_else(|| self.err(Kind::StreamUnderflow, "strided span is too large"))?;
                self.need(span)?;
                let data = self.data;
                let base = self.cursor;
                let buffer = self.buffer_mut(ins.imm1 as usize);
                for index in 0..count {
                    let start = base + index * stride;
                    // SAFETY: `need` covered the last element.
                    let raw = unsafe { slice::from_raw_parts(data.add(start), size) };
                    buffer.append_stream(raw, 1, typecode);
                }
                self.cursor += span;
            }
            Op::ReadAppend => {
                let typecode = typecode(ins.imm2);
                let value = self.read_register(typecode)?;
                self.buffer_mut(ins.imm1 as usize).append_bits(value);
            }
            Op::ReadFields => {
                let count = self.reg(ins.ra) as i64;
                let layout = ins.imm1 as usize;
                let field_count = self.module.layouts[layout].fields.len();
                // SAFETY: layouts are immutable for the machine's lifetime; the
                // raw pointer exists only to keep `self` mutably borrowable for
                // the stream reads inside the loop.
                let fields = self.module.layouts[layout].fields.as_ptr();
                for _ in 0..count.max(0) {
                    for field_index in 0..field_count {
                        let field = unsafe { *fields.add(field_index) };
                        match field.kind {
                            FieldKind::Skip => {
                                let nbytes = field.count as usize;
                                self.need(nbytes)?;
                                self.cursor += nbytes;
                            }
                            FieldKind::Scalar => {
                                let value = self.read_register(field.typecode)?;
                                self.buffer_mut(field.buffer_id as usize).append_bits(value);
                            }
                            FieldKind::ArrayFixed => {
                                let size = field.typecode.size();
                                let elements = field.count as usize;
                                let span = elements.checked_mul(size).ok_or_else(|| {
                                    self.err(Kind::StreamUnderflow, "layout array is too large")
                                })?;
                                self.need(span)?;
                                // SAFETY: `need` checked the range.
                                let raw = unsafe {
                                    slice::from_raw_parts(self.data.add(self.cursor), span)
                                };
                                self.buffer_mut(field.buffer_id as usize).append_stream(
                                    raw,
                                    elements,
                                    field.typecode,
                                );
                                self.cursor += span;
                            }
                            FieldKind::TobjectSkip => {
                                return Err(self.err(
                                    Kind::UnsupportedFeature,
                                    "layout field kind TOBJECT_SKIP",
                                ));
                            }
                        }
                    }
                }
            }

            // -- buffer access ---------------------------------------------------
            Op::BufAppend => {
                let value = self.reg(ins.ra);
                self.buffer_mut(ins.imm1 as usize).append_bits(value);
            }
            Op::BufAppendConst => {
                self.buffer_mut(ins.imm1 as usize)
                    .append_bits(ins.imm2 as u64);
            }
            Op::BufAppendOffset => {
                let delta = self.reg(ins.ra);
                let buffer = self.buffer_mut(ins.imm1 as usize);
                let value = buffer.last_bits().wrapping_add(delta);
                buffer.append_bits(value);
            }
            Op::BufAppendLen => {
                let count = self.buffer(ins.imm2 as usize).count();
                self.buffer_mut(ins.imm1 as usize).append_bits(count);
            }
            Op::BufLast => {
                let value = self.buffer(ins.imm1 as usize).last_bits();
                self.set_reg(ins.rd, value);
            }
            Op::BufLen => {
                let value = self.buffer(ins.imm1 as usize).count();
                self.set_reg(ins.rd, value);
            }

            // -- program state ---------------------------------------------------
            Op::StateGet => {
                let value = self.state(ins.imm1 as usize);
                self.set_reg(ins.rd, value);
            }
            Op::StateSet => {
                self.set_state(ins.imm1 as usize, self.reg(ins.ra));
            }
            Op::StateIadd => {
                let delta = ins.imm2 as u64;
                let value = self.state(ins.imm1 as usize).wrapping_add(delta);
                self.set_state(ins.imm1 as usize, value);
            }

            // -- object references ------------------------------------------------
            Op::RefHas => {
                let tag = self.reg(ins.ra);
                self.set_reg(ins.rd, self.refs.contains_key(&tag) as u64);
            }
            Op::RefGetObj => {
                let tag = self.reg(ins.ra);
                let value = self.refs.get(&tag).map(|entry| entry.object).unwrap_or(0);
                self.set_reg(ins.rd, value as u64);
            }
            Op::RefGetClass => {
                let tag = self.reg(ins.ra);
                let value = self
                    .refs
                    .get(&tag)
                    .map(|entry| entry.class_id)
                    .unwrap_or(-1);
                self.set_reg(ins.rd, value as u64);
            }
            Op::RefSetObj => {
                let tag = self.reg(ins.ra);
                self.refs.insert(
                    tag,
                    RefEntry {
                        object: self.reg(ins.rb) as i64,
                        ..RefEntry::default()
                    },
                );
            }
            Op::RefSetClass => {
                let tag = self.reg(ins.ra);
                self.refs.insert(
                    tag,
                    RefEntry {
                        class_id: self.reg(ins.rb) as i64,
                        ..RefEntry::default()
                    },
                );
            }
            Op::RefCount => self.set_reg(ins.rd, self.refs.len() as u64),

            // -- strings ---------------------------------------------------------
            Op::StrConst => self.set_reg(ins.rd, ins.imm1 as u64),
            Op::StrEq => {
                let left = self.string_of(self.reg(ins.ra));
                let right = self.string_of(self.reg(ins.rb));
                let equal = matches!((left, right), (Some(a), Some(b)) if a == b);
                self.set_reg(ins.rd, equal as u64);
            }
            Op::StrStore => {
                self.set_state(ins.imm1 as usize, self.reg(ins.ra));
            }
            Op::StrLoad => {
                let value = self.state(ins.imm1 as usize);
                self.set_reg(ins.rd, value);
            }

            // -- verification ------------------------------------------------------
            Op::Assert => {
                if self.reg(ins.ra) == 0 {
                    return Err(self.err(Kind::AssertionFailed, self.message_of(ins.imm1)));
                }
            }
            Op::AssertCursorEq => {
                let expected = self.reg(ins.ra);
                if self.cursor as u64 != expected {
                    let message = format!(
                        "{} (expected {expected}, got {})",
                        self.message_of(ins.imm1),
                        self.cursor
                    );
                    return Err(self.err(Kind::AssertionFailed, message));
                }
            }
            Op::AssertStrEq => {
                let left = self.string_of(self.reg(ins.ra));
                let right = self.string_of(self.reg(ins.rb));
                if !matches!((left, right), (Some(a), Some(b)) if a == b) {
                    let message = format!(
                        "{} (expected '{}', got '{}')",
                        self.message_of(ins.imm1),
                        right.unwrap_or("<none>"),
                        left.unwrap_or("<none>")
                    );
                    return Err(self.err(Kind::AssertionFailed, message));
                }
            }
            Op::Throw => {
                return Err(self.err(Kind::AssertionFailed, self.message_of(ins.imm1)));
            }
        }
        Ok(())
    }
}

#[inline]
fn typecode(value: i64) -> Typecode {
    debug_assert!((1..=10).contains(&value));
    // SAFETY: validation accepts only the ten `#[repr(u8)]` type codes.
    unsafe { std::mem::transmute::<u8, Typecode>(value as u8) }
}

/// Sign-extend the low `bits` bits of `value` to 64 bits.
fn sign_extend(value: u64, bits: u32) -> u64 {
    let shift = 64 - bits;
    ((value << shift) as i64 >> shift) as u64
}

/// Truncating division, defined for every pair (`i64::MIN / -1` included).
fn trunc_div(a: i64, b: i64) -> i64 {
    if b == 0 {
        return 0;
    }
    if a == i64::MIN && b == -1 {
        return i64::MIN;
    }
    a / b
}

/// Keep the declaration type used by `reset` visible to rustdoc.
pub fn declaration_kind(decl: &BufferDecl) -> crate::isa::BufferKind {
    decl.kind
}
