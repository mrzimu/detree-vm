#ifndef DETREE_MODULE_HPP
#define DETREE_MODULE_HPP

#include <cstddef>
#include <cstdint>
#include <string>
#include <vector>

#include "isa.hpp"

namespace detree {
    /// One declared output buffer.
    struct BufferDecl {
        std::string name;
        std::string dtype;
        BufferKind kind = BufferKind::DATA;
        std::vector<std::int64_t> init;
    };

    /// One declared state slot: an integer, or a name slot when @c is_string.
    struct StateDecl {
        std::string name;
        std::int64_t init = 0;
        bool is_string    = false;
    };

    /// One field of a flat layout.
    struct LayoutField {
        FieldKind kind         = FieldKind::SCALAR;
        Typecode typecode      = Typecode::UINT8;
        std::int32_t count     = 0;
        std::int32_t buffer_id = 0;
    };

    /// One class-version layout, read member by member by @c READ_FIELDS.
    struct Layout {
        std::uint32_t id = 0;
        std::vector<LayoutField> fields;
    };

    /// One function: a name, a register budget and a slice of the code pool.
    struct FuncDecl {
        std::uint32_t id = 0;
        std::string name;
        std::uint8_t arity      = 0;
        std::uint16_t reg_count = 0;
        std::uint32_t code_off  = 0;
        std::uint32_t code_len  = 0;

        /// One past the last instruction index this function owns.
        std::uint32_t end() const noexcept { return code_off + code_len; }
    };

    /**
     * @brief A decoded module: the whole of what a front end hands a machine.
     *
     * The wire format is defined by ``docs/spec/module.md`` and implemented by
     * ``detree.spec.module.Module.to_bytes``; this class is the other
     * implementation of it. The Python conformance tests decode modules the
     * Python side produced to keep the two in step.
     */
    class Module {
      public:
        std::uint16_t isa_major = kIsaMajor;
        std::uint16_t isa_minor = kIsaMinor;
        std::uint16_t entry     = 0;
        std::uint16_t regs_max  = 0;
        std::vector<BufferDecl> buffers;
        std::vector<StateDecl> states;
        std::vector<std::string> strings;
        std::vector<Layout> layouts;
        std::vector<FuncDecl> funcs;
        std::vector<Ins> code;

        /**
         * @brief Decode a serialized module.
         * @throws ModuleError if the bytes are truncated, malformed or carry a
         *         trailing byte; a module never fails to decode silently.
         */
        static Module from_bytes( const std::uint8_t* raw, std::size_t size );
    };

    /**
     * @brief Everything about @p module that can be wrong by construction.
     *
     * Mirrors ``detree.spec.validate``: register indices, jump targets,
     * function, buffer, state, layout and string references, typecodes, the ISA
     * version and the per-function register budget. It runs on every module a
     * machine decodes, because a wrong jump target found at run time is a
     * corrupt read rather than an error.
     *
     * @throws ModuleError naming the function, instruction and operand at fault.
     */
    void validate( const Module& module );

} // namespace detree

#endif // DETREE_MODULE_HPP
