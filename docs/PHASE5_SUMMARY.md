# Phase 5 Summary — DataLens (In Progress)

Started: 2026-05-18
Tests: 117 passing

## Completed

### P5-T1 File Paths in DiffResult
`f1_path` and `f2_path` added to `DiffResult` as `Optional[str] = None`.
Populated from `FileMetadata` in `diff_files()`. Serialized in `api.py` with `getattr` fallback.
Excel Summary sheet now shows both filenames.

### P5-T2 Full Diff Export
Entire diff streamed to CSV via Polars `sink_csv`. No RAM overhead.
`diff_lf` LazyFrame added to `DiffResult` — filtered to changed rows only, never collected.
Previously only 200-row sample was exported.

### P5-T3 Ignore Rules UI
Collapsible panel in Compare tab with controls for case, whitespace, null-vs-blank, date format.
`getIgnoreRules()` sends `ignore_rules` dict to API. `api.py` converts to `IgnoreRules` dataclass.
Wired end-to-end to the engine. Backward compatible with legacy flat fields.

### P5-T4 Sample-First Preview Mode
Preview checkbox truncates both files to 100k rows via `.head()` before compare.
Yellow banner on results. `(sample)` suffix on metric counts.
Run Full Compare button re-runs without preview. `preview_mode=False` is fully backward compatible.

## Pending

### P5-T5 Windows Packaging
One-click installer for non-technical users.
`run_datalense.bat` + `run_datalense.sh` launcher scripts + `docs/INSTALL.md`.
