#ifndef DETREE_ISA_HPP
#define DETREE_ISA_HPP

#include <cstddef>
#include <cstdint>
#include <string_view>

namespace detree {
    /// First four bytes of a serialized module.
    inline constexpr char kMagic[4] = { 'D', 'E', 'T', 'R' };

    /// The machine implements this major version, and only this one.
    inline constexpr std::uint16_t kIsaMajor = 0;
    /// Minor versions are additive; a newer minor is refused.
    inline constexpr std::uint16_t kIsaMinor = 1;

    /// Bytes per instruction: one opcode, three register bytes, two i64.
    inline constexpr std::size_t kInsSize = 20;

    /**
     * @brief Opcode table, mirroring ``src/detree/spec/isa.py``.
     *
     * Values are part of the wire format. ``spec/isa.toml`` is the authority;
     * ``tools/gen_isa.py --check`` compares the tables.
     */
    enum class Op : std::uint8_t {
        HALT              = 0x00,
        NOP               = 0x01,
        LINE              = 0x02,
        DEBUG_PRINT       = 0x03,
        CONST             = 0x04,
        CONST_F           = 0x05,
        MOV               = 0x06,
        ZEXT32            = 0x07,
        ZEXT16            = 0x08,
        ZEXT8             = 0x09,
        SEXT32            = 0x0A,
        SEXT16            = 0x0B,
        SEXT8             = 0x0C,
        ADD               = 0x10,
        SUB               = 0x11,
        MUL               = 0x12,
        DIV_S             = 0x13,
        DIV_U             = 0x14,
        MOD_S             = 0x15,
        MOD_U             = 0x16,
        NEG               = 0x17,
        SHL               = 0x18,
        SHR_S             = 0x19,
        SHR_U             = 0x1A,
        AND               = 0x1B,
        OR                = 0x1C,
        XOR               = 0x1D,
        NOT               = 0x1E,
        NOT_B             = 0x1F,
        CMP_EQ            = 0x20,
        CMP_NE            = 0x21,
        CMP_LT_S          = 0x22,
        CMP_LE_S          = 0x23,
        CMP_GT_S          = 0x24,
        CMP_GE_S          = 0x25,
        CMP_LT_U          = 0x26,
        CMP_LE_U          = 0x27,
        CMP_GT_U          = 0x28,
        CMP_GE_U          = 0x29,
        JMP               = 0x30,
        JZ                = 0x31,
        JNZ               = 0x32,
        BR_EQ             = 0x33,
        BR_NE             = 0x34,
        BR_LT_S           = 0x35,
        BR_LE_S           = 0x36,
        BR_GT_S           = 0x37,
        BR_GE_S           = 0x38,
        BR_LT_U           = 0x39,
        BR_LE_U           = 0x3A,
        BR_GT_U           = 0x3B,
        BR_GE_U           = 0x3C,
        CALL              = 0x3D,
        RET               = 0x3E,
        SREAD             = 0x40,
        SREAD_FNB         = 0x41,
        SREAD_VERSION     = 0x42,
        SREAD_CSTR        = 0x43,
        SKIP              = 0x44,
        SKIP_R            = 0x45,
        SKIP_FNB          = 0x46,
        SKIP_VERSION      = 0x47,
        SKIP_TOBJECT      = 0x48,
        SKIP_OBJHDR       = 0x49,
        SKIP_CSTR         = 0x4A,
        CURSOR            = 0x4B,
        SET_CURSOR        = 0x4C,
        ENTRIES           = 0x4D,
        INIT_OFFSET       = 0x4E,
        DATA_SIZE         = 0x4F,
        OFFSET_AT         = 0x60,
        OFFSET_SEEK       = 0x61,
        EVENT_END         = 0x62,
        READ_BULK         = 0x70,
        READ_STRIDED      = 0x71,
        READ_FIELDS       = 0x72,
        READ_APPEND       = 0x73,
        READ_BULK_UNTIL   = 0x74,
        READ_STRING       = 0x75,
        BUF_APPEND        = 0x80,
        BUF_APPEND_CONST  = 0x81,
        BUF_APPEND_OFFSET = 0x82,
        BUF_APPEND_LEN    = 0x83,
        BUF_LAST          = 0x84,
        BUF_LEN           = 0x85,
        BUF_RESERVE       = 0x86,
        STATE_GET         = 0x90,
        STATE_SET         = 0x91,
        STATE_IADD        = 0x92,
        REF_HAS           = 0xA0,
        REF_GET_OBJ       = 0xA1,
        REF_GET_CLASS     = 0xA2,
        REF_SET_OBJ       = 0xA3,
        REF_SET_CLASS     = 0xA4,
        REF_COUNT         = 0xA5,
        STR_CONST         = 0xB0,
        STR_EQ            = 0xB1,
        STR_STORE         = 0xB2,
        STR_LOAD          = 0xB3,
        ASSERT            = 0xC0,
        ASSERT_CURSOR_EQ  = 0xC1,
        ASSERT_STR_EQ     = 0xC2,
        THROW             = 0xC3,
    };

    /// Scalar stream element types, mirroring ``Typecode`` in Python.
    enum class Typecode : std::uint8_t {
        UINT8   = 0x01,
        UINT16  = 0x02,
        UINT32  = 0x03,
        UINT64  = 0x04,
        INT8    = 0x05,
        INT16   = 0x06,
        INT32   = 0x07,
        INT64   = 0x08,
        FLOAT32 = 0x09,
        FLOAT64 = 0x0A,
    };

