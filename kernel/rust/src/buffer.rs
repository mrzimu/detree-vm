//! Typed buffer storage and the owner handle.
//!
//! A buffer is an `Arc<dyn Storage>`; an output handle clones the `Arc`, so a
//! view can outlive the machine's next run without copying. The storage is
//! append-only while it is the machine's current buffer, and never written
//! again once `reset` replaces it -- that invariant is what makes handing out a
//! pointer to its bytes sound.

use std::cell::UnsafeCell;
use std::slice;
use std::sync::Arc;

use crate::errors::{Error, Result};
use crate::isa::{buffer_typecode, BufferKind, Typecode};
use crate::module::BufferDecl;

/// One dtype's storage, type-erased so a `Buffer` does not care which.
pub trait Storage: Send + Sync {
    fn append_bits(&self, value: u64);
    fn append_stream(&self, raw: &[u8], count: usize, typecode: Typecode);
    fn reserve_more(&self, extra: usize);
    fn last_bits(&self) -> u64;
    fn count(&self) -> u64;
    fn element_size(&self) -> usize;
    fn bytes(&self) -> &[u8];
}

/// A value's low bytes, stored the way a buffer of `Self` stores them.
trait FromBits: Copy + Send + Sync + 'static {
    const IS_FLOAT: bool;

    fn from_bits(value: u64) -> Self;
    fn to_bits(self) -> u64;
    fn from_stream(raw: &[u8], typecode: Typecode) -> Self;

    fn from_be(raw: &[u8]) -> Self {
        Self::from_bits(stream_bits(raw))
    }
}

/// One big-endian unsigned element, as an integer.
fn stream_bits(raw: &[u8]) -> u64 {
    let mut bits = 0u64;
    for byte in raw {
        bits = (bits << 8) | *byte as u64;
    }
    bits
}

/// One big-endian float element, as a real number.
fn stream_float(raw: &[u8], typecode: Typecode) -> f64 {
    if typecode == Typecode::Float32 {
        f32::from_bits(stream_bits(raw) as u32) as f64
    } else {
        f64::from_bits(stream_bits(raw))
    }
}

/// One big-endian signed element, sign-extended to 64 bits.
fn stream_signed(raw: &[u8], typecode: Typecode) -> i64 {
    let shift = 64 - typecode.size() * 8;
    ((stream_bits(raw) << shift) as i64) >> shift
}

macro_rules! unsigned_bits {
    ( $( $type:ty ),* ) => {
        $(
            impl FromBits for $type {
                const IS_FLOAT: bool = false;

                fn from_bits(value: u64) -> Self { value as $type }
                fn to_bits(self) -> u64 { self as u64 }

                fn from_stream(raw: &[u8], typecode: Typecode) -> Self {
                    if typecode.is_float() {
                        (stream_float(raw, typecode).trunc() as i64) as $type
                    } else if typecode.is_signed() {
                        stream_signed(raw, typecode) as $type
                    } else {
                        stream_bits(raw) as $type
                    }
                }
            }
        )*
    };
}

macro_rules! signed_bits {
    ( $( $type:ty ),* ) => {
        $(
            impl FromBits for $type {
                const IS_FLOAT: bool = false;

                fn from_bits(value: u64) -> Self { value as $type }
                fn to_bits(self) -> u64 { self as u64 }

                fn from_stream(raw: &[u8], typecode: Typecode) -> Self {
                    if typecode.is_float() {
                        (stream_float(raw, typecode).trunc() as i64) as $type
                    } else if typecode.is_signed() {
                        stream_signed(raw, typecode) as $type
                    } else {
                        stream_bits(raw) as $type
                    }
                }
            }
        )*
    };
}

unsigned_bits!(u8, u16, u32, u64);
signed_bits!(i8, i16, i32, i64);

impl FromBits for f32 {
    const IS_FLOAT: bool = true;

    fn from_bits(value: u64) -> Self {
        f32::from_bits(value as u32)
    }

    fn to_bits(self) -> u64 {
        // ``BUF_LAST``/``BUF_APPEND_OFFSET`` treat a float buffer's last value
        // as an integer read of it, exactly like the Python reference's
        // ``int(value)``.
        (self.trunc() as i64) as u64
    }

    fn from_stream(raw: &[u8], typecode: Typecode) -> Self {
        if typecode.is_float() {
            stream_float(raw, typecode) as f32
        } else if typecode.is_signed() {
            stream_signed(raw, typecode) as f32
        } else {
            stream_bits(raw) as f32
        }
    }
}

