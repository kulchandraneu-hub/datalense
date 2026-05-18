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
