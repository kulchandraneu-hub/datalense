# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

CSV/Excel comparison and validation utility (~5,500 lines across 15 files) that validates whether a candidate data file matches the structure of a reference file before upload. Supports files tested at 5GB+ with 13M+ rows.

**Web UI:** FastAPI backend + single-file HTML frontend served at `http://127.0.0.1:8787`

The web UI provides a vectorized comparison engine powered by **Polars** (Rust-based, 30-50x faster than pandas on large files).

**Build status:** All three phases complete and tested.

## Architecture & Design Decisions

**Core Engine (Polars):**
- Vectorized operations: native joins and type casts instead of Python row loops
- Key-based row matching: rows matched by unique column value, not position (handles reordered rows)
- Full-scan type checking: Polars native casts on ALL rows, zero rows missed (not sampled)
- All four null types (polars null, empty string, whitespace-only, textual null) collected in ONE `.select()` pass per column
- Always `pl.scan_csv()` (LazyFrame); `.collect()` only for profiler stats, diff samples, and row counts

**Web Layer:**
- FastAPI + single-file HTML frontend (no framework dependencies in frontend)
- File paths on disk (not browser uploads) for large files; backend reads directly
- `/api/compare` and `/api/validate` return a `job_id` immediately — comparison runs in a daemon thread
- Progress flows through `queue.Queue` (thread-safe) from worker thread → SSE generator → browser
- Cancel token (`threading.Event`) checked at phase boundaries (not mid-Polars — would corrupt allocator)
- Output files written with datetime postfix; never overwrites existing files
- SQLite history stored at `history.db` in project root

**Excel Support:**
- Detected by `.xlsx` extension; sheet names loaded via `openpyxl`
- Sheet picker shown in UI when multiple sheets exist
- Selected sheet converted to temp CSV (`tempfile.mkstemp` + `os.close(fd)` for Windows handle safety), fed to existing engine
- Temp file cleaned up in a `finally` block

**Memory Guards:**
- `check_memory_guard()` in `utils.py` returns `ok / warn / error / block` based on file size
- Called in `/api/file-info`; frontend shows yellow/red banner and disables Run button on `block`

## Development Setup & Commands

**Install dependencies:**
```bash
pip install polars pydantic charset-normalizer openpyxl fastapi uvicorn python-multipart
```

**Run Web UI:**
```bash
python run_web.py
# Opens at http://127.0.0.1:8787
```

**Smoke-test individual engine modules:**
```bash
set PYTHONUTF8=1
python utils.py
python encoding_detect.py
python key_discovery.py
python metadata.py
python profiler.py
python differ.py
python compare.py   # end-to-end test with temp CSV files
```

## File Structure & Key Files

| File | Purpose |
|------|---------|
| `compare.py` | Orchestration entry point — calls metadata → profile → key discovery → diff → validate → report |
| `validator.py` | Validation checks with INFO/WARNING/ERROR/CRITICAL severity; Pydantic config at JSON boundary only |
| `profiler.py` | Column profiling — all 4 null types in one `.select()` pass; inject progress callbacks here |
| `differ.py` | Vectorized row-level diff via full-outer join; semantic vs formatting-only distinction |
| `metadata.py` | Load file metadata, detect delimiter, compare schemas, compatibility score |
| `key_discovery.py` | Auto-detect/validate unique key columns; supports composite keys up to `max_composite` |
| `reporters.py` | HTML, Excel, JSON, CSV report renderers |
| `encoding_detect.py` | BOM → UTF-8 → charset-normalizer → cp1252 fallback |
| `utils.py` | `Progress`, `CancelledError`, `check_cancel`, memory guards, fmt helpers |
| `run_web.py` | Launcher — dep check, uvicorn on port 8787, browser open |
| `web/__init__.py` | Package marker (keep empty) |
| `web/api.py` | FastAPI app — all endpoints, async job runner, SSE streaming, result serialisation |
| `web/history.py` | SQLite history manager (`history.db`) — save/get/delete runs |
| `web/static/index.html` | Complete dark-theme frontend — Compare/Validate tabs, file browser modal, inline progress card, results with Diff/Validation/Profile sub-tabs, history sidebar |

**Folder structure must be preserved:** `web/` subfolder with `__init__.py`, `api.py`, `history.py`, and `static/index.html` stay intact.

## API Endpoints

