# CLAUDE.md

## Files to Read at Session Start

Always read these before making any code changes:

| File | Contains |
|------|----------|
| `docs/IMPLEMENTATION_ROADMAP.md` | Prioritised task list with file locations, acceptance criteria, and execution order |
| `docs/KNOWN_ISSUES.md` | All known bugs with severity, affected file+line, and fix target |
| `docs/COMPARE_ENGINE_RULES.md` | Authoritative spec for row classification, null handling, ignore rules, and output guarantees |
| `docs/DECISIONS.md` | All design decisions made, with rationale and invariants |
| `docs/BENCHMARK_TEST_PLAN.md` | Expected counts, assertion list, and benchmark file locations |
| `PROJECT_BRIEF.md` | Use-case context, quality goals, and phased workflow rules |

---

## AI Workflow Rules

These rules apply to every session. Do not violate them.

1. **Phase gate:** Never start Phase 2 work while Phase 1 tasks remain open. Check `docs/IMPLEMENTATION_ROADMAP.md` status before writing code.
2. **No invented requirements:** Only implement what is explicitly described in PROJECT_BRIEF.md, IMPLEMENTATION_ROADMAP.md, or COMPARE_ENGINE_RULES.md.
3. **Keep validation and diff separate:** `validator.py` must not perform row-comparison logic. `differ.py` must not perform validation checks. See module ownership table below.
4. **No silent sampling:** Any code path that produces counts from less than the full file must set `is_full_count=False` and `rows_scanned=N` explicitly.
5. **Phase output protocol:** After each phase, summarize: (a) what was found/changed, (b) files changed, (c) why the change was needed, (d) benchmark result impact. Pause for approval before the next phase if the change is major.
6. **Benchmark is the acceptance test:** A fix is not done until the benchmark assertions in `docs/BENCHMARK_TEST_PLAN.md` pass. Do not claim success without running them.
7. **Deterministic logic only:** No heuristics that produce different results on the same input. No clever shortcuts that sacrifice auditability.

---

## Architectural Invariants (Non-Negotiable)

These may never be violated. See `docs/DECISIONS.md` for rationale.

| ID | Rule |
|----|------|
| INV-1 | Always `pl.scan_csv()`. Never `pl.read_csv()`. Call `.collect()` only for stats, samples, and row counts. |
| INV-2 | Cancel checks at phase boundaries only — never inside a Polars expression (corrupts allocator). |
| INV-3 | Excel temp files: `tempfile.mkstemp()` + `os.close(fd)` before writing. Windows handle safety. |
| INV-4 | Pydantic only at API boundaries. Internal types are `@dataclass`. |
| INV-5 | Validation must not perform row-comparison logic. Diff must not perform validation logic. |
| INV-6 | Full-file counts only. Any sample-based count must set `is_full_count=False`. |
| INV-7 | Export files written eagerly to disk, not lazily at download time. |

---

## Module Ownership

Do not put logic in the wrong module.

| Module | Owns | Does NOT own |
|--------|------|-------------|
| `differ.py` | Row classification, diff counts, sample_diffs, ignore rule application | Validation checks, profiling, key discovery |
| `validator.py` | Null rates, type consistency, business rules, schema drift | Row-level diff, added/removed detection |
| `profiler.py` | Column-level statistics (null types, type distribution, min/max) | Row comparisons, diff counting |
| `key_discovery.py` | Key candidate detection, key uniqueness validation | Diff logic, validation |
| `compare.py` | Orchestration only — calls other modules in order | Any logic that belongs in sub-modules |

---

## Development Setup

```bash
pip install polars pydantic charset-normalizer openpyxl fastapi uvicorn python-multipart

# Run web UI
python run_web.py
# Opens at http://127.0.0.1:8787

# Smoke-test individual modules
set PYTHONUTF8=1
python utils.py
python encoding_detect.py
python key_discovery.py
python metadata.py
python profiler.py
python differ.py
python compare.py
```

---

## Benchmark Workflow

Benchmark files (do not modify):
```
testing_input_files/
├─ benchmark_500k_file_A.csv    # reference — 500k rows, integer Salary, ISO dates
├─ benchmark_500k_file_B.csv    # candidate — 500k rows, float Salary, mixed dates, ~9897 null Salary
└─ benchmark_500k_summary.txt   # ground truth counts
```

Required assertions after Phase 1 (from `docs/BENCHMARK_TEST_PLAN.md`):
```python
assert diff.added_rows == 5_000
assert diff.removed_rows == 5_000
assert diff.modified_rows == 50_000
assert diff.is_full_count == True
assert diff.total_rows_f1 == 500_000
```

Run baseline capture before Phase 1 work to document broken pre-fix behavior, then re-run after each step to track improvement.

---

## File Structure

| File | Purpose |
|------|---------|
| `compare.py` | Orchestration — calls metadata → profile → key discovery → diff → validate → report |
| `validator.py` | Validation checks with INFO/WARNING/ERROR/CRITICAL severity |
| `profiler.py` | Column profiling — all 4 null types in one `.select()` pass |
| `differ.py` | Vectorized row-level diff via full-outer join; semantic vs formatting-only distinction |
| `metadata.py` | Load file metadata, detect delimiter, compare schemas, compatibility score |
| `key_discovery.py` | Auto-detect/validate unique key columns; supports composite keys |
| `reporters.py` | HTML, Excel, JSON, CSV report renderers |
| `encoding_detect.py` | BOM → UTF-8 → charset-normalizer → cp1252 fallback |
| `utils.py` | `Progress`, `CancelledError`, `check_cancel`, memory guards |
| `run_web.py` | Launcher — dep check, uvicorn on port 8787, browser open |
| `web/api.py` | FastAPI app — all endpoints, async job runner, SSE streaming |
| `web/history.py` | SQLite history manager (`history.db`) |
| `web/static/index.html` | Complete dark-theme frontend |

**Folder structure must be preserved:** `web/` subfolder with `__init__.py`, `api.py`, `history.py`, and `static/index.html`.

---

## User Context

- **Use case:** Validates a candidate file before system upload; compares it against a known-good reference.
- **Scale:** Real files are two 4.7 GB CSVs with 13M rows, 22 columns each.
- **Platform:** Windows.
- **Preferences:** Dark theme UI, minimal copy-pasting, file browser over path input.
- **Backend pattern:** File paths on disk — browser does not upload file content (confirmed fast for large files).