impl FromBits for f64 {
    const IS_FLOAT: bool = true;

    fn from_bits(value: u64) -> Self {
        f64::from_bits(value)
    }

    fn to_bits(self) -> u64 {
        (self.trunc() as i64) as u64
    }

    fn from_stream(raw: &[u8], typecode: Typecode) -> Self {
        if typecode.is_float() {
            stream_float(raw, typecode)
        } else if typecode.is_signed() {
            stream_signed(raw, typecode) as f64
        } else {
            stream_bits(raw) as f64
        }
    }
}

/// A `Vec<T>` with interior mutability, shared with output owners.
struct TypedStorage<T: FromBits> {
    values: UnsafeCell<Vec<T>>,
}

// SAFETY: the machine writes through `&self` only while it owns the buffer;
// owners read after it stops. `reset` allocates a new storage instead of
// clearing the old one, so a handed-out pointer is never invalidated.
unsafe impl<T: FromBits> Send for TypedStorage<T> {}
unsafe impl<T: FromBits> Sync for TypedStorage<T> {}

impl<T: FromBits> TypedStorage<T> {
    fn new(init: &[i64]) -> TypedStorage<T> {
        let mut values = Vec::with_capacity(init.len());
        for value in init {
            values.push(T::from_bits(*value as u64));
        }
        TypedStorage {
            values: UnsafeCell::new(values),
        }
    }

    fn values(&self) -> &mut Vec<T> {
        // SAFETY: callers are the machine's single thread; see the type comment.
        unsafe { &mut *self.values.get() }
    }
}

impl<T: FromBits> Storage for TypedStorage<T> {
    fn append_bits(&self, value: u64) {
        self.values().push(T::from_bits(value));
    }

    fn append_stream(&self, raw: &[u8], count: usize, typecode: Typecode) {
        if count == 0 {
            return;
        }
        let width = typecode.size();
        let values = self.values();
        values.reserve(count);
        if width == std::mem::size_of::<T>() && typecode.is_float() == T::IS_FLOAT {
            for index in 0..count {
                values.push(T::from_be(&raw[index * width..index * width + width]));
            }
        } else {
            for index in 0..count {
                let start = index * width;
                values.push(T::from_stream(&raw[start..start + width], typecode));
            }
        }
    }

    fn reserve_more(&self, extra: usize) {
        let values = self.values();
        values.reserve(extra);
    }

    fn last_bits(&self) -> u64 {
        let values = self.values();
        values.last().map(|value| value.to_bits()).unwrap_or(0)
    }

    fn count(&self) -> u64 {
        self.values().len() as u64
    }

    fn element_size(&self) -> usize {
        std::mem::size_of::<T>()
    }

    fn bytes(&self) -> &[u8] {
        // SAFETY: the vector is not mutated after an owner is handed out; the
        // slice lives as long as `&self`, which the owner keeps alive via `Arc`.
        unsafe {
            let values = &*self.values.get();
            slice::from_raw_parts(
                values.as_ptr() as *const u8,
                values.len() * std::mem::size_of::<T>(),
            )
        }
    }
}

/// `bool` stores one byte per element, but every nonzero value is 1.
struct BoolStorage {
    values: UnsafeCell<Vec<u8>>,
}

// SAFETY: same invariant as `TypedStorage`: append-only until reset replaces it.
unsafe impl Send for BoolStorage {}
unsafe impl Sync for BoolStorage {}

impl BoolStorage {
    fn new(init: &[i64]) -> BoolStorage {
        BoolStorage {
            values: UnsafeCell::new(init.iter().map(|value| (*value != 0) as u8).collect()),
        }
    }

    fn values(&self) -> &mut Vec<u8> {
        // SAFETY: the machine is single-threaded; see `TypedStorage`.
        unsafe { &mut *self.values.get() }
    }
}

impl Storage for BoolStorage {
    fn append_bits(&self, value: u64) {
        self.values().push((value != 0) as u8);
    }

    fn append_stream(&self, raw: &[u8], count: usize, typecode: Typecode) {
        let values = self.values();
        values.reserve(count);
        for index in 0..count {
            let start = index * typecode.size();
            let element = &raw[start..start + typecode.size()];
            values.push(if typecode.is_float() {
                (stream_float(element, typecode) != 0.0) as u8
            } else {
                (stream_bits(element) != 0) as u8
            });
        }
    }

    fn reserve_more(&self, extra: usize) {
        self.values().reserve(extra);
    }