| Endpoint | Method | Purpose |
|----------|--------|---------|
| `/api/file-info` | POST | Load file metadata; returns memory guard status and sheet names for Excel |
| `/api/compare` | POST | Start comparison in background thread; returns `{job_id}` immediately |
| `/api/validate` | POST | Start validation in background thread; returns `{job_id}` immediately |
| `/api/progress/{job_id}` | GET | SSE stream — `progress`, `complete`, `error`, `cancelled` events |
| `/api/cancel` | POST | Set cancel token for a running job |
| `/api/browse` | POST | Navigate local filesystem; returns dirs and CSV/xlsx files |
| `/api/history` | GET | Past runs from SQLite |
| `/api/export-csv` | GET | Download diff CSV from last comparison |
| `/api/job/{job_id}` | GET | Poll job status (non-SSE fallback) |

**SSE event format:**
```json
{"type": "progress", "phase": "Profiling", "detail": "Column 7/22: salary", "current": 7, "total": 22}
{"type": "complete", "result": { ...full serialised result... }}
{"type": "error",    "message": "..."}
{"type": "cancelled"}
```

## Key Architectural Constraints (Non-Negotiable)

1. **Always `pl.scan_csv()`, never `pl.read_csv()`** — `.collect()` only in profiler stats, diff samples, row counts
2. **Cancel at phase granularity only** — no mid-Polars interrupts (corrupts allocator)
3. **Export files to disk immediately** — not lazy at download time
4. **Windows temp fix** — `tempfile.mkstemp()` + `os.close(fd)` before writing Excel temp CSV
5. **Pydantic at boundaries only** — internal types are plain `@dataclass`
6. **`jobs` dict is the only global state** — CPython GIL makes simple dict ops atomic; no extra locking needed
7. **Daemon threads** — background job threads must be `daemon=True` so server shutdown is clean

## Pending Features (Prioritised)

### 1. Drag-Drop Upload for Small Files
- Add drop zone to file input area in `web/static/index.html`
- Files ≤ 500 MB: upload to server temp dir via multipart POST to new `/api/upload` endpoint
- Files > 500 MB: show warning suggesting Browse instead
- Uploaded temp path used like any disk path internally; clean up after job completes

### 2. Tiered Validation by File Size
- `validator.py` currently always full-scans regardless of file size
- Goal: for files > 2 GB sample-first then full-scan only failing columns
- Add `validation_tier` field to `ValidationConfig`

### 3. Duplicate Key Guidance
- Currently warns on duplicate keys but proceeds
- Add explicit guidance: offer to show duplicate rows, suggest dedup strategy

## Deferred (Phase 2 Roadmap)

- Column mapping UI (File 1 `employee_id` → File 2 `employeeId` side-by-side)
- Column selection checkboxes (`selected_columns` API param exists, no UI yet)
- Saved comparison templates / presets
- Per-column business rules UI (config exists in `ValidationConfig.business_rules`, no UI)
- Ignore-columns feature (`updated_at` always differs — skip it)
- PDF export

## Known Quirks

- `metadata.py`: uses `lf.collect_schema()` (not deprecated `lf.schema`) to avoid Polars PerformanceWarning
- `profiler.py`: whitespace-only count excludes empty strings (condition: `len > 0 AND stripped_len == 0`)
- `differ.py`: counts are from `.head(1000)` sample only — column change rates are approximate for large files
- `web/api.py`: jobs older than 1 hour are purged from the in-memory `jobs` dict on next request
- `encoding_detect.py`: charset-normalizer may misidentify single-byte encodings (latin-1/cp1252) as `cp1006`; fallback to `utf8-lossy` in Polars is safe

## User Context

- **Use case:** Validates candidate file before system upload
- **Scale:** Real test files are two 4.7 GB CSVs with 13M rows, 22 columns each
- **Platform:** Windows
- **Preferences:** Dark theme UI, minimal copy-pasting, file browser over path input
- **Architecture note:** File browser picks path only; file is NOT loaded into browser (confirmed fast for large files)

## Testing the Web UI

When making changes to the web UI or comparison engine:
1. Run `python run_web.py`
2. Test the golden path: load two files, run comparison, check results in all three sub-tabs (Diff / Validation / Profile)
3. For progress: test with a moderately large file (100 MB+) to confirm SSE updates are granular
4. Check history sidebar shows the completed run with correct summary
5. Test Excel: load a `.xlsx` file, confirm sheet picker appears, run comparison
6. Verify memory banner appears for large files (> 500 MB)
7. Test cancel: start a run and hit Stop — confirm inputs re-enable and progress card hides
