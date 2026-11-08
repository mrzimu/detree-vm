//! ISA constants, the opcode table, and instruction decoding.
//!
//! Values are part of the wire format. `spec/isa.toml` is the authority;
//! `tools/gen_isa.py --check` compares the tables.

/// Bumped when a struct layout or a symbol meaning changes.
pub const C_API_VERSION: u32 = 1;

/// Incompatible changes bump the major version.
pub const ISA_MAJOR: u16 = 0;

/// Additive changes bump the minor version.
pub const ISA_MINOR: u16 = 1;

/// The first four bytes of every module.
pub const MAGIC: [u8; 4] = *b"DETR";

/// Every instruction is 20 bytes.
pub const INSTRUCTION_SIZE: usize = 20;

/// The name this implementation reports through the C ABI.
pub const BACKEND_NAME: &str = "rust";

/// Opcode values. Every variant is one byte on the wire.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Op {
    Halt = 0x00,
    Nop = 0x01,
    Line = 0x02,
    DebugPrint = 0x03,
    Const = 0x04,
    ConstF = 0x05,
    Mov = 0x06,
    Zext32 = 0x07,
    Zext16 = 0x08,
    Zext8 = 0x09,
    Sext32 = 0x0A,
    Sext16 = 0x0B,
    Sext8 = 0x0C,
    Add = 0x10,
    Sub = 0x11,
    Mul = 0x12,
    DivS = 0x13,
    DivU = 0x14,
    ModS = 0x15,
    ModU = 0x16,
    Neg = 0x17,
    Shl = 0x18,
    ShrS = 0x19,
    ShrU = 0x1A,
    And = 0x1B,
    Or = 0x1C,
    Xor = 0x1D,
    Not = 0x1E,
    NotB = 0x1F,
    CmpEq = 0x20,
    CmpNe = 0x21,
    CmpLtS = 0x22,
    CmpLeS = 0x23,
    CmpGtS = 0x24,
    CmpGeS = 0x25,
    CmpLtU = 0x26,
    CmpLeU = 0x27,
    CmpGtU = 0x28,
    CmpGeU = 0x29,
    Jmp = 0x30,
    Jz = 0x31,
    Jnz = 0x32,
    BrEq = 0x33,
    BrNe = 0x34,
    BrLtS = 0x35,
    BrLeS = 0x36,
    BrGtS = 0x37,
    BrGeS = 0x38,
    BrLtU = 0x39,
    BrLeU = 0x3A,
    BrGtU = 0x3B,
    BrGeU = 0x3C,
    Call = 0x3D,
    Ret = 0x3E,
    Sread = 0x40,
    SreadFnb = 0x41,
    SreadVersion = 0x42,
    SreadCstr = 0x43,
    Skip = 0x44,
    SkipR = 0x45,
    SkipFnb = 0x46,
    SkipVersion = 0x47,
    SkipTobject = 0x48,
    SkipObjhdr = 0x49,
    SkipCstr = 0x4A,
    Cursor = 0x4B,
    SetCursor = 0x4C,
    Entries = 0x4D,
    InitOffset = 0x4E,
    DataSize = 0x4F,
    OffsetAt = 0x60,
    OffsetSeek = 0x61,
    EventEnd = 0x62,
    ReadBulk = 0x70,
    ReadStrided = 0x71,
    ReadFields = 0x72,
    ReadAppend = 0x73,
    ReadBulkUntil = 0x74,
    ReadString = 0x75,
    BufAppend = 0x80,
    BufAppendConst = 0x81,
    BufAppendOffset = 0x82,
    BufAppendLen = 0x83,
    BufLast = 0x84,
    BufLen = 0x85,
    BufReserve = 0x86,
    StateGet = 0x90,
    StateSet = 0x91,
    StateIadd = 0x92,
    RefHas = 0xA0,
    RefGetObj = 0xA1,
    RefGetClass = 0xA2,
    RefSetObj = 0xA3,
    RefSetClass = 0xA4,
    RefCount = 0xA5,
    StrConst = 0xB0,
    StrEq = 0xB1,
    StrStore = 0xB2,
    StrLoad = 0xB3,
    Assert = 0xC0,
    AssertCursorEq = 0xC1,
    AssertStrEq = 0xC2,
    Throw = 0xC3,
}

