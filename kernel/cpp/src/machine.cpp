// The machine: event loop, frames, and instruction dispatch. Semantics are
// normative in `docs/spec/isa.md`; the Python reference is the oracle this port
// is tested against.

#include "machine.hpp"
#include "errors.hpp"

#include <algorithm>
#include <limits>
#include <string>

namespace detree {
    namespace {
        constexpr std::uint64_t kMask64 = ~static_cast<std::uint64_t>( 0 );

        /// The signed value a 64-bit register slot holds.
        std::int64_t to_signed( std::uint64_t value ) noexcept {
            return static_cast<std::int64_t>( value );
        }

        /// Sign-extend the low @p bits of @p value to 64 bits.
        std::uint64_t sign_extend( std::uint64_t value, unsigned bits ) noexcept {
            const std::uint64_t mask =
                ( bits >= 64 ) ? kMask64 : ( ( std::uint64_t{ 1 } << bits ) - 1 );
            value &= mask;
            if ( value & ( std::uint64_t{ 1 } << ( bits - 1 ) ) ) { value |= ~mask; }
            return value;
        }

        /// Big-endian unsigned value of @p width bytes at @p raw.
        std::uint64_t read_be( const std::uint8_t* raw, std::size_t width ) noexcept {
            std::uint64_t value = 0;
            for ( std::size_t i = 0; i < width; ++i ) { value = ( value << 8 ) | raw[i]; }
            return value;
        }

        /// Truncating division, defined for every pair (``INT64_MIN / -1`` included).
        std::int64_t trunc_div( std::int64_t a, std::int64_t b ) noexcept {
            if ( b == 0 ) { return 0; }
            if ( a == INT64_MIN && b == -1 ) { return INT64_MIN; }
            return a / b;
        }

    } // namespace

    Machine::Machine( const std::uint8_t* raw, std::size_t size )
        : m_module( Module::from_bytes( raw, size ) ) {
        validate( m_module );
        for ( const FuncDecl& func : m_module.funcs )
        {
            m_max_regs = std::max<std::size_t>( m_max_regs,
                                                static_cast<std::size_t>( func.reg_count ) );
        }
        m_reg_pool.assign( ( kMaxDepth + 1 ) * m_max_regs, 0 );
        m_buffers.reserve( m_module.buffers.size() );
        for ( const BufferDecl& decl : m_module.buffers ) { m_buffers.emplace_back( decl ); }
        reset();
    }

    void Machine::reset() {
        m_data          = nullptr;
        m_data_len      = 0;
        m_offsets       = nullptr;
        m_n_offsets     = 0;
        m_cursor        = 0;
        m_cursor_offset = 0;
        m_event         = 0;
        m_entry_end     = 0;
        m_frames.clear();
        m_nb_stack.clear();
        m_depth      = 0;
        m_func_index = m_module.entry;
        m_ip         = 0;
        m_halted     = true;

        const FuncDecl& entry = m_module.funcs[m_module.entry];
        m_reg_count           = entry.reg_count;
        m_code_off            = entry.code_off;
        m_code_len            = entry.code_len;

        m_buffers.clear();
        m_buffers.reserve( m_module.buffers.size() );
        for ( const BufferDecl& decl : m_module.buffers ) { m_buffers.emplace_back( decl ); }

        m_states.clear();
        m_states.reserve( m_module.states.size() );
        for ( const StateDecl& decl : m_module.states )
        { m_states.push_back( static_cast<std::uint64_t>( decl.init ) ); }

        // String arena: the module's constants come first, in order, so a `STR_CONST`
        // id is simply an index; strings read from the stream are interned after
        // them and keep their id for the whole run.
        m_strings.clear();
        m_str_ids.clear();
        m_strings.reserve( m_module.strings.size() );
        for ( const std::string& text : m_module.strings ) { intern( text ); }

        m_refs.clear();
    }

    std::uint64_t Machine::intern( const std::string& text ) {
        const auto found = m_str_ids.find( text );
        if ( found != m_str_ids.end() ) { return found->second; }
        const auto id = static_cast<std::uint64_t>( m_strings.size() );
        m_strings.push_back( text );
        m_str_ids.emplace( text, id );
        return id;
    }

    const std::string* Machine::string_of( std::uint64_t ident ) const {
        if ( ident == kMask64 ) { return nullptr; } // -1: "no string"
        if ( ident < m_strings.size() )
        { return &m_strings[static_cast<std::size_t>( ident )]; }
        return nullptr;
    }

