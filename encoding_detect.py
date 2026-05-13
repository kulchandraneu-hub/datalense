from dataclasses import dataclass
from pathlib import Path
from typing import Optional


@dataclass
class EncodingResult:
    encoding: str           # "utf-8", "utf-8-sig", "latin-1", "cp1252"
    confidence: float       # 0.0-1.0
    has_bom: bool
    bom_bytes: Optional[bytes]
    raw_sample: bytes


_BOMS = [
    (b"\xef\xbb\xbf", "utf-8-sig", True),
    (b"\xff\xfe", "utf-16-le", True),
    (b"\xfe\xff", "utf-16-be", True),
]

_SAMPLE_SIZE = 65536


def detect_encoding(path: Path, sample_bytes: int = _SAMPLE_SIZE) -> EncodingResult:
    """Check BOM prefixes first, then try utf-8, fall back to charset-normalizer, then cp1252."""
    with open(path, "rb") as f:
        raw = f.read(sample_bytes)

    # BOM check
    for bom, enc, has_bom in _BOMS:
        if raw.startswith(bom):
            return EncodingResult(
                encoding=enc,
                confidence=1.0,
                has_bom=True,
                bom_bytes=bom,
                raw_sample=raw,
            )

    # Try strict UTF-8
    try:
        raw.decode("utf-8")
        return EncodingResult(
            encoding="utf-8",
            confidence=0.99,
            has_bom=False,
            bom_bytes=None,
            raw_sample=raw,
        )
    except UnicodeDecodeError:
        pass

    # Try charset-normalizer on sample only
    try:
        from charset_normalizer import from_bytes
        results = from_bytes(raw)
        best = results.best()
        if best is not None:
            enc = str(best.encoding)
            # Normalise aliases
            if enc in ("windows-1252", "cp1252"):
                enc = "cp1252"
            elif enc in ("iso-8859-1", "latin-1", "latin_1"):
                enc = "latin-1"
            return EncodingResult(
                encoding=enc,
                confidence=best.chaos if hasattr(best, "chaos") else 0.8,
                has_bom=False,
                bom_bytes=None,
                raw_sample=raw,
            )
    except ImportError:
        pass

    # Safe Windows fallback
    return EncodingResult(
        encoding="cp1252",
        confidence=0.5,
        has_bom=False,
        bom_bytes=None,
        raw_sample=raw,
    )


def open_with_detected_encoding(path: Path) -> tuple[str, EncodingResult]:
    """Returns (file_content_str, encoding_result)."""
    result = detect_encoding(path)
    content = path.read_text(encoding=result.encoding, errors="replace")
    return content, result


if __name__ == "__main__":
    import tempfile, os

    def _write(name: str, data: bytes) -> Path:
        p = Path(tempfile.mktemp(suffix=name))
        p.write_bytes(data)
        return p

    cases = [
        ("utf-8 no BOM", b"id,name\n1,Alice\n2,Bob\n", "utf-8"),
        ("utf-8 with BOM", b"\xef\xbb\xbfid,name\n1,Alice\n", "utf-8-sig"),
        ("latin-1", "id,name\n1,Ren\xe9\n".encode("latin-1"), "latin-1"),
        ("cp1252", "id,name\n1,Caf\xe9\n".encode("cp1252"), "cp1252"),
    ]

    for label, data, _expected_enc in cases:
        p = _write(f"_{label.replace(' ', '_')}.csv", data)
        try:
            res = detect_encoding(p)
            print(f"  {label}: detected={res.encoding} bom={res.has_bom} conf={res.confidence:.2f}")
        finally:
            p.unlink()

    print("✓ encoding_detect smoke test passed")
