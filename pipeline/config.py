"""Central knobs for the autodraft pipeline.

Kept free of behaviour so every step reads the same values and tests can
import the defaults without pulling in I/O. Paths are intentionally
relative; run.py resolves them against the current directory and lets the
CLI override the ones operators need to move (docs in, JSON out).
"""

from __future__ import annotations

DOCS_DIR = "documents"
WORK_DIR = "work"
OUT_DIR = "output"

# 200 DPI is the sweet spot for vision models reading dense invoice scans:
# legible small print without multi-megapixel images.
RENDER_DPI = 200

# Some providers reject or silently downscale very large images, so cap the
# long side and lower the render scale to fit instead.
MAX_PIXELS_LONG_SIDE = 3000

# PNG is lossless; never risk compression artefacts on tiny digits and tax
# rates that later steps must read back exactly.
IMAGE_FORMAT = "png"