    Machine::StateValue Machine::state_value( std::size_t index ) const {
        StateValue value;
        value.raw = m_states.at( index );
        if ( m_module.states.at( index ).is_string ) { value.text = string_of( value.raw ); }
        return value;
    }

    void Machine::skip_cstr() {
        const std::size_t start = m_cursor;
        while ( m_cursor < m_data_len && m_data[m_cursor] != 0 ) { ++m_cursor; }
        if ( m_cursor >= m_data_len )
        {
            m_cursor = start;
            fail<StreamUnderflow>( "unterminated string" );
        }
        ++m_cursor;
    }

    void Machine::run( const std::uint8_t* data, std::size_t size, const std::int64_t* offsets,
                       std::size_t n_offsets, std::size_t cursor_offset ) {
        reset();

        m_data          = data;
        m_data_len      = size;
        m_offsets       = offsets;
        m_n_offsets     = n_offsets;
        m_cursor_offset = cursor_offset;

        if ( n_offsets < 2 )
        { throw ReadLengthMismatch( "a basket needs at least two entry offsets" ); }

        const FuncDecl& entry = m_module.funcs[m_module.entry];

        // The cursor runs on across events: entry offsets are cumulative from the
        // first byte of the basket.
        m_cursor = 0;
        for ( std::size_t event = 0; event + 1 < n_offsets; ++event )
        {
            m_event     = event;
            m_entry_end = static_cast<std::size_t>( offsets[event + 1] );
            m_frames.clear();
            m_nb_stack.clear();
            m_depth      = 0;
            m_func_index = m_module.entry;
            m_reg_count  = entry.reg_count;
            m_code_off   = entry.code_off;
            m_code_len   = entry.code_len;
            m_ip         = 0;
            m_halted     = false;
            std::fill_n( reg_at( 0 ), entry.reg_count, 0 );
            loop();
            if ( m_cursor != m_entry_end )
            {
                throw ReadLengthMismatch( "event " + std::to_string( event ) + ": expected " +
                                              std::to_string( m_entry_end ) + " bytes, read " +
                                              std::to_string( m_cursor ),
                                          entry.name, -1, m_cursor );
            }
        }
    }

    void Machine::need( std::size_t nbytes ) {
        if ( nbytes > m_data_len || m_cursor > m_data_len - nbytes )
        {
            fail<StreamUnderflow>( "need " + std::to_string( nbytes ) + " bytes, " +
                                   std::to_string( m_data_len - m_cursor ) + " left" );
        }
    }

    void Machine::require_nb_depth( std::size_t expected ) {
        if ( m_nb_stack.size() != expected )
        {
            fail<Error>( "return with byte-count depth " +
                         std::to_string( m_nb_stack.size() ) + ", expected " +
                         std::to_string( expected ) );
        }
    }

    std::uint64_t Machine::read_bits( std::size_t size ) {
        need( size );
        const std::uint64_t value = read_be( m_data + m_cursor, size );
        m_cursor += size;
        return value;
    }

    std::uint64_t Machine::read_register( Typecode typecode ) {
        const std::uint64_t raw = read_bits( typecode_size( typecode ) );
        switch ( typecode )
        {
        case Typecode::INT8: return sign_extend( raw, 8 );
        case Typecode::INT16: return sign_extend( raw, 16 );
        case Typecode::INT32: return sign_extend( raw, 32 );
        default: return raw;
        }
    }

    void Machine::loop() {
        while ( !m_halted )
        {
            // A function that falls through its last instruction is malformed, not
            // merely unlucky: refuse rather than read past the code pool.
            if ( m_ip >= m_code_len )
            {
                throw Error( m_module.funcs[m_func_index].name +
                                 ": execution ran past the end of the function",
                             m_module.funcs[m_func_index].name,
                             static_cast<std::int64_t>( m_ip ), m_cursor );
            }
            const Ins& ins = m_module.code[m_code_off + m_ip];
            ++m_ip;
            step( ins );
        }
    }

