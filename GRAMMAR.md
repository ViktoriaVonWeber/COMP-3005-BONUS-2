# Design Log

One entry per working session. What I was trying to do, what I tried, what broke.
At least two places per module where AI gave me something wrong, slow, or quietly
incorrect, and how I found it.

## [2026-10-06] — Week 1: P1 → library refactor

**Trying to:** turn the Project 1 engine into four importable modules with no
printing and no file I/O, per Project 2 §1.3.

**Tried:** kept the P1 monolith and wrapped it. Failed the "importable independently"
rule because the operators still read in-memory relation lists.

**Broke:** projection deduplicated (set semantics). Under Project 2 §1.3 point 6,
projection must keep duplicates and dedup becomes a separate `distinct` operator.
P1 test case 23 changed meaning: `project[DID]` now returns 3 rows, not 2. P1 case 24
(`project[Name, Name]`) now keeps both columns instead of erroring. Both recorded
here as required by §1.3 point 7.

**Changed:** split into lexer / parser / tree / condition / operators / errors;
operators now pull tuples from an injected source; added `distinct`.

