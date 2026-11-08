//! The C ABI exported by the Rust machine.
//!
//! This is the only module with `unsafe` code and the only module with
//! `#[no_mangle]`. It mirrors `kernel/include/detree.h`; the Python package
//! loads either library through that header's declarations.

use std::cell::RefCell;
use std::ffi::c_char;
use std::panic::{catch_unwind, AssertUnwindSafe};
use std::ptr;
use std::slice;
use std::sync::Arc;

use crate::buffer::Storage;
use crate::errors::Error;
use crate::isa::{C_API_VERSION, ISA_MAJOR, ISA_MINOR};
use crate::machine::Machine;

/// An opaque machine: one decoded module, reused basket after basket.
pub struct DetreeMachine {
    machine: Machine,
}

/// One retained buffer storage.
pub struct DetreeOwner {
    _storage: Arc<dyn Storage>,
}

/// The last failure on the thread; mirrors `detree_error` in `c_api.h`.
#[repr(C)]
#[derive(Clone, Copy)]
pub struct DetreeError {
    pub status: i32,
    pub reserved: i32,
    pub ip: i64,
    pub cursor: i64,
    pub message: *const c_char,
    pub message_length: usize,
    pub function: *const c_char,
    pub function_length: usize,
}

/// One output handle; mirrors `detree_output` in `c_api.h`.
#[repr(C)]
pub struct DetreeOutput {
    pub data: *const u8,
    pub length: usize,
    pub itemsize: usize,
    pub owner: *mut DetreeOwner,
}

/// One state slot; mirrors `detree_state` in `c_api.h`.
#[repr(C)]
pub struct DetreeState {
    pub name: *const c_char,
    pub name_length: usize,
    pub is_name: i32,
    pub reserved: u32,
    pub value: u64,
    pub text: *const c_char,
    pub text_length: usize,
}

// Status values, matching `detree_status` in the C header.
const DETREE_OK: i32 = 0;
const DETREE_ERROR: i32 = 8;
const DETREE_BAD_ARGUMENT: i32 = 9;

// Capability bits, matching `DETREE_CAP_*` in the C header.
const DETREE_CAP_ALL: u64 = 0x1F;

/// Everything `detree_last_error` reports, owned so the pointers are safe.
struct LastError {
    status: i32,
    ip: i64,
    cursor: i64,
    message: String,
    function: String,
}

impl LastError {
    fn empty() -> LastError {
        LastError {
            status: DETREE_OK,
            ip: -1,
            cursor: -1,
            message: String::new(),
            function: String::new(),
        }
    }
}

thread_local! {
    static LAST_ERROR: RefCell<LastError> = RefCell::new(LastError::empty());
}

fn clear_error() {
    LAST_ERROR.with(|slot| *slot.borrow_mut() = LastError::empty());
}

fn record(error: &Error) {
    LAST_ERROR.with(|slot| {
        let mut last = slot.borrow_mut();
        last.status = error.kind.status();
        last.ip = error.ip;
        last.cursor = error.cursor;
        last.message = error.message.clone();
        last.function = error.function.clone();
    });
}

fn record_status(status: i32, message: impl Into<String>) {
    LAST_ERROR.with(|slot| {
        let mut last = slot.borrow_mut();
        last.status = status;
        last.ip = -1;
        last.cursor = -1;
        last.message = message.into();
        last.function.clear();
    });
}

fn panic_message(payload: Box<dyn std::any::Any + Send>) -> String {
    if let Some(message) = payload.downcast_ref::<&str>() {
        format!("panic: {message}")
    } else if let Some(message) = payload.downcast_ref::<String>() {
        format!("panic: {message}")
    } else {
        "panic in the Rust machine".to_owned()
    }
}

#[no_mangle]
pub extern "C" fn detree_c_api_version() -> u32 {
    C_API_VERSION
}

