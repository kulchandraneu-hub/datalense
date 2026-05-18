"""
FastAPI backend for the CSV/Excel comparison utility.

Job lifecycle:
  POST /api/compare  →  returns {job_id}
  GET  /api/progress/{job_id}  →  SSE stream
  POST /api/cancel   →  sets cancel token

Jobs run in daemon threads. Progress events flow through a queue.Queue
(thread-safe). The SSE generator drains the queue without blocking the
asyncio event loop.
"""

import sys
import os
# Ensure project root is importable regardless of working directory.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import asyncio
import json
import queue
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional, Any

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse, FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from compare import run_compare, CompareRequest as CoreCompareRequest, CompareResult, _load_lazy_frame
from validator import validate_two_files, ValidationConfig, ColumnRuleConfig, ValidationReport
from metadata import load_metadata
from differ import IgnoreRules
from utils import check_memory_guard, fmt_bytes, CancelledError, Progress
from web.history import HistoryManager


# ---------------------------------------------------------------------------
# App setup
# ---------------------------------------------------------------------------

app = FastAPI(title="CSV Compare API", version="1.0.0")
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

history = HistoryManager()


# ---------------------------------------------------------------------------
# Job management
# ---------------------------------------------------------------------------

@dataclass
class Job:
    job_id: str
    job_type: str               # "compare" | "validate"
    status: str                 # "running" | "complete" | "error" | "cancelled"
    cancel_token: threading.Event
    progress_queue: "queue.Queue[dict]"
    result: Optional[dict] = None
    error: Optional[str] = None
    started_at: float = field(default_factory=time.time)


# CPython GIL makes simple dict get/set atomic — no lock needed.
jobs: dict[str, Job] = {}

# All comparison exports go here, created on first use.
EXPORTS_DIR = Path(__file__).parent.parent / "exports"
EXPORTS_DIR.mkdir(parents=True, exist_ok=True)

# Path to the most recent CSV diff export (served by /api/export-csv).
_last_csv_path: Optional[Path] = None

# Purge jobs older than 1 hour to prevent unbounded memory growth.
_JOB_TTL_S = 3600


def _purge_old_jobs() -> None:
    cutoff = time.time() - _JOB_TTL_S
    stale = [jid for jid, j in jobs.items() if j.started_at < cutoff]
    for jid in stale:
        del jobs[jid]


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------

class FileInfoRequest(BaseModel):
    path: str
    sheet_name: Optional[str] = None


class CompareAPIRequest(BaseModel):
    file1: str
    file2: str
    sheet1: Optional[str] = None
    sheet2: Optional[str] = None
    key_columns: Optional[list[str]] = None
    key_columns_f1: Optional[list[str]] = None
    key_columns_f2: Optional[list[str]] = None
    column_map: Optional[list[dict]] = None
    compare_columns: Optional[list[str]] = None
    ignore_case: bool = False
    ignore_whitespace: bool = False
    output_dir: Optional[str] = None


class ValueSetPair(BaseModel):
    f1_col: str
    f2_col: str


class ValueSetRequest(BaseModel):
    file1: str
    file2: str
    sheet1: Optional[str] = None
    sheet2: Optional[str] = None
    pairs: list[ValueSetPair]


class ValidateAPIRequest(BaseModel):
    file1: str
    file2: str
    sheet1: Optional[str] = None
    sheet2: Optional[str] = None
    key_columns: Optional[list[str]] = None
    null_warn_threshold: float = 0.50
    null_error_threshold: float = 0.90
    business_rules: list[dict] = []


class BrowseRequest(BaseModel):
    path: str = ""


