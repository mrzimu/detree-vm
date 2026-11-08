//! The Rust implementation of the Detree machine.
//!
//! `cargo build` produces a `cdylib` (and an `rlib` for the crate's own tests)
//! with the C ABI and no Python dependency. The release wheel installs that
//! cdylib under `detree/_libs/` and the Python package loads it through the
//! `detree_*` C ABI.
//!
//! The crate is an internal artifact: it is not published, and there is no
//! pyo3 or maturin path (ADR 0009).
//!
//! The machine passes conformance level 3 and is the only machine the release
//! wheel ships.

#![deny(unsafe_op_in_unsafe_fn)]

pub mod buffer;
pub mod capi;
pub mod errors;
pub mod isa;
pub mod machine;
pub mod module;

pub use isa::{BACKEND_NAME, C_API_VERSION, ISA_MAJOR, ISA_MINOR};
