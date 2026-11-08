//! Error kinds and their mapping to the C status values.
//!
//! The variants and meanings must match `docs/spec/machine.md` §12 and the
//! `detree_status` enum in `kernel/include/detree.h`.

use std::fmt;

/// What went wrong. The C ABI uses the same distinctions as status codes.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Kind {
    /// The module does not decode or does not validate.
    Module,
    /// A read or a skip would leave the end of the basket.
    StreamUnderflow,
    /// `fNBytes` lacks `kByteCountMask`.
    InvalidByteCount,
    /// An event did not end where the entry offsets say it should.
    ReadLengthMismatch,
    /// An instruction this machine does not implement.
    UnsupportedFeature,
    /// An `ASSERT`/`THROW` fired: the data is not what the module expects.
    AssertionFailed,
    /// Calls nested deeper than the frame limit.
    StackOverflow,
    /// Any other machine failure.
    Other,
}

impl Kind {
    /// The `detree_status` value of this kind.
    pub fn status(self) -> i32 {
        match self {
            Kind::Module => 1,
            Kind::StreamUnderflow => 2,
            Kind::InvalidByteCount => 3,
            Kind::ReadLengthMismatch => 4,
            Kind::UnsupportedFeature => 5,
            Kind::AssertionFailed => 6,
            Kind::StackOverflow => 7,
            Kind::Other => 8,
        }
    }
}

/// A failure with the context the Python reference reports.
#[derive(Debug, Clone)]
pub struct Error {
    pub kind: Kind,
    pub message: String,
    pub function: String,
    pub ip: i64,
    pub cursor: i64,
}

impl Error {
    /// A failure before a function is known (module decoding, static checks).
    pub fn module(message: impl Into<String>) -> Self {
        Error {
            kind: Kind::Module,
            message: message.into(),
            function: String::new(),
            ip: -1,
            cursor: -1,
        }
    }

    /// A failure without machine context, but with a specific kind.
    pub fn plain(kind: Kind, message: impl Into<String>) -> Self {
        Error {
            kind,
            message: message.into(),
            function: String::new(),
            ip: -1,
            cursor: -1,
        }
    }

    /// A run-time failure, with the current function, instruction and cursor.
    pub fn at(
        kind: Kind,
        message: impl Into<String>,
        function: impl Into<String>,
        ip: i64,
        cursor: usize,
    ) -> Self {
        Error {
            kind,
            message: message.into(),
            function: function.into(),
            ip,
            cursor: cursor as i64,
        }
    }
}

impl fmt::Display for Error {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "{}", self.message)?;
        if !self.function.is_empty() {
            write!(f, " function={}", self.function)?;
        }
        if self.ip >= 0 {
            write!(f, " ip={}", self.ip)?;
        }
        if self.cursor >= 0 {
            write!(f, " cursor={}", self.cursor)?;
        }
        Ok(())
    }
}

impl std::error::Error for Error {}

/// The result type every machine entry point returns.
pub type Result<T> = std::result::Result<T, Error>;
