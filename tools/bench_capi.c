/*
 * The native side of `tools/bench.py`.
 *
 * It loads a machine with dlopen, calls `detree_read` directly -- no Python FFI,
 * no ctypes, no GIL -- and prints the median nanoseconds of `--repeat` runs.
 * Outputs are excluded: capacity 0 means the call times the decode, not the
 * transfer. This is the harness ADR 0008 requires for the release gate.
 *
 *   bench_capi <library> <module> <data> <offsets> <cursor> <repeat>
 *
 * `offsets` is a comma-separated list of i64 values. The module and data files
 * are raw bytes.
 */

#define _POSIX_C_SOURCE 200809L

#include "detree.h"

#include <dlfcn.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <time.h>

typedef detree_machine* ( *machine_new_fn )( const uint8_t*, size_t );
typedef void ( *machine_free_fn )( detree_machine* );
typedef detree_status ( *read_fn )( detree_machine*, const uint8_t*, size_t, const int64_t*,
                                    size_t, size_t, detree_output*, size_t, size_t* );
typedef detree_error ( *last_error_fn )( void );

static uint8_t* read_file( const char* path, size_t* size ) {
    FILE* file = fopen( path, "rb" );
    if ( file == NULL )
    {
        fprintf( stderr, "cannot open %s\n", path );
        return NULL;
    }
    if ( fseek( file, 0, SEEK_END ) != 0 )
    {
        fclose( file );
        return NULL;
    }
    long length = ftell( file );
    if ( length < 0 || fseek( file, 0, SEEK_SET ) != 0 )
    {
        fclose( file );
        return NULL;
    }
    uint8_t* data = (uint8_t*)malloc( (size_t)length + 1 );
    if ( data == NULL )
    {
        fclose( file );
        return NULL;
    }
    if ( length > 0 && fread( data, 1, (size_t)length, file ) != (size_t)length )
    {
        free( data );
        fclose( file );
        return NULL;
    }
    fclose( file );
    *size = (size_t)length;
    return data;
}

static int compare_i64( const void* left, const void* right ) {
    int64_t a = *(const int64_t*)left;
    int64_t b = *(const int64_t*)right;
    return ( a > b ) - ( a < b );
}

static int64_t now_ns( void ) {
    struct timespec ts;
    clock_gettime( CLOCK_MONOTONIC, &ts );
    return (int64_t)ts.tv_sec * 1000000000LL + (int64_t)ts.tv_nsec;
}

int main( int argc, char** argv ) {
    if ( argc != 7 )
    {
        fprintf( stderr, "usage: %s <library> <module> <data> <offsets> <cursor> <repeat>\n",
                 argv[0] );
        return 2;
    }
    size_t module_size = 0;
    size_t data_size   = 0;
    uint8_t* module    = read_file( argv[2], &module_size );
    uint8_t* data      = read_file( argv[3], &data_size );
    if ( module == NULL || data == NULL )
    {
        free( module );
        free( data );
        return 1;
    }

    int64_t offsets[4096];
    size_t n_offsets = 0;
    char* copy       = strdup( argv[4] );
    char* token      = strtok( copy, "," );
    while ( token != NULL && n_offsets < 4096 )
    {
        offsets[n_offsets++] = strtoll( token, NULL, 10 );
        token                = strtok( NULL, "," );
    }
    free( copy );
    size_t cursor = (size_t)strtoull( argv[5], NULL, 10 );
    int repeat    = atoi( argv[6] );
    if ( repeat < 1 ) { repeat = 1; }

    void* library = dlopen( argv[1], RTLD_NOW | RTLD_LOCAL );
    if ( library == NULL )
    {
        fprintf( stderr, "cannot load %s: %s\n", argv[1], dlerror() );
        free( module );
        free( data );
        return 1;
    }
    machine_new_fn machine_new   = (machine_new_fn)dlsym( library, "detree_machine_new" );
    machine_free_fn machine_free = (machine_free_fn)dlsym( library, "detree_machine_free" );
    read_fn read                 = (read_fn)dlsym( library, "detree_read" );
    last_error_fn last_error     = (last_error_fn)dlsym( library, "detree_last_error" );
    if ( machine_new == NULL || machine_free == NULL || read == NULL || last_error == NULL )
    {
        fprintf( stderr, "library does not export the C ABI\n" );
        dlclose( library );
        free( module );
        free( data );
        return 1;
    }

    detree_machine* machine = machine_new( module, module_size );
    if ( machine == NULL )
    {
        detree_error error = last_error();
        fprintf( stderr, "cannot build machine: %.*s\n", (int)error.message_length,
                 error.message );
        dlclose( library );
        free( module );
        free( data );
        return 1;
    }

    size_t written = 0;
    detree_status status =
        read( machine, data, data_size, offsets, n_offsets, cursor, NULL, 0, &written );
    if ( status != DETREE_OK )
    {
        detree_error error = last_error();
        fprintf( stderr, "warmup failed: %.*s at ip %lld\n", (int)error.message_length,
                 error.message, (long long)error.ip );
        machine_free( machine );
        dlclose( library );
        free( module );
        free( data );
        return 1;
    }

    int64_t* samples = (int64_t*)malloc( sizeof( int64_t ) * (size_t)repeat );
    if ( samples == NULL )
    {
        machine_free( machine );
        dlclose( library );
        free( module );
        free( data );
        return 1;
    }
    for ( int i = 0; i < repeat; ++i )
    {
        int64_t start = now_ns();
        status =
            read( machine, data, data_size, offsets, n_offsets, cursor, NULL, 0, &written );
        samples[i] = now_ns() - start;
        if ( status != DETREE_OK )
        {
            fprintf( stderr, "run failed\n" );
            free( samples );
            machine_free( machine );
            dlclose( library );
            free( module );
            free( data );
            return 1;
        }
    }
    qsort( samples, (size_t)repeat, sizeof( int64_t ), compare_i64 );
    int64_t median = samples[repeat / 2];

    printf( "native_ns=%lld\n", (long long)median );
    free( samples );
    machine_free( machine );
    dlclose( library );
    free( module );
    free( data );
    return 0;
}
