/*
 * A minimal C embedder.
 *
 * Build (from a checkout):
 *   cmake -S kernel -B build/cpp -DDETREE_SHIP_BACKEND=cpp
 *   cmake --build build/cpp
 *   cc -Ikernel/include examples/c/driver.c build/cpp/cpp/libcppdetree.so -o driver
 *   LD_LIBRARY_PATH=build/cpp/cpp ./driver
 */

#include "detree.h"

#include <stdio.h>

int main( void ) {
    printf( "detree %s %s, C ABI %u, ISA %u.%u\n", detree_backend_name(),
            detree_backend_version(), detree_c_api_version(), detree_isa_major(),
            detree_isa_minor() );

    const unsigned char module[] = { 'D', 'E', 'T', 'R' };
    detree_machine* machine      = detree_machine_new( module, sizeof( module ) );
    if ( machine == NULL )
    {
        detree_error error = detree_last_error();
        printf( "expected failure: status=%d %.*s\n", error.status, (int)error.message_length,
                error.message );
        return 0;
    }

    detree_machine_free( machine );
    return 0;
}