impl Op {
    /// Decode one opcode byte, or `None` when it is not in this ISA version.
    pub fn from_u8(value: u8) -> Option<Op> {
        Some(match value {
            0x00 => Op::Halt,
            0x01 => Op::Nop,
            0x02 => Op::Line,
            0x03 => Op::DebugPrint,
            0x04 => Op::Const,
            0x05 => Op::ConstF,
            0x06 => Op::Mov,
            0x07 => Op::Zext32,
            0x08 => Op::Zext16,
            0x09 => Op::Zext8,
            0x0A => Op::Sext32,
            0x0B => Op::Sext16,
            0x0C => Op::Sext8,
            0x10 => Op::Add,
            0x11 => Op::Sub,
            0x12 => Op::Mul,
            0x13 => Op::DivS,
            0x14 => Op::DivU,
            0x15 => Op::ModS,
            0x16 => Op::ModU,
            0x17 => Op::Neg,
            0x18 => Op::Shl,
            0x19 => Op::ShrS,
            0x1A => Op::ShrU,
            0x1B => Op::And,
            0x1C => Op::Or,
            0x1D => Op::Xor,
            0x1E => Op::Not,
            0x1F => Op::NotB,
            0x20 => Op::CmpEq,
            0x21 => Op::CmpNe,
            0x22 => Op::CmpLtS,
            0x23 => Op::CmpLeS,
            0x24 => Op::CmpGtS,
            0x25 => Op::CmpGeS,
            0x26 => Op::CmpLtU,
            0x27 => Op::CmpLeU,
            0x28 => Op::CmpGtU,
            0x29 => Op::CmpGeU,
            0x30 => Op::Jmp,
            0x31 => Op::Jz,
            0x32 => Op::Jnz,
            0x33 => Op::BrEq,
            0x34 => Op::BrNe,
            0x35 => Op::BrLtS,
            0x36 => Op::BrLeS,
            0x37 => Op::BrGtS,
            0x38 => Op::BrGeS,
            0x39 => Op::BrLtU,
            0x3A => Op::BrLeU,
            0x3B => Op::BrGtU,
            0x3C => Op::BrGeU,
            0x3D => Op::Call,
            0x3E => Op::Ret,
            0x40 => Op::Sread,
            0x41 => Op::SreadFnb,
            0x42 => Op::SreadVersion,
            0x43 => Op::SreadCstr,
            0x44 => Op::Skip,
            0x45 => Op::SkipR,
            0x46 => Op::SkipFnb,
            0x47 => Op::SkipVersion,
            0x48 => Op::SkipTobject,
            0x49 => Op::SkipObjhdr,
            0x4A => Op::SkipCstr,
            0x4B => Op::Cursor,
            0x4C => Op::SetCursor,
            0x4D => Op::Entries,
            0x4E => Op::InitOffset,
            0x4F => Op::DataSize,
            0x60 => Op::OffsetAt,
            0x61 => Op::OffsetSeek,
            0x62 => Op::EventEnd,
            0x70 => Op::ReadBulk,
            0x71 => Op::ReadStrided,
            0x72 => Op::ReadFields,
            0x73 => Op::ReadAppend,
            0x74 => Op::ReadBulkUntil,
            0x75 => Op::ReadString,
            0x80 => Op::BufAppend,
            0x81 => Op::BufAppendConst,
            0x82 => Op::BufAppendOffset,
            0x83 => Op::BufAppendLen,
            0x84 => Op::BufLast,
            0x85 => Op::BufLen,
            0x86 => Op::BufReserve,
            0x90 => Op::StateGet,
            0x91 => Op::StateSet,
            0x92 => Op::StateIadd,
            0xA0 => Op::RefHas,
            0xA1 => Op::RefGetObj,
            0xA2 => Op::RefGetClass,
            0xA3 => Op::RefSetObj,
            0xA4 => Op::RefSetClass,
            0xA5 => Op::RefCount,
            0xB0 => Op::StrConst,
            0xB1 => Op::StrEq,
            0xB2 => Op::StrStore,
            0xB3 => Op::StrLoad,
            0xC0 => Op::Assert,
            0xC1 => Op::AssertCursorEq,
            0xC2 => Op::AssertStrEq,
            0xC3 => Op::Throw,
            _ => return None,
        })
    }

