# Architecture Audit — DataLens CSV/Excel Compare Utility

_Audit date: 2026-05-14. Status: Phase 1 pending._

---

## System Overview

| Layer | Entry point | Technology |
|-------|-------------|------------|
| Web server | `run_web.py` → `web/api.py` | FastAPI + uvicorn, port 8787 |
| Background jobs | `web/api.py:_run_compare_job`, `_run_validate_job` | `threading.Thread(daemon=True)` |
| Progress streaming | `GET /api/progress/{job_id}` | SSE via `queue.Queue` |
| Compare engine | `compare.py:run_compare()` | Polars LazyFrame pipeline |
| Diff engine | `differ.py:diff_files()` | Polars full-outer join |
| Validation | `validator.py:validate_two_files()` | Polars + profiler |
| Profiler | `profiler.py:profile_file()` | Polars `.select()` per column |
| Key discovery | `key_discovery.py:discover_keys()` | Polars sample-based |
| Frontend | `web/static/index.html` | Single-file vanilla JS, dark theme |
| History | `web/history.py` | SQLite (`history.db`) |
| Reports | `reporters.py` | HTML / Excel / JSON / CSV |

No React. No build step. Frontend is a self-contained HTML file.

---

## Data Flow — Compare Run

```
POST /api/compare
  → _run_compare_job()
      1. load_metadata(file1)               metadata.py  — encoding, delimiter, row/col count
      2. load_metadata(file2)               metadata.py
      3. compare_schemas(m1, m2)            metadata.py  — schema diff, compatibility score
      4. _load_lazy_frame(file1)            compare.py   — pl.scan_csv, infer_schema_length=1000 ⚠
      5. _load_lazy_frame(file2)            compare.py
      6. profile_file(lf1)                 profiler.py  — PROFILING PASS 1a ⚠ DUPLICATE
      7. profile_file(lf2)                 profiler.py  — PROFILING PASS 1b ⚠ DUPLICATE
      8. discover_keys(lf1)                key_discovery.py — head(100_000) SAMPLE ONLY ⚠
      9. diff_files(lf1, lf2, key_cols)    differ.py    — head(1000) ALL COUNTS ⚠ CRITICAL
     10. validate_two_files(lf1, m1, ...)  validator.py
           └─ validate_file(lf1) → profile_file(lf1)  — PROFILING PASS 2a ⚠ DUPLICATE
           └─ validate_file(lf2) → profile_file(lf2)  — PROFILING PASS 2b ⚠ DUPLICATE
     11. _write_reports(result, output_dir)            — sample_diffs only ⚠
```

**Profile passes per compare run: 4 (should be 2).**
**Full-file diff counting: does not exist.**

---

## Data Flow — Validate Run

```
POST /api/validate
  → _run_validate_job()
      1. load_metadata(file1), load_metadata(file2)
      2. _load_lazy_frame(file1), _load_lazy_frame(file2)
      3. validate_two_files(lf1, m1, lf2, m2)
           └─ validate_file(lf1) → profile_file(lf1)
           └─ validate_file(lf2) → profile_file(lf2)
```

Validate flow is clean — no duplicate profiling.

---

## Key Module Responsibilities

### `compare.py`
- Orchestrates the full pipeline.
- Owns `CompareRequest` / `CompareResult` dataclasses.
- Calls profiler, key discovery, diff, validator, reporters in order.
- **Bug:** passes profile to neither the differ nor the validator — causes re-profiling.

### `differ.py`
- Performs the row-level diff via full-outer join.
- **Critical bug:** counts are derived from `sem_joined.head(1000)` — sample only.
- Owns `DiffResult`, `RowDiff`, `ColumnDiffStats`, `IgnoreRules`.
- Sentinel-column based added/removed detection: **not implemented** (uses fragile null heuristic).

### `profiler.py`
- Profiles a single file: all 4 null types, type distribution, min/max, sample values.
- **Bug:** `_infer_type()` always returns "string" as dominant type (normalization flaw).
- Performs 3 `.collect()` calls per column (should be 1).

### `key_discovery.py`
- `discover_keys()`: sample-based (head 100k). Finds single or two-column composite keys.
- `validate_key()`: full-scan. Correct. Called from validator only.
- **Gap:** `validate_key()` is not called before `diff_files()` in the compare flow.

### `validator.py`
- `validate_file()`: re-profiles the file internally. Should accept pre-computed profile.
- `validate_two_files()`: adds schema-drift checks to individual file reports.
- "Mixed Types" check (`_check_type_consistency`) is permanently inactive due to `_infer_type()` bug.

### `metadata.py`
- Reads encoding, delimiter (20-line sample), row count (full lazy scan), schema.
- `infer_schema_length=1000` — may mis-infer type for mixed-format columns.

### `reporters.py`
- `render_excel_diff()`: writes only `f1_values` for modified rows. Missing `f2_values`. **Bug.**
- All renderers output `sample_diffs` only (≤200 rows). No full-file export path.

### `web/api.py`
- Job lifecycle management, SSE streaming, serialization.
- `_serialize_diff()`: presents sample-based counts with no caveat. No `is_full_count` field.

---

## File Structure (must be preserved per CLAUDE.md)

```
project root/
├─ compare.py          orchestration
├─ differ.py           row diff engine
├─ profiler.py         column profiler
├─ validator.py        validation checks
├─ metadata.py         file metadata + schema diff
├─ key_discovery.py    key auto-detection
├─ reporters.py        HTML/Excel/JSON/CSV renderers
├─ encoding_detect.py  BOM/encoding detection
├─ utils.py            Progress, cancel, memory guards, fmt helpers
├─ run_web.py          launcher
├─ web/
│  ├─ __init__.py      (empty — keep)
│  ├─ api.py           FastAPI app
│  ├─ history.py       SQLite history
│  └─ static/
│     └─ index.html    complete frontend
├─ docs/               project documentation (this folder)
└─ testing_input_files/
   ├─ benchmark_500k_file_A.csv
   ├─ benchmark_500k_file_B.csv
   └─ benchmark_500k_summary.txt
```

---

## Architectural Constraints (from CLAUDE.md — non-negotiable)

1. Always `pl.scan_csv()`, never `pl.read_csv()`. `.collect()` only for stats, samples, row counts.
2. Cancel at phase granularity only — no mid-Polars interrupts.
3. Export files written to disk immediately, not lazily at download time.
4. Windows temp fix: `tempfile.mkstemp()` + `os.close(fd)` before writing Excel temp CSV.
5. Pydantic at API boundaries only — internal types are plain `@dataclass`.
6. `jobs` dict is the only global state.
7. Background threads must be `daemon=True`.

---

## Known Dead Code

| Symbol | File | Status |
|--------|------|--------|
| `LogCapture` class | `utils.py:74–91` | Never imported or used |
| `DiffResult.export_path` | `differ.py:51` | Declared, never set |
| `estimate_polars_ram()` | `utils.py:65–67` | Defined, never called |
