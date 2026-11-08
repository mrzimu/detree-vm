#ifndef DETREE_C_API_H
#define DETREE_C_API_H

/*
 * The whole public surface of a compiled Detree machine: plain C99, no C++
 * types, no Python. ``detree.abi.capi`` loads exactly this through ctypes, and
 * a C program can drive the machine with it (``cpp/tests/test_capi.c``).
 *
 * Three rules the caller has to know:
 *
 * 1. One machine belongs to one thread. Registers, the cursor, and the buffers
 *    are its state; ``detree_last_error`` is thread-local.
 * 2. Pointers marked *borrowed* belong to the machine and stay valid until the
 *    next ``detree_run`` / ``detree_machine_reset`` / ``detree_machine_free``
 *    on it. A buffer's bytes outlive that if you ``detree_acquire_outputs`` and
 *    keep the ``detree_owner``.
 * 3. Nothing here throws. A call returns ``detree_status`` (or a count, or a
 *    NULL pointer) and the reason is read with ``detree_last_error``, which the
 *    next failing call overwrites.
 *
 * The machine's *input* is the module byte format of ``docs/spec/module.md``, which
 * is what another implementation has to reproduce; this header is how another
 * language calls this implementation.
 */

#include <stddef.h>
#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/** Bumped when a struct below changes layout or a symbol changes meaning. */
#define DETREE_C_API_VERSION 1

/** Marks the exported symbols; the library is built with hidden visibility. */
#if defined( __GNUC__ ) || defined( __clang__ )
#    define DETREE_API __attribute__( ( visibility( "default" ) ) )
#else
#    define DETREE_API
#endif

/** An opaque machine: one decoded module, reused basket after basket. */
typedef struct detree_machine detree_machine;

/** One retained buffer storage; release it with :func:`detree_owner_release`. */
typedef struct detree_owner detree_owner;

/** What a call failed with. ``DETREE_OK`` is success. */
typedef enum detree_status {
    DETREE_OK = 0,
    /** The module does not decode or does not validate: a static problem. */
    DETREE_MODULE_ERROR = 1,
    /** A read or a skip would leave the end of the basket. */
    DETREE_STREAM_UNDERFLOW = 2,
    /** ``fNBytes`` lacks ``kByteCountMask``. */
    DETREE_INVALID_BYTE_COUNT = 3,
    /** An event did not end where the entry offsets say it should. */
    DETREE_READ_LENGTH_MISMATCH = 4,
    /** An instruction this machine does not implement. */
    DETREE_UNSUPPORTED_FEATURE = 5,
    /** An ``ASSERT``/``THROW`` fired: the data is not what the module expects. */
    DETREE_ASSERTION_FAILED = 6,
    /** Calls nested deeper than the frame limit. */
    DETREE_STACK_OVERFLOW = 7,
    /** Any other machine failure. */
    DETREE_ERROR = 8,
    /** The caller passed a null pointer or an impossible size. */
    DETREE_BAD_ARGUMENT = 9
} detree_status;

/** Capability bits reported by :func:`detree_capabilities`. */
#define DETREE_CAP_STREAMS ( 1ull << 0 )
#define DETREE_CAP_BULK ( 1ull << 1 )
#define DETREE_CAP_STRINGS ( 1ull << 2 )
#define DETREE_CAP_REFS ( 1ull << 3 )
#define DETREE_CAP_LAYOUTS ( 1ull << 4 )
#define DETREE_CAP_ALL                                                                        \
    ( DETREE_CAP_STREAMS | DETREE_CAP_BULK | DETREE_CAP_STRINGS | DETREE_CAP_REFS |           \
      DETREE_CAP_LAYOUTS )

/** The last failure on this thread, with the machine's context. */
typedef struct detree_error {
    int32_t status; /**< a :type:`detree_status` */
    int32_t reserved;
    int64_t ip;          /**< instruction index, or -1 when unknown */
    int64_t cursor;      /**< stream offset, or -1 when unknown */
    const char* message; /**< borrowed, UTF-8, not NUL-terminated */
    size_t message_length;
    const char* function; /**< borrowed function name, or NULL */
    size_t function_length;
} detree_error;

/** One output buffer, as :func:`detree_acquire_outputs` hands it out. */
typedef struct detree_output {
    const void* data;    /**< borrowed element bytes, native byte order */
    size_t length;       /**< element count */
    size_t itemsize;     /**< bytes per element */
    detree_owner* owner; /**< retained; release with detree_owner_release */
} detree_output;

