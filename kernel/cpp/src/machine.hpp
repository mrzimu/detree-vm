#ifndef DETREE_MACHINE_HPP
#define DETREE_MACHINE_HPP

#include <cstddef>
#include <cstdint>
#include <string>
#include <unordered_map>
#include <vector>

#include "buffer.hpp"
#include "errors.hpp"
#include "isa.hpp"
#include "module.hpp"

namespace detree {
    /**
     * @brief A Detree machine for one module, reused basket after basket.
     *
     * The machine owns the per-basket event loop, so a call from another language
     * is one call per basket. Building it decodes and validates the module --
     * the expensive part -- and :meth:`run` then resets and reads, which is why the
     * same instance serves every basket of a branch.
     *
     * Registers are 64-bit slots: integers are two's complement and wrap modulo
     * :math:`2^{64}`, floats are only *moved*, so a float typecode puts its
     * IEEE-754 bit pattern in the register and a buffer reinterprets it.
     *
     * One machine is not thread-safe; the register window, the cursor and the
     * buffers are instance state.
     */
    class Machine {
      public:
        /// Call depth limit, matching the Python machine's ``MAX_DEPTH``.
        static constexpr std::size_t kMaxDepth = 256;

        /**
         * @brief Decode and validate a serialized module.
         * @throws ModuleError if the module cannot be decoded or is not executable.
         */
        explicit Machine( const std::uint8_t* raw, std::size_t size );

        /**
         * @brief Return to the state just after construction: nothing read yet.
         *
         * Fresh storage is allocated rather than the old buffers being cleared,
         * because a caller may still hold a view of what a previous run produced.
         */
        void reset();

        /**
         * @brief Read every event of one basket.
         *
         * @param data          the decompressed basket body
         * @param size          its length in bytes
         * @param offsets       ``fEntryOffset``-style cumulative entry offsets
         * @param n_offsets     how many offsets there are; at least two
         * @param cursor_offset the key length to start object references at
         *
         * @throws Error (a subclass per kind) if the data does not match the module,
         *         and ReadLengthMismatch if an event does not end at its offset.
         */
        void run( const std::uint8_t* data, std::size_t size, const std::int64_t* offsets,
                  std::size_t n_offsets, std::size_t cursor_offset );

        const Module& module() const noexcept { return m_module; }

        std::size_t buffer_count() const noexcept { return m_buffers.size(); }
        const Buffer& buffer( std::size_t index ) const { return m_buffers.at( index ); }

        std::size_t state_count() const noexcept { return m_states.size(); }
        std::uint64_t state( std::size_t index ) const { return m_states.at( index ); }
        const StateDecl& state_decl( std::size_t index ) const {
            return m_module.states.at( index );
        }

        /**
         * @brief A state slot as the Python machine reports it.
         *
         * An integer slot is its 64-bit value; a name slot is the interned string
         * (``nullptr`` for ``-1``, "no name").
         */
        struct StateValue {
            std::uint64_t raw       = 0;
            const std::string* text = nullptr;
        };
        StateValue state_value( std::size_t index ) const;

      private:
        /// One saved caller: where to resume and which register receives the result.
        ///
        /// The registers themselves are not saved: each call depth owns a
        /// ``max_regs``-wide window of :member:`m_reg_pool`, so a call switches
        /// windows instead of copying one.
        struct Frame {
            std::size_t func = 0;
            std::size_t ip   = 0;
            std::uint8_t dst = 0;
        };

        /// What a reference tag points at: an object, or a class.
        struct RefEntry {
            std::int64_t object   = -1;
            std::int64_t class_id = -1;
        };

        void loop();
        void step( const Ins& ins );

        /// Throw @p Exception with the current function, instruction and cursor.
        template <typename Exception>
        [[noreturn]] void fail( std::string message ) {
            throw Exception( std::move( message ), m_module.funcs[m_func_index].name,
                             static_cast<std::int64_t>( m_ip ) - 1, m_cursor );
        }

        void need( std::size_t nbytes );
        /// Read @p size big-endian bytes as one register value.
        std::uint64_t read_bits( std::size_t size );
        /// Read one scalar the way the Python reference loads a register:
        /// signed typecodes sign-extend, floats keep their bit pattern.
        std::uint64_t read_register( Typecode typecode );

        /// The first register slot of call depth @p depth.
        std::uint64_t* reg_at( std::size_t depth ) noexcept {
            return m_reg_pool.data() + depth * m_max_regs;
        }
        std::uint64_t& reg( std::uint8_t index ) noexcept { return reg_at( m_depth )[index]; }
        /// The module's message string @p ident, for an ``ASSERT`` or a ``THROW``.
        std::string message_of( std::int64_t ident ) const;

        /// Intern @p text, returning its id; the arena is per run.
        std::uint64_t intern( const std::string& text );
        /// The string a register id names, or ``nullptr`` for ``-1``.
        const std::string* string_of( std::uint64_t ident ) const;
        /// Skip a NUL-terminated string at the cursor (``SKIP_CSTR``).
        void skip_cstr();

        Module m_module;
        std::vector<Buffer> m_buffers;
        std::vector<std::uint64_t> m_states;
        std::vector<std::string> m_strings;
        std::unordered_map<std::string, std::uint64_t> m_str_ids;
        std::unordered_map<std::uint64_t, RefEntry> m_refs;

        const std::uint8_t* m_data    = nullptr;
        std::size_t m_data_len        = 0;
        const std::int64_t* m_offsets = nullptr;
        std::size_t m_n_offsets       = 0;
        std::size_t m_cursor          = 0;
        std::int64_t m_cursor_offset  = 0;
        std::size_t m_event           = 0;
        std::size_t m_entry_end       = 0;

        std::size_t m_func_index = 0;
        std::size_t m_ip         = 0;
        bool m_halted            = true;

        /// One window per call depth; ``CALL`` moves to the next instead of copying.
        std::vector<std::uint64_t> m_reg_pool;
        std::size_t m_max_regs  = 1;
        std::size_t m_reg_count = 0;
        std::size_t m_depth     = 0;
        std::size_t m_code_off  = 0;
        std::size_t m_code_len  = 0;
        std::vector<Frame> m_frames;
    };

} // namespace detree

#endif // DETREE_MACHINE_HPP