    /// The mnemonic used in diagnostics.
    pub fn name(self) -> &'static str {
        match self {
            Op::Halt => "HALT",
            Op::Nop => "NOP",
            Op::Line => "LINE",
            Op::DebugPrint => "DEBUG_PRINT",
            Op::Const => "CONST",
            Op::ConstF => "CONST_F",
            Op::Mov => "MOV",
            Op::Zext32 => "ZEXT32",
            Op::Zext16 => "ZEXT16",
            Op::Zext8 => "ZEXT8",
            Op::Sext32 => "SEXT32",
            Op::Sext16 => "SEXT16",
            Op::Sext8 => "SEXT8",
            Op::Add => "ADD",
            Op::Sub => "SUB",
            Op::Mul => "MUL",
            Op::DivS => "DIV_S",
            Op::DivU => "DIV_U",
            Op::ModS => "MOD_S",
            Op::ModU => "MOD_U",
            Op::Neg => "NEG",
            Op::Shl => "SHL",
            Op::ShrS => "SHR_S",
            Op::ShrU => "SHR_U",
            Op::And => "AND",
            Op::Or => "OR",
            Op::Xor => "XOR",
            Op::Not => "NOT",
            Op::NotB => "NOT_B",
            Op::CmpEq => "CMP_EQ",
            Op::CmpNe => "CMP_NE",
            Op::CmpLtS => "CMP_LT_S",
            Op::CmpLeS => "CMP_LE_S",
            Op::CmpGtS => "CMP_GT_S",
            Op::CmpGeS => "CMP_GE_S",
            Op::CmpLtU => "CMP_LT_U",
            Op::CmpLeU => "CMP_LE_U",
            Op::CmpGtU => "CMP_GT_U",
            Op::CmpGeU => "CMP_GE_U",
            Op::Jmp => "JMP",
            Op::Jz => "JZ",
            Op::Jnz => "JNZ",
            Op::BrEq => "BR_EQ",
            Op::BrNe => "BR_NE",
            Op::BrLtS => "BR_LT_S",
            Op::BrLeS => "BR_LE_S",
            Op::BrGtS => "BR_GT_S",
            Op::BrGeS => "BR_GE_S",
            Op::BrLtU => "BR_LT_U",
            Op::BrLeU => "BR_LE_U",
            Op::BrGtU => "BR_GT_U",
            Op::BrGeU => "BR_GE_U",
            Op::Call => "CALL",
            Op::Ret => "RET",
            Op::Sread => "SREAD",
            Op::SreadFnb => "SREAD_FNB",
            Op::SreadVersion => "SREAD_VERSION",
            Op::SreadCstr => "SREAD_CSTR",
            Op::Skip => "SKIP",
            Op::SkipR => "SKIP_R",
            Op::SkipFnb => "SKIP_FNB",
            Op::SkipVersion => "SKIP_VERSION",
            Op::SkipTobject => "SKIP_TOBJECT",
            Op::SkipObjhdr => "SKIP_OBJHDR",
            Op::SkipCstr => "SKIP_CSTR",
            Op::Cursor => "CURSOR",
            Op::SetCursor => "SET_CURSOR",
            Op::Entries => "ENTRIES",
            Op::InitOffset => "INIT_OFFSET",
            Op::DataSize => "DATA_SIZE",
            Op::OffsetAt => "OFFSET_AT",
            Op::OffsetSeek => "OFFSET_SEEK",
            Op::EventEnd => "EVENT_END",
            Op::ReadBulk => "READ_BULK",
            Op::ReadStrided => "READ_STRIDED",
            Op::ReadFields => "READ_FIELDS",
            Op::ReadAppend => "READ_APPEND",
            Op::ReadBulkUntil => "READ_BULK_UNTIL",
            Op::ReadString => "READ_STRING",
            Op::BufAppend => "BUF_APPEND",
            Op::BufAppendConst => "BUF_APPEND_CONST",
            Op::BufAppendOffset => "BUF_APPEND_OFFSET",
            Op::BufAppendLen => "BUF_APPEND_LEN",
            Op::BufLast => "BUF_LAST",
            Op::BufLen => "BUF_LEN",
            Op::BufReserve => "BUF_RESERVE",
            Op::StateGet => "STATE_GET",
            Op::StateSet => "STATE_SET",
            Op::StateIadd => "STATE_IADD",
            Op::RefHas => "REF_HAS",
            Op::RefGetObj => "REF_GET_OBJ",
            Op::RefGetClass => "REF_GET_CLASS",
            Op::RefSetObj => "REF_SET_OBJ",
            Op::RefSetClass => "REF_SET_CLASS",
            Op::RefCount => "REF_COUNT",
            Op::StrConst => "STR_CONST",
            Op::StrEq => "STR_EQ",
            Op::StrStore => "STR_STORE",
            Op::StrLoad => "STR_LOAD",
            Op::Assert => "ASSERT",
            Op::AssertCursorEq => "ASSERT_CURSOR_EQ",
            Op::AssertStrEq => "ASSERT_STR_EQ",
            Op::Throw => "THROW",
        }
    }

    /// True when the target comes from `imm1`.
    pub fn is_jump(self) -> bool {
        matches!(
            self,
            Op::Jmp
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
        )
    }

    /// True when `imm1` names a buffer.
    pub fn is_buffer(self) -> bool {
        matches!(
            self,
            Op::BufAppend
                | Op::BufAppendConst
                | Op::BufAppendOffset
                | Op::BufLast
                | Op::BufLen
                | Op::BufReserve
                | Op::ReadAppend
        )
    }

    /// True when `imm1` names a state slot.
    pub fn is_state(self) -> bool {
        matches!(
            self,
            Op::StateGet | Op::StateSet | Op::StateIadd | Op::StrStore | Op::StrLoad
        )
    }

    /// True when `imm1` names a message string.
    pub fn is_message(self) -> bool {
        matches!(
            self,
            Op::Assert | Op::AssertCursorEq | Op::AssertStrEq | Op::Throw | Op::DebugPrint
        )
    }
}