    /// Storage class of a buffer.
    enum class BufferKind : std::uint8_t {
        DATA    = 0,
        OFFSETS = 1,
    };

    /// How one field of a flat layout is read.
    enum class FieldKind : std::uint8_t {
        SCALAR       = 0,
        SKIP         = 1,
        ARRAY_FIXED  = 2,
        TOBJECT_SKIP = 3,
    };

    /// One decoded instruction.
    struct Ins {
        Op op             = Op::HALT;
        std::uint8_t rd   = 0;
        std::uint8_t ra   = 0;
        std::uint8_t rb   = 0;
        std::int64_t imm1 = 0;
        std::int64_t imm2 = 0;
    };

    /// Decode one 20-byte instruction (``<BBBBqq``).
    inline Ins decode_ins( const std::uint8_t* raw ) noexcept {
        Ins ins;
        ins.op             = static_cast<Op>( raw[0] );
        ins.rd             = raw[1];
        ins.ra             = raw[2];
        ins.rb             = raw[3];
        std::uint64_t imm1 = 0;
        std::uint64_t imm2 = 0;
        for ( int i = 0; i < 8; ++i )
        {
            imm1 |= static_cast<std::uint64_t>( raw[4 + i] ) << ( 8 * i );
            imm2 |= static_cast<std::uint64_t>( raw[12 + i] ) << ( 8 * i );
        }
        ins.imm1 = static_cast<std::int64_t>( imm1 );
        ins.imm2 = static_cast<std::int64_t>( imm2 );
        return ins;
    }

    /// Size in bytes of one element of @p tc.
    constexpr std::size_t typecode_size( Typecode tc ) noexcept {
        switch ( tc )
        {
        case Typecode::UINT8:
        case Typecode::INT8: return 1;
        case Typecode::UINT16:
        case Typecode::INT16: return 2;
        case Typecode::UINT32:
        case Typecode::INT32:
        case Typecode::FLOAT32: return 4;
        case Typecode::UINT64:
        case Typecode::INT64:
        case Typecode::FLOAT64: return 8;
        }
        return 0;
    }

    /// True for the type codes the ISA defines.
    constexpr bool is_known_typecode( std::uint8_t value ) noexcept {
        return value >= 0x01 && value <= 0x0A;
    }

    constexpr bool is_known_typecode( Typecode tc ) noexcept {
        return is_known_typecode( static_cast<std::uint8_t>( tc ) );
    }

    /// True when an immediate carries a type code (it must not truncate to one).
    constexpr bool is_known_typecode( std::int64_t value ) noexcept {
        return value >= 0x01 && value <= 0x0A;
    }

    /// True for instructions that take their target from ``imm1``.
    constexpr bool is_jump_op( Op op ) noexcept {
        switch ( op )
        {
        case Op::JMP:
        case Op::JZ:
        case Op::JNZ:
        case Op::BR_EQ:
        case Op::BR_NE:
        case Op::BR_LT_S:
        case Op::BR_LE_S:
        case Op::BR_GT_S:
        case Op::BR_GE_S:
        case Op::BR_LT_U:
        case Op::BR_LE_U:
        case Op::BR_GT_U:
        case Op::BR_GE_U: return true;
        default: return false;
        }
    }

    /// True for instructions whose ``imm1`` names a buffer.
    constexpr bool is_buffer_op( Op op ) noexcept {
        switch ( op )
        {
        case Op::BUF_APPEND:
        case Op::BUF_APPEND_CONST:
        case Op::BUF_APPEND_OFFSET:
        case Op::BUF_LAST:
        case Op::BUF_LEN:
        case Op::BUF_RESERVE:
        case Op::READ_APPEND: return true;
        default: return false;
        }
    }

    /// True for instructions whose ``imm1`` names a state slot.
    constexpr bool is_state_op( Op op ) noexcept {
        switch ( op )
        {
        case Op::STATE_GET:
        case Op::STATE_SET:
        case Op::STATE_IADD:
        case Op::STR_STORE:
        case Op::STR_LOAD: return true;
        default: return false;
        }
    }

    /// True for instructions whose ``imm1`` names a message string.
    constexpr bool is_message_op( Op op ) noexcept {
        switch ( op )
        {
        case Op::ASSERT:
        case Op::ASSERT_CURSOR_EQ:
        case Op::ASSERT_STR_EQ:
        case Op::THROW:
        case Op::DEBUG_PRINT: return true;
        default: return false;
        }
    }

    /// True for instructions that end a frame without falling through.
    constexpr bool is_terminator( Op op ) noexcept {
        return op == Op::HALT || op == Op::RET || op == Op::THROW;
    }

    /// Buffer dtype name -> the typecode used to read one element.
    bool buffer_typecode( std::string_view dtype, Typecode& out ) noexcept;

    /// True when @p dtype names a buffer dtype this machine knows.
    bool is_known_buffer_dtype( std::string_view dtype ) noexcept;

    /// True when @p dtype names a buffer dtype that holds integers.
    bool is_integer_buffer_dtype( std::string_view dtype ) noexcept;

    /// True when @p op is an opcode of this ISA version.
    bool is_known_op( Op op ) noexcept;

    /// True when @p value is the byte of an opcode this ISA version defines.
    bool is_known_op( std::uint8_t value ) noexcept;

    /// Mnemonic of @p op for an error message, or ``<unknown>``.
    std::string_view op_name( Op op ) noexcept;

} // namespace detree

#endif // DETREE_ISA_HPP
