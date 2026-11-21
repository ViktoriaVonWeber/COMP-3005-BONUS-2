# DESIGN_M1 — Storage Engine and Buffer Pool

## 1. Record format (M1 §2)

- **INT**: 4 bytes, signed two's complement, little-endian.
- **VARCHAR(n)**: 2-byte unsigned length L, then L UTF-8 bytes. L ≤ n ≤ 255.
- No record header, no null bitmap (no NULLs in this project).
- Fixed length when all columns are INT; variable otherwise.

**§2 worked example verified by test** (`tests/test_m1_record.py::test_m1_2_worked_example`):
schema `(a INT, b VARCHAR(5), c INT)`, tuple `(42, 'bob', 7)` → 13 bytes,
hex `2A 00 00 00 03 00 62 6F 62 07 00 00 00`.

## 2. Page layout (M1 §3) — from a real page in my file

Layout (all fields required by M1 §3):