class CancelRequest(BaseModel):
    job_id: str


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.post("/api/file-info")
async def file_info(req: FileInfoRequest):
    """Return metadata for a file + memory guard status."""
    path = Path(req.path)
    if not path.exists():
        raise HTTPException(404, f"File not found: {req.path}")
    if not path.is_file():
        raise HTTPException(400, f"Not a file: {req.path}")

    mem_status, mem_msg = check_memory_guard(path.stat().st_size)

    try:
        meta = load_metadata(path, req.sheet_name)
    except Exception as exc:
        raise HTTPException(500, f"Could not read file: {exc}")

    # Excel: return sheet names so the UI can show a picker.
    sheet_names: list[str] = []
    if path.suffix.lower() == ".xlsx":
        try:
            import openpyxl
            wb = openpyxl.load_workbook(path, read_only=True)
            sheet_names = wb.sheetnames
            wb.close()
        except Exception:
            pass

    return {
        "path": str(path),
        "name": path.name,
        "size_bytes": meta.size_bytes,
        "size_display": fmt_bytes(meta.size_bytes),
        "encoding": meta.encoding.encoding,
        "delimiter": meta.delimiter,
        "row_count": meta.row_count,
        "column_count": meta.column_count,
        "columns": meta.columns,
        "dtypes": meta.dtypes,
        "sheet_name": meta.sheet_name,
        "sheet_names": sheet_names,
        "memory_status": mem_status,
        "memory_message": mem_msg,
    }


@app.get("/api/headers")
async def get_headers(path: str, sheet: Optional[str] = None):
    """Return column names for a file without a full metadata scan."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {path}")
    try:
        meta = load_metadata(p, sheet)
        return {"columns": meta.columns}
    except Exception as exc:
        raise HTTPException(500, f"Could not read headers: {exc}")


@app.get("/api/sheets")
async def get_sheets(path: str):
    """Return sheet names for an Excel file. Returns [] for CSV/TSV."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, f"File not found: {path}")
    try:
        from excel_loader import list_sheets
        return {"sheets": list_sheets(p)}
    except Exception as exc:
        raise HTTPException(500, f"Could not read sheets: {exc}")


@app.post("/api/value_set_compare")
async def value_set_compare(req: ValueSetRequest):
    """
    For each column pair, return the distinct value sets and their intersections.
    Uses pl.scan_csv() — never read_csv(). Collects unique values per column only.
    """
    if not req.pairs:
        raise HTTPException(400, "At least one column pair required")

    p1, p2 = Path(req.file1), Path(req.file2)
    for p in (p1, p2):
        if not p.exists():
            raise HTTPException(404, f"File not found: {p}")

    temp_files: list[Path] = []
    try:
        m1 = load_metadata(p1, req.sheet1)
        m2 = load_metadata(p2, req.sheet2)
        lf1 = _load_lazy_frame(p1, m1, temp_files)
        lf2 = _load_lazy_frame(p2, m2, temp_files)

        results = []
        for pair in req.pairs:
            try:
                raw1 = lf1.select(pl.col(pair.f1_col).cast(pl.Utf8)).unique().collect()[pair.f1_col].to_list()
                raw2 = lf2.select(pl.col(pair.f2_col).cast(pl.Utf8)).unique().collect()[pair.f2_col].to_list()
                vals1 = {v for v in raw1 if v is not None}
                vals2 = {v for v in raw2 if v is not None}
                only_f1 = sorted(vals1 - vals2)
                only_f2 = sorted(vals2 - vals1)
                in_both = sorted(vals1 & vals2)
                results.append({
                    "f1_col": pair.f1_col,
                    "f2_col": pair.f2_col,
                    "only_in_f1_count": len(only_f1),
                    "only_in_f2_count": len(only_f2),
                    "in_both_count": len(in_both),
                    "only_in_f1_sample": only_f1[:500],
                    "only_in_f2_sample": only_f2[:500],
                    "in_both_sample": in_both[:500],
                })
            except Exception as exc:
                results.append({
                    "f1_col": pair.f1_col,
                    "f2_col": pair.f2_col,
                    "error": str(exc),
                })
    finally:
        for tmp in temp_files:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass

    return {"results": results}


