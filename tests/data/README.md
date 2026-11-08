# Golden corpus

The corpus is written by `tools/golden.py` from the development-only Python reference build (the oracle) and replayed by every build in `tests/test_golden.py`. Each `*.dtg` file contains one module, one basket, the entry offsets, the cursor offset, the expected status and instruction index, and the expected buffers and states. The format is defined in [`docs/spec/conformance.md`](../../docs/spec/conformance.md) §2.

Rules:

- the corpus is written by `tools/golden.py` from the development-only Python reference build, never by the build under test;
- a change to the module format, the compiler, or the oracle re-records the corpus in the same commit;
- a new opcode adds a case before it adds an implementation;
- the files are small binaries; do not edit them by hand.

`tools/golden.py --check` fails when the committed corpus is stale.
