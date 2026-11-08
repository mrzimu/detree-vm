// Module decoder and static validator. The format is normative in
// `docs/spec/module.md`; this is the C++ implementation of it, byte for byte the
// inverse of the Python reference's `to_bytes`.

#include "module.hpp"

#include <algorithm>
#include <cstring>
#include <string>
#include <tuple>
#include <utility>

#include "errors.hpp"

namespace detree {
    namespace {
        /// Little-endian cursor over serialized module bytes.
        class Decoder {
          public:
            Decoder( const std::uint8_t* raw, std::size_t size )
                : m_raw( raw ), m_size( size ) {}

            std::size_t remaining() const noexcept { return m_size - m_pos; }
            bool at_end() const noexcept { return m_pos == m_size; }

            const std::uint8_t* take( std::size_t size, const char* what ) {
                if ( size > remaining() )
                {
                    throw ModuleError( std::string( "module is truncated: wanted " ) +
                                       std::to_string( size ) + " bytes for " + what + " at " +
                                       std::to_string( m_pos ) + ", only " +
                                       std::to_string( remaining() ) + " left" );
                }
                const std::uint8_t* chunk = m_raw + m_pos;
                m_pos += size;
                return chunk;
            }

            std::uint8_t u8( const char* what ) { return *take( 1, what ); }

            std::uint16_t u16( const char* what ) {
                const std::uint8_t* p = take( 2, what );
                return static_cast<std::uint16_t>( p[0] | ( p[1] << 8 ) );
            }

            std::uint32_t u32( const char* what ) {
                const std::uint8_t* p = take( 4, what );
                return static_cast<std::uint32_t>( p[0] ) |
                       ( static_cast<std::uint32_t>( p[1] ) << 8 ) |
                       ( static_cast<std::uint32_t>( p[2] ) << 16 ) |
                       ( static_cast<std::uint32_t>( p[3] ) << 24 );
            }

            std::int32_t i32( const char* what ) {
                return static_cast<std::int32_t>( u32( what ) );
            }

            std::int64_t i64( const char* what ) {
                const std::uint8_t* p = take( 8, what );
                std::uint64_t value   = 0;
                for ( int i = 7; i >= 0; --i ) { value = ( value << 8 ) | p[i]; }
                return static_cast<std::int64_t>( value );
            }

            std::string text( const char* what ) {
                const std::size_t size = u16( what );
                const std::uint8_t* p  = take( size, what );
                return std::string( reinterpret_cast<const char*>( p ), size );
            }

          private:
            const std::uint8_t* m_raw;
            std::size_t m_size;
            std::size_t m_pos = 0;
        };

        /// Refuse a count the remaining bytes cannot possibly satisfy, before reserving.
        void check_count( std::uint32_t count, std::size_t minimum_each, std::size_t remaining,
                          const char* what ) {
            if ( static_cast<std::size_t>( count ) > remaining / minimum_each )
            {
                throw ModuleError( std::string( "module declares " ) +
                                   std::to_string( count ) + " " + what +
                                   ", which does not fit in " + std::to_string( remaining ) +
                                   " remaining bytes" );
            }
        }

        std::string where( const FuncDecl& func, std::size_t offset ) {
            return func.name + "#" + std::to_string( offset );
        }

        /// Two lower-case hex digits, which is how the Python side prints an opcode.
        std::string hex_byte( std::uint8_t value ) {
            static const char digits[] = "0123456789abcdef";
            std::string out( 2, '0' );
            out[0] = digits[value >> 4];
            out[1] = digits[value & 0x0F];
            return out;
        }

