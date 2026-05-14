# DataLens App — Implementation Brief

## Goal
Improve the CSV/Excel compare and validation app so it can reliably handle large files, produce correct validation results, and generate trustworthy compare output.

## Current modules
1. File Validator
   - Validates schema, types, nulls, and compatibility
   - Must NOT perform added/removed/changed comparison logic

2. File Compare
   - Compares files by key
   - Detects added, removed, and modified rows/cells

## Benchmark files to use
- benchmark_500k_file_A.csv
- benchmark_500k_file_B.csv
- benchmark_500k_summary.txt

These files must be used as test inputs during development and validation.

## Expected benchmark behavior
The modified benchmark file contains:
- changed rows
- added rows
- removed rows
- nulls in Salary
- mixed JoinDate formats
- some formatting-only changes
- some semantic changes

The app should correctly detect these cases.

## Important quality goals
- Correctness first
- No hallucinated behavior
- Use deterministic logic
- Distinguish semantic changes from formatting-only changes
- Make sample vs full-compare behavior explicit
- Keep validation separate from compare

## Known issues to investigate
- Compare may be sample-based instead of full-file based
- Added/removed rows may not be detected correctly
- Candidate column profiling may over-convert mixed columns into string
- UI needs clearer compare summary and stronger trust indicators
- Performance needs improvement for 500k-row files

## Required development workflow
Work in phases.

### Phase 1 — Audit
- Inspect current architecture
- Identify validation flow
- Identify compare flow
- Identify where row matching happens
- Identify where sampling is used
- Produce a TODO list before making changes

### Phase 2 — Correctness fixes
- Fix key-based matching if needed
- Ensure added/removed rows are detected
- Ensure modified rows are counted correctly
- Ensure formatting-only changes are separated from semantic changes
- Ensure validation outputs are accurate

### Phase 3 — Performance improvements
- Improve large-file handling
- Reduce memory pressure
- Improve profiling speed
- Improve compare speed
- Avoid unnecessary full-table rendering

### Phase 4 — UI improvements
- Add clear top-level summary
- Show compare method used
- Show whether compare is full-file or sampled
- Improve diff readability
- Add better tooltip or side-by-side old/new view

### Phase 5 — Regression testing
- Run benchmark files again
- Compare results against expected behavior
- Report what changed
- Do not claim success unless the benchmark output is consistent

## Output expectations
After each phase:
1. Summarize what was found
2. List files changed
3. Explain why the change was needed
4. Show benchmark result impact
5. Pause and wait for approval before the next phase if the change is major

## Rules
- Do not invent requirements
- Do not remove existing working behavior without explaining why
- Do not mix validation logic with compare logic
- Do not silently sample data unless explicitly marked as sample mode
- Prefer clear, auditable logic over clever shortcuts