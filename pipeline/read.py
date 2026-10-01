"""Step 2: read one rendered PDF with Sarvam DocAI — capture, never interpret.

The reader sends the page PNGs from step 1 to Sarvam's documented extract
job (POST /doc-ai/v1/job/extract with repeated `file` parts and the
inline `schema` form field from config.READ_SCHEMA, then polls
/job/{id}/status and fetches /job/{id}/results) and writes what comes
back to work/<stem>/read.json. It exists to put the printed values on
disk untouched: "1.049.579,86" and "02.02.2026" stay those exact
strings, absent fields stay null, and nothing is parsed, computed,
matched against master data or classified here — that is all step 3+.
The safest way to guarantee that is the pass-through rule below: the
code moves result objects around without ever editing a leaf value.
Classification output is requested but deliberately not interpreted: any
class label Sarvam returns stays in sarvam_raw.json only.

Cache key = sha256 + SCHEMA_VERSION (both stored in read.json): the sha
proves the bytes we read are the bytes we rendered; the schema version
proves the inline schema asked for the field list we still expect. If
either changes, the old capture is refetched rather than trusted.

Failure policy mirrors render.py: every API/shape problem becomes a
status "error" result for that one file, never an exception that stops
the batch. The exception is a missing SARVAM_API_KEY, which is fatal for
the whole run (exit 2 via run.py) because no file can succeed without
it. The API key is read from the environment only when a call is
actually needed (a fully cached folder runs without it) and is never
written to any file or log line.
"""

from __future__ import annotations

import json
import logging
import os
import time
from pathlib import Path
from typing import Any

import requests

from . import config

log = logging.getLogger(__name__)

# Terminal job states per the documented status endpoint — do not assume
# only "completed"/"failed" exist. Anything else means still in flight.
_TERMINAL = frozenset({"completed", "partially_completed", "failed", "rejected"})
_FAILED = frozenset({"failed", "rejected"})

# The result object must carry at least these; the job ran with
# config.READ_SCHEMA and a response missing one of them is a schema
# problem to report, not parts to fill in.
_REQUIRED_RESULT_KEYS = ("documents", "unreadable_pages")

# Sarvam's documented auth header for all examples and official SDKs.
_AUTH_HEADER = "api-subscription-key"


class ReadConfigError(Exception):
    """Missing SARVAM_API_KEY.

    Raised rather than recorded: one absent env var dooms every file in
    the batch, so run.py treats it as fatal (exit 2), like no docs folder.
    """


class ReadAPIError(Exception):
    """A Sarvam call failed, retried out, or returned an unusable shape.

    Carries the plain message that lands in read.json and the log line.
    One bad response must never stop the batch, so this is always caught
    by read_pdf and turned into status "error".
    """


def _credentials() -> str:
    """Return the API key from the environment, or raise.

    Only SARVAM_API_KEY is required now — the schema travels inline with
    every job. The message names the variable but never echoes its value:
    the key must not reach stderr, logs, or any committed file.
    """
    api_key = os.environ.get(config.SARVAM_API_KEY_ENV)
    if not api_key:
        raise ReadConfigError(
            f"missing environment variable: {config.SARVAM_API_KEY_ENV} — "
            "the read step calls Sarvam DocAI and cannot run without it"
        )
    return str(api_key)


def _do_request(method: str, url: str, **kwargs: Any) -> requests.Response:
    """The single function that touches the network; tests patch this."""
    return requests.request(method, url, **kwargs)


def _snippet(response: requests.Response) -> str:
    """HTTP status + body prefix, for error messages.

    Sarvam's own status and text are preserved verbatim (truncated only
    to keep read.json sane); we invent no error JSON of our own.
    """
    try:
        body = (response.text or "").strip()
    except Exception:  # pragma: no cover - defensive, .text is lazy-decoded
        body = ""
    return f"HTTP {response.status_code}: {body[:300]}"


def _json_body(response: requests.Response, what: str) -> Any:
    try:
        return response.json()
    except ValueError:
        raise ReadAPIError(
            f"{what} response is not JSON — {_snippet(response)}"
        ) from None


