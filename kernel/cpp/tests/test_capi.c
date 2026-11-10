/*
 * The C ABI smoke test. Compiled as C, linked only against `cppdetree`, and
 * includes only the public header: if the ABI ever stops being C-only, this
 * file stops compiling.
 *
 * It also builds the smallest possible module by hand (one function that
 * halts), so the whole path -- decode, validate, run, acquire -- is exercised
 * without Python.
 */

#include "detree.h"

#include <assert.h>
#include <stdint.h>
#include <string.h>

static uint8_t* put8( uint8_t* out, uint8_t value ) {
    *out++ = value;
    return out;
}

static uint8_t* put16( uint8_t* out, uint16_t value ) {
    *out++ = (uint8_t)( value & 0xFF );
    *out++ = (uint8_t)( value >> 8 );
    return out;
}

static uint8_t* put32( uint8_t* out, uint32_t value ) {
    int i;
    for ( i = 0; i < 4; ++i ) { *out++ = (uint8_t)( value >> ( 8 * i ) ); }
    return out;
}

static uint8_t* put64( uint8_t* out, uint64_t value ) {
    int i;
    for ( i = 0; i < 8; ++i ) { *out++ = (uint8_t)( value >> ( 8 * i ) ); }
    return out;
}

static uint8_t* put_text( uint8_t* out, const char* text ) {
    size_t size = strlen( text );
    out         = put16( out, (uint16_t)size );
    memcpy( out, text, size );
    return out + size;
}

/* One function `main` with arity 0, reg_count 0, a single HALT instruction. */
static size_t build_halt_module( uint8_t* out ) {
    uint8_t* at = out;
    memcpy( at, "DETR", 4 );
    at += 4;
    at = put16( at, 0 ); /* isa_major */
    at = put16( at, 2 ); /* isa_minor */
    at = put16( at, 0 ); /* entry */
    at = put16( at, 0 ); /* regs_max */
    at = put32( at, 0 ); /* buffers */
    at = put32( at, 0 ); /* states */
    at = put32( at, 0 ); /* strings */
    at = put32( at, 0 ); /* layouts */
    at = put32( at, 1 ); /* functions */

    at = put32( at, 0 ); /* function id */
    at = put_text( at, "main" );
    at = put8( at, 0 );  /* arity */
    at = put16( at, 0 ); /* reg_count */
    at = put16( at, 0 ); /* reserved */
    at = put32( at, 0 ); /* code_off */
    at = put32( at, 1 ); /* code_len */

    at = put32( at, 1 ); /* instruction count */
    at = put8( at, 0 );  /* HALT */
    at = put8( at, 0 );  /* rd */
    at = put8( at, 0 );  /* ra */
    at = put8( at, 0 );  /* rb */
    at = put64( at, 0 ); /* imm1 */
    at = put64( at, 0 ); /* imm2 */
    return (size_t)( at - out );
}

int main( void ) {
    detree_error error;
    detree_machine* machine;
    detree_output outputs[1];
    detree_state states[1];
    uint8_t module[128];
    size_t module_size;
    int64_t offsets[2] = { 0, 0 };

    assert( detree_c_api_version() == DETREE_C_API_VERSION );
    assert( detree_backend_name() != NULL );
    assert( strcmp( detree_backend_name(), "cpp" ) == 0 );
    assert( detree_backend_version() != NULL );
    assert( detree_isa_major() == 0 );
    assert( detree_isa_minor() == 2 );
    assert( detree_capabilities() == DETREE_CAP_ALL );

    /* A null module is a caller error, reported without crashing. */
    machine = detree_machine_new( NULL, 0 );
    assert( machine == NULL );
    error = detree_last_error();
    assert( error.status == DETREE_BAD_ARGUMENT );
    assert( error.ip == -1 );
    assert( error.cursor == -1 );
    assert( error.message != NULL );

    /* Garbage is a module error, not a crash. */
    {
        static const uint8_t junk[4] = { 'N', 'O', 'P', 'E' };
        machine                      = detree_machine_new( junk, sizeof( junk ) );
        assert( machine == NULL );
        assert( detree_last_error().status == DETREE_MODULE_ERROR );
    }

    module_size = build_halt_module( module );
    machine     = detree_machine_new( module, module_size );
    assert( machine != NULL );
    assert( detree_machine_isa_major( machine ) == 0 );
    assert( detree_machine_isa_minor( machine ) == 2 );
    assert( detree_buffer_count( machine ) == 0 );
    assert( detree_state_count( machine ) == 0 );

    assert( detree_run( machine, NULL, 0, offsets, 2, 0 ) == DETREE_OK );
    assert( detree_acquire_outputs( machine, outputs, 1 ) == 0 );
    assert( detree_get_states( machine, states, 1 ) == 0 );

    /* One offset is not a basket: refuse it. */
    assert( detree_run( machine, NULL, 0, offsets, 1, 0 ) == DETREE_READ_LENGTH_MISMATCH );

    detree_machine_reset( machine );
    detree_machine_free( machine );

    detree_owner_release( NULL ); /* a documented no-op */
    return 0;
}
