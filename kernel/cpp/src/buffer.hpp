#ifndef DETREE_BUFFER_HPP
#define DETREE_BUFFER_HPP

#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>

#include "isa.hpp"
#include "module.hpp"

namespace detree {
    /**
     * @brief A buffer's storage, behind an interface a caller can keep alive.
     *
     * One implementation per dtype exists (a ``std::vector<T>``), chosen once in
     * :meth:`Buffer::Buffer`, so appending never switches on the element width
     * again. A caller may hold the ``shared_ptr`` after :meth:`Machine::reset`
     * replaced the storage: the old vector outlives the buffer object, which is
     * what keeps a view of a previous run's bytes valid.
     */
    class BufferStorage {
      public:
        virtual ~BufferStorage() = default;

        /// Append the bytes a register value carries, as the dtype stores them.
        virtual void append_bits( std::uint64_t value ) = 0;

        /// Append a real number: ``READ_BULK`` and ``READ_FIELDS`` decode floats.
        virtual void append_value( double value ) = 0;

        /**
         * @brief Append @p count big-endian stream elements of @p typecode.
         *
         * The buffer's own dtype need not equal the stream's: the value is
         * converted the way the Python reference converts it (an integer
         * buffer truncates a float, a float buffer widens an integer).
         */
        virtual void append_stream( const std::uint8_t* raw, std::size_t count,
                                    Typecode typecode ) = 0;

        /// Reserve room for @p extra more elements (``BUF_RESERVE``).
        virtual void reserve_more( std::size_t extra ) = 0;

        /// The last element as a register value; ``0`` when the buffer is empty.
        virtual std::uint64_t last_bits() const = 0;

        virtual std::uint64_t count() const noexcept       = 0;
        virtual std::size_t element_size() const noexcept  = 0;
        virtual const std::uint8_t* bytes() const noexcept = 0;
        virtual std::size_t size_bytes() const noexcept    = 0;
    };

    /// A storage a caller may keep alive, and read the bytes of.
    using BufferStoragePtr = std::shared_ptr<const BufferStorage>;

    /**
     * @brief One output buffer: typed element storage plus the dtype it holds.
     *
     * The machine appends through :meth:`append_bits`, which takes the value a
     * register carries and stores it the way the declared dtype stores it, so
     * integers wrap to the width and a float buffer reinterprets the IEEE-754
     * bit pattern ``SREAD`` loaded.
     */
    class Buffer {
      public:
        /// @throws ModuleError if @p decl names a dtype this machine cannot hold.
        explicit Buffer( const BufferDecl& decl );

        /// Drop every element and restore the declared initial values.
        void reset();

        /// Append the value @p value a register carries, coerced to the dtype.
        void append_bits( std::uint64_t value );

        /// Append a real number: ``READ_BULK`` decodes floats into their value.
        void append_value( double value );

        /**
         * @brief Append @p count big-endian stream elements of @p typecode.
         *
         * @p typecode is the *stream* element type, which need not equal the
         * buffer's own: ``READ_BULK`` reads a typecode and appends into a
         * declared dtype, so the two are only the same in the common case.
         */
        void append_stream( const std::uint8_t* raw, std::size_t count, Typecode typecode );

        /// Reserve room for @p extra more elements (``BUF_RESERVE``).
        void reserve_more( std::size_t extra );

        /// The last element as a register value; ``0`` when the buffer is empty.
        std::uint64_t last_bits() const;

        std::uint64_t count() const noexcept { return m_storage->count(); }
        const std::string& name() const noexcept { return m_decl.name; }
        const std::string& dtype() const noexcept { return m_decl.dtype; }
        BufferKind kind() const noexcept { return m_decl.kind; }
        Typecode typecode() const noexcept { return m_typecode; }
        std::size_t element_size() const noexcept { return m_esize; }
        const std::uint8_t* bytes() const noexcept { return m_storage->bytes(); }
        std::size_t size_bytes() const noexcept { return m_storage->size_bytes(); }

        /// The storage, so a caller can keep a view of it alive.
        BufferStoragePtr storage() const noexcept { return m_storage; }

      private:
        BufferDecl m_decl;
        Typecode m_typecode = Typecode::UINT8;
        std::size_t m_esize = 1;
        std::shared_ptr<BufferStorage> m_storage;
    };

} // namespace detree

#endif // DETREE_BUFFER_HPP
