// Typed output storage. The dtype dispatch happens once, when the machine is
// built; appending is then a typed push and a byte swap.

#include "buffer.hpp"
#include "errors.hpp"

#include <cmath>
#include <cstring>
#include <type_traits>

namespace detree {
    namespace {
        /// Big-endian unsigned value of @p width bytes at @p raw.
        std::uint64_t read_be( const std::uint8_t* raw, std::size_t width ) noexcept {
            std::uint64_t value = 0;
            for ( std::size_t i = 0; i < width; ++i ) { value = ( value << 8 ) | raw[i]; }
            return value;
        }

        constexpr bool is_float_typecode( Typecode tc ) noexcept {
            return tc == Typecode::FLOAT32 || tc == Typecode::FLOAT64;
        }

        constexpr bool is_signed_typecode( Typecode tc ) noexcept {
            switch ( tc )
            {
            case Typecode::INT8:
            case Typecode::INT16:
            case Typecode::INT32:
            case Typecode::INT64: return true;
            default: return false;
            }
        }

        /// Sign-extend the low @p bits of @p value to 64 bits.
        std::uint64_t sign_extend_bits( std::uint64_t value, unsigned bits ) noexcept {
            const std::uint64_t mask = ( bits >= 64 ) ? ~static_cast<std::uint64_t>( 0 )
                                                      : ( ( std::uint64_t{ 1 } << bits ) - 1 );
            value &= mask;
            if ( value & ( std::uint64_t{ 1 } << ( bits - 1 ) ) ) { value |= ~mask; }
            return value;
        }

        /// The real value of one big-endian float element.
        double decode_float( const std::uint8_t* raw, Typecode tc ) noexcept {
            const std::uint64_t bits = read_be( raw, typecode_size( tc ) );
            if ( tc == Typecode::FLOAT32 )
            {
                const std::uint32_t narrow = static_cast<std::uint32_t>( bits );
                float value;
                std::memcpy( &value, &narrow, 4 );
                return static_cast<double>( value );
            }
            double value;
            std::memcpy( &value, &bits, 8 );
            return value;
        }

        /// One big-endian signed integer element, sign-extended to 64 bits.
        std::uint64_t decode_signed( const std::uint8_t* raw, Typecode tc ) noexcept {
            return sign_extend_bits( read_be( raw, typecode_size( tc ) ),
                                     static_cast<unsigned>( typecode_size( tc ) * 8 ) );
        }

        /// One big-endian unsigned integer element.
        std::uint64_t decode_unsigned( const std::uint8_t* raw, Typecode tc ) noexcept {
            return read_be( raw, typecode_size( tc ) );
        }

        /// The value the low bytes of @p bits carry, in this machine's byte order.
        template <typename T>
        T from_bits( std::uint64_t bits ) noexcept {
            if constexpr ( std::is_floating_point_v<T> )
            {
                if constexpr ( sizeof( T ) == 4 )
                {
                    const std::uint32_t narrow = static_cast<std::uint32_t>( bits );
                    T value;
                    std::memcpy( &value, &narrow, 4 );
                    return value;
                }
                else
                {
                    T value;
                    std::memcpy( &value, &bits, 8 );
                    return value;
                }
            }
            else
            {
                // Narrowing through the unsigned type is the wrap a register-to-
                // buffer store has always applied.
                using Unsigned = std::make_unsigned_t<T>;
                return static_cast<T>( static_cast<Unsigned>( bits ) );
            }
        }

        /// One big-endian stream element of @p T's width, in this machine's order.
        template <typename T>
        T from_be( const std::uint8_t* raw ) noexcept {
            std::uint64_t bits = 0;
            for ( std::size_t i = 0; i < sizeof( T ); ++i ) { bits = ( bits << 8 ) | raw[i]; }
            return from_bits<T>( bits );
        }

        /// The register value the last element of a @p T buffer carries.
        template <typename T>
        std::uint64_t to_bits( T value ) noexcept {
            if constexpr ( std::is_floating_point_v<T> )
            {
                // A float buffer's "last value" is an integer read of the value,
                // which is what BUF_LAST and BUF_APPEND_OFFSET see.
                return static_cast<std::uint64_t>( static_cast<std::int64_t>( value ) );
            }
            else if constexpr ( std::is_signed_v<T> )
            { return static_cast<std::uint64_t>( static_cast<std::int64_t>( value ) ); }
            else { return static_cast<std::uint64_t>( value ); }
        }

