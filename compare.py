import polars as pl
from dataclasses import dataclass
from pathlib import Path
from typing import Optional
import time
import threading
import tempfile
import os
import json

from encoding_detect import detect_encoding
from metadata import load_metadata, FileMetadata, compare_schemas, SchemaDiff
from profiler import profile_file, FileProfile
from validator import validate_two_files, ValidationReport, ValidationConfig
from differ import diff_files, DiffResult, IgnoreRules
from reporters import render_html_report, render_excel_diff, render_json_diff, render_csv_diff
from utils import Progress, check_cancel, unique_output_path


@dataclass
class CompareRequest:
    file1: Path
    file2: Path
    sheet1: Optional[str] = None
    sheet2: Optional[str] = None
    key_columns: Optional[list[str]] = None
    ignore_rules: Optional[IgnoreRules] = None
    validation_config: Optional[ValidationConfig] = None
    output_dir: Optional[Path] = None


@dataclass
class CompareResult:
    request: CompareRequest
    schema_diff: SchemaDiff
    validation_f1: ValidationReport
    validation_f2: ValidationReport
    diff: DiffResult
    duration_s: float
    html_report_path: Optional[Path] = None
    excel_diff_path: Optional[Path] = None
    json_diff_path: Optional[Path] = None
    csv_diff_path: Optional[Path] = None


def run_compare(
    request: CompareRequest,
    progress: Optional[Progress] = None,
    cancel_token: Optional[threading.Event] = None,
) -> CompareResult:
    """
    Full comparison pipeline.

    Order:
    1.  Load metadata for both files (encoding, delimiter, schema)
    2.  Compare schemas
    3.  Load both files as LazyFrames
    4.  Profile file 1
    5.  Profile file 2
    6.  Auto-discover keys (if not supplied)
    7.  Diff files
    8.  Validate both files
    9.  Render and write reports (if output_dir set)
    """
    start = time.time()
    check_cancel(cancel_token)

    # Step 1: metadata
    if progress:
        progress.update("Loading", "File 1 metadata", 0, 2)
    m1 = load_metadata(request.file1, request.sheet1)

    if progress:
        progress.update("Loading", "File 2 metadata", 1, 2)
    m2 = load_metadata(request.file2, request.sheet2)

    check_cancel(cancel_token)

    # Step 2: schema comparison
    if progress:
        progress.update("Schema", "Comparing schemas", 0, 1)
    schema_diff = compare_schemas(m1, m2)

    check_cancel(cancel_token)

    # Step 3: lazy frames
    _temp_files: list[Path] = []
    try:
        lf1 = _load_lazy_frame(request.file1, m1, _temp_files)
        lf2 = _load_lazy_frame(request.file2, m2, _temp_files)

        check_cancel(cancel_token)

        # Step 4: profile file 1
        if progress:
            progress.update("Profiling", f"File 1 ({m1.column_count} columns)", 0, 2)
        profile1 = profile_file(lf1, m1, progress, cancel_token)

        check_cancel(cancel_token)

        # Step 5: profile file 2
        if progress:
            progress.update("Profiling", f"File 2 ({m2.column_count} columns)", 1, 2)
        profile2 = profile_file(lf2, m2, progress, cancel_token)

        check_cancel(cancel_token)

        # Step 6: key discovery
        key_columns = request.key_columns
        if not key_columns:
            if progress:
                progress.update("Key Discovery", "Auto-detecting key columns", 0, 1)
            from key_discovery import discover_keys
            candidates = discover_keys(lf1, progress=progress, cancel_token=cancel_token)
            if candidates and candidates[0].is_unique:
                key_columns = candidates[0].columns
            else:
                key_columns = [m1.columns[0]]  # fallback: first column

        check_cancel(cancel_token)

        # Step 7: diff
        if progress:
            progress.update("Diffing", f"Key columns: {', '.join(key_columns)}", 0, 1)
        diff = diff_files(
            lf1, m1, lf2, m2,
            key_columns=key_columns,
            ignore_rules=request.ignore_rules,
            progress=progress,
            cancel_token=cancel_token,
        )

        check_cancel(cancel_token)

        # Step 8: validate
        if progress:
            progress.update("Validation", "Validating both files", 0, 1)
        report1, report2, _ = validate_two_files(
            lf1, m1, lf2, m2,
            config=request.validation_config,
            progress=progress,
            cancel_token=cancel_token,
        )

        check_cancel(cancel_token)

        # Step 9: reports
        result = CompareResult(
            request=request,
            schema_diff=schema_diff,
            validation_f1=report1,
            validation_f2=report2,
            diff=diff,
            duration_s=time.time() - start,
        )

        if request.output_dir:
            _write_reports(result, request.output_dir, progress)

        return result

    finally:
        # Clean up any Excel temp files
        for tmp in _temp_files:
            try:
                tmp.unlink(missing_ok=True)
            except Exception:
                pass


