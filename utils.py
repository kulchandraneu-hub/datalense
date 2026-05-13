import threading
from typing import Optional, Protocol, Literal
from dataclasses import dataclass, field
import time
from pathlib import Path
from datetime import datetime


# ---------------------------------------------------------------------------
# Progress
# ---------------------------------------------------------------------------

class ProgressCallback(Protocol):
    def __call__(self, phase: str, detail: str, current: int, total: int) -> None: ...


@dataclass
class Progress:
    """Thread-safe progress tracker."""
    callback: Optional[ProgressCallback] = None
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False)

    def update(self, phase: str, detail: str, current: int, total: int) -> None:
        if self.callback:
            with self._lock:
                self.callback(phase, detail, current, total)


# ---------------------------------------------------------------------------
# Cancellation
# ---------------------------------------------------------------------------

class CancelledError(Exception):
    pass


def check_cancel(cancel_token: Optional[threading.Event]) -> None:
    if cancel_token and cancel_token.is_set():
        raise CancelledError("Operation cancelled by user")


# ---------------------------------------------------------------------------
# Memory guards
# ---------------------------------------------------------------------------

MEMORY_WARN_MB = 500
MEMORY_ERROR_MB = 2048
MEMORY_BLOCK_MB = 8192

MemoryStatus = Literal["ok", "warn", "error", "block"]


def check_memory_guard(file_size_bytes: int) -> tuple[MemoryStatus, str]:
    """Return (status, message). Thresholds are heuristic, not hard limits."""
    size_mb = file_size_bytes / (1024 ** 2)
    if size_mb > MEMORY_BLOCK_MB:
        return "block", f"File too large ({fmt_bytes(file_size_bytes)}). Max {MEMORY_BLOCK_MB} MB."
    elif size_mb > MEMORY_ERROR_MB:
        return "error", f"Large file ({fmt_bytes(file_size_bytes)}). Expect 5-10 GB RAM usage."
    elif size_mb > MEMORY_WARN_MB:
        return "warn", f"Moderate file size ({fmt_bytes(file_size_bytes)}). May use 1-2 GB RAM."
    return "ok", ""


def estimate_polars_ram(file_size_bytes: int, num_columns: int) -> int:
    """Rough estimate: Polars can use 3-5x file size during joins."""
    return int(file_size_bytes * 4)


# ---------------------------------------------------------------------------
# Log capture
# ---------------------------------------------------------------------------

class LogCapture:
    """Context manager to capture log output for embedding in API responses."""
    def __init__(self):
        self._lines: list[str] = []

    def __enter__(self) -> "LogCapture":
        return self

    def __exit__(self, *_):
        pass

    def append(self, line: str) -> None:
        self._lines.append(line)

    @property
    def lines(self) -> list[str]:
        return self._lines


# ---------------------------------------------------------------------------
# Formatting helpers
# ---------------------------------------------------------------------------

def fmt_bytes(n: int) -> str:
    """fmt_bytes(5_000_000_000) → '4.7 GB'"""
    value: float = float(n)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024:
            return f"{value:.1f} {unit}" if value < 100 else f"{int(value)} {unit}"
        value /= 1024
    return f"{value:.1f} PB"


def fmt_rows(n: int) -> str:
    """fmt_rows(13_200_000) → '13.2M'"""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    elif n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


def fmt_duration(seconds: float) -> str:
    """fmt_duration(134.5) → '2m 14s'"""
    mins, secs = divmod(int(seconds), 60)
    return f"{mins}m {secs}s"


def fmt_pct(value: float, total: float) -> str:
    """fmt_pct(3, 10) → '30.0%'"""
    return f"{(value / total) * 100:.1f}%" if total > 0 else "0%"


# ---------------------------------------------------------------------------
# Output path helper
# ---------------------------------------------------------------------------

def unique_output_path(base_path: Path, suffix: str) -> Path:
    """
    Generate unique output path with datetime postfix.
    /path/file.csv → /path/file_20260513_143022.csv
    Never overwrites existing files.
    """
    stem = base_path.stem + "_" + datetime.now().strftime("%Y%m%d_%H%M%S")
    return base_path.parent / (stem + suffix)


if __name__ == "__main__":
    # Formatting
    assert fmt_bytes(0) == "0.0 B", fmt_bytes(0)
    assert fmt_bytes(1024) == "1.0 KB", fmt_bytes(1024)
    assert fmt_bytes(5_000_000_000) == "4.7 GB", fmt_bytes(5_000_000_000)
    assert fmt_rows(13_200_000) == "13.2M", fmt_rows(13_200_000)
    assert fmt_duration(134) == "2m 14s", fmt_duration(134)

    # Memory guards
    assert check_memory_guard(100_000_000) == ("ok", "")
    assert check_memory_guard(1_000_000_000)[0] == "warn"
    assert check_memory_guard(3_000_000_000)[0] == "error"
    assert check_memory_guard(10_000_000_000)[0] == "block"

    # Cancellation
    token = threading.Event()
    check_cancel(token)  # must not raise
    token.set()
    try:
        check_cancel(token)
        assert False, "Should have raised CancelledError"
    except CancelledError:
        pass

    # Progress callback
    received = []
    def _cb(phase, detail, current, total):
        received.append((phase, current, total))

    p = Progress(callback=_cb)
    p.update("Test", "step 1", 1, 5)
    assert received == [("Test", 1, 5)]

    print("✓ All utils tests passed")
