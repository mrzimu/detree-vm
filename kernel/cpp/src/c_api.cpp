/// The C ABI of the machine: every symbol in ``detree.h``, nothing else.
///
/// This file is the only place a ``detree::`` name meets an ``extern "C"``
/// boundary. Two things it owns that the C++ core does not:
///
/// * the **thread-local last error**, because a C caller has no exception to
///   catch and a machine may fail to be constructed at all;
/// * the **retained buffer storage** (:class:`detree_owner`), because a C caller
///   that keeps a pointer past the next basket must say so explicitly.
#include "detree.h"

#include <algorithm>
#include <cstddef>
#include <cstdint>
#include <memory>
#include <string>
#include <utility>
#include <vector>

#include "buffer.hpp"
#include "errors.hpp"
#include "machine.hpp"
#include "module.hpp"

struct detree_machine {
    detree_machine( const std::uint8_t* raw, std::size_t size ) : impl( raw, size ) {}

    detree::Machine impl;
};

struct detree_owner {
    detree::BufferStoragePtr storage;
};

namespace {
    /// Everything :func:`detree_last_error` reports, owned so the pointers are safe.
    struct LastError {
        detree_status status = DETREE_OK;
        std::int64_t ip      = -1;
        std::int64_t cursor  = -1;
        std::string message;
        std::string function;
    };

    thread_local LastError g_last;

    void set_error( detree_status status, std::string message, std::string function = {},
                    std::int64_t ip = -1, std::int64_t cursor = -1 ) {
        g_last.status   = status;
        g_last.message  = std::move( message );
        g_last.function = std::move( function );
        g_last.ip       = ip;
        g_last.cursor   = cursor;
    }

    void clear_error() {
        g_last.status = DETREE_OK;
        g_last.message.clear();
        g_last.function.clear();
        g_last.ip     = -1;
        g_last.cursor = -1;
    }

    /// The status of a run-time failure, and the context the exception carries.
    detree_status classify( const detree::Error& error ) {
        if ( dynamic_cast<const detree::StreamUnderflow*>( &error ) != nullptr )
        { return DETREE_STREAM_UNDERFLOW; }
        if ( dynamic_cast<const detree::InvalidByteCount*>( &error ) != nullptr )
        { return DETREE_INVALID_BYTE_COUNT; }
        if ( dynamic_cast<const detree::ReadLengthMismatch*>( &error ) != nullptr )
        { return DETREE_READ_LENGTH_MISMATCH; }
        if ( dynamic_cast<const detree::UnsupportedFeature*>( &error ) != nullptr )
        { return DETREE_UNSUPPORTED_FEATURE; }
        if ( dynamic_cast<const detree::AssertionFailed*>( &error ) != nullptr )
        { return DETREE_ASSERTION_FAILED; }
        if ( dynamic_cast<const detree::StackOverflow*>( &error ) != nullptr )
        { return DETREE_STACK_OVERFLOW; }
        return DETREE_ERROR;
    }

    /// The machine's cursor uses ``kNoCursor`` for "unknown"; the ABI says -1.
    std::int64_t cursor_of( const detree::Error& error ) {
        if ( error.cursor() == detree::kNoCursor ) { return -1; }
        return static_cast<std::int64_t>( error.cursor() );
    }

    void record( const detree::Error& error ) {
        set_error( classify( error ), error.what(), error.function(), error.ip(),
                   cursor_of( error ) );
    }

#ifndef DETREE_BACKEND_VERSION
#    define DETREE_BACKEND_VERSION "0.1"
#endif

    constexpr const char* kBackendName    = "cpp";
    constexpr const char* kBackendVersion = DETREE_BACKEND_VERSION;

} // namespace