@app.post("/api/compare")
async def start_compare(req: CompareAPIRequest):
    """Start a full comparison in a background thread. Returns job_id."""
    _purge_old_jobs()
    job_id = uuid.uuid4().hex[:8]
    job = Job(
        job_id=job_id,
        job_type="compare",
        status="running",
        cancel_token=threading.Event(),
        progress_queue=queue.Queue(),
    )
    jobs[job_id] = job
    threading.Thread(target=_run_compare_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.post("/api/validate")
async def start_validate(req: ValidateAPIRequest):
    """Start a validation-only run in a background thread. Returns job_id."""
    _purge_old_jobs()
    job_id = uuid.uuid4().hex[:8]
    job = Job(
        job_id=job_id,
        job_type="validate",
        status="running",
        cancel_token=threading.Event(),
        progress_queue=queue.Queue(),
    )
    jobs[job_id] = job
    threading.Thread(target=_run_validate_job, args=(job_id, req), daemon=True).start()
    return {"job_id": job_id}


@app.get("/api/progress/{job_id}")
async def stream_progress(job_id: str):
    """
    SSE stream for a running job.

    Event types:
      {"type": "progress", "phase": "...", "detail": "...", "current": N, "total": N}
      {"type": "complete", "result": {...}}
      {"type": "error",    "message": "..."}
      {"type": "cancelled"}
    """
    if job_id not in jobs:
        raise HTTPException(404, "Job not found")

    async def _generator():
        job = jobs[job_id]
        try:
            while True:
                # Non-blocking drain of all queued events.
                while True:
                    try:
                        event = job.progress_queue.get_nowait()
                        yield f"data: {json.dumps(event)}\n\n"
                        if event.get("type") in ("complete", "error", "cancelled"):
                            return
                    except queue.Empty:
                        break

                # If job finished and queue is empty, synthesise a terminal event.
                if job.status in ("complete", "error", "cancelled"):
                    terminal: dict = {"type": job.status}
                    if job.status == "complete":
                        terminal["result"] = job.result
                    elif job.status == "error":
                        terminal["message"] = job.error or "Unknown error"
                    yield f"data: {json.dumps(terminal)}\n\n"
                    return

                await asyncio.sleep(0.1)
        except asyncio.CancelledError:
            # Client disconnected — stop streaming silently.
            return

    return StreamingResponse(
        _generator(),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/api/cancel")
async def cancel_job(req: CancelRequest):
    job = jobs.get(req.job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    job.cancel_token.set()
    job.status = "cancelled"
    return {"status": "cancelled", "job_id": req.job_id}


def _get_quick_access() -> list[dict]:
    home = Path.home()
    cwd = Path.cwd()
    candidates = [
        ("project", "Project",   cwd),
        ("downloads","Downloads", home / "Downloads"),
        ("desktop",  "Desktop",   home / "Desktop"),
        ("documents","Documents", home / "Documents"),
    ]
    return [{"id": id_, "name": nm, "path": str(p)} for id_, nm, p in candidates if p.exists()]


@app.post("/api/browse")
async def browse(req: BrowseRequest):
    """Navigate the local filesystem. Returns dirs and CSV/xlsx files."""
    raw = req.path.strip().strip('"').strip("'")   # handle Windows "Copy as path" quotes
    path = Path(raw) if raw else Path.cwd()

    if not path.exists():
        path = Path.cwd()
    if path.is_file():
        path = path.parent

    try:
        entries = []
        parent = path.parent
        if parent != path:
            entries.append({"name": "..", "path": str(parent), "type": "up"})

        items = sorted(path.iterdir(), key=lambda x: (x.is_file(), x.name.lower()))
        for item in items:
            if item.is_dir():
                entries.append({"name": item.name, "path": str(item), "type": "dir"})
            elif item.suffix.lower() in (".csv", ".tsv", ".txt", ".xlsx"):
                sz = item.stat().st_size
                entries.append({
                    "name": item.name,
                    "path": str(item),
                    "type": "file",
                    "size_bytes": sz,
                    "size_display": fmt_bytes(sz),
                })
    except PermissionError:
        raise HTTPException(403, f"Permission denied: {path}")
    except Exception as exc:
        raise HTTPException(500, str(exc))

    return {
        "current_path": str(path),
        "parent_path": str(path.parent) if path.parent != path else None,
        "entries": entries,
        "quick_access": _get_quick_access(),
    }


@app.get("/api/history")
async def get_history(limit: int = 50):
    return {"runs": history.get_runs(limit=limit)}


@app.delete("/api/history")
async def clear_history():
    history.clear()
    return {"cleared": True}


@app.delete("/api/history/{job_id}")
async def delete_history_run(job_id: str):
    deleted = history.delete_run(job_id)
    if not deleted:
        raise HTTPException(404, "Run not found")
    return {"deleted": job_id}


@app.get("/api/export-csv")
async def export_csv(path: Optional[str] = None):
    """Download a diff CSV. If `path` is given serve that file; else serve the last run's CSV."""
    global _last_csv_path
    csv_path = Path(path) if path else _last_csv_path
    if not csv_path or not csv_path.exists():
        raise HTTPException(404, "No CSV export available. Run a comparison first.")
    return FileResponse(
        path=str(csv_path),
        filename=csv_path.name,
        media_type="text/csv",
    )


@app.get("/api/job/{job_id}")
async def get_job_status(job_id: str):
    """Poll job status without SSE (fallback for clients that don't support SSE)."""
    job = jobs.get(job_id)
    if not job:
        # Check history
        run = history.get_run(job_id)
        if run:
            return {"job_id": job_id, "status": run["status"], "from_history": True}
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job_id,
        "status": job.status,
        "error": job.error,
        "has_result": job.result is not None,
    }


# ---------------------------------------------------------------------------
# Static frontend (mounted last so API routes take precedence)
# ---------------------------------------------------------------------------

_static_dir = Path(__file__).parent / "static"
if _static_dir.exists() and any(_static_dir.iterdir()):
    app.mount("/", StaticFiles(directory=str(_static_dir), html=True), name="static")


# ---------------------------------------------------------------------------
# Background job runners
# ---------------------------------------------------------------------------

# Maps every phase name emitted by the compare pipeline to a 1-based step number.
# The 8 phases mirror the order in compare.py: Loading → Schema → Profiling →
# Key Discovery → Key Validation → Diffing → Validation → Exporting.
_PHASE_STEPS: dict[str, int] = {
    "Loading":        1,
    "Schema":         2,
    "Profiling":      3,
    "Key Discovery":  4,
    "Key Validation": 5,
    "Diffing":        6,
    "Validation":     7,
    "Exporting":      8,
}
_TOTAL_STEPS = 8


def _make_progress(job_id: str) -> Progress:
    def _cb(phase: str, detail: str, current: int, total: int) -> None:
        job = jobs.get(job_id)
        if job and job.status == "running":
            job.progress_queue.put({
                "type": "progress",
                "phase": phase,
                "detail": detail,
                "current": current,
                "total": total,
                # Granular step fields — added for KI-020 (P4-T3).
                # Backward-compatible: old clients that ignore unknown fields are unaffected.
                "step": _PHASE_STEPS.get(phase, 0),
                "total_steps": _TOTAL_STEPS,
            })
    return Progress(callback=_cb)


def _run_compare_job(job_id: str, req: CompareAPIRequest) -> None:
    global _last_csv_path
    job = jobs[job_id]
    progress = _make_progress(job_id)

    try:
        out_dir = EXPORTS_DIR

        # Resolve effective key columns (f1 wins; fall back to shared key_columns).
        eff_key = req.key_columns_f1 or req.key_columns

        # Merge asymmetric key column names into column_map so differ.py can rename f2.
        col_map: list[dict] = list(req.column_map or [])
        if req.key_columns_f1 and req.key_columns_f2 and req.key_columns_f1 != req.key_columns_f2:
            for k1, k2 in zip(req.key_columns_f1, req.key_columns_f2):
                if k1 != k2:
                    col_map.append({"f1": k1, "f2": k2})

        core_req = CoreCompareRequest(
            file1=Path(req.file1),
            file2=Path(req.file2),
            sheet1=req.sheet1,
            sheet2=req.sheet2,
            key_columns=eff_key,
            ignore_rules=IgnoreRules(case=req.ignore_case, whitespace=req.ignore_whitespace),
            output_dir=out_dir,
            column_map=col_map or None,
            compare_columns=req.compare_columns or None,
        )

        result = run_compare(core_req, progress=progress, cancel_token=job.cancel_token)

        if result.csv_diff_path:
            _last_csv_path = result.csv_diff_path

        serialized = _serialize_compare_result(result)
        job.result = serialized
        job.status = "complete"

        history.save_run(
            job_id=job_id,
            run_type="compare",
            file1=req.file1,
            file2=req.file2,
            duration_s=result.duration_s,
            status="complete",
            summary={
                "added": result.diff.added_rows,
                "removed": result.diff.removed_rows,
                "modified": result.diff.modified_rows,
                "formatting_only": result.diff.formatting_only_rows,
                "compatibility_score": result.schema_diff.compatibility_score,
            },
        )

        job.progress_queue.put({"type": "complete", "result": serialized})

    except Exception as exc:
        if isinstance(exc, CancelledError):
            job.status = "cancelled"
            job.progress_queue.put({"type": "cancelled"})
            _save_failed(job_id, "compare", req.file1, getattr(req, "file2", ""),
                         time.time() - job.started_at, "cancelled")
        else:
            job.status = "error"
            job.error = str(exc)
            job.progress_queue.put({"type": "error", "message": str(exc)})
            _save_failed(job_id, "compare", req.file1, getattr(req, "file2", ""),
                         time.time() - job.started_at, "error")


def _run_validate_job(job_id: str, req: ValidateAPIRequest) -> None:
    job = jobs[job_id]
    progress = _make_progress(job_id)

    try:
        m1 = load_metadata(Path(req.file1), req.sheet1)
        m2 = load_metadata(Path(req.file2), req.sheet2)

        temp_files: list[Path] = []
        lf1 = _load_lazy_frame(Path(req.file1), m1, temp_files)
        lf2 = _load_lazy_frame(Path(req.file2), m2, temp_files)

        try:
            val_config = ValidationConfig(
                null_warn_threshold=req.null_warn_threshold,
                null_error_threshold=req.null_error_threshold,
                business_rules=[ColumnRuleConfig(**r) for r in req.business_rules],
            )
            report1, report2, schema_diff = validate_two_files(
                lf1, m1, lf2, m2,
                config=val_config,
                progress=progress,
                cancel_token=job.cancel_token,
            )
        finally:
            for tmp in temp_files:
                try:
                    tmp.unlink(missing_ok=True)
                except Exception:
                    pass

        duration_s = time.time() - job.started_at
        serialized = {
            "job_type": "validate",
            "duration_s": round(duration_s, 2),
            "schema": _serialize_schema_diff(schema_diff),
            "validation_f1": _serialize_validation(report1),
            "validation_f2": _serialize_validation(report2),
        }

        job.result = serialized
        job.status = "complete"

        history.save_run(
            job_id=job_id,
            run_type="validate",
            file1=req.file1,
            file2=req.file2,
            duration_s=duration_s,
            status="complete",
            summary={
                "compatibility_score": schema_diff.compatibility_score,
                "warnings_f1": report1.summary.get("WARNING", 0),
                "errors_f1": report1.summary.get("ERROR", 0),
                "critical_f1": report1.summary.get("CRITICAL", 0),
            },
        )

        job.progress_queue.put({"type": "complete", "result": serialized})

    except Exception as exc:
        if isinstance(exc, CancelledError):
            job.status = "cancelled"
            job.progress_queue.put({"type": "cancelled"})
            _save_failed(job_id, "validate", req.file1, req.file2,
                         time.time() - job.started_at, "cancelled")
        else:
            job.status = "error"
            job.error = str(exc)
            job.progress_queue.put({"type": "error", "message": str(exc)})
            _save_failed(job_id, "validate", req.file1, req.file2,
                         time.time() - job.started_at, "error")


def _save_failed(job_id, run_type, file1, file2, duration_s, status):
    try:
        history.save_run(
            job_id=job_id,
            run_type=run_type,
            file1=file1,
            file2=file2,
            duration_s=duration_s,
            status=status,
            summary={},
        )
    except Exception:
        pass


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _serialize_compare_result(result: CompareResult) -> dict:
    return {
        "job_type": "compare",
        "duration_s": round(result.duration_s, 2),
        "schema": _serialize_schema_diff(result.schema_diff),
        "diff": _serialize_diff(result.diff),
        "validation_f1": _serialize_validation(result.validation_f1),
        "validation_f2": _serialize_validation(result.validation_f2),
        "html_report": str(result.html_report_path) if result.html_report_path else None,
        "csv_export": str(result.csv_diff_path) if result.csv_diff_path else None,
        "json_export": str(result.json_diff_path) if result.json_diff_path else None,
    }


def _serialize_schema_diff(sd) -> dict:
    return {
        "columns_only_in_f1": sd.columns_only_in_f1,
        "columns_only_in_f2": sd.columns_only_in_f2,
        "columns_in_both": sd.columns_in_both,
        "renamed_candidates": [[a, b, round(s, 3)] for a, b, s in sd.renamed_candidates],
        "reorder_detected": sd.reorder_detected,
        "compatibility_score": round(sd.compatibility_score, 1),
        "column_order_f1": sd.column_order_f1,
        "column_order_f2": sd.column_order_f2,
    }


def _serialize_diff(d) -> dict:
    return {
        "added_rows": d.added_rows,
        "removed_rows": d.removed_rows,
        "modified_rows": d.modified_rows,
        "formatting_only_rows": d.formatting_only_rows,
        "total_rows_f1": d.total_rows_f1,
        "total_rows_f2": d.total_rows_f2,
        "confidence_score": round(d.confidence_score, 3),
        "is_full_count": d.is_full_count,
        "rows_scanned": d.rows_scanned,
        "key_columns": d.key_columns,
        "column_diffs": {
            name: {
                "modified": s.modified_count,
                "formatting_only": s.formatting_only_count,
                "null_introduced": s.null_introduced_count,
                "null_resolved": s.null_resolved_count,
                "change_rate": round(s.change_rate, 4),
            }
            for name, s in d.column_diffs.items()
        },
        "sample_diffs": [
            {
                "key": r.key_value,
                "change_type": r.change_type,
                "columns_changed": r.columns_changed,
                "f1": r.f1_values,
                "f2": r.f2_values,
            }
            for r in d.sample_diffs[:100]
        ],
    }


def _serialize_validation(r: ValidationReport) -> dict:
    return {
        "total_count": r.total_count,
        "column_count": r.column_count,
        "summary": r.summary,
        "compatibility_score": r.compatibility_score,
        "checks": [
            {
                "name": c.name,
                "severity": c.severity,
                "passed": c.passed,
                "message": c.message,
                "column": c.column,
                "affected_count": c.affected_count,
            }
            for c in r.checks
        ],
        "profile": {
            "columns": [
                {
                    "name": col.name,
                    "inferred_type": col.inferred_type,
                    "null_variant_rate": round(col.null_variant_rate, 4),
                    "total_null_variants": col.total_null_variants,
                    "polars_null_count": col.polars_null_count,
                    "empty_string_count": col.empty_string_count,
                    "whitespace_only_count": col.whitespace_only_count,
                    "textual_null_count": col.textual_null_count,
                    "distinct_count": col.distinct_count,
                    "total_count": col.total_count,
                    "min_value": col.min_value,
                    "max_value": col.max_value,
                    "sample_values": col.sample_values[:5],
                }
                for col in r.profile.columns
            ]
        },
    }
