message(STATUS "Configuring C++ build for Detree machine")

enable_language(CXX)

# One shared library carries both the core and the C ABI: the C++ classes stay
# hidden, and the only exported symbols are the `detree_*` entry points that
# `detree.h` declares with default visibility.
add_library(cpp_detree SHARED
    src/buffer.cpp
    src/c_api.cpp
    src/isa.cpp
    src/machine.cpp
    src/module.cpp
)

target_include_directories(cpp_detree
    PUBLIC $<BUILD_INTERFACE:${CMAKE_CURRENT_SOURCE_DIR}/../include>
)

target_compile_features(cpp_detree PUBLIC cxx_std_17)

set_target_properties(cpp_detree PROPERTIES
    OUTPUT_NAME cppdetree
    CXX_EXTENSIONS OFF
    CXX_VISIBILITY_PRESET hidden
    VISIBILITY_INLINES_HIDDEN ON)

if(MSVC)
    target_compile_options(cpp_detree PRIVATE /W4 /permissive- /Zc:__cplusplus)
    set_target_properties(cpp_detree PROPERTIES WINDOWS_EXPORT_ALL_SYMBOLS ON)
else()
    target_compile_options(cpp_detree PRIVATE -Wall -Wextra)
endif()

install(TARGETS cpp_detree
    LIBRARY DESTINATION detree/_libs
    RUNTIME DESTINATION detree/_libs
)

if(DETREE_BUILD_TESTS)
    # Compiled as C: the machine's public surface is the C header, not the C++
    # classes. If the ABI ever stops being C-only, this file stops compiling.
    add_executable(detree_capi_smoke tests/test_capi.c)
    target_link_libraries(detree_capi_smoke PRIVATE cpp_detree)
    add_test(NAME detree_capi_smoke COMMAND detree_capi_smoke)
    set_tests_properties(detree_capi_smoke PROPERTIES TIMEOUT 120)
endif()