        /**
         * @brief One dtype's storage: a ``vector<T>`` with the format's coercions.
         *
         * The element width is a compile-time constant here, so appending is one
         * push and one byte swap, not a switch and a per-element ``resize``.
         */
        template <typename T>
        class TypedStorage final : public BufferStorage {
          public:
            explicit TypedStorage( const std::vector<std::int64_t>& init )
                : m_values( std::make_shared<std::vector<T>>() ) {
                m_values->reserve( init.size() );
                for ( const std::int64_t value : init )
                { m_values->push_back( from_bits<T>( static_cast<std::uint64_t>( value ) ) ); }
            }

            void append_bits( std::uint64_t value ) override {
                m_values->push_back( from_bits<T>( value ) );
            }

            void append_value( double value ) override {
                if constexpr ( std::is_floating_point_v<T> )
                { m_values->push_back( static_cast<T>( value ) ); }
                else
                {
                    // An integer buffer receives a truncation towards zero, then
                    // the wrap from_bits applies.
                    const double truncated = std::trunc( value );
                    std::int64_t as_int    = 0;
                    if ( truncated >= -9223372036854775808.0 &&
                         truncated < 9223372036854775808.0 )
                    { as_int = static_cast<std::int64_t>( truncated ); }
                    m_values->push_back(
                        from_bits<T>( static_cast<std::uint64_t>( as_int ) ) );
                }
            }

            void append_stream( const std::uint8_t* raw, std::size_t count,
                                Typecode typecode ) override {
                if ( count == 0 ) { return; }
                const std::size_t width = typecode_size( typecode );
                const std::size_t old   = m_values->size();
                m_values->resize( old + count );
                T* out = m_values->data() + old;

                // The common case: same width and same kind, so a byte swap is
                // all the conversion a register-to-buffer store needs.
                if ( width == sizeof( T ) &&
                     is_float_typecode( typecode ) == std::is_floating_point_v<T> )
                {
                    for ( std::size_t i = 0; i < count; ++i )
                    { out[i] = from_be<T>( raw + i * width ); }
                    return;
                }

                for ( std::size_t i = 0; i < count; ++i )
                {
                    const std::uint8_t* element = raw + i * width;
                    if constexpr ( std::is_floating_point_v<T> )
                    {
                        if ( is_float_typecode( typecode ) )
                        { out[i] = static_cast<T>( decode_float( element, typecode ) ); }
                        else if ( is_signed_typecode( typecode ) )
                        {
                            out[i] = static_cast<T>( static_cast<std::int64_t>(
                                decode_signed( element, typecode ) ) );
                        }
                        else
                        { out[i] = static_cast<T>( decode_unsigned( element, typecode ) ); }
                    }
                    else
                    {
                        if ( is_float_typecode( typecode ) )
                        {
                            // An integer buffer receives a truncation towards
                            // zero, then the wrap from_bits applies.
                            const double truncated =
                                std::trunc( decode_float( element, typecode ) );
                            std::int64_t as_int = 0;
                            if ( truncated >= -9223372036854775808.0 &&
                                 truncated < 9223372036854775808.0 )
                            { as_int = static_cast<std::int64_t>( truncated ); }
                            out[i] = from_bits<T>( static_cast<std::uint64_t>( as_int ) );
                        }
                        else if ( is_signed_typecode( typecode ) )
                        { out[i] = from_bits<T>( decode_signed( element, typecode ) ); }
                        else { out[i] = from_bits<T>( decode_unsigned( element, typecode ) ); }
                    }
                }
            }

            void reserve_more( std::size_t extra ) override {
                m_values->reserve( m_values->size() + extra );
            }

            std::uint64_t last_bits() const override {
                return m_values->empty() ? 0 : to_bits<T>( m_values->back() );
            }

            std::uint64_t count() const noexcept override {
                return static_cast<std::uint64_t>( m_values->size() );
            }

            std::size_t element_size() const noexcept override { return sizeof( T ); }

            const std::uint8_t* bytes() const noexcept override {
                return reinterpret_cast<const std::uint8_t*>( m_values->data() );
            }

            std::size_t size_bytes() const noexcept override {
                return m_values->size() * sizeof( T );
            }

          private:
            std::shared_ptr<std::vector<T>> m_values;
        };