        /// True when @p op writes ``rd`` (mirrors ``_RD`` in the Python validator).
        bool uses_rd( Op op ) noexcept {
            switch ( op )
            {
            case Op::CONST:
            case Op::CONST_F:
            case Op::MOV:
            case Op::ZEXT32:
            case Op::ZEXT16:
            case Op::ZEXT8:
            case Op::SEXT32:
            case Op::SEXT16:
            case Op::SEXT8:
            case Op::ADD:
            case Op::SUB:
            case Op::MUL:
            case Op::DIV_S:
            case Op::DIV_U:
            case Op::MOD_S:
            case Op::MOD_U:
            case Op::NEG:
            case Op::SHL:
            case Op::SHR_S:
            case Op::SHR_U:
            case Op::AND:
            case Op::OR:
            case Op::XOR:
            case Op::NOT:
            case Op::NOT_B:
            case Op::CMP_EQ:
            case Op::CMP_NE:
            case Op::CMP_LT_S:
            case Op::CMP_LE_S:
            case Op::CMP_GT_S:
            case Op::CMP_GE_S:
            case Op::CMP_LT_U:
            case Op::CMP_LE_U:
            case Op::CMP_GT_U:
            case Op::CMP_GE_U:
            case Op::CALL:
            case Op::SREAD:
            case Op::SREAD_FNB:
            case Op::SREAD_VERSION:
            case Op::SREAD_CSTR:
            case Op::CURSOR:
            case Op::ENTRIES:
            case Op::INIT_OFFSET:
            case Op::DATA_SIZE:
            case Op::OFFSET_AT:
            case Op::OFFSET_SEEK:
            case Op::EVENT_END:
            case Op::BUF_LAST:
            case Op::BUF_LEN:
            case Op::STATE_GET:
            case Op::REF_HAS:
            case Op::REF_GET_OBJ:
            case Op::REF_GET_CLASS:
            case Op::REF_COUNT:
            case Op::STR_CONST:
            case Op::STR_EQ:
            case Op::STR_LOAD: return true;
            default: return false;
            }
        }

        /// True when @p op reads ``ra`` (mirrors ``_RA`` in the Python validator).
        bool uses_ra( Op op ) noexcept {
            switch ( op )
            {
            case Op::MOV:
            case Op::ZEXT32:
            case Op::ZEXT16:
            case Op::ZEXT8:
            case Op::SEXT32:
            case Op::SEXT16:
            case Op::SEXT8:
            case Op::NEG:
            case Op::NOT:
            case Op::NOT_B:
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
            case Op::BR_GE_U:
            case Op::CALL:
            case Op::RET:
            case Op::SKIP_R:
            case Op::SET_CURSOR:
            case Op::OFFSET_AT:
            case Op::OFFSET_SEEK:
            case Op::READ_BULK:
            case Op::READ_STRIDED:
            case Op::READ_FIELDS:
            case Op::BUF_APPEND:
            case Op::BUF_APPEND_OFFSET:
            case Op::BUF_RESERVE:
            case Op::STATE_SET:
            case Op::REF_HAS:
            case Op::REF_GET_OBJ:
            case Op::REF_GET_CLASS:
            case Op::REF_SET_OBJ:
            case Op::REF_SET_CLASS:
            case Op::STR_EQ:
            case Op::STR_STORE:
            case Op::ASSERT:
            case Op::ASSERT_CURSOR_EQ:
            case Op::ASSERT_STR_EQ:
            case Op::DEBUG_PRINT: return true;
            default: return false;
            }
        }

        /// True when @p op reads ``rb`` (mirrors ``_RB`` in the Python validator).
        bool uses_rb( Op op ) noexcept {
            switch ( op )
            {
            case Op::ADD:
            case Op::SUB:
            case Op::MUL:
            case Op::DIV_S:
            case Op::DIV_U:
            case Op::MOD_S:
            case Op::MOD_U:
            case Op::SHL:
            case Op::SHR_S:
            case Op::SHR_U:
            case Op::AND:
            case Op::OR:
            case Op::XOR:
            case Op::CMP_EQ:
            case Op::CMP_NE:
            case Op::CMP_LT_S:
            case Op::CMP_LE_S:
            case Op::CMP_GT_S:
            case Op::CMP_GE_S:
            case Op::CMP_LT_U:
            case Op::CMP_LE_U:
            case Op::CMP_GT_U:
            case Op::CMP_GE_U:
            case Op::BR_EQ:
            case Op::BR_NE:
            case Op::BR_LT_S:
            case Op::BR_LE_S:
            case Op::BR_GT_S:
            case Op::BR_GE_S:
            case Op::BR_LT_U:
            case Op::BR_LE_U:
            case Op::BR_GT_U:
            case Op::BR_GE_U:
            case Op::READ_STRIDED:
            case Op::REF_SET_OBJ:
            case Op::REF_SET_CLASS:
            case Op::STR_EQ:
            case Op::ASSERT_STR_EQ: return true;
            default: return false;
            }
        }

    } // namespace

