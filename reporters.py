from pathlib import Path
from typing import Optional
import json

from differ import DiffResult, RowDiff
from validator import ValidationReport


def render_html_report(
    validation_f1: ValidationReport,
    validation_f2: Optional[ValidationReport],
    diff: Optional[DiffResult],
) -> str:
    """Generate a self-contained dark-theme HTML report (no external dependencies)."""

    def _sev_class(sev: str) -> str:
        return {"INFO": "info", "WARNING": "warn", "ERROR": "error", "CRITICAL": "critical"}.get(sev, "info")

    def _checks_html(report: ValidationReport) -> str:
        parts = []
        for chk in report.checks:
            cnt = f" ({chk.affected_count:,} rows)" if chk.affected_count is not None else ""
            icon = {"INFO": "ℹ", "WARNING": "⚠", "ERROR": "✖", "CRITICAL": "☠"}.get(chk.severity, "•")
            parts.append(
                f'<div class="check {_sev_class(chk.severity)}">'
                f'{icon} <strong>{chk.name}</strong> [{chk.severity}]: {chk.message}{cnt}'
                f'</div>'
            )
        return "\n".join(parts) if parts else "<p>No issues found.</p>"

    f1_path = validation_f1.profile.metadata.path
    f1_rows = f"{validation_f1.total_count:,}"
    f1_cols = validation_f1.column_count
    f1_score = (
        f"{validation_f1.compatibility_score:.0f}/100"
        if validation_f1.compatibility_score is not None else "N/A"
    )

    diff_section = ""
    if diff:
        col_rows = "\n".join(
            f"<tr><td>{n}</td><td>{s.modified_count:,}</td>"
            f"<td>{s.formatting_only_count:,}</td>"
            f"<td>{s.change_rate * 100:.1f}%</td></tr>"
            for n, s in diff.column_diffs.items()
        )
        diff_section = f"""
        <h2>Row-Level Changes</h2>
        <div class="stat-row">
          <div class="stat green">Added<br><span>{diff.added_rows:,}</span></div>
          <div class="stat red">Removed<br><span>{diff.removed_rows:,}</span></div>
          <div class="stat yellow">Modified<br><span>{diff.modified_rows:,}</span></div>
          <div class="stat grey">Fmt Only<br><span>{diff.formatting_only_rows:,}</span></div>
        </div>
        <h3>Column Changes</h3>
        <table>
          <tr><th>Column</th><th>Modified</th><th>Fmt-only</th><th>Change rate</th></tr>
          {col_rows}
        </table>
        """

    f2_section = ""
    if validation_f2:
        f2_path = validation_f2.profile.metadata.path
        f2_section = f"""
        <h2>File 2: {f2_path.name}</h2>
        <p>Rows: {validation_f2.total_count:,} | Columns: {validation_f2.column_count}</p>
        <h3>Validation Checks</h3>
        {_checks_html(validation_f2)}
        """

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Data Quality Report</title>
  <style>
    body {{ background:#0F172A; color:#F1F5F9; font-family:monospace; padding:24px; max-width:960px; margin:auto }}
    h1 {{ color:#3B82F6; border-bottom:1px solid #1E293B; padding-bottom:8px }}
    h2 {{ color:#7DD3FC; margin-top:24px }}
    h3 {{ color:#94A3B8 }}
    .check {{ padding:8px 12px; margin:4px 0; border-left:4px solid; border-radius:2px }}
    .check.info     {{ border-color:#3B82F6; background:#1E3A5F }}
    .check.warn     {{ border-color:#F59E0B; background:#4A3A0E }}
    .check.error    {{ border-color:#EF4444; background:#4A1515 }}
    .check.critical {{ border-color:#8B5CF6; background:#2D1B4E }}
    table {{ border-collapse:collapse; width:100%; margin:12px 0 }}
    th,td {{ border:1px solid #334155; padding:8px; text-align:left }}
    th {{ background:#1E293B }}
    .stat-row {{ display:flex; gap:12px; margin:12px 0 }}
    .stat {{ flex:1; padding:12px; border-radius:4px; text-align:center; font-size:0.85em }}
    .stat span {{ font-size:1.6em; font-weight:bold; display:block; margin-top:4px }}
    .stat.green  {{ background:#14532D }}
    .stat.red    {{ background:#7F1D1D }}
    .stat.yellow {{ background:#78350F }}
    .stat.grey   {{ background:#1F2937 }}
  </style>
</head>
<body>
  <h1>Data Quality Report</h1>
  <h2>File 1: {f1_path.name}</h2>
  <p>Rows: {f1_rows} | Columns: {f1_cols} | Compatibility: {f1_score}</p>
  <h3>Validation Checks</h3>
  {_checks_html(validation_f1)}
  {f2_section}
  {diff_section}
</body>
</html>"""


def render_excel_diff(diff: DiffResult, output_path: Path) -> None:
    """Write diff to Excel with color-coded rows."""
    try:
        import openpyxl
        from openpyxl.styles import PatternFill, Font
    except ImportError:
        raise RuntimeError("openpyxl is required for Excel export: pip install openpyxl")

    wb = openpyxl.Workbook()
    ws = wb.active
    ws.title = "Diff"

    fills = {
        "added":            PatternFill("solid", fgColor="166534"),
        "removed":          PatternFill("solid", fgColor="7F1D1D"),
        "modified":         PatternFill("solid", fgColor="78350F"),
        "formatting_only":  PatternFill("solid", fgColor="1F2937"),
    }

    header = [*diff.key_columns, "change_type", *list(diff.column_diffs.keys())]
    ws.append(header)
    for cell in ws[1]:
        cell.font = Font(bold=True)

    for row in diff.sample_diffs:
        vals = [row.key_value, row.change_type]
        for col in diff.column_diffs:
            vals.append(row.f1_values.get(col, ""))
        ws.append(vals)
        fill = fills.get(row.change_type)
        if fill:
            for cell in ws[ws.max_row]:
                cell.fill = fill

    wb.save(output_path)


def render_json_diff(diff: DiffResult) -> dict:
    """Return diff as a JSON-serialisable dict."""
    return {
        "summary": {
            "added": diff.added_rows,
            "removed": diff.removed_rows,
            "modified": diff.modified_rows,
            "formatting_only": diff.formatting_only_rows,
            "total_f1": diff.total_rows_f1,
            "total_f2": diff.total_rows_f2,
            "confidence_score": diff.confidence_score,
            "key_columns": diff.key_columns,
        },
        "column_diffs": {
            name: {
                "modified": s.modified_count,
                "formatting_only": s.formatting_only_count,
                "null_introduced": s.null_introduced_count,
                "null_resolved": s.null_resolved_count,
                "change_rate": round(s.change_rate, 4),
            }
            for name, s in diff.column_diffs.items()
        },
        "sample_rows": [
            {
                "key": r.key_value,
                "change_type": r.change_type,
                "columns_changed": r.columns_changed,
                "f1": r.f1_values,
                "f2": r.f2_values,
            }
            for r in diff.sample_diffs[:100]
        ],
    }


def render_csv_diff(diff: DiffResult) -> str:
    """Return diff sample rows as CSV string."""
    import csv, io

    buf = io.StringIO()
    writer = csv.writer(buf)

    col_names = list(diff.column_diffs.keys())
    writer.writerow([*diff.key_columns, "change_type", *[f"{c}_f1" for c in col_names], *[f"{c}_f2" for c in col_names]])

    for row in diff.sample_diffs:
        f1_vals = [row.f1_values.get(c, "") for c in col_names]
        f2_vals = [row.f2_values.get(c, "") for c in col_names]
        writer.writerow([row.key_value, row.change_type, *f1_vals, *f2_vals])

    return buf.getvalue()


if __name__ == "__main__":
    print("✓ Reporters module ready for integration")
