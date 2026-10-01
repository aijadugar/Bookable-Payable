"""Render one PDF to page images and a manifest.

WHY: later steps are vision-model calls, which are slow, paid, and
nondeterministic about retries — so every PDF is rasterised once, cached
under work/<stem>/, and reused as long as the bytes have not changed
(verified by sha256, not mtime, because git checkouts and OneDrive sync
touch mtimes freely). PyMuPDF is used instead of poppler so the whole
step is a pure pip install with no system dependency.

Failure policy: a broken PDF produces an honest error manifest rather
than an exception, because one unreadable document must never stop a
batch. The two things that DO raise are a collision between two stems
and nothing else — writing two documents into one cache folder would
silently corrupt both.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
from pathlib import Path
from typing import Any

try:  # PyMuPDF >= 1.24 prefers the pymupdf name; fitz stays valid forever
    import pymupdf as fitz
except ImportError:  # pragma: no cover - older installs
    import fitz  # type: ignore[no-redef]

from . import config

log = logging.getLogger(__name__)

_PDF_BASE_DPI = 72  # PDF units are 1/72 inch; Matrix scale = DPI / 72


class PdfCollisionError(Exception):
    """Two different PDFs want the same work/<stem>/ folder.

    Raised rather than recorded in a manifest: proceeding would let the
    second file overwrite the first one's cache, and every page-count or
    sha check afterwards would compare against the wrong document.
    """


def sha256_of(path: Path) -> str:
    """Hex sha256 of a file's bytes; the cache's correctness key."""
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def _page_image_name(page_number: int) -> str:
    """page_001.png ... Zero padding keeps lexical order == numeric order."""
    return f"page_{page_number:03d}.{config.IMAGE_FORMAT}"


def _render_page(page: "fitz.Page") -> "fitz.Pixmap":
    """Rasterise one page, respecting its /Rotate so text is upright.

    PyMuPDF applies page rotation in get_pixmap by default. If the long
    side still exceeds MAX_PIXELS_LONG_SIDE we shrink the scale and
    re-render once — provider-side downsampling of oversized images is
    lossy and invisible to us, so we do the downscale ourselves instead.
    """
    scale = config.RENDER_DPI / _PDF_BASE_DPI
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    long_side = max(pix.width, pix.height)
    if long_side > config.MAX_PIXELS_LONG_SIDE:
        # One extra pass: ceil-rounding in pixmap sizing makes a pure
        # prediction off-by-one often enough to be worth re-checking.
        scale *= config.MAX_PIXELS_LONG_SIDE / long_side
        pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale))
    return pix


def _write_manifest(folder: Path, manifest: dict[str, Any]) -> None:
    """Write manifest.json atomically (temp + rename).

    A half-written manifest from a crash would poison the cache: the
    next run would trust truncated JSON or a "pages" list missing its
    last entries. os.replace is atomic within a filesystem.
    """
    tmp = folder / "manifest.json.tmp"
    tmp.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    os.replace(tmp, folder / "manifest.json")


def _error_manifest(pdf_path: Path, sha: str, message: str) -> dict[str, Any]:
    return {
        "file": pdf_path.name,
        "sha256": sha,
        "page_count": 0,
        "status": "error",
        "error": message,
        "pages": [],
    }


def _load_cached_manifest(
    folder: Path, pdf_path: Path, sha: str, work_dir: Path
) -> dict[str, Any] | None:
    """Return a previous manifest if it is still valid, else None.

    Valid means: status ok, bytes unchanged (sha256), and every page
    image still on disk — OneDrive evicts cold files to placeholders,
    so a manifest can outlive its images.
    """
    mf = folder / "manifest.json"
    if not mf.is_file():
        return None
    try:
        data = json.loads(mf.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("unreadable manifest in %s, re-rendering", folder)
        return None
    if data.get("status") != "ok" or data.get("sha256") != sha:
        return None
    for page in data.get("pages", []):
        if not (work_dir / page["image"]).is_file():
            log.info("cached image %s missing, re-rendering", page["image"])
            return None
    return data


def render_pdf(pdf_path: Path, work_dir: Path) -> dict[str, Any]:
    """Render pdf_path into work_dir/<stem>/ and return its manifest.

    Never raises for bad file contents (encrypted, corrupt, empty,
    zero-page): those come back as status "error" so run.py can log the
    file and carry on. Raises PdfCollisionError if a *different* PDF
    already owns this stem's cache folder.
    """
    pdf_path = Path(pdf_path)
    work_dir = Path(work_dir)
    folder = work_dir / pdf_path.stem

    sha = sha256_of(pdf_path)

    # Collision check reads the manifest field, not the folder name:
    # two names may share a stem while differing in case or extension.
    existing = folder / "manifest.json"
    if existing.is_file():
        try:
            owner = json.loads(existing.read_text(encoding="utf-8")).get("file")
        except (json.JSONDecodeError, OSError):
            owner = None
        if owner is not None and owner != pdf_path.name:
            raise PdfCollisionError(
                f"{pdf_path.name} and {owner} both map to cache folder "
                f"'{folder.name}' — rename one of the inputs"
            )

    cached = _load_cached_manifest(folder, pdf_path, sha, work_dir)
    if cached is not None:
        log.debug("cache hit for %s", pdf_path.name)
        return cached

    # Clean slate: stale images from an earlier version of this PDF must
    # not linger and get picked up by the next step.
    if folder.exists():
        shutil.rmtree(folder)
    folder.mkdir(parents=True)

    try:
        if pdf_path.stat().st_size == 0:
            # fitz reports 0-byte files as generic FileDataError; say why
            return _fail(pdf_path, folder, sha, "PDF file is empty (0 bytes)")
        return _render_into(pdf_path, folder, sha)
    except fitz.FileDataError:
        message = "not a readable PDF (corrupted or not really a PDF)"
    except RuntimeError as exc:  # PyMuPDF signals structural problems here
        message = str(exc) or "PDF could not be opened"
    except OSError as exc:
        message = f"could not read file: {exc}"
    except Exception as exc:  # noqa: BLE001 - never let one file stop a batch
        # Last-resort net so an unexpected PyMuPDF failure mode still
        # becomes a logged error manifest; logged at exception level so
        # the traceback is kept for post-mortem.
        log.exception("unexpected error rendering %s", pdf_path.name)
        message = f"unexpected error: {exc}"
    log.error("%s: %s", pdf_path.name, message)
    manifest = _error_manifest(pdf_path, sha, message)
    _write_manifest(folder, manifest)
    return manifest


def _render_into(pdf_path: Path, folder: Path, sha: str) -> dict[str, Any]:
    """Open and render; raises are caught by render_pdf, refusals here.

    "Refusals" are readable-but-unusable documents (encrypted, zero
    pages): they get an error manifest written now, same as a crash.
    """
    with fitz.open(pdf_path) as doc:
        if doc.needs_pass:
            return _fail(pdf_path, folder, sha, "PDF is encrypted")
        page_count = doc.page_count
        if page_count == 0:
            return _fail(pdf_path, folder, sha, "PDF contains no pages")

        pages: list[dict[str, Any]] = []
        for i, page in enumerate(doc, start=1):
            pix = _render_page(page)
            # image path is work_dir-relative so manifests move with the
            # cache folder without rewriting absolute paths
            rel = f"{folder.name}/{_page_image_name(i)}"
            pix.save(folder / _page_image_name(i))
            pages.append(
                {
                    "page": i,
                    "image": rel,
                    "width": pix.width,
                    "height": pix.height,
                    # Recorded only: scanned PDFs often carry a garbled
                    # text layer, so later steps must not trust it — but
                    # its size is a useful "is this a digital PDF?" hint.
                    "text_layer_chars": len(page.get_text()),
                }
            )

    manifest: dict[str, Any] = {
        "file": pdf_path.name,
        "sha256": sha,
        "page_count": page_count,
        "status": "ok",
        "error": None,
        "pages": pages,
    }
    _write_manifest(folder, manifest)
    return manifest


def _fail(
    pdf_path: Path, folder: Path, sha: str, message: str
) -> dict[str, Any]:
    """Build, log and persist an error manifest (encrypted / zero-page)."""
    log.error("%s: %s", pdf_path.name, message)
    manifest = _error_manifest(pdf_path, sha, message)
    _write_manifest(folder, manifest)
    return manifest