    Module Module::from_bytes( const std::uint8_t* raw, std::size_t size ) {
        if ( raw == nullptr && size != 0 ) { throw ModuleError( "module bytes are null" ); }
        Decoder decoder( raw, size );

        if ( size < sizeof( kMagic ) )
        { throw ModuleError( "module is truncated: no magic" ); }
        const std::uint8_t* magic = decoder.take( sizeof( kMagic ), "magic" );
        if ( std::memcmp( magic, kMagic, sizeof( kMagic ) ) != 0 )
        { throw ModuleError( "bad magic: not a detree module" ); }

        Module module;
        module.isa_major = decoder.u16( "isa_major" );
        module.isa_minor = decoder.u16( "isa_minor" );
        module.entry     = decoder.u16( "entry" );
        module.regs_max  = decoder.u16( "regs_max" );

        const std::uint32_t n_buffers = decoder.u32( "buffer count" );
        const std::uint32_t n_states  = decoder.u32( "state count" );
        const std::uint32_t n_strings = decoder.u32( "string count" );
        const std::uint32_t n_layouts = decoder.u32( "layout count" );
        const std::uint32_t n_funcs   = decoder.u32( "function count" );

        // A declared count is attacker-controlled input: bound it by what is left
        // before reserving, or a 4-byte header can ask for gigabytes.
        check_count( n_buffers, 7, decoder.remaining(), "buffers" );
        check_count( n_states, 11, decoder.remaining(), "states" );
        check_count( n_strings, 2, decoder.remaining(), "strings" );
        check_count( n_layouts, 4, decoder.remaining(), "layouts" );
        check_count( n_funcs, 15, decoder.remaining(), "functions" );

        module.buffers.reserve( n_buffers );
        for ( std::uint32_t i = 0; i < n_buffers; ++i )
        {
            BufferDecl decl;
            decl.name  = decoder.text( "buffer name" );
            decl.dtype = decoder.text( "buffer dtype" );
            decl.kind  = static_cast<BufferKind>( decoder.u8( "buffer kind" ) );
            const std::uint32_t n_init = decoder.u32( "buffer init count" );
            check_count( n_init, 8, decoder.remaining(), "buffer init values" );
            decl.init.reserve( n_init );
            for ( std::uint32_t j = 0; j < n_init; ++j )
            { decl.init.push_back( decoder.i64( "buffer init value" ) ); }
            module.buffers.push_back( std::move( decl ) );
        }

        module.states.reserve( n_states );
        for ( std::uint32_t i = 0; i < n_states; ++i )
        {
            StateDecl decl;
            decl.name      = decoder.text( "state name" );
            decl.init      = decoder.i64( "state init" );
            decl.is_string = ( decoder.u8( "state flags" ) & 0x01 ) != 0;
            module.states.push_back( std::move( decl ) );
        }

        module.strings.reserve( n_strings );
        for ( std::uint32_t i = 0; i < n_strings; ++i )
        { module.strings.push_back( decoder.text( "string" ) ); }

        module.layouts.reserve( n_layouts );
        for ( std::uint32_t i = 0; i < n_layouts; ++i )
        {
            Layout layout;
            layout.id                   = decoder.u32( "layout id" );
            const std::uint32_t n_field = decoder.u32( "layout field count" );
            check_count( n_field, 10, decoder.remaining(), "layout fields" );
            layout.fields.reserve( n_field );
            for ( std::uint32_t j = 0; j < n_field; ++j )
            {
                LayoutField field;
                field.kind      = static_cast<FieldKind>( decoder.u8( "field kind" ) );
                field.typecode  = static_cast<Typecode>( decoder.u8( "field typecode" ) );
                field.count     = decoder.i32( "field count" );
                field.buffer_id = decoder.i32( "field buffer" );
                layout.fields.push_back( field );
            }
            module.layouts.push_back( std::move( layout ) );
        }

        module.funcs.reserve( n_funcs );
        for ( std::uint32_t i = 0; i < n_funcs; ++i )
        {
            FuncDecl func;
            func.id        = decoder.u32( "function id" );
            func.name      = decoder.text( "function name" );
            func.arity     = decoder.u8( "function arity" );
            func.reg_count = decoder.u16( "function reg_count" );
            (void)decoder.u16( "function reserved" ); // reserved, always zero
            func.code_off = decoder.u32( "function code offset" );
            func.code_len = decoder.u32( "function code length" );
            module.funcs.push_back( std::move( func ) );
        }

        const std::uint32_t n_code = decoder.u32( "code length" );
        check_count( n_code, kInsSize, decoder.remaining(), "instructions" );
        module.code.reserve( n_code );
        for ( std::uint32_t i = 0; i < n_code; ++i )
        {
            const Ins ins = decode_ins( decoder.take( kInsSize, "instruction" ) );
            if ( !is_known_op( ins.op ) )
            {
                throw ModuleError( "instruction " + std::to_string( i ) +
                                   " has unknown opcode 0x" +
                                   hex_byte( static_cast<std::uint8_t>( ins.op ) ) );
            }
            module.code.push_back( ins );
        }

        if ( !decoder.at_end() )
        {
            throw ModuleError( "module has " + std::to_string( decoder.remaining() ) +
                               " trailing bytes after the instructions" );
        }
        return module;
    }