#[no_mangle]
pub extern "C" fn detree_backend_name() -> *const c_char {
    b"rust\0".as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn detree_backend_version() -> *const c_char {
    concat!(env!("CARGO_PKG_VERSION"), "\0").as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn detree_isa_major() -> u16 {
    ISA_MAJOR
}

#[no_mangle]
pub extern "C" fn detree_isa_minor() -> u16 {
    ISA_MINOR
}

#[no_mangle]
pub extern "C" fn detree_capabilities() -> u64 {
    DETREE_CAP_ALL
}

#[no_mangle]
pub extern "C" fn detree_machine_new(module: *const u8, size: usize) -> *mut DetreeMachine {
    clear_error();
    if module.is_null() {
        record_status(DETREE_BAD_ARGUMENT, "module is null");
        return ptr::null_mut();
    }
    // SAFETY: the caller passed a readable buffer of `size` bytes, as the ABI
    // requires. A zero-size module is rejected by the decoder.
    let raw = unsafe { slice::from_raw_parts(module, size) };
    match catch_unwind(|| Machine::new(raw)) {
        Ok(Ok(machine)) => Box::into_raw(Box::new(DetreeMachine { machine })),
        Ok(Err(error)) => {
            record(&error);
            ptr::null_mut()
        }
        Err(payload) => {
            record_status(DETREE_ERROR, panic_message(payload));
            ptr::null_mut()
        }
    }
}

#[no_mangle]
pub extern "C" fn detree_machine_free(machine: *mut DetreeMachine) {
    if machine.is_null() {
        return;
    }
    // SAFETY: the pointer came from `detree_machine_new` and is freed once.
    unsafe { drop(Box::from_raw(machine)) };
}

#[no_mangle]
pub extern "C" fn detree_machine_reset(machine: *mut DetreeMachine) {
    if machine.is_null() {
        return;
    }
    clear_error();
    // SAFETY: the caller passed a live machine.
    let machine = unsafe { &mut *machine };
    if catch_unwind(AssertUnwindSafe(|| machine.machine.reset())).is_err() {
        record_status(DETREE_ERROR, "panic while resetting the machine");
    }
}

#[no_mangle]
pub extern "C" fn detree_machine_isa_major(machine: *const DetreeMachine) -> u16 {
    if machine.is_null() {
        return 0;
    }
    // SAFETY: the caller passed a live machine.
    unsafe { (*machine).machine.module.isa_major }
}

#[no_mangle]
pub extern "C" fn detree_machine_isa_minor(machine: *const DetreeMachine) -> u16 {
    if machine.is_null() {
        return 0;
    }
    // SAFETY: the caller passed a live machine.
    unsafe { (*machine).machine.module.isa_minor }
}

#[no_mangle]
pub extern "C" fn detree_buffer_count(machine: *const DetreeMachine) -> usize {
    if machine.is_null() {
        return 0;
    }
    // SAFETY: the caller passed a live machine.
    unsafe { (*machine).machine.buffers.len() }
}

#[no_mangle]
pub extern "C" fn detree_buffer_name(
    machine: *const DetreeMachine,
    index: usize,
    length: *mut usize,
) -> *const c_char {
    if machine.is_null() {
        return ptr::null();
    }
    // SAFETY: the caller passed a live machine.
    let machine = unsafe { &*machine };
    let Some(buffer) = machine.machine.buffers.get(index) else {
        return ptr::null();
    };
    if !length.is_null() {
        // SAFETY: the caller passed a writable pointer, as the ABI requires.
        unsafe { *length = buffer.name().len() };
    }
    buffer.name().as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn detree_buffer_dtype(
    machine: *const DetreeMachine,
    index: usize,
    length: *mut usize,
) -> *const c_char {
    if machine.is_null() {
        return ptr::null();
    }
    // SAFETY: the caller passed a live machine.
    let machine = unsafe { &*machine };
    let Some(buffer) = machine.machine.buffers.get(index) else {
        return ptr::null();
    };
    if !length.is_null() {
        // SAFETY: the caller passed a writable pointer, as the ABI requires.
        unsafe { *length = buffer.dtype().len() };
    }
    buffer.dtype().as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn detree_run(
    machine: *mut DetreeMachine,
    data: *const u8,
    size: usize,
    offsets: *const i64,
    n_offsets: usize,
    cursor_offset: usize,
) -> i32 {
    clear_error();
    if machine.is_null() {
        record_status(DETREE_BAD_ARGUMENT, "machine is null");
        return DETREE_BAD_ARGUMENT;
    }
    if data.is_null() && size != 0 {
        record_status(DETREE_BAD_ARGUMENT, "data is null but size is not zero");
        return DETREE_BAD_ARGUMENT;
    }
    if offsets.is_null() {
        record_status(DETREE_BAD_ARGUMENT, "offsets is null");
        return DETREE_BAD_ARGUMENT;
    }

    // SAFETY: the caller passed readable buffers of the given lengths.
    let data = if size == 0 {
        &[]
    } else {
        unsafe { slice::from_raw_parts(data, size) }
    };
    // SAFETY: the caller passed `n_offsets` readable offsets.
    let offsets = unsafe { slice::from_raw_parts(offsets, n_offsets) };
    // SAFETY: the caller passed a live machine.
    let machine = unsafe { &mut *machine };

    match catch_unwind(AssertUnwindSafe(|| {
        machine.machine.run(data, offsets, cursor_offset)
    })) {
        Ok(Ok(())) => DETREE_OK,
        Ok(Err(error)) => {
            record(&error);
            error.kind.status()
        }
        Err(payload) => {
            record_status(DETREE_ERROR, panic_message(payload));
            DETREE_ERROR
        }
    }
}

#[no_mangle]
pub extern "C" fn detree_read(
    machine: *mut DetreeMachine,
    data: *const u8,
    size: usize,
    offsets: *const i64,
    n_offsets: usize,
    cursor_offset: usize,
    outputs: *mut DetreeOutput,
    output_capacity: usize,
    outputs_written: *mut usize,
) -> i32 {
    if !outputs_written.is_null() {
        // SAFETY: the caller passed a writable pointer, as the ABI requires.
        unsafe { *outputs_written = 0 };
    }
    let status = detree_run(machine, data, size, offsets, n_offsets, cursor_offset);
    if status != DETREE_OK {
        return status;
    }
    let written = detree_acquire_outputs(machine, outputs, output_capacity);
    if !outputs_written.is_null() {
        // SAFETY: the caller passed a writable pointer, as the ABI requires.
        unsafe { *outputs_written = written };
    }
    DETREE_OK
}

#[no_mangle]
pub extern "C" fn detree_acquire_outputs(
    machine: *mut DetreeMachine,
    out: *mut DetreeOutput,
    capacity: usize,
) -> usize {
    if machine.is_null() || out.is_null() {
        return 0;
    }
    // SAFETY: the caller passed a live machine.
    let machine = unsafe { &mut *machine };
    let count = capacity.min(machine.machine.buffers.len());
    for index in 0..count {
        let buffer = &machine.machine.buffers[index];
        let owner = Box::into_raw(Box::new(DetreeOwner {
            _storage: buffer.storage(),
        }));
        // SAFETY: `out` has capacity for `capacity` entries.
        unsafe {
            *out.add(index) = DetreeOutput {
                data: buffer.bytes().as_ptr(),
                length: buffer.count() as usize,
                itemsize: buffer.element_size(),
                owner,
            };
        }
    }
    count
}

#[no_mangle]
pub extern "C" fn detree_owner_release(owner: *mut DetreeOwner) {
    if owner.is_null() {
        return;
    }
    // SAFETY: the pointer came from `detree_acquire_outputs` and is freed once.
    unsafe { drop(Box::from_raw(owner)) };
}

#[no_mangle]
pub extern "C" fn detree_state_count(machine: *const DetreeMachine) -> usize {
    if machine.is_null() {
        return 0;
    }
    // SAFETY: the caller passed a live machine.
    unsafe { (*machine).machine.states.len() }
}

#[no_mangle]
pub extern "C" fn detree_state_name(
    machine: *const DetreeMachine,
    index: usize,
    length: *mut usize,
) -> *const c_char {
    if machine.is_null() {
        return ptr::null();
    }
    // SAFETY: the caller passed a live machine.
    let machine = unsafe { &*machine };
    let Some(decl) = machine.machine.module.states.get(index) else {
        return ptr::null();
    };
    if !length.is_null() {
        // SAFETY: the caller passed a writable pointer, as the ABI requires.
        unsafe { *length = decl.name.len() };
    }
    decl.name.as_ptr() as *const c_char
}

#[no_mangle]
pub extern "C" fn detree_get_states(
    machine: *const DetreeMachine,
    out: *mut DetreeState,
    capacity: usize,
) -> usize {
    if machine.is_null() || out.is_null() {
        return 0;
    }
    // SAFETY: the caller passed a live machine.
    let machine = unsafe { &*machine };
    let count = capacity.min(machine.machine.states.len());
    for index in 0..count {
        let decl = machine.machine.state_decl(index);
        let value = machine.machine.state_value(index);
        let text = value.text.unwrap_or("");
        let text_ptr = if value.text.is_some() {
            text.as_ptr() as *const c_char
        } else {
            ptr::null()
        };
        // SAFETY: `out` has capacity for `capacity` entries.
        unsafe {
            *out.add(index) = DetreeState {
                name: decl.name.as_ptr() as *const c_char,
                name_length: decl.name.len(),
                is_name: decl.is_string as i32,
                reserved: 0,
                value: value.raw,
                text: text_ptr,
                text_length: if value.text.is_some() { text.len() } else { 0 },
            };
        }
    }
    count
}

#[no_mangle]
pub extern "C" fn detree_last_error() -> DetreeError {
    LAST_ERROR.with(|slot| {
        let last = slot.borrow();
        DetreeError {
            status: last.status,
            reserved: 0,
            ip: last.ip,
            cursor: last.cursor,
            message: if last.message.is_empty() {
                ptr::null()
            } else {
                last.message.as_ptr() as *const c_char
            },
            message_length: last.message.len(),
            function: if last.function.is_empty() {
                std::ptr::null()
            } else {
                last.function.as_ptr() as *const c_char
            },
            function_length: last.function.len(),
        }
    })
}