    fn last_bits(&self) -> u64 {
        self.values().last().copied().unwrap_or(0) as u64
    }

    fn count(&self) -> u64 {
        self.values().len() as u64
    }

    fn element_size(&self) -> usize {
        1
    }

    fn bytes(&self) -> &[u8] {
        // SAFETY: the vector is not mutated after an owner is handed out.
        unsafe { &*self.values.get() }
    }
}

fn make_storage(typecode: Typecode, decl: &BufferDecl) -> Result<Arc<dyn Storage>> {
    if decl.dtype == "bool" {
        return Ok(Arc::new(BoolStorage::new(&decl.init)));
    }
    Ok(match typecode {
        Typecode::Uint8 => Arc::new(TypedStorage::<u8>::new(&decl.init)),
        Typecode::Uint16 => Arc::new(TypedStorage::<u16>::new(&decl.init)),
        Typecode::Uint32 => Arc::new(TypedStorage::<u32>::new(&decl.init)),
        Typecode::Uint64 => Arc::new(TypedStorage::<u64>::new(&decl.init)),
        Typecode::Int8 => Arc::new(TypedStorage::<i8>::new(&decl.init)),
        Typecode::Int16 => Arc::new(TypedStorage::<i16>::new(&decl.init)),
        Typecode::Int32 => Arc::new(TypedStorage::<i32>::new(&decl.init)),
        Typecode::Int64 => Arc::new(TypedStorage::<i64>::new(&decl.init)),
        Typecode::Float32 => Arc::new(TypedStorage::<f32>::new(&decl.init)),
        Typecode::Float64 => Arc::new(TypedStorage::<f64>::new(&decl.init)),
    })
}

/// One output buffer: typed element storage plus the dtype it holds.
pub struct Buffer {
    decl: BufferDecl,
    typecode: Typecode,
    esize: usize,
    storage: Arc<dyn Storage>,
}

impl Buffer {
    /// Build a buffer, or fail when the declared dtype is not a buffer dtype.
    pub fn new(decl: BufferDecl) -> Result<Buffer> {
        let typecode = buffer_typecode(&decl.dtype).ok_or_else(|| {
            Error::module(format!(
                "buffer '{}': dtype '{}' is not a buffer dtype",
                decl.name, decl.dtype
            ))
        })?;
        let storage = make_storage(typecode, &decl)?;
        Ok(Buffer {
            esize: typecode.size(),
            decl,
            typecode,
            storage,
        })
    }

    /// Drop every element and restore the declared initial values.
    pub fn reset(&mut self) {
        self.storage = make_storage(self.typecode, &self.decl)
            .expect("the dtype was checked when the buffer was built");
    }

    /// Append one value given as a 64-bit slot.
    pub fn append_bits(&mut self, value: u64) {
        self.storage.append_bits(value);
    }

    /// Append `count` big-endian values decoded from `raw`.
    pub fn append_stream(&mut self, raw: &[u8], count: usize, typecode: Typecode) {
        self.storage.append_stream(raw, count, typecode);
    }

    /// Reserve room for `extra` more elements.
    pub fn reserve_more(&mut self, extra: usize) {
        self.storage.reserve_more(extra);
    }

    /// The last element as a 64-bit slot, or zero when the buffer is empty.
    pub fn last_bits(&self) -> u64 {
        self.storage.last_bits()
    }

    /// The number of elements in the buffer.
    pub fn count(&self) -> u64 {
        self.storage.count()
    }

    /// The declared buffer name.
    pub fn name(&self) -> &str {
        &self.decl.name
    }

    /// The declared dtype string.
    pub fn dtype(&self) -> &str {
        &self.decl.dtype
    }

    /// The declared buffer kind.
    pub fn kind(&self) -> BufferKind {
        self.decl.kind
    }

    /// The typecode the buffer stores.
    pub fn typecode(&self) -> Typecode {
        self.typecode
    }

    /// Bytes per element.
    pub fn element_size(&self) -> usize {
        self.esize
    }

    /// The buffer contents as raw bytes, in native order.
    pub fn bytes(&self) -> &[u8] {
        self.storage.bytes()
    }

    /// The shared storage, so a caller can keep a view of it alive.
    pub fn storage(&self) -> Arc<dyn Storage> {
        Arc::clone(&self.storage)
    }
}

/// Keep the declaration available for a reset without re-reading the module.
pub fn declarations_match(a: &BufferDecl, b: &BufferDecl) -> bool {
    a.name == b.name && a.dtype == b.dtype && a.kind == b.kind && a.init == b.init
}
