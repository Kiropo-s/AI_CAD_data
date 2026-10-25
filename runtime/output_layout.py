"""Shared locations for generated artifacts.

Layout:
    out/cases/<case_id>/      curated single-case artifacts
    out/batches/<batch_id>/   one self-contained batch run
    out/tmp/                  scratch, debug, and disposable work
    out/corpus/<run_name>/    readable code, independent validation and train-ready manifest
"""

from pathlib import Path


from .paths import OUT_DIR
CASES_DIR = OUT_DIR / "cases"
BATCHES_DIR = OUT_DIR / "batches"
TMP_DIR = OUT_DIR / "tmp"
CORPUS_DIR = OUT_DIR / "corpus"


def _checked_name(value: str, kind: str) -> str:
    if not value or value in {".", ".."} or any(char in value for char in "/\\"):
        raise ValueError(f"invalid {kind} name: {value!r}")
    return value


def case_dir(stem: str, override: Path | None = None) -> Path:
    stem = _checked_name(stem, "case")
    return (override if override is not None else CASES_DIR / stem).resolve()


def batch_dir(name: str, override: Path | None = None) -> Path:
    name = _checked_name(name, "batch")
    return (override if override is not None else BATCHES_DIR / name).resolve()


def corpus_dir(name: str, override: Path | None = None) -> Path:
    name = _checked_name(name, "corpus")
    return (override if override is not None else CORPUS_DIR / name).resolve()