def _write_reports(result: CompareResult, output_dir: Path, progress: Optional[Progress]) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)

    if progress:
        progress.update("Exporting", "Generating reports", 0, 4)

    html_content = render_html_report(result.validation_f1, result.validation_f2, result.diff)
    html_path = unique_output_path(output_dir / "report", ".html")
    html_path.write_text(html_content, encoding="utf-8")
    result.html_report_path = html_path

    try:
        excel_path = unique_output_path(output_dir / "diff", ".xlsx")
        render_excel_diff(result.diff, excel_path)
        result.excel_diff_path = excel_path
    except Exception:
        pass  # openpyxl optional

    json_data = render_json_diff(result.diff)
    json_path = unique_output_path(output_dir / "diff", ".json")
    json_path.write_text(json.dumps(json_data, indent=2), encoding="utf-8")
    result.json_diff_path = json_path

    csv_content = render_csv_diff(result.diff)
    csv_path = unique_output_path(output_dir / "diff", ".csv")
    csv_path.write_text(csv_content, encoding="utf-8")
    result.csv_diff_path = csv_path


def _load_lazy_frame(path: Path, metadata: FileMetadata, temp_files: list[Path]) -> pl.LazyFrame:
    """Load a file as a LazyFrame using detected metadata."""
    if path.suffix.lower() == ".xlsx":
        tmp = _excel_to_temp_csv(path, metadata.sheet_name or "Sheet1")
        temp_files.append(tmp)
        return pl.scan_csv(tmp, encoding="utf8", separator=",", ignore_errors=True)

    from metadata import _polars_encoding
    return pl.scan_csv(
        path,
        encoding=_polars_encoding(metadata.encoding.encoding),
        separator=metadata.delimiter,
        infer_schema_length=1000,
        ignore_errors=True,
    )


def _excel_to_temp_csv(xlsx_path: Path, sheet_name: str) -> Path:
    """
    Convert an Excel sheet to a temp CSV. Caller must delete the file.
    Uses mkstemp + os.close() to avoid Windows handle-sharing issues.
    """
    try:
        import openpyxl
    except ImportError:
        raise RuntimeError("openpyxl is required for Excel support: pip install openpyxl")

    fd, tmp_str = tempfile.mkstemp(suffix=".csv")
    os.close(fd)  # release OS handle before writing on Windows
    tmp_path = Path(tmp_str)

    wb = openpyxl.load_workbook(xlsx_path, read_only=True, data_only=True)
    ws = wb[sheet_name]

    import csv
    with open(tmp_path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f)
        for row in ws.iter_rows(values_only=True):
            writer.writerow([str(v) if v is not None else "" for v in row])

    wb.close()
    return tmp_path


if __name__ == "__main__":
    import tempfile as _tf

    # Build two small in-memory CSVs on disk and compare them
    f1 = Path(_tf.mktemp(suffix=".csv"))
    f2 = Path(_tf.mktemp(suffix=".csv"))

    f1.write_text("id,name,salary\n1,Alice,50000\n2,Bob,60000\n3,Charlie,70000\n")
    f2.write_text("id,name,salary\n1,Alice,50000\n2,BOB,61000\n4,David,80000\n")

    try:
        events: list[str] = []
        def _cb(phase, detail, current, total):
            events.append(f"{phase}: {detail}")

        request = CompareRequest(
            file1=f1,
            file2=f2,
            output_dir=Path(_tf.mkdtemp()),
        )
        result = run_compare(request, progress=Progress(callback=_cb))

        assert result.diff.removed_rows == 1, f"Expected 1 removed, got {result.diff.removed_rows}"
        assert result.diff.added_rows == 1, f"Expected 1 added, got {result.diff.added_rows}"
        assert result.html_report_path and result.html_report_path.exists()
        assert result.json_diff_path and result.json_diff_path.exists()
        assert result.csv_diff_path and result.csv_diff_path.exists()

        print(f"  Duration: {result.duration_s:.2f}s")
        print(f"  Added: {result.diff.added_rows}, Removed: {result.diff.removed_rows}, "
              f"Modified: {result.diff.modified_rows}")
        print(f"  Schema compatibility: {result.schema_diff.compatibility_score:.0f}/100")
        print(f"  Progress events: {len(events)}")
        print("✓ Comparison smoke test passed")
    finally:
        f1.unlink(missing_ok=True)
        f2.unlink(missing_ok=True)
