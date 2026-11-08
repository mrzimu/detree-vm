cmake_minimum_required(VERSION 3.20)

project(detree LANGUAGES C)

# A machine that a benchmark counts on has to be optimized: a plain
# `cmake -S kernel -B build` would otherwise produce a -O0 library that decodes
# at a fraction of the speed of the same code in a wheel.
if(NOT CMAKE_BUILD_TYPE AND NOT CMAKE_CONFIGURATION_TYPES)
    set(CMAKE_BUILD_TYPE Release CACHE STRING "Build type" FORCE)
endif()

# The release wheel ships one machine, the Rust one. `DETREE_SHIP_BACKEND` is a
# build-time switch:
#   rust  - the Rust machine (the shipped one; built with cargo)
#   cpp   - the C++ machine (development and CI comparison)
#   both  - both, for conformance and benchmarks
# A wheel build defaults to Rust; a standalone `cmake -S kernel` defaults to the
# C++ machine so the core still builds with nothing but a C++ compiler.
# `-D` wins over the environment so a CI job and a local build cannot diverge
# silently.
if(NOT DEFINED DETREE_SHIP_BACKEND)
    set(DETREE_SHIP_BACKEND "$ENV{DETREE_SHIP_BACKEND}")
endif()
if(DETREE_SHIP_BACKEND STREQUAL "")
    if(DEFINED SKBUILD_PROJECT_NAME)
        set(DETREE_SHIP_BACKEND "rust")
    else()
        set(DETREE_SHIP_BACKEND "cpp")
    endif()
endif()

if(DETREE_SHIP_BACKEND STREQUAL "rust")
    set(DETREE_CPP_DEFAULT OFF)
    set(DETREE_RUST_DEFAULT ON)
elseif(DETREE_SHIP_BACKEND STREQUAL "both")
    set(DETREE_CPP_DEFAULT ON)
    set(DETREE_RUST_DEFAULT ON)
elseif(DETREE_SHIP_BACKEND STREQUAL "cpp")
    set(DETREE_CPP_DEFAULT ON)
    set(DETREE_RUST_DEFAULT OFF)
else()
    message(FATAL_ERROR "DETREE_SHIP_BACKEND must be cpp, rust, or both")
endif()

# The defaults follow the shipping switch, but an explicit -D always wins. They
# are normal variables rather than cache entries so that a changed
# DETREE_SHIP_BACKEND is picked up on the next configure.
if(NOT DEFINED DETREE_BUILD_CPP)
    set(DETREE_BUILD_CPP ${DETREE_CPP_DEFAULT})
endif()
if(NOT DEFINED DETREE_BUILD_RUST)
    set(DETREE_BUILD_RUST ${DETREE_RUST_DEFAULT})
endif()
if(NOT DEFINED DETREE_BUILD_TESTS)
    if(DEFINED SKBUILD_PROJECT_NAME)
        set(DETREE_BUILD_TESTS OFF)
    else()
        set(DETREE_BUILD_TESTS ON)
    endif()
endif()

message(STATUS
    "Detree: ship=${DETREE_SHIP_BACKEND} cpp=${DETREE_BUILD_CPP} "
    "rust=${DETREE_BUILD_RUST} tests=${DETREE_BUILD_TESTS}"
)

if(DETREE_BUILD_TESTS)
    enable_testing()
endif()

if(DETREE_BUILD_RUST)
    add_subdirectory(rust)
endif()

if(DETREE_BUILD_CPP)
    add_subdirectory(cpp)
endif()

# The C ABI header belongs to every release artifact, including a Rust-only
# wheel: an embedder needs the header that declares the exported symbols.
if(DEFINED SKBUILD_PROJECT_NAME)
    install(FILES include/detree.h DESTINATION detree/include)
endif()
