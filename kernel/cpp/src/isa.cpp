// Opcode metadata: names used in diagnostics and the membership tests the
// machine's dispatch relies on. Kept in one place so `tools/gen_isa.py --check`
// can compare it with `spec/isa.toml` and the other two machines.

#include "isa.hpp"

#include <array>

namespace detree {
    namespace {
        struct OpName {
            Op op;
            std::string_view name;
        };

        // Ordered like the ISA document so a missing row is easy to spot.
        constexpr std::array<OpName, 105> kOpNames = { {
            { Op::HALT, "HALT" },
            { Op::NOP, "NOP" },
            { Op::LINE, "LINE" },
            { Op::DEBUG_PRINT, "DEBUG_PRINT" },
            { Op::CONST, "CONST" },
            { Op::CONST_F, "CONST_F" },
            { Op::MOV, "MOV" },
            { Op::ZEXT32, "ZEXT32" },
            { Op::ZEXT16, "ZEXT16" },
            { Op::ZEXT8, "ZEXT8" },
            { Op::SEXT32, "SEXT32" },
            { Op::SEXT16, "SEXT16" },
            { Op::SEXT8, "SEXT8" },
            { Op::ADD, "ADD" },
            { Op::SUB, "SUB" },
            { Op::MUL, "MUL" },
            { Op::DIV_S, "DIV_S" },
            { Op::DIV_U, "DIV_U" },
            { Op::MOD_S, "MOD_S" },
            { Op::MOD_U, "MOD_U" },
            { Op::NEG, "NEG" },
            { Op::SHL, "SHL" },
            { Op::SHR_S, "SHR_S" },
            { Op::SHR_U, "SHR_U" },
            { Op::AND, "AND" },
            { Op::OR, "OR" },
            { Op::XOR, "XOR" },
            { Op::NOT, "NOT" },
            { Op::NOT_B, "NOT_B" },
            { Op::CMP_EQ, "CMP_EQ" },
            { Op::CMP_NE, "CMP_NE" },
            { Op::CMP_LT_S, "CMP_LT_S" },
            { Op::CMP_LE_S, "CMP_LE_S" },
            { Op::CMP_GT_S, "CMP_GT_S" },
            { Op::CMP_GE_S, "CMP_GE_S" },
            { Op::CMP_LT_U, "CMP_LT_U" },
            { Op::CMP_LE_U, "CMP_LE_U" },
            { Op::CMP_GT_U, "CMP_GT_U" },
            { Op::CMP_GE_U, "CMP_GE_U" },
            { Op::JMP, "JMP" },
            { Op::JZ, "JZ" },
            { Op::JNZ, "JNZ" },
            { Op::BR_EQ, "BR_EQ" },
            { Op::BR_NE, "BR_NE" },
            { Op::BR_LT_S, "BR_LT_S" },
            { Op::BR_LE_S, "BR_LE_S" },
            { Op::BR_GT_S, "BR_GT_S" },
            { Op::BR_GE_S, "BR_GE_S" },
            { Op::BR_LT_U, "BR_LT_U" },
            { Op::BR_LE_U, "BR_LE_U" },
            { Op::BR_GT_U, "BR_GT_U" },
            { Op::BR_GE_U, "BR_GE_U" },
            { Op::CALL, "CALL" },
            { Op::RET, "RET" },
            { Op::SREAD, "SREAD" },
            { Op::SREAD_FNB, "SREAD_FNB" },
            { Op::SREAD_VERSION, "SREAD_VERSION" },
            { Op::SREAD_CSTR, "SREAD_CSTR" },
            { Op::SKIP, "SKIP" },
            { Op::SKIP_R, "SKIP_R" },
            { Op::SKIP_FNB, "SKIP_FNB" },
            { Op::SKIP_VERSION, "SKIP_VERSION" },
            { Op::SKIP_TOBJECT, "SKIP_TOBJECT" },
            { Op::SKIP_OBJHDR, "SKIP_OBJHDR" },
            { Op::SKIP_CSTR, "SKIP_CSTR" },
            { Op::CURSOR, "CURSOR" },
            { Op::SET_CURSOR, "SET_CURSOR" },
            { Op::ENTRIES, "ENTRIES" },
            { Op::INIT_OFFSET, "INIT_OFFSET" },
            { Op::DATA_SIZE, "DATA_SIZE" },
            { Op::FNB_BEGIN, "FNB_BEGIN" },
            { Op::FNB_END, "FNB_END" },
            { Op::OFFSET_AT, "OFFSET_AT" },
            { Op::OFFSET_SEEK, "OFFSET_SEEK" },
            { Op::EVENT_END, "EVENT_END" },
            { Op::READ_BULK, "READ_BULK" },
            { Op::READ_STRIDED, "READ_STRIDED" },
            { Op::READ_FIELDS, "READ_FIELDS" },
            { Op::READ_APPEND, "READ_APPEND" },
            { Op::READ_BULK_UNTIL, "READ_BULK_UNTIL" },
            { Op::READ_STRING, "READ_STRING" },
            { Op::BUF_APPEND, "BUF_APPEND" },
            { Op::BUF_APPEND_CONST, "BUF_APPEND_CONST" },
            { Op::BUF_APPEND_OFFSET, "BUF_APPEND_OFFSET" },
            { Op::BUF_APPEND_LEN, "BUF_APPEND_LEN" },
            { Op::BUF_LAST, "BUF_LAST" },
            { Op::BUF_LEN, "BUF_LEN" },
            { Op::BUF_RESERVE, "BUF_RESERVE" },
            { Op::STATE_GET, "STATE_GET" },
            { Op::STATE_SET, "STATE_SET" },
            { Op::STATE_IADD, "STATE_IADD" },
            { Op::REF_HAS, "REF_HAS" },
            { Op::REF_GET_OBJ, "REF_GET_OBJ" },
            { Op::REF_GET_CLASS, "REF_GET_CLASS" },
            { Op::REF_SET_OBJ, "REF_SET_OBJ" },
            { Op::REF_SET_CLASS, "REF_SET_CLASS" },
            { Op::REF_COUNT, "REF_COUNT" },
            { Op::STR_CONST, "STR_CONST" },
            { Op::STR_EQ, "STR_EQ" },
            { Op::STR_STORE, "STR_STORE" },
            { Op::STR_LOAD, "STR_LOAD" },
            { Op::ASSERT, "ASSERT" },
            { Op::ASSERT_CURSOR_EQ, "ASSERT_CURSOR_EQ" },
            { Op::ASSERT_STR_EQ, "ASSERT_STR_EQ" },
            { Op::THROW, "THROW" },
        } };
    } // namespace

