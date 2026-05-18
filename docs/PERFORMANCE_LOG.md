# Performance Log — DataLens

Tracks benchmark results after each phase.
Test file: benchmark_500k (500k rows, 22 cols)

| Phase | Task | Elapsed | Key Change |
|-------|------|---------|-----------|
| Baseline | — | ~344s | Original code |
| Phase 3 | P3-T1 | 93.9s | Full-file Polars counts |
| Phase 3 | P3-T2 | 85.7s | Single combined join |
| Phase 3 | P3-T3 | 2.1s | Batch profiling |
| Phase 3 | P3-T4 | 2.4s | Pre-sort + streaming sink |

## Notes
- P3-T3 was the dominant gain: 176 `.collect()` calls reduced to 2
- P3-T4 sort benefit realises at 5GB scale, not 500k rows
- All measurements on 500k row benchmark file

## Phase 4 — UI and Excel Support
No performance-sensitive changes.
Test count: 101 (was 97 entering Phase 4, +20 Excel loader tests in P4-T1, remaining tasks frontend-only).

## Phase 5 — Premium Features
No performance-sensitive changes in P5-T1 through P5-T4.
Test count: 117 (was 101 entering Phase 5, +16 new export tests in P5-T2).
