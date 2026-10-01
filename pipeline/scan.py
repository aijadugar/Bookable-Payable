"""Discover input PDFs in the documents folder.

WHY: the real inbox is messy — macOS resource forks (._*), .DS_Store,
stray notes.txt. Silently ignoring them hides operator mistakes (a PDF
renamed to .pdf.tmp never gets processed), so every skipped file is
returned with a reason for run.py to log. Hidden dotfiles are skipped
before the extension check because ._* files end in ".pdf" and would
otherwise sneak into the pipeline as garbage "documents".
"""

from __future__ import annotations

import sys
from pathlib import Path


class DocsDirError(Exception):
    """Fatal setup problem: the documents folder is unusable.

    run.py maps this to exit code 2 — a missing inbox is an operator
    mistake, not a per-file error to tolerate.
    """


def find_pdfs(docs_dir: Path) -> tuple[list[Path], list[tuple[Path, str]]]:
    """Return (pdfs, skipped) for the top level of docs_dir.

    pdfs are regular ".pdf" files sorted by name for deterministic runs;
    skipped pairs each rejected entry with a human-readable reason. The
    scan itself never raises on odd files — only a missing/invalid
    docs_dir is fatal, via DocsDirError.
    """
    docs_dir = Path(docs_dir)
    if not docs_dir.is_dir():
        raise DocsDirError(f"documents folder not found: {docs_dir}")

    pdfs: list[Path] = []
    skipped: list[tuple[Path, str]] = []

    for entry in sorted(docs_dir.iterdir(), key=lambda p: p.name):
        # Hidden files first: AppleDouble "._INV-01.pdf" carries a .pdf
        # suffix, so this check must precede the extension test.
        if entry.name.startswith("."):
            skipped.append((entry, "hidden"))
        elif entry.is_dir():
            skipped.append((entry, "not a file"))
        elif entry.suffix.lower() != ".pdf":
            skipped.append((entry, "not a pdf"))
        else:
            pdfs.append(entry)

    return pdfs, skipped