/// Scalar stream element types.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Typecode {
    Uint8 = 0x01,
    Uint16 = 0x02,
    Uint32 = 0x03,
    Uint64 = 0x04,
    Int8 = 0x05,
    Int16 = 0x06,
    Int32 = 0x07,
    Int64 = 0x08,
    Float32 = 0x09,
    Float64 = 0x0A,
}

impl Typecode {
    /// Decode a typecode, or `None` for an unknown value.
    pub fn from_u8(value: u8) -> Option<Typecode> {
        Some(match value {
            0x01 => Typecode::Uint8,
            0x02 => Typecode::Uint16,
            0x03 => Typecode::Uint32,
            0x04 => Typecode::Uint64,
            0x05 => Typecode::Int8,
            0x06 => Typecode::Int16,
            0x07 => Typecode::Int32,
            0x08 => Typecode::Int64,
            0x09 => Typecode::Float32,
            0x0A => Typecode::Float64,
            _ => return None,
        })
    }

    /// Bytes per element.
    pub fn size(self) -> usize {
        match self {
            Typecode::Uint8 | Typecode::Int8 => 1,
            Typecode::Uint16 | Typecode::Int16 => 2,
            Typecode::Uint32 | Typecode::Int32 | Typecode::Float32 => 4,
            Typecode::Uint64 | Typecode::Int64 | Typecode::Float64 => 8,
        }
    }

    /// True for the two floating-point codes.
    pub fn is_float(self) -> bool {
        matches!(self, Typecode::Float32 | Typecode::Float64)
    }

    /// True for the four signed integer codes.
    pub fn is_signed(self) -> bool {
        matches!(
            self,
            Typecode::Int8 | Typecode::Int16 | Typecode::Int32 | Typecode::Int64
        )
    }
}

/// Storage class of a buffer.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum BufferKind {
    Data = 0,
    Offsets = 1,
    Count = 2,
}

impl BufferKind {
    /// Decode a buffer kind, or `None` for an unknown value.
    pub fn from_u8(value: u8) -> Option<BufferKind> {
        Some(match value {
            0 => BufferKind::Data,
            1 => BufferKind::Offsets,
            2 => BufferKind::Count,
            _ => return None,
        })
    }
}

/// How one field of a flat layout is read.
#[repr(u8)]
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum FieldKind {
    Scalar = 0,
    Skip = 1,
    ArrayFixed = 2,
    TobjectSkip = 3,
}

impl FieldKind {
    /// Decode a layout field kind, or `None` for an unknown value.
    pub fn from_u8(value: u8) -> Option<FieldKind> {
        Some(match value {
            0 => FieldKind::Scalar,
            1 => FieldKind::Skip,
            2 => FieldKind::ArrayFixed,
            3 => FieldKind::TobjectSkip,
            _ => return None,
        })
    }
}

/// One decoded instruction.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub struct Ins {
    pub op: Op,
    pub rd: u8,
    pub ra: u8,
    pub rb: u8,
    pub imm1: i64,
    pub imm2: i64,
}

impl Ins {
    /// Decode one 20-byte instruction (`<BBBBqq`).
    pub fn decode(raw: &[u8]) -> Ins {
        let imm1 = i64::from_le_bytes(raw[4..12].try_into().unwrap());
        let imm2 = i64::from_le_bytes(raw[12..20].try_into().unwrap());
        Ins {
            op: Op::from_u8(raw[0]).expect("caller checked the opcode"),
            rd: raw[1],
            ra: raw[2],
            rb: raw[3],
            imm1,
            imm2,
        }
    }
}

/// Buffer dtype name -> the typecode used to read one element.
pub fn buffer_typecode(dtype: &str) -> Option<Typecode> {
    Some(match dtype {
        "bool" | "uint8" => Typecode::Uint8,
        "uint16" => Typecode::Uint16,
        "uint32" => Typecode::Uint32,
        "uint64" => Typecode::Uint64,
        "int8" => Typecode::Int8,
        "int16" => Typecode::Int16,
        "int32" => Typecode::Int32,
        "int64" => Typecode::Int64,
        "float32" => Typecode::Float32,
        "float64" => Typecode::Float64,
        _ => return None,
    })
}