        /// ``bool`` stores one byte per element, but every nonzero value is 1.
        class BoolStorage final : public BufferStorage {
          public:
            explicit BoolStorage( const std::vector<std::int64_t>& init )
                : m_values( std::make_shared<std::vector<std::uint8_t>>() ) {
                m_values->reserve( init.size() );
                for ( const std::int64_t value : init )
                { m_values->push_back( value != 0 ? 1 : 0 ); }
            }

            void append_bits( std::uint64_t value ) override {
                m_values->push_back( value != 0 ? 1 : 0 );
            }

            void append_value( double value ) override {
                m_values->push_back( value != 0.0 ? 1 : 0 );
            }

            void append_stream( const std::uint8_t* raw, std::size_t count,
                                Typecode typecode ) override {
                const std::size_t old = m_values->size();
                m_values->resize( old + count );
                for ( std::size_t i = 0; i < count; ++i )
                {
                    const std::uint8_t* element = raw + i * typecode_size( typecode );
                    m_values->at( old + i ) =
                        ( is_float_typecode( typecode )
                              ? decode_float( element, typecode ) != 0.0
                              : decode_unsigned( element, typecode ) != 0 )
                            ? 1
                            : 0;
                }
            }

            void reserve_more( std::size_t extra ) override {
                m_values->reserve( m_values->size() + extra );
            }

            std::uint64_t last_bits() const override {
                return m_values->empty() ? 0 : m_values->back();
            }

            std::uint64_t count() const noexcept override {
                return static_cast<std::uint64_t>( m_values->size() );
            }

            std::size_t element_size() const noexcept override { return 1; }

            const std::uint8_t* bytes() const noexcept override { return m_values->data(); }

            std::size_t size_bytes() const noexcept override { return m_values->size(); }

          private:
            std::shared_ptr<std::vector<std::uint8_t>> m_values;
        };

        /// The one dispatch on dtype; everything after it is a typed vector.
        std::shared_ptr<BufferStorage> make_storage( Typecode typecode,
                                                     const BufferDecl& decl ) {
            if ( decl.dtype == "bool" ) { return std::make_shared<BoolStorage>( decl.init ); }
            switch ( typecode )
            {
            case Typecode::UINT8:
                return std::make_shared<TypedStorage<std::uint8_t>>( decl.init );
            case Typecode::UINT16:
                return std::make_shared<TypedStorage<std::uint16_t>>( decl.init );
            case Typecode::UINT32:
                return std::make_shared<TypedStorage<std::uint32_t>>( decl.init );
            case Typecode::UINT64:
                return std::make_shared<TypedStorage<std::uint64_t>>( decl.init );
            case Typecode::INT8:
                return std::make_shared<TypedStorage<std::int8_t>>( decl.init );
            case Typecode::INT16:
                return std::make_shared<TypedStorage<std::int16_t>>( decl.init );
            case Typecode::INT32:
                return std::make_shared<TypedStorage<std::int32_t>>( decl.init );
            case Typecode::INT64:
                return std::make_shared<TypedStorage<std::int64_t>>( decl.init );
            case Typecode::FLOAT32: return std::make_shared<TypedStorage<float>>( decl.init );
            case Typecode::FLOAT64: return std::make_shared<TypedStorage<double>>( decl.init );
            }
            throw ModuleError( "buffer '" + decl.name + "': dtype '" + decl.dtype +
                               "' is not a buffer dtype" );
        }

    } // namespace

    Buffer::Buffer( const BufferDecl& decl ) : m_decl( decl ) {
        if ( !buffer_typecode( decl.dtype, m_typecode ) )
        {
            throw ModuleError( "buffer '" + decl.name + "': dtype '" + decl.dtype +
                               "' is not a buffer dtype" );
        }
        m_esize   = typecode_size( m_typecode );
        m_storage = make_storage( m_typecode, m_decl );
    }

    void Buffer::reset() { m_storage = make_storage( m_typecode, m_decl ); }

    void Buffer::append_bits( std::uint64_t value ) { m_storage->append_bits( value ); }

    void Buffer::append_value( double value ) { m_storage->append_value( value ); }

    void Buffer::append_stream( const std::uint8_t* raw, std::size_t count,
                                Typecode typecode ) {
        m_storage->append_stream( raw, count, typecode );
    }

    void Buffer::reserve_more( std::size_t extra ) { m_storage->reserve_more( extra ); }

    std::uint64_t Buffer::last_bits() const { return m_storage->last_bits(); }

} // namespace detree
