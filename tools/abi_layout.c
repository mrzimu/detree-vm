/*
 * Print the size and field offsets of the C ABI structs.
 *
 * The Python layout test compiles this file, runs it, and compares the output
 * with the ctypes definitions in `detree.abi.capi`. If the header stops being
 * C99, this file stops compiling.
 */

#include "detree.h"

#include <stddef.h>
#include <stdio.h>

int main( void ) {
    printf( "detree_error sizeof=%zu status=%zu reserved=%zu ip=%zu cursor=%zu "
            "message=%zu message_length=%zu function=%zu function_length=%zu\n",
            sizeof( detree_error ), offsetof( detree_error, status ),
            offsetof( detree_error, reserved ), offsetof( detree_error, ip ),
            offsetof( detree_error, cursor ), offsetof( detree_error, message ),
            offsetof( detree_error, message_length ), offsetof( detree_error, function ),
            offsetof( detree_error, function_length ) );

    printf( "detree_output sizeof=%zu data=%zu length=%zu itemsize=%zu owner=%zu\n",
            sizeof( detree_output ), offsetof( detree_output, data ),
            offsetof( detree_output, length ), offsetof( detree_output, itemsize ),
            offsetof( detree_output, owner ) );

    printf( "detree_state sizeof=%zu name=%zu name_length=%zu is_name=%zu reserved=%zu "
            "value=%zu text=%zu text_length=%zu\n",
            sizeof( detree_state ), offsetof( detree_state, name ),
            offsetof( detree_state, name_length ), offsetof( detree_state, is_name ),
            offsetof( detree_state, reserved ), offsetof( detree_state, value ),
            offsetof( detree_state, text ), offsetof( detree_state, text_length ) );

    return 0;
}