    void Machine::step( const Ins& ins ) {
        switch ( ins.op )
        {
        // -- control and constants ------------------------------------------
        case Op::HALT: m_halted = true; return;
        case Op::NOP:
        case Op::LINE:
        case Op::DEBUG_PRINT: return;
        case Op::BUF_RESERVE:
            // A hint, not an observable effect: ignore a size no real basket can
            // justify instead of inviting an allocation failure.
            if ( reg( ins.ra ) <= ( std::uint64_t{ 1 } << 24 ) )
            {
                m_buffers[static_cast<std::size_t>( ins.imm1 )].reserve_more(
                    static_cast<std::size_t>( reg( ins.ra ) ) );
            }
            return;
        case Op::CONST:
        case Op::CONST_F: reg( ins.rd ) = static_cast<std::uint64_t>( ins.imm1 ); return;
        case Op::MOV: reg( ins.rd ) = reg( ins.ra ); return;
        case Op::ZEXT8: reg( ins.rd ) = reg( ins.ra ) & 0xFF; return;
        case Op::ZEXT16: reg( ins.rd ) = reg( ins.ra ) & 0xFFFF; return;
        case Op::ZEXT32: reg( ins.rd ) = reg( ins.ra ) & 0xFFFFFFFF; return;
        case Op::SEXT8: reg( ins.rd ) = sign_extend( reg( ins.ra ), 8 ); return;
        case Op::SEXT16: reg( ins.rd ) = sign_extend( reg( ins.ra ), 16 ); return;
        case Op::SEXT32: reg( ins.rd ) = sign_extend( reg( ins.ra ), 32 ); return;

        // -- integer arithmetic ---------------------------------------------
        case Op::ADD: reg( ins.rd ) = reg( ins.ra ) + reg( ins.rb ); return;
        case Op::SUB: reg( ins.rd ) = reg( ins.ra ) - reg( ins.rb ); return;
        case Op::MUL: reg( ins.rd ) = reg( ins.ra ) * reg( ins.rb ); return;
        case Op::DIV_S: {
            const std::int64_t divisor = to_signed( reg( ins.rb ) );
            reg( ins.rd )              = divisor == 0 ? 0
                                                      : static_cast<std::uint64_t>( trunc_div(
                                               to_signed( reg( ins.ra ) ), divisor ) );
            return;
        }
        case Op::DIV_U:
            reg( ins.rd ) = reg( ins.rb ) == 0 ? 0 : reg( ins.ra ) / reg( ins.rb );
            return;
        case Op::MOD_S: {
            const std::int64_t a        = to_signed( reg( ins.ra ) );
            const std::int64_t b        = to_signed( reg( ins.rb ) );
            const std::int64_t quotient = trunc_div( a, b );
            reg( ins.rd ) = b == 0 ? 0 : static_cast<std::uint64_t>( a - quotient * b );
            return;
        }
        case Op::MOD_U:
            reg( ins.rd ) = reg( ins.rb ) == 0 ? 0 : reg( ins.ra ) % reg( ins.rb );
            return;
        case Op::NEG: reg( ins.rd ) = ~reg( ins.ra ) + 1; return;
        case Op::SHL: reg( ins.rd ) = reg( ins.ra ) << ( reg( ins.rb ) & 63 ); return;
        case Op::SHR_S:
            // An arithmetic shift: the compiler rounds towards minus infinity for
            // a signed left operand, which is what a program expects.
            reg( ins.rd ) = static_cast<std::uint64_t>( to_signed( reg( ins.ra ) ) >>
                                                        ( reg( ins.rb ) & 63 ) );
            return;
        case Op::SHR_U: reg( ins.rd ) = reg( ins.ra ) >> ( reg( ins.rb ) & 63 ); return;
        case Op::AND: reg( ins.rd ) = reg( ins.ra ) & reg( ins.rb ); return;
        case Op::OR: reg( ins.rd ) = reg( ins.ra ) | reg( ins.rb ); return;
        case Op::XOR: reg( ins.rd ) = reg( ins.ra ) ^ reg( ins.rb ); return;
        case Op::NOT: reg( ins.rd ) = ~reg( ins.ra ); return;
        case Op::NOT_B: reg( ins.rd ) = reg( ins.ra ) == 0 ? 1 : 0; return;

        // -- comparison -------------------------------------------------------
        case Op::CMP_EQ: reg( ins.rd ) = reg( ins.ra ) == reg( ins.rb ) ? 1 : 0; return;
        case Op::CMP_NE: reg( ins.rd ) = reg( ins.ra ) != reg( ins.rb ) ? 1 : 0; return;
        case Op::CMP_LT_S:
            reg( ins.rd ) = to_signed( reg( ins.ra ) ) < to_signed( reg( ins.rb ) ) ? 1 : 0;
            return;
        case Op::CMP_LE_S:
            reg( ins.rd ) = to_signed( reg( ins.ra ) ) <= to_signed( reg( ins.rb ) ) ? 1 : 0;
            return;
        case Op::CMP_GT_S:
            reg( ins.rd ) = to_signed( reg( ins.ra ) ) > to_signed( reg( ins.rb ) ) ? 1 : 0;
            return;
        case Op::CMP_GE_S:
            reg( ins.rd ) = to_signed( reg( ins.ra ) ) >= to_signed( reg( ins.rb ) ) ? 1 : 0;
            return;
        case Op::CMP_LT_U: reg( ins.rd ) = reg( ins.ra ) < reg( ins.rb ) ? 1 : 0; return;
        case Op::CMP_LE_U: reg( ins.rd ) = reg( ins.ra ) <= reg( ins.rb ) ? 1 : 0; return;
        case Op::CMP_GT_U: reg( ins.rd ) = reg( ins.ra ) > reg( ins.rb ) ? 1 : 0; return;
        case Op::CMP_GE_U: reg( ins.rd ) = reg( ins.ra ) >= reg( ins.rb ) ? 1 : 0; return;

        // -- control flow -----------------------------------------------------
        case Op::JMP: m_ip = static_cast<std::size_t>( ins.imm1 ); return;
        case Op::JZ:
            if ( reg( ins.ra ) == 0 ) { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::JNZ:
            if ( reg( ins.ra ) != 0 ) { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_EQ:
            if ( reg( ins.ra ) == reg( ins.rb ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_NE:
            if ( reg( ins.ra ) != reg( ins.rb ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_LT_S:
            if ( to_signed( reg( ins.ra ) ) < to_signed( reg( ins.rb ) ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_LE_S:
            if ( to_signed( reg( ins.ra ) ) <= to_signed( reg( ins.rb ) ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_GT_S:
            if ( to_signed( reg( ins.ra ) ) > to_signed( reg( ins.rb ) ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_GE_S:
            if ( to_signed( reg( ins.ra ) ) >= to_signed( reg( ins.rb ) ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_LT_U:
            if ( reg( ins.ra ) < reg( ins.rb ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_LE_U:
            if ( reg( ins.ra ) <= reg( ins.rb ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_GT_U:
            if ( reg( ins.ra ) > reg( ins.rb ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::BR_GE_U:
            if ( reg( ins.ra ) >= reg( ins.rb ) )
            { m_ip = static_cast<std::size_t>( ins.imm1 ); }
            return;
        case Op::CALL: {
            if ( m_frames.size() >= kMaxDepth )
            { fail<StackOverflow>( "call depth above " + std::to_string( kMaxDepth ) ); }
            const std::size_t callee_id  = static_cast<std::size_t>( ins.imm1 );
            const FuncDecl& callee       = m_module.funcs[callee_id];
            const std::uint64_t argument = ins.ra < m_reg_count ? reg( ins.ra ) : 0;

            Frame frame;
            frame.func     = m_func_index;
            frame.ip       = m_ip;
            frame.dst      = ins.rd;
            frame.nb_depth = m_nb_stack.size();
            m_frames.push_back( frame );

            // Switch to the callee's window instead of moving the caller's: the
            // caller's registers stay where they are until RET switches back.
            ++m_depth;
            m_func_index = callee_id;
            m_ip         = 0;
            m_reg_count  = callee.reg_count;
            m_code_off   = callee.code_off;
            m_code_len   = callee.code_len;
            std::fill_n( reg_at( m_depth ), callee.reg_count, 0 );
            if ( callee.arity != 0 && callee.reg_count != 0 ) { reg( 0 ) = argument; }
            return;
        }
        case Op::RET: {
            const std::uint64_t value = ins.ra < m_reg_count ? reg( ins.ra ) : 0;
            if ( m_frames.empty() )
            {
                require_nb_depth( 0 );
                m_halted = true;
                return;
            }
            const Frame frame = m_frames.back();
            m_frames.pop_back();

            require_nb_depth( frame.nb_depth );
            --m_depth;
            m_func_index           = frame.func;
            m_ip                   = frame.ip;
            const FuncDecl& caller = m_module.funcs[m_func_index];
            m_reg_count            = caller.reg_count;
            m_code_off             = caller.code_off;
            m_code_len             = caller.code_len;
            reg( frame.dst )       = value;
            return;
        }

        // -- stream access -----------------------------------------------------
        case Op::SREAD: {
            const Typecode typecode = static_cast<Typecode>( ins.imm1 );
            reg( ins.rd )           = read_register( typecode );
            return;
        }
        case Op::SREAD_FNB: {
            const std::uint64_t value = read_bits( 4 );
            if ( ( value & 0x40000000 ) == 0 )
            { // kByteCountMask
                fail<InvalidByteCount>( "fNBytes=" + std::to_string( value ) +
                                        " lacks kByteCountMask" );
            }
            reg( ins.rd ) = value & ~static_cast<std::uint64_t>( 0x40000000 );
            return;
        }
        case Op::SREAD_VERSION: reg( ins.rd ) = sign_extend( read_bits( 2 ), 16 ); return;
        case Op::SREAD_CSTR: {
            const std::size_t start = m_cursor;
            while ( m_cursor < m_data_len && m_data[m_cursor] != 0 ) { ++m_cursor; }
            if ( m_cursor >= m_data_len )
            {
                m_cursor = start;
                fail<StreamUnderflow>( "unterminated string" );
            }
            const std::string text( reinterpret_cast<const char*>( m_data + start ),
                                    m_cursor - start );
            ++m_cursor;
            reg( ins.rd ) = intern( text );
            return;
        }
        case Op::SKIP:
            need( static_cast<std::size_t>( ins.imm1 ) );
            m_cursor += static_cast<std::size_t>( ins.imm1 );
            return;
        case Op::SKIP_R: {
            const std::uint64_t nbytes = reg( ins.ra );
            need( static_cast<std::size_t>( nbytes ) );
            m_cursor += static_cast<std::size_t>( nbytes );
            return;
        }
        case Op::SKIP_VERSION:
            need( 2 );
            m_cursor += 2;
            return;
        case Op::SKIP_FNB: {
            const std::uint64_t value = read_bits( 4 );
            if ( ( value & 0x40000000 ) == 0 )
            {
                fail<InvalidByteCount>( "fNBytes=" + std::to_string( value ) +
                                        " lacks kByteCountMask" );
            }
            return;
        }
        case Op::FNB_BEGIN: {
            const std::uint64_t value = read_bits( 4 );
            if ( ( value & 0x40000000 ) == 0 )
            {
                fail<InvalidByteCount>( "fNBytes=" + std::to_string( value ) +
                                        " lacks kByteCountMask" );
            }
            if ( m_nb_stack.size() >= kMaxDepth )
            { fail<StackOverflow>( "byte-count depth above " + std::to_string( kMaxDepth ) ); }
            m_nb_stack.push_back( static_cast<std::uint64_t>( m_cursor ) +
                                  ( value & ~static_cast<std::uint64_t>( 0x40000000 ) ) );
            return;
        }
        case Op::FNB_END: {
            if ( m_nb_stack.empty() )
            { fail<Error>( "FNB_END with no open byte-count frame" ); }
            const std::uint64_t expected = m_nb_stack.back();
            m_nb_stack.pop_back();
            if ( m_cursor != expected )
            {
                fail<AssertionFailed>( message_of( ins.imm1 ) + " (expected " +
                                       std::to_string( expected ) + ", got " +
                                       std::to_string( m_cursor ) + ")" );
            }
            return;
        }
        case Op::SKIP_CSTR: skip_cstr(); return;
        case Op::SKIP_TOBJECT: {
            // fVersion (2) + fUniqueID (4), then a TObject-style header whose
            // kIsReferenced bit adds a 2-byte reference number.
            need( 6 );
            m_cursor += 6;
            const std::uint64_t bits = read_bits( 4 );
            if ( ( bits & ( 1u << 4 ) ) != 0 ) // kIsReferenced
            {
                need( 2 );
                m_cursor += 2;
            }
            return;
        }
        case Op::SKIP_OBJHDR: {
            const std::uint64_t value = read_bits( 4 );
            if ( ( value & 0x40000000 ) == 0 ) // kByteCountMask
            {
                fail<InvalidByteCount>( "fNBytes=" + std::to_string( value ) +
                                        " lacks kByteCountMask" );
            }
            const std::uint64_t tag = read_bits( 4 );
            if ( tag == 0xFFFFFFFF ) { skip_cstr(); } // kNewClassTag
            return;
        }
        case Op::CURSOR: reg( ins.rd ) = m_cursor; return;
        case Op::SET_CURSOR: {
            const std::uint64_t value = reg( ins.ra );
            if ( value > m_data_len )
            {
                fail<StreamUnderflow>( "cursor " + std::to_string( value ) +
                                       " outside the basket" );
            }
            m_cursor = static_cast<std::size_t>( value );
            return;
        }
        case Op::ENTRIES: reg( ins.rd ) = m_n_offsets - 1; return;
        case Op::INIT_OFFSET:
            reg( ins.rd ) = static_cast<std::uint64_t>( m_cursor_offset );
            return;
        case Op::DATA_SIZE:
            reg( ins.rd ) =
                static_cast<std::uint64_t>( m_offsets[m_event + 1] - m_offsets[m_event] );
            return;
        case Op::OFFSET_AT: {
            const std::size_t index = static_cast<std::size_t>( reg( ins.ra ) );
            if ( index >= m_n_offsets )
            {
                fail<ReadLengthMismatch>( "entry offset " + std::to_string( index ) +
                                          " does not exist" );
            }
            reg( ins.rd ) = static_cast<std::uint64_t>( m_offsets[index] );
            return;
        }
        case Op::OFFSET_SEEK: {
            const std::int64_t value  = to_signed( reg( ins.ra ) );
            const std::int64_t* first = m_offsets;
            const std::int64_t* last  = m_offsets + m_n_offsets;
            const std::int64_t* found = std::upper_bound( first, last, value );
            if ( found == last )
            { fail<ReadLengthMismatch>( "no entry offset after " + std::to_string( value ) ); }
            reg( ins.rd ) = static_cast<std::uint64_t>( *found );
            return;
        }
        case Op::EVENT_END:
            reg( ins.rd ) = static_cast<std::uint64_t>( m_offsets[m_event + 1] );
            return;

        // -- coarse-grained reads ------------------------------------------------
        case Op::READ_BULK: {
            const std::uint64_t count = reg( ins.ra );
            if ( count == 0 || static_cast<std::int64_t>( count ) < 0 ) { return; }
            const Typecode typecode = static_cast<Typecode>( ins.imm2 );
            const std::size_t size  = typecode_size( typecode );
            if ( count > std::numeric_limits<std::size_t>::max() / size )
            {
                fail<StreamUnderflow>( "bulk count " + std::to_string( count ) +
                                       " is too large" );
            }
            need( static_cast<std::size_t>( count ) * size );
            m_buffers[static_cast<std::size_t>( ins.imm1 )].append_stream(
                m_data + m_cursor, static_cast<std::size_t>( count ), typecode );
            m_cursor += static_cast<std::size_t>( count ) * size;
            return;
        }
        case Op::READ_BULK_UNTIL: {
            const std::uint64_t end = reg( ins.ra );
            const Typecode typecode = static_cast<Typecode>( ins.imm2 );
            const std::size_t size  = typecode_size( typecode );
            if ( end < m_cursor )
            {
                fail<ReadLengthMismatch>( "bulk end " + std::to_string( end ) +
                                          " is behind the cursor " +
                                          std::to_string( m_cursor ) );
            }
            const std::size_t span = static_cast<std::size_t>( end ) - m_cursor;
            if ( size == 0 || span % size != 0 )
            {
                fail<ReadLengthMismatch>( std::to_string( span ) +
                                          " bytes are not a whole number of " +
                                          std::to_string( size ) + "-byte elements" );
            }
            if ( span != 0 )
            {
                need( span );
                m_buffers[static_cast<std::size_t>( ins.imm1 )].append_stream(
                    m_data + m_cursor, span / size, typecode );
                m_cursor += span;
            }
            return;
        }
        case Op::READ_STRING: {
            Buffer& chars    = m_buffers[static_cast<std::size_t>( ins.imm1 )];
            Buffer& offsets  = m_buffers[static_cast<std::size_t>( ins.imm2 )];
            std::size_t size = static_cast<std::size_t>( read_bits( 1 ) );
            if ( size == 255 ) { size = static_cast<std::size_t>( read_bits( 4 ) ); }
            need( size );
            chars.append_stream( m_data + m_cursor, size, Typecode::UINT8 );
            m_cursor += size;
            const std::uint64_t end = ins.rb == 0 ? chars.count() : offsets.last_bits() + size;
            offsets.append_bits( end );
            return;
        }
        case Op::READ_STRIDED: {
            const std::uint64_t count        = reg( ins.ra );
            const std::int64_t signed_stride = to_signed( reg( ins.rb ) );
            if ( count == 0 || static_cast<std::int64_t>( count ) < 0 ) { return; }
            const Typecode typecode = static_cast<Typecode>( ins.imm2 );
            const std::size_t size  = typecode_size( typecode );
            if ( signed_stride < static_cast<std::int64_t>( size ) )
            {
                fail<StreamUnderflow>( "stride " + std::to_string( signed_stride ) +
                                       " below element size " + std::to_string( size ) );
            }
            const std::size_t stride = static_cast<std::size_t>( signed_stride );
            if ( count > 1 &&
                 stride > ( std::numeric_limits<std::size_t>::max() - size ) / ( count - 1 ) )
            { fail<StreamUnderflow>( "strided span is too large" ); }
            need( static_cast<std::size_t>( count - 1 ) * stride + size );
            Buffer& buffer = m_buffers[static_cast<std::size_t>( ins.imm1 )];
            for ( std::uint64_t i = 0; i < count; ++i )
            { buffer.append_stream( m_data + m_cursor + i * stride, 1, typecode ); }
            m_cursor += static_cast<std::size_t>( count - 1 ) * stride + size;
            return;
        }
        case Op::READ_APPEND: {
            const Typecode typecode  = static_cast<Typecode>( ins.imm2 );
            const std::uint64_t bits = read_register( typecode );
            m_buffers[static_cast<std::size_t>( ins.imm1 )].append_bits( bits );
            return;
        }
        case Op::READ_FIELDS: {
            const std::int64_t count = to_signed( reg( ins.ra ) );
            const Layout& layout     = m_module.layouts[static_cast<std::size_t>( ins.imm1 )];
            for ( std::int64_t i = 0; i < count; ++i )
            {
                for ( const LayoutField& field : layout.fields )
                {
                    switch ( field.kind )
                    {
                    case FieldKind::SKIP:
                        need( static_cast<std::size_t>( field.count ) );
                        m_cursor += static_cast<std::size_t>( field.count );
                        break;
                    case FieldKind::SCALAR: {
                        const std::uint64_t bits = read_register( field.typecode );
                        m_buffers[static_cast<std::size_t>( field.buffer_id )].append_bits(
                            bits );
                        break;
                    }
                    case FieldKind::ARRAY_FIXED: {
                        const std::size_t size = typecode_size( field.typecode );
                        const auto elements    = static_cast<std::size_t>( field.count );
                        if ( elements > std::numeric_limits<std::size_t>::max() / size )
                        { fail<StreamUnderflow>( "layout array is too large" ); }
                        need( elements * size );
                        m_buffers[static_cast<std::size_t>( field.buffer_id )].append_stream(
                            m_data + m_cursor, elements, field.typecode );
                        m_cursor += elements * size;
                        break;
                    }
                    default:
                        fail<UnsupportedFeature>(
                            std::string( "layout field kind " ) +
                            std::to_string( static_cast<int>( field.kind ) ) );
                    }
                }
            }
            return;
        }

        // -- buffer access ---------------------------------------------------------
        case Op::BUF_APPEND:
            m_buffers[static_cast<std::size_t>( ins.imm1 )].append_bits( reg( ins.ra ) );
            return;
        case Op::BUF_APPEND_CONST:
            m_buffers[static_cast<std::size_t>( ins.imm1 )].append_bits(
                static_cast<std::uint64_t>( ins.imm2 ) );
            return;
        case Op::BUF_APPEND_OFFSET: {
            Buffer& buffer = m_buffers[static_cast<std::size_t>( ins.imm1 )];
            buffer.append_bits( buffer.last_bits() + reg( ins.ra ) );
            return;
        }
        case Op::BUF_APPEND_LEN: {
            Buffer& target       = m_buffers[static_cast<std::size_t>( ins.imm1 )];
            const Buffer& source = m_buffers[static_cast<std::size_t>( ins.imm2 )];
            target.append_bits( source.count() );
            return;
        }
        case Op::BUF_LAST:
            reg( ins.rd ) = m_buffers[static_cast<std::size_t>( ins.imm1 )].last_bits();
            return;
        case Op::BUF_LEN:
            reg( ins.rd ) = m_buffers[static_cast<std::size_t>( ins.imm1 )].count();
            return;

        // -- program state -----------------------------------------------------------
        case Op::STATE_GET:
            reg( ins.rd ) = m_states[static_cast<std::size_t>( ins.imm1 )];
            return;
        case Op::STATE_SET:
            m_states[static_cast<std::size_t>( ins.imm1 )] = reg( ins.ra );
            return;
        case Op::STATE_IADD:
            m_states[static_cast<std::size_t>( ins.imm1 )] +=
                static_cast<std::uint64_t>( ins.imm2 );
            return;

        // -- object references ------------------------------------------------
        // A reference is (kind, object index, class id) with kind 0 = object and
        // 1 = class, and -1 for the field it does not carry.
        case Op::REF_HAS: reg( ins.rd ) = m_refs.count( reg( ins.ra ) ) != 0 ? 1 : 0; return;
        case Op::REF_GET_OBJ: {
            const auto found = m_refs.find( reg( ins.ra ) );
            reg( ins.rd ) =
                found == m_refs.end() ? 0 : static_cast<std::uint64_t>( found->second.object );
            return;
        }
        case Op::REF_GET_CLASS: {
            const auto found = m_refs.find( reg( ins.ra ) );
            reg( ins.rd )    = found == m_refs.end()
                                   ? kMask64
                                   : static_cast<std::uint64_t>( found->second.class_id );
            return;
        }
        case Op::REF_SET_OBJ: {
            RefEntry entry;
            entry.object          = to_signed( reg( ins.rb ) );
            m_refs[reg( ins.ra )] = entry;
            return;
        }
        case Op::REF_SET_CLASS: {
            RefEntry entry;
            entry.class_id        = to_signed( reg( ins.rb ) );
            m_refs[reg( ins.ra )] = entry;
            return;
        }
        case Op::REF_COUNT:
            reg( ins.rd ) = static_cast<std::uint64_t>( m_refs.size() );
            return;

        // -- strings -------------------------------------------------------------
        case Op::STR_CONST: reg( ins.rd ) = static_cast<std::uint64_t>( ins.imm1 ); return;
        case Op::STR_EQ: {
            const std::string* left  = string_of( reg( ins.ra ) );
            const std::string* right = string_of( reg( ins.rb ) );
            reg( ins.rd ) = ( left != nullptr && right != nullptr && *left == *right ) ? 1 : 0;
            return;
        }
        case Op::STR_STORE:
            m_states[static_cast<std::size_t>( ins.imm1 )] = reg( ins.ra );
            return;
        case Op::STR_LOAD:
            reg( ins.rd ) = m_states[static_cast<std::size_t>( ins.imm1 )];
            return;

        // -- verification ------------------------------------------------------------
        case Op::ASSERT:
            if ( reg( ins.ra ) == 0 ) { fail<AssertionFailed>( message_of( ins.imm1 ) ); }
            return;
        case Op::ASSERT_CURSOR_EQ:
            if ( m_cursor != reg( ins.ra ) )
            {
                fail<AssertionFailed>( message_of( ins.imm1 ) + " (expected " +
                                       std::to_string( reg( ins.ra ) ) + ", got " +
                                       std::to_string( m_cursor ) + ")" );
            }
            return;
        case Op::ASSERT_STR_EQ: {
            const std::string* left  = string_of( reg( ins.ra ) );
            const std::string* right = string_of( reg( ins.rb ) );
            if ( left == nullptr || right == nullptr || *left != *right )
            {
                fail<AssertionFailed>(
                    message_of( ins.imm1 ) + " (expected '" +
                    ( right == nullptr ? std::string( "<none>" ) : *right ) + "', got '" +
                    ( left == nullptr ? std::string( "<none>" ) : *left ) + "')" );
            }
            return;
        }
        case Op::THROW: fail<AssertionFailed>( message_of( ins.imm1 ) ); return;

        // -- not in this build ---------------------------------------------------------
        default:
            fail<UnsupportedFeature>( std::string( "opcode " ) +
                                      std::string( op_name( ins.op ) ) +
                                      " is not implemented" );
        }
    }

    std::string Machine::message_of( std::int64_t ident ) const {
        if ( ident >= 0 && static_cast<std::size_t>( ident ) < m_module.strings.size() )
        { return m_module.strings[static_cast<std::size_t>( ident )]; }
        return "<message " + std::to_string( ident ) + ">";
    }

} // namespace detree