def _request(method: str, url: str, *, api_key: str, **kwargs: Any) -> requests.Response:
    """One HTTP call with the documented auth header, timeout, and retry.

    Retries on 429/5xx and network errors only, up to READ_MAX_RETRIES
    times with exponential backoff. Any other 4xx (403 auth, 400 bad
    request, 413 too large...) returns immediately — retrying those just
    multiplies a permanent failure. Non-2xx responses are handed back to
    the caller to phrase per endpoint; only exhausted retries raise.
    """
    kwargs.setdefault("timeout", config.READ_TIMEOUT)
    kwargs["headers"] = {_AUTH_HEADER: api_key}
    attempts = config.READ_MAX_RETRIES + 1  # first try + retries
    last_error = "unknown error"
    for attempt in range(attempts):
        try:
            response = _do_request(method, url, **kwargs)
        except requests.RequestException as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if response.status_code != 429 and response.status_code < 500:
                return response
            last_error = _snippet(response)
        if attempt < attempts - 1:
            time.sleep(config.READ_RETRY_BACKOFF * (2**attempt))
    raise ReadAPIError(f"{method} {url} failed after {attempts} attempt(s): {last_error}")


def _start_job(api_key: str, work_dir: Path, pages: list[dict[str, Any]]) -> tuple[str, Any]:
    """POST every page PNG as repeated `file` parts plus the inline schema.

    The schema is a multipart form field carrying config.READ_SCHEMA as a
    JSON string (json.dumps) — there is no saved Config id any more. The
    endpoint also gets language/classification/auto_orient/output_format
    as plain form fields. The classification label, if Sarvam returns
    one anywhere, is not interpreted here; it stays in sarvam_raw.json.
    """
    files: list[tuple[str, tuple[str, bytes, str]]] = []
    for page in sorted(pages, key=lambda p: p["page"]):
        image = work_dir / page["image"]
        if not image.is_file():
            # OneDrive evicts cold files to placeholders; without the
            # PNG the job would silently see fewer pages than rendered.
            raise ReadAPIError(f"page image missing on disk: {page['image']}")
        files.append(("file", (image.name, image.read_bytes(), f"image/{config.IMAGE_FORMAT}")))

    url = f"{config.SARVAM_BASE_URL}/doc-ai/v1/job/extract"
    response = _request(
        "POST",
        url,
        api_key=api_key,
        data={
            "schema": json.dumps(config.READ_SCHEMA),
            "output_format": "json",
            "language": "en-IN",
            "classification": "false",
            "auto_orient": "true",
        },
        files=files,
    )
    if response.status_code not in (200, 201):
        raise ReadAPIError(f"extract job request refused — {_snippet(response)}")
    payload = _json_body(response, "extract")
    job_id = payload.get("job_id") if isinstance(payload, dict) else None
    if not job_id:
        raise ReadAPIError(f"extract response has no job_id — {_snippet(response)}")
    return str(job_id), payload


def _await_job(api_key: str, job_id: str) -> tuple[str, Any]:
    """Poll /job/{id}/status until a terminal state or the deadline.

    404 means the job is genuinely unknown to Sarvam (the documented
    status error) and is surfaced, not looped on forever.
    """
    url = f"{config.SARVAM_BASE_URL}/doc-ai/v1/job/{job_id}/status"
    deadline = time.monotonic() + config.READ_POLL_TIMEOUT
    while True:
        response = _request("GET", url, api_key=api_key)
        if response.status_code != 200:
            raise ReadAPIError(f"job status lookup failed — {_snippet(response)}")
        payload = _json_body(response, "status")
        if not isinstance(payload, dict):
            raise ReadAPIError("job status response is not a JSON object")
        job_status = payload.get("status")
        if not isinstance(job_status, str):
            raise ReadAPIError("job status response has no 'status' field")
        if job_status in _TERMINAL:
            return job_status, payload
        if time.monotonic() >= deadline:
            raise ReadAPIError(
                f"job {job_id} still '{job_status}' after {config.READ_POLL_TIMEOUT:.0f}s"
            )
        time.sleep(config.READ_POLL_INTERVAL)


def _fetch_results(api_key: str, job_id: str) -> Any:
    """GET /job/{id}/results?format=json (only valid after terminal status;
    a non-terminal 409 here means something is very wrong, so it surfaces)."""
    url = f"{config.SARVAM_BASE_URL}/doc-ai/v1/job/{job_id}/results?format=json"
    response = _request("GET", url, api_key=api_key)
    if response.status_code != 200:
        raise ReadAPIError(f"job results lookup failed — {_snippet(response)}")
    return _json_body(response, "results")