/** One state slot, as :func:`detree_get_states` hands it out. */
typedef struct detree_state {
    const char* name; /**< borrowed, machine-owned (a module declaration) */
    size_t name_length;
    int32_t is_name; /**< nonzero: a name slot, read ``text`` */
    uint32_t reserved;
    uint64_t value;   /**< the slot's 64-bit value (the arena id for a name slot) */
    const char* text; /**< borrowed string, or NULL when the slot holds none */
    size_t text_length;
} detree_state;

/** The version this library was built with; a caller checks it once. */
DETREE_API uint32_t detree_c_api_version( void );

/** A stable implementation name: "cpp", "rust", ... (borrowed, NUL-terminated). */
DETREE_API const char* detree_backend_name( void );

/** The implementation's own version (borrowed, NUL-terminated). */
DETREE_API const char* detree_backend_version( void );

/** The ISA version this machine implements. */
DETREE_API uint16_t detree_isa_major( void );
DETREE_API uint16_t detree_isa_minor( void );

/** A bitmask of the capabilities above. */
DETREE_API uint64_t detree_capabilities( void );

/**
 * @brief Decode and validate a module, and build a machine for it.
 *
 * @return a machine, or NULL on failure (:func:`detree_last_error`).
 */
DETREE_API detree_machine* detree_machine_new( const uint8_t* module, size_t size );

DETREE_API void detree_machine_free( detree_machine* machine );

/** Return to the state just after construction, allocating fresh buffers. */
DETREE_API void detree_machine_reset( detree_machine* machine );

DETREE_API uint16_t detree_machine_isa_major( const detree_machine* machine );
DETREE_API uint16_t detree_machine_isa_minor( const detree_machine* machine );

/** How many buffers the module declares. */
DETREE_API size_t detree_buffer_count( const detree_machine* machine );

/** Name of buffer @p index (borrowed; live for the machine's life), or NULL. */
DETREE_API const char* detree_buffer_name( const detree_machine* machine, size_t index,
                                           size_t* length );

/** Dtype of buffer @p index (borrowed), or NULL. A portable name: "int32". */
DETREE_API const char* detree_buffer_dtype( const detree_machine* machine, size_t index,
                                            size_t* length );

/**
 * @brief Read every event of one basket.
 *
 * @param machine       a machine
 * @param data          the decompressed basket body
 * @param size          its length in bytes
 * @param offsets       cumulative entry offsets; at least two
 * @param n_offsets     how many offsets there are
 * @param cursor_offset the key length, used for object references
 */
DETREE_API detree_status detree_run( detree_machine* machine, const uint8_t* data, size_t size,
                                     const int64_t* offsets, size_t n_offsets,
                                     size_t cursor_offset );

/**
 * @brief Read one basket and take its outputs, in one call.
 *
 * The hot path: a Python caller crosses the FFI once per basket instead of
 * twice, and each crossing costs a GIL release and re-acquire.
 *
 * @return the run's status; on failure nothing was written.
 */
DETREE_API detree_status detree_read( detree_machine* machine, const uint8_t* data,
                                      size_t size, const int64_t* offsets, size_t n_offsets,
                                      size_t cursor_offset, detree_output* outputs,
                                      size_t output_capacity, size_t* outputs_written );

/**
 * @brief Take every output buffer at once: its bytes plus a retained owner.
 *
 * @return the number of outputs written (``min(capacity, buffer_count)``).
 */
DETREE_API size_t detree_acquire_outputs( detree_machine* machine, detree_output* out,
                                          size_t capacity );

/** Drop a retained buffer storage. NULL is a no-op. */
DETREE_API void detree_owner_release( detree_owner* owner );

/** How many state slots the module declares. */
DETREE_API size_t detree_state_count( const detree_machine* machine );

/** Name of state @p index (borrowed), or NULL. */
DETREE_API const char* detree_state_name( const detree_machine* machine, size_t index,
                                          size_t* length );

/** Fill @p out with every state slot; returns how many were written. */
DETREE_API size_t detree_get_states( const detree_machine* machine, detree_state* out,
                                     size_t capacity );

/** The last failure on this thread, or a zeroed record with ``DETREE_OK``. */
DETREE_API detree_error detree_last_error( void );

#ifdef __cplusplus
} /* extern "C" */
#endif

#endif /* DETREE_C_API_H */
