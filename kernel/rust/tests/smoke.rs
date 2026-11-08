use rustdetree::capi::*;

fn put8(out: &mut Vec<u8>, value: u8) {
    out.push(value);
}

fn put16(out: &mut Vec<u8>, value: u16) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn put32(out: &mut Vec<u8>, value: u32) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn put64(out: &mut Vec<u8>, value: u64) {
    out.extend_from_slice(&value.to_le_bytes());
}

fn put_text(out: &mut Vec<u8>, text: &str) {
    put16(out, text.len() as u16);
    out.extend_from_slice(text.as_bytes());
}

/// One function `main`, arity 0, reg_count 0, a single HALT.
fn halt_module() -> Vec<u8> {
    let mut out = Vec::new();
    out.extend_from_slice(b"DETR");
    put16(&mut out, 0); // isa_major
    put16(&mut out, 1); // isa_minor
    put16(&mut out, 0); // entry
    put16(&mut out, 0); // regs_max
    put32(&mut out, 0); // buffers
    put32(&mut out, 0); // states
    put32(&mut out, 0); // strings
    put32(&mut out, 0); // layouts
    put32(&mut out, 1); // functions

    put32(&mut out, 0); // function id
    put_text(&mut out, "main");
    put8(&mut out, 0); // arity
    put16(&mut out, 0); // reg_count
    put16(&mut out, 0); // reserved
    put32(&mut out, 0); // code_off
    put32(&mut out, 1); // code_len

    put32(&mut out, 1); // instruction count
    put8(&mut out, 0); // HALT
    put8(&mut out, 0); // rd
    put8(&mut out, 0); // ra
    put8(&mut out, 0); // rb
    put64(&mut out, 0); // imm1
    put64(&mut out, 0); // imm2
    out
}

#[test]
fn c_api_reports_identity() {
    assert_eq!(detree_c_api_version(), 1);
    assert_eq!(detree_isa_major(), 0);
    assert_eq!(detree_isa_minor(), 1);
    assert_eq!(detree_capabilities(), 0x1F);

    // SAFETY: the pointers come from static NUL-terminated byte strings.
    unsafe {
        let name = std::ffi::CStr::from_ptr(detree_backend_name());
        assert_eq!(name.to_str().unwrap(), "rust");
        let version = std::ffi::CStr::from_ptr(detree_backend_version());
        assert!(!version.to_string_lossy().is_empty());
    }
}

#[test]
fn null_module_is_a_caller_error() {
    let machine = detree_machine_new(std::ptr::null(), 0);
    assert!(machine.is_null());

    let error = detree_last_error();
    assert_eq!(error.status, 9); // DETREE_BAD_ARGUMENT
    assert_eq!(error.ip, -1);
    assert_eq!(error.cursor, -1);
    assert!(!error.message.is_null());
}

#[test]
fn hlt_module_runs_through_the_c_abi() {
    let module = halt_module();
    let machine = detree_machine_new(module.as_ptr(), module.len());
    assert!(!machine.is_null());

    assert_eq!(detree_buffer_count(machine), 0);
    assert_eq!(detree_state_count(machine), 0);

    let offsets = [0i64, 0i64];
    assert_eq!(
        detree_run(
            machine,
            std::ptr::null(),
            0,
            offsets.as_ptr(),
            offsets.len(),
            0,
        ),
        0
    );

    let mut outputs = [DetreeOutput {
        data: std::ptr::null(),
        length: 0,
        itemsize: 0,
        owner: std::ptr::null_mut(),
    }];
    assert_eq!(detree_acquire_outputs(machine, outputs.as_mut_ptr(), 1), 0);

    // One offset is not a basket: refuse it.
    assert_eq!(
        detree_run(machine, std::ptr::null(), 0, offsets.as_ptr(), 1, 0),
        4 // DETREE_READ_LENGTH_MISMATCH
    );

    detree_machine_free(machine);
    detree_owner_release(std::ptr::null_mut());
}