extern "C" {

uint32_t detree_c_api_version( void ) { return DETREE_C_API_VERSION; }

const char* detree_backend_name( void ) { return kBackendName; }

const char* detree_backend_version( void ) { return kBackendVersion; }

uint16_t detree_isa_major( void ) { return detree::kIsaMajor; }

uint16_t detree_isa_minor( void ) { return detree::kIsaMinor; }

uint64_t detree_capabilities( void ) { return DETREE_CAP_ALL; }

detree_machine* detree_machine_new( const uint8_t* module, size_t size ) {
    clear_error();
    if ( module == nullptr )
    {
        set_error( DETREE_BAD_ARGUMENT, "module is null" );
        return nullptr;
    }
    try
    { return new detree_machine( module, size ); } catch ( const detree::ModuleError& error )
    { set_error( DETREE_MODULE_ERROR, error.what() ); } catch ( const std::exception& error )
    { set_error( DETREE_ERROR, error.what() ); }
    return nullptr;
}

void detree_machine_free( detree_machine* machine ) { delete machine; }

void detree_machine_reset( detree_machine* machine ) {
    if ( machine == nullptr ) { return; }
    clear_error();
    try
    { machine->impl.reset(); } catch ( const std::exception& error )
    { set_error( DETREE_ERROR, error.what() ); }
}

uint16_t detree_machine_isa_major( const detree_machine* machine ) {
    return machine == nullptr ? 0 : machine->impl.module().isa_major;
}

uint16_t detree_machine_isa_minor( const detree_machine* machine ) {
    return machine == nullptr ? 0 : machine->impl.module().isa_minor;
}

size_t detree_buffer_count( const detree_machine* machine ) {
    return machine == nullptr ? 0 : machine->impl.buffer_count();
}

const char* detree_buffer_name( const detree_machine* machine, size_t index, size_t* length ) {
    if ( machine == nullptr || index >= machine->impl.buffer_count() ) { return nullptr; }
    const std::string& name = machine->impl.buffer( index ).name();
    if ( length != nullptr ) { *length = name.size(); }
    return name.c_str();
}

const char* detree_buffer_dtype( const detree_machine* machine, size_t index,
                                 size_t* length ) {
    if ( machine == nullptr || index >= machine->impl.buffer_count() ) { return nullptr; }
    const std::string& dtype = machine->impl.buffer( index ).dtype();
    if ( length != nullptr ) { *length = dtype.size(); }
    return dtype.c_str();
}

detree_status detree_run( detree_machine* machine, const std::uint8_t* data, size_t size,
                          const std::int64_t* offsets, size_t n_offsets,
                          size_t cursor_offset ) {
    clear_error();
    if ( machine == nullptr )
    {
        set_error( DETREE_BAD_ARGUMENT, "machine is null" );
        return DETREE_BAD_ARGUMENT;
    }
    if ( data == nullptr && size != 0 )
    {
        set_error( DETREE_BAD_ARGUMENT, "data is null but size is not zero" );
        return DETREE_BAD_ARGUMENT;
    }
    if ( offsets == nullptr )
    {
        set_error( DETREE_BAD_ARGUMENT, "offsets is null" );
        return DETREE_BAD_ARGUMENT;
    }

    try
    {
        machine->impl.run( data, size, offsets, n_offsets, cursor_offset );
    } catch ( const detree::Error& error )
    {
        record( error );
        return g_last.status;
    } catch ( const std::exception& error )
    {
        set_error( DETREE_ERROR, error.what() );
        return g_last.status;
    }
    return DETREE_OK;
}

detree_status detree_read( detree_machine* machine, const std::uint8_t* data, size_t size,
                           const std::int64_t* offsets, size_t n_offsets, size_t cursor_offset,
                           detree_output* outputs, size_t output_capacity,
                           size_t* outputs_written ) {
    if ( outputs_written != nullptr ) { *outputs_written = 0; }
    const detree_status status =
        detree_run( machine, data, size, offsets, n_offsets, cursor_offset );
    if ( status != DETREE_OK ) { return status; }
    const size_t written = detree_acquire_outputs( machine, outputs, output_capacity );
    if ( outputs_written != nullptr ) { *outputs_written = written; }
    return DETREE_OK;
}

size_t detree_acquire_outputs( detree_machine* machine, detree_output* out, size_t capacity ) {
    if ( machine == nullptr || out == nullptr ) { return 0; }
    clear_error();
    try
    {
        const std::size_t count = std::min( capacity, machine->impl.buffer_count() );
        for ( std::size_t i = 0; i < count; ++i )
        {
            const detree::Buffer& buffer = machine->impl.buffer( i );
            out[i].data                  = buffer.bytes();
            out[i].length                = buffer.count();
            out[i].itemsize              = buffer.element_size();
            // The storage is shared, so keeping it alive here is what lets a caller
            // hold the bytes across the machine's next run.
            out[i].owner = new detree_owner{ buffer.storage() };
        }
        return count;
    } catch ( const std::exception& error )
    { set_error( DETREE_ERROR, error.what() ); }
    return 0;
}

void detree_owner_release( detree_owner* owner ) { delete owner; }

size_t detree_state_count( const detree_machine* machine ) {
    return machine == nullptr ? 0 : machine->impl.state_count();
}

const char* detree_state_name( const detree_machine* machine, size_t index, size_t* length ) {
    if ( machine == nullptr || index >= machine->impl.state_count() ) { return nullptr; }
    const std::string& name = machine->impl.state_decl( index ).name;
    if ( length != nullptr ) { *length = name.size(); }
    return name.c_str();
}

size_t detree_get_states( const detree_machine* machine, detree_state* out, size_t capacity ) {
    if ( machine == nullptr || out == nullptr ) { return 0; }
    const std::size_t count = std::min( capacity, machine->impl.state_count() );
    for ( std::size_t i = 0; i < count; ++i )
    {
        const detree::StateDecl& decl           = machine->impl.state_decl( i );
        const detree::Machine::StateValue value = machine->impl.state_value( i );

        out[i].name        = decl.name.c_str();
        out[i].name_length = decl.name.size();
        out[i].is_name     = decl.is_string ? 1 : 0;
        out[i].reserved    = 0;
        out[i].value       = value.raw;
        out[i].text        = value.text == nullptr ? nullptr : value.text->c_str();
        out[i].text_length = value.text == nullptr ? 0 : value.text->size();
    }
    return count;
}

detree_error detree_last_error( void ) {
    detree_error out{};
    out.status          = static_cast<int32_t>( g_last.status );
    out.ip              = g_last.ip;
    out.cursor          = g_last.cursor;
    out.message         = g_last.message.c_str();
    out.message_length  = g_last.message.size();
    out.function        = g_last.function.empty() ? nullptr : g_last.function.c_str();
    out.function_length = g_last.function.size();
    return out;
}

} // extern "C"