    bool buffer_typecode( std::string_view dtype, Typecode& out ) noexcept {
        if ( dtype == "bool" || dtype == "uint8" ) { out = Typecode::UINT8; }
        else if ( dtype == "uint16" ) { out = Typecode::UINT16; }
        else if ( dtype == "uint32" ) { out = Typecode::UINT32; }
        else if ( dtype == "uint64" ) { out = Typecode::UINT64; }
        else if ( dtype == "int8" ) { out = Typecode::INT8; }
        else if ( dtype == "int16" ) { out = Typecode::INT16; }
        else if ( dtype == "int32" ) { out = Typecode::INT32; }
        else if ( dtype == "int64" ) { out = Typecode::INT64; }
        else if ( dtype == "float32" ) { out = Typecode::FLOAT32; }
        else if ( dtype == "float64" ) { out = Typecode::FLOAT64; }
        else { return false; }
        return true;
    }

    bool is_known_buffer_dtype( std::string_view dtype ) noexcept {
        Typecode tc;
        return buffer_typecode( dtype, tc );
    }

    bool is_integer_buffer_dtype( std::string_view dtype ) noexcept {
        if ( dtype == "bool" ) { return false; }
        Typecode tc;
        if ( !buffer_typecode( dtype, tc ) ) { return false; }
        return tc != Typecode::FLOAT32 && tc != Typecode::FLOAT64;
    }

    bool is_known_op( Op op ) noexcept {
        for ( const auto& entry : kOpNames )
        {
            if ( entry.op == op ) { return true; }
        }
        return false;
    }

    bool is_known_op( std::uint8_t value ) noexcept {
        return is_known_op( static_cast<Op>( value ) );
    }

    std::string_view op_name( Op op ) noexcept {
        for ( const auto& entry : kOpNames )
        {
            if ( entry.op == op ) { return entry.name; }
        }
        return "<unknown>";
    }

} // namespace detree
