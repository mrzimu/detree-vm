#ifndef DETREE_ERRORS_HPP
#define DETREE_ERRORS_HPP

#include <cstddef>
#include <cstdint>
#include <stdexcept>
#include <string>
#include <utility>

namespace detree {
    /// Offset used when a failure happened before any byte was consumed.
    inline constexpr std::size_t kNoCursor = static_cast<std::size_t>( -1 );

    /// Base of every failure a module can cause at run time.
    class Error : public std::runtime_error {
      public:
        explicit Error( std::string message, std::string function = {}, std::int64_t ip = -1,
                        std::size_t cursor = kNoCursor )
            : std::runtime_error( std::move( message ) )
            , m_function( std::move( function ) )
            , m_ip( ip )
            , m_cursor( cursor ) {}

        /// Function name the failure happened in, or empty when unknown.
        const std::string& function() const noexcept { return m_function; }
        /// Instruction index inside that function, or ``-1`` when unknown.
        std::int64_t ip() const noexcept { return m_ip; }
        /// Byte offset of the stream when the failure happened.
        std::size_t cursor() const noexcept { return m_cursor; }

      private:
        std::string m_function;
        std::int64_t m_ip;
        std::size_t m_cursor;
    };

    /// The stream ended before the module finished reading a value.
    class StreamUnderflow : public Error {
      public:
        using Error::Error;
    };

    /// An ``fNBytes`` field lacks ``kByteCountMask``.
    class InvalidByteCount : public Error {
      public:
        using Error::Error;
    };

    /// A record did not end where the module said it would.
    class ReadLengthMismatch : public Error {
      public:
        using Error::Error;
    };

    /// The module uses an instruction this machine does not implement.
    class UnsupportedFeature : public Error {
      public:
        using Error::Error;
    };

    /// An ``ASSERT``/``THROW`` instruction fired: the data is not as expected.
    class AssertionFailed : public Error {
      public:
        using Error::Error;
    };

    /// Calls nested deeper than the frame limit.
    class StackOverflow : public Error {
      public:
        using Error::Error;
    };

    /// A module that cannot be decoded or executed as written.
    class ModuleError : public std::runtime_error {
      public:
        using std::runtime_error::runtime_error;
    };

} // namespace detree

#endif // DETREE_ERRORS_HPP