def _validate_results(results_payload: Any) -> dict[str, Any]:
    """Pull the captured fields out of the documented wrapper, or raise.

    The extracted values live under "result" (never the old guessed
    "output"), and must carry the required top-level keys. Everything
    inside them is returned as-is — no renaming, defaulting or repair of
    leaves, because a wrong schema must show as data, not be masked here.
    Anything else Sarvam puts in the wrapper — a classification label
    included — is not interpreted or required; it is preserved in
    sarvam_raw.json by read_pdf and nothing else.
    """
    if not isinstance(results_payload, dict):
        raise ReadAPIError("results response is not a JSON object")
    result = results_payload.get("result")
    if not isinstance(result, dict):
        raise ReadAPIError(
            "results response has no 'result' object — Sarvam did not return "
            "the schema's extraction shape"
        )
    missing = [key for key in _REQUIRED_RESULT_KEYS if key not in result]
    if missing:
        raise ReadAPIError(
            f"extracted result is missing required key(s): {', '.join(missing)}"
        )
    if not isinstance(result["documents"], list):
        raise ReadAPIError("extracted result 'documents' is not a list")
    if not isinstance(result["unreadable_pages"], list):
        raise ReadAPIError("extracted result 'unreadable_pages' is not a list")
    return result


def _write_json(path: Path, payload: Any) -> None:
    """Atomic JSON write (temp + rename), same reasoning as manifest.json:
    a half-written file must never pose as a cached result."""
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _cached_document(folder: Path, manifest: dict[str, Any]) -> dict[str, Any] | None:
    """Return a previous ok read.json if sha and schema version still match."""
    path = folder / "read.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        log.warning("unreadable read.json in %s, refetching", folder)
        return None
    if not isinstance(data, dict):
        return None
    if data.get("status") != "ok":
        return None
    if data.get("sha256") != manifest["sha256"]:
        return None
    if data.get("schema_version") != config.SCHEMA_VERSION:
        return None
    return data


def read_pdf(manifest: dict[str, Any], work_dir: Path) -> dict[str, Any]:
    """Read one rendered PDF (status "ok" manifest) and write read.json.

    Returns the saved document: status "ok" with documents[] /
    unreadable_pages[] exactly as Sarvam produced them under our inline
    schema (raw printed strings, nulls for anything not printed), or
    status "error" with a plain message and no partial fields. Raises
    ReadConfigError only when the environment makes any Sarvam call
    impossible.
    """
    work_dir = Path(work_dir)
    folder = work_dir / Path(manifest["file"]).stem

    cached = _cached_document(folder, manifest)
    if cached is not None:
        log.debug("cache hit for %s", manifest["file"])
        return cached

    api_key = _credentials()
    # render_pdf created this folder; re-assert before writing in case the
    # cache dir was cleaned between steps (OneDrive eviction, manual rm).
    folder.mkdir(parents=True, exist_ok=True)

    base = {
        "file": manifest["file"],
        "sha256": manifest["sha256"],
        "schema_version": config.SCHEMA_VERSION,
    }
    # Debug copy lands progressively and BEFORE any processing: every
    # response Sarvam actually sent is on disk even if a later call fails
    # or validation below rejects the shape.
    raw: dict[str, Any] = {}
    try:
        job_id, job_payload = _start_job(api_key, work_dir, manifest["pages"])
        raw["extract"] = job_payload
        _write_json(folder / "sarvam_raw.json", raw)
        job_status, status_payload = _await_job(api_key, job_id)
        raw["status"] = status_payload
        _write_json(folder / "sarvam_raw.json", raw)
        if job_status in _FAILED:
            # Nothing usable comes out of a failed/rejected job; asking
            # for its results risks a 409 that would hide this message.
            raise ReadAPIError(f"extraction job {job_status} (see sarvam_raw.json)")
        # "partially_completed" continues: pages that succeeded are truth.
        results_payload = _fetch_results(api_key, job_id)
        raw["results"] = results_payload
        _write_json(folder / "sarvam_raw.json", raw)
        captured = _validate_results(results_payload)
    except ReadAPIError as exc:
        log.debug("%s: read failed: %s", manifest["file"], exc)
        document = {**base, "status": "error", "error": str(exc)}
    else:
        document = {
            **base,
            "status": "ok",
            "error": None,
            "job_id": job_id,
            # "partially_completed" tells step 3 some pages failed
            # upstream; it is not an error — what was read is still truth.
            "job_status": job_status,
            "documents": captured["documents"],
            "unreadable_pages": captured["unreadable_pages"],
        }
    _write_json(folder / "read.json", document)
    return document
