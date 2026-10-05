"""What bwrap and srt really printed when a sandbox couldn't start, kept verbatim in tests/fixtures/real/."""
from pathlib import Path

REAL = Path(__file__).resolve().parent / "fixtures" / "real"


def real(name: str) -> str:
    return (REAL / name).read_text(encoding="utf-8")


def line(name: str) -> str:
    """The file's first line: the one bwrap or srt printed."""
    return real(name).splitlines()[0]
