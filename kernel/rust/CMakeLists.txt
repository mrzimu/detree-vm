message("Configuring Rust build for Detree machine")

find_program(CARGO_EXECUTABLE cargo REQUIRED)
set(DETREE_RUST_TARGET_DIR ${CMAKE_CURRENT_BINARY_DIR})
set(DETREE_RUST_LIBRARY ${DETREE_RUST_TARGET_DIR}/release/${CMAKE_SHARED_LIBRARY_PREFIX}rustdetree${CMAKE_SHARED_LIBRARY_SUFFIX})

file(GLOB_RECURSE DETREE_RUST_SOURCES CONFIGURE_DEPENDS
    ${CMAKE_CURRENT_SOURCE_DIR}/src/*.rs
    ${CMAKE_CURRENT_SOURCE_DIR}/Cargo.toml
    ${CMAKE_CURRENT_SOURCE_DIR}/Cargo.lock
)

# Build mode
if(CMAKE_BUILD_TYPE STREQUAL "Debug")
    set(DETREE_RUST_BUILD_MODE "--debug")
else()
    set(DETREE_RUST_BUILD_MODE "--release")
endif()

add_custom_command(
    OUTPUT ${DETREE_RUST_LIBRARY}
    COMMAND ${CARGO_EXECUTABLE} build
        ${DETREE_RUST_BUILD_MODE}
        --manifest-path ${CMAKE_CURRENT_SOURCE_DIR}/Cargo.toml
        --target-dir ${DETREE_RUST_TARGET_DIR}
    DEPENDS ${DETREE_RUST_SOURCES}
    COMMENT "Building the Rust implementation of the Detree machine"
    VERBATIM
)

add_custom_target(detree_rust ALL DEPENDS ${DETREE_RUST_LIBRARY})

if(DEFINED SKBUILD_PROJECT_NAME)
    install(FILES ${DETREE_RUST_LIBRARY} DESTINATION detree/_libs)
endif()