    void validate( const Module& module ) {
        if ( module.isa_major != kIsaMajor )
        {
            throw ModuleError( "module isa_major " + std::to_string( module.isa_major ) +
                               " != machine " + std::to_string( kIsaMajor ) );
        }
        if ( module.isa_minor > kIsaMinor )
        {
            throw ModuleError( "module isa_minor " + std::to_string( module.isa_minor ) +
                               " is newer than machine " + std::to_string( kIsaMinor ) );
        }
        if ( module.funcs.empty() ) { throw ModuleError( "module has no functions" ); }
        if ( module.entry >= module.funcs.size() )
        {
            throw ModuleError( "entry function " + std::to_string( module.entry ) +
                               " does not exist" );
        }

        // -- functions: ids, arity, ranges, and non-overlapping code slices -----
        std::vector<std::tuple<std::uint32_t, std::uint32_t, std::string>> spans;
        for ( std::size_t index = 0; index < module.funcs.size(); ++index )
        {
            const FuncDecl& func   = module.funcs[index];
            const std::string name = func.name.empty() ? "<anonymous>" : func.name;
            if ( func.id != index )
            {
                throw ModuleError( "function " + std::to_string( index ) + " has id " +
                                   std::to_string( func.id ) );
            }
            if ( func.arity > 1 )
            { throw ModuleError( name + ": arity " + std::to_string( func.arity ) ); }
            if ( func.arity == 1 && func.reg_count == 0 )
            { throw ModuleError( name + ": arity 1 needs a register for the argument" ); }
            if ( func.code_len == 0 ) { throw ModuleError( name + ": empty body" ); }
            if ( func.code_off > module.code.size() || func.end() > module.code.size() )
            { throw ModuleError( name + ": code range out of bounds" ); }
            if ( module.regs_max < func.reg_count )
            {
                throw ModuleError( "regs_max " + std::to_string( module.regs_max ) +
                                   " below " + name + " reg_count " +
                                   std::to_string( func.reg_count ) );
            }
            spans.emplace_back( func.code_off, func.end(), name );
        }
        std::sort( spans.begin(), spans.end() );
        for ( std::size_t i = 1; i < spans.size(); ++i )
        {
            if ( std::get<1>( spans[i - 1] ) > std::get<0>( spans[i] ) )
            {
                throw ModuleError( std::get<2>( spans[i - 1] ) + " and " +
                                   std::get<2>( spans[i] ) + " have overlapping code slices" );
            }
        }

        // -- buffers -------------------------------------------------------------
        std::vector<std::string> names;
        for ( const BufferDecl& decl : module.buffers )
        {
            const std::string name = decl.name.empty() ? "<anonymous>" : decl.name;
            if ( std::find( names.begin(), names.end(), decl.name ) != names.end() )
            { throw ModuleError( "duplicate buffer name '" + decl.name + "'" ); }
            names.push_back( decl.name );
            if ( !is_known_buffer_dtype( decl.dtype ) )
            { throw ModuleError( "buffer " + name + ": dtype '" + decl.dtype + "'" ); }
            if ( static_cast<std::uint8_t>( decl.kind ) >
                 static_cast<std::uint8_t>( BufferKind::COUNT ) )
            {
                throw ModuleError( "buffer " + name + ": kind " +
                                   std::to_string( static_cast<int>( decl.kind ) ) );
            }
            if ( decl.kind != BufferKind::DATA && decl.dtype != "int64" )
            {
                throw ModuleError( "buffer " + name +
                                   ": OFFSETS/COUNT buffers must be int64" );
            }
        }

        // -- layouts ---------------------------------------------------------------
        for ( const Layout& layout : module.layouts )
        {
            for ( const LayoutField& field : layout.fields )
            {
                const std::string at = "layout " + std::to_string( layout.id );
                if ( field.kind == FieldKind::TOBJECT_SKIP )
                { throw ModuleError( at + ": TOBJECT_SKIP unsupported" ); }
                if ( field.kind != FieldKind::SCALAR && field.kind != FieldKind::ARRAY_FIXED &&
                     field.kind != FieldKind::SKIP )
                {
                    throw ModuleError( at + ": unknown field kind " +
                                       std::to_string( static_cast<int>( field.kind ) ) );
                }
                if ( field.count < 0 ) { throw ModuleError( at + ": negative field count" ); }
                if ( field.kind != FieldKind::SKIP )
                {
                    if ( field.buffer_id < 0 ||
                         static_cast<std::size_t>( field.buffer_id ) >= module.buffers.size() )
                    {
                        throw ModuleError( at + ": buffer " +
                                           std::to_string( field.buffer_id ) +
                                           " does not exist" );
                    }
                    if ( !is_known_typecode( field.typecode ) )
                    {
                        throw ModuleError(
                            at + ": unknown typecode " +
                            std::to_string( static_cast<int>( field.typecode ) ) );
                    }
                }
            }
        }

        // -- code ------------------------------------------------------------------
        const std::size_t n_buffers = module.buffers.size();
        const std::size_t n_states  = module.states.size();
        const std::size_t n_layouts = module.layouts.size();
        const std::size_t n_strings = module.strings.size();

        for ( const FuncDecl& func : module.funcs )
        {
            const std::size_t first = func.code_off;
            for ( std::size_t offset = 0; offset < func.code_len; ++offset )
            {
                const Ins& ins       = module.code[first + offset];
                const std::string at = where( func, offset );

                if ( uses_rd( ins.op ) && ins.rd >= func.reg_count )
                {
                    throw ModuleError( at + ": register " + std::to_string( ins.rd ) +
                                       " >= reg_count " + std::to_string( func.reg_count ) );
                }
                if ( uses_ra( ins.op ) && ins.ra >= func.reg_count )
                {
                    throw ModuleError( at + ": register " + std::to_string( ins.ra ) +
                                       " >= reg_count " + std::to_string( func.reg_count ) );
                }
                if ( uses_rb( ins.op ) && ins.rb >= func.reg_count )
                {
                    throw ModuleError( at + ": register " + std::to_string( ins.rb ) +
                                       " >= reg_count " + std::to_string( func.reg_count ) );
                }

                if ( is_jump_op( ins.op ) )
                {
                    if ( ins.imm1 < 0 ||
                         static_cast<std::size_t>( ins.imm1 ) >= func.code_len )
                    {
                        throw ModuleError( at + ": jump target " + std::to_string( ins.imm1 ) +
                                           " outside the function" );
                    }
                }

                if ( ins.op == Op::SKIP && ins.imm1 < 0 )
                { throw ModuleError( at + ": negative skip " + std::to_string( ins.imm1 ) ); }

                if ( ins.op == Op::CALL )
                {
                    if ( ins.imm1 < 0 ||
                         static_cast<std::size_t>( ins.imm1 ) >= module.funcs.size() )
                    {
                        throw ModuleError( at + ": function " + std::to_string( ins.imm1 ) +
                                           " does not exist" );
                    }
                }

                if ( is_buffer_op( ins.op ) )
                {
                    if ( ins.imm1 < 0 || static_cast<std::size_t>( ins.imm1 ) >= n_buffers )
                    {
                        throw ModuleError( at + ": buffer " + std::to_string( ins.imm1 ) +
                                           " does not exist" );
                    }
                }

                if ( ins.op == Op::BUF_APPEND_LEN )
                {
                    for ( const std::int64_t operand : { ins.imm1, ins.imm2 } )
                    {
                        if ( operand < 0 || static_cast<std::size_t>( operand ) >= n_buffers )
                        {
                            throw ModuleError( at + ": buffer " + std::to_string( operand ) +
                                               " does not exist" );
                        }
                    }
                }

                if ( ins.op == Op::READ_BULK || ins.op == Op::READ_STRIDED ||
                     ins.op == Op::READ_BULK_UNTIL )
                {
                    if ( ins.imm1 < 0 || static_cast<std::size_t>( ins.imm1 ) >= n_buffers )
                    {
                        throw ModuleError( at + ": buffer " + std::to_string( ins.imm1 ) +
                                           " does not exist" );
                    }
                    if ( !is_known_typecode( ins.imm2 ) )
                    {
                        throw ModuleError( at + ": unknown typecode " +
                                           std::to_string( ins.imm2 ) );
                    }
                }

                if ( ins.op == Op::READ_STRING )
                {
                    for ( const std::int64_t buffer : { ins.imm1, ins.imm2 } )
                    {
                        if ( buffer < 0 || static_cast<std::size_t>( buffer ) >= n_buffers )
                        {
                            throw ModuleError( at + ": buffer " + std::to_string( buffer ) +
                                               " does not exist" );
                        }
                    }
                }

                if ( ins.op == Op::SREAD || ins.op == Op::READ_APPEND )
                {
                    const std::int64_t typecode = ins.op == Op::SREAD ? ins.imm1 : ins.imm2;
                    if ( !is_known_typecode( typecode ) )
                    {
                        throw ModuleError( at + ": unknown typecode " +
                                           std::to_string( typecode ) );
                    }
                }

                if ( is_state_op( ins.op ) )
                {
                    if ( ins.imm1 < 0 || static_cast<std::size_t>( ins.imm1 ) >= n_states )
                    {
                        throw ModuleError( at + ": state " + std::to_string( ins.imm1 ) +
                                           " does not exist" );
                    }
                }

                if ( ins.op == Op::STR_CONST )
                {
                    if ( ins.imm1 < 0 || static_cast<std::size_t>( ins.imm1 ) >= n_strings )
                    {
                        throw ModuleError( at + ": string " + std::to_string( ins.imm1 ) +
                                           " does not exist" );
                    }
                }

                if ( ins.op == Op::READ_FIELDS )
                {
                    if ( ins.imm1 < 0 || static_cast<std::size_t>( ins.imm1 ) >= n_layouts )
                    {
                        throw ModuleError( at + ": layout " + std::to_string( ins.imm1 ) +
                                           " does not exist" );
                    }
                }

                if ( is_message_op( ins.op ) )
                {
                    if ( ins.imm1 < 0 || static_cast<std::size_t>( ins.imm1 ) >= n_strings )
                    {
                        throw ModuleError( at + ": message " + std::to_string( ins.imm1 ) +
                                           " does not exist" );
                    }
                }
            }
        }
    }

} // namespace detree
