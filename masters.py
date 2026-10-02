"""Master-data lookups. Every function returns (code, reason); code is "" when there is no safe match."""
import json
import logging
import os
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

logger = logging.getLogger(__name__)

MASTER_DIR = Path(os.environ.get("BOOKABLE_MASTER_DIR", "master_data"))
FUZZY_MIN = 0.92
FUZZY_GAP = 0.03
MAX_CANDIDATES = 50

# Candidate column names per master (first one present in a row is used).
FIELDS = {
    "suppliers": {
        "code": ["supplier_id", "id", "supplier_code", "code"],
        "name": ["name", "supplier_name"],
        "vat": ["vat_id", "vat", "vat_number", "tax_id", "tax_number"],
    },
    "chart_of_books": {
        # rows are nested (company -> business_units -> locations); load_rows flattens them
        # into one dict per (company, unit, location) carrying exactly these keys.
        "company_code": ["company_code"],
        "business_unit_code": ["business_unit_code"],
        "location_code": ["location_code"],
        "name": ["company_name", "name", "legal_name", "business_unit_name", "location_name"],
        "vat": ["vat_id", "vat", "vat_number", "tax_id", "tax_number"],
    },
    "payment_terms": {
        "code": ["payment_term_id", "id", "code", "term_id"],
        "name": ["name", "description", "label"],
        "days": ["days", "net_days", "due_days"],
        "aliases": ["text_aliases", "aliases"],
    },
    "po_master": {
        "code": ["po_id", "id", "code"],
        "number": ["po_number", "number", "po"],
    },
    "tax_master": {
        "code": ["tax_type_code", "tax_code", "code", "id"],
        "name": ["tax_name", "name", "description"],
        "rate": ["tax_rate", "rate", "percentage"],
    },
}

# system can compare company names by their actual business identity
LEGAL_WORDS = {
    "gmbh", "llc", "ltd", "limited", "inc", "incorporated", "corp", "corporation", "co", "company",
    "sa", "bv", "nv", "ag", "kg", "ug", "srl", "spa", "sas", "sarl", "plc", "llp", "pvt", "private",
    "pty", "bhd", "sdn", "oy", "ou", "as", "ltda", "lda", "the",
}

REQUIRED_MASTERS = {"suppliers", "chart_of_books", "payment_terms", "po_master", "tax_master"}


class MasterDataError(Exception):
    """Raised when a master file is missing, unreadable, or malformed."""
    pass


def _resolve_master_dir(path=None):
    global MASTER_DIR
    if path is not None:
        MASTER_DIR = Path(path)
    else:
        MASTER_DIR = Path(os.environ.get("BOOKABLE_MASTER_DIR", "master_data"))
    return MASTER_DIR


def norm_id(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def norm_name(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", s).split() if t not in LEGAL_WORDS]
    return " ".join(tokens)


def norm_rate(s):
    try:
        return "%g" % float(str(s).replace("%", "").replace(",", ".").strip())
    except (TypeError, ValueError):
        return ""


def days_in(text):
    text = str(text)
    m = re.search(r"(\d{1,3})\s*(?:days?|d\b)", text, re.I) or re.search(r"net\s*(\d{1,3})", text, re.I)
    return m.group(1) if m else ""


def load_rows(name):
    path = MASTER_DIR / f"{name}.json"
    if not path.exists():
        raise MasterDataError(f"Missing master file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise MasterDataError(f"Malformed JSON in {path}: {exc}") from exc
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if lists else list(data.values())
    if not isinstance(data, list):
        logger.warning("Master file %s did not deserialize to a list; coercing to [data]", path)
        data = [data]
    rows = []
    for idx, row in enumerate(data):
        if not isinstance(row, dict):
            logger.warning("Skipping non-dict row %s in %s", idx, path)
            continue
        rows.append({k.lower(): v for k, v in row.items()})
    return rows


def pick(row, names, required=False):
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return str(v).strip()
    if required:
        logger.warning("Missing required field; candidates=%s row_keys=%s", names, list(row.keys()))
    return ""


def flatten_books(rows):
    """chart_of_books rows are companies with business_units[] and locations[] inside.
    Emit one flat dict per (company, unit, location), carrying every name column so the
    same indexing loop works. Already-flat rows pass through untouched."""
    out = []
    for idx, c in enumerate(rows):
        if not isinstance(c, dict):
            logger.warning("Skipping non-dict chart_of_books row %s: %s", idx, type(c).__name__)
            continue
        if not isinstance(c.get("business_units"), list):
            out.append(c)
            continue
        for bu_idx, bu in enumerate(c["business_units"]):
            if not isinstance(bu, dict):
                logger.warning("business_units[%s] in chart_of_books row %s is not a dict", bu_idx, idx)
                continue
            locs = bu.get("locations")
            if locs is not None and not isinstance(locs, list):
                logger.warning("locations in chart_of_books row %s business_unit %s is not a list", idx, bu_idx)
                locs = []
            for loc_idx, loc in enumerate(locs or [{}]):
                if not isinstance(loc, dict):
                    logger.warning("Skipping non-dict location at row %s / business unit %s / location %s", idx, bu_idx, loc_idx)
                    continue
                out.append({
                    "company_code": c.get("company_code", ""),
                    "company_name": c.get("company_name", ""),
                    "business_unit_code": bu.get("business_unit_code", ""),
                    "business_unit_name": bu.get("business_unit_name", ""),
                    "location_code": loc.get("location_code", ""),
                    "location_name": loc.get("location_name", ""),
                })
    return out


class Index:
    def __init__(self):
        self.d = defaultdict(set)

    def add(self, key, code):
        if key and code:
            self.d[key].add(code)

    def get(self, key):
        return self.d.get(key, set()) if key else set()


class NameIndex(Index):
    def __init__(self):
        super().__init__()
        self.postings = defaultdict(set)

    def add(self, key, code):
        n = norm_name(key)
        if not n or not code:
            return
        self.d[n].add(code)
        for t in n.split():
            self.postings[t].add(n)

    def find(self, name):
        n = norm_name(name)
        if not n:
            return set(), ""
        if n in self.d:
            return self.d[n], "name exact"
        tokens = sorted(set(n.split()), key=lambda t: len(self.postings.get(t, ())))
        cands = set()
        for t in tokens[:3]:
            cands |= self.postings.get(t, set())
            if len(cands) > MAX_CANDIDATES:
                break
        scored = sorted(((SequenceMatcher(None, n, c).ratio(), c) for c in list(cands)[:MAX_CANDIDATES]), reverse=True)
        if not scored or scored[0][0] < FUZZY_MIN:
            return set(), ""
        best, best_name = scored[0]
        rivals = [c for r, c in scored[1:] if r >= best - FUZZY_GAP and self.d[c] != self.d[best_name]]
        if rivals:
            codes = self.d[best_name] | set().union(*[self.d[c] for c in rivals])
            logger.warning("Ambiguous name match for %r: best=%s score=%.3f rivals=%s", name, best_name, best, rivals)
            return codes, f"name fuzzy {best:.2f} (ambiguous)"
        return self.d[best_name], f"name fuzzy {best:.2f}"


def resolve(steps):
    """steps = [(codes, reason), ...]; first step with exactly one code wins."""
    note = "no match"
    for codes, reason in steps:
        if len(codes) == 1:
            return next(iter(codes)), reason
        if len(codes) > 1:
            note = f"ambiguous ({reason})"
    return "", note


# ---------- build indexes once ----------
_SUP_VAT, _SUP_NAME = Index(), NameIndex()
_BOOK_ROWS = []
_BOOK_VAT, _BOOK_NAME = Index(), NameIndex()
_PT_TEXT, _PT_DAYS = NameIndex(), Index()
_PO = Index()
_TAX_FULL, _TAX_RATE = Index(), Index()


def reindex(master_dir=None):
    """Build or rebuild the master-data indexes."""
    global MASTER_DIR, _SUP_VAT, _SUP_NAME, _BOOK_ROWS, _BOOK_VAT, _BOOK_NAME, _PT_TEXT, _PT_DAYS, _PO, _TAX_FULL, _TAX_RATE
    if master_dir is not None:
        MASTER_DIR = Path(master_dir)
    else:
        MASTER_DIR = Path(os.environ.get("BOOKABLE_MASTER_DIR", "master_data"))

    missing = [f"{MASTER_DIR / f'{name}.json'}" for name in REQUIRED_MASTERS if not (MASTER_DIR / f"{name}.json").exists()]
    if missing:
        raise MasterDataError(f"Missing required master files: {', '.join(missing)}")

    _SUP_VAT, _SUP_NAME = Index(), NameIndex()
    for r in load_rows("suppliers"):
        f = FIELDS["suppliers"]
        code = pick(r, f["code"])
        if not code:
            continue
        _SUP_VAT.add(norm_id(pick(r, f["vat"])), code)
        _SUP_NAME.add(pick(r, f["name"]), code)
    if not _SUP_VAT.d and not _SUP_NAME.d:
        logger.warning("Supplier index is empty after reindex()")

    _BOOK_ROWS = []
    _BOOK_VAT, _BOOK_NAME = Index(), NameIndex()
    rows = flatten_books(load_rows("chart_of_books"))
    for i, r in enumerate(rows):
        f = FIELDS["chart_of_books"]
        _BOOK_ROWS.append({k: pick(r, f[k]) for k in ("company_code", "business_unit_code", "location_code")})
        vat = pick(r, f["vat"])
        if vat:
            _BOOK_VAT.add(norm_id(vat), str(i))
        for n in f["name"]:
            _BOOK_NAME.add(str(r.get(n, "")), str(i))
    if not _BOOK_VAT.d and not _BOOK_NAME.d:
        logger.warning("Chart-of-books index is empty after reindex()")

    _PT_TEXT, _PT_DAYS = NameIndex(), Index()
    for r in load_rows("payment_terms"):
        f = FIELDS["payment_terms"]
        code = pick(r, f["code"])
        name = pick(r, f["name"])
        if not code:
            continue
        aliases = next((v for a in f["aliases"] if isinstance(v := r.get(a), list)), [])
        for t in [code, name] + [str(a) for a in aliases]:
            _PT_TEXT.add(t, code)
        _PT_DAYS.add(pick(r, f["days"]) or days_in(name) or days_in(code), code)
    if not _PT_TEXT.d and not _PT_DAYS.d:
        logger.warning("Payment terms index is empty after reindex()")

    _PO = Index()
    for r in load_rows("po_master"):
        f = FIELDS["po_master"]
        code = pick(r, f["code"])
        if code:
            _PO.add(norm_id(pick(r, f["number"])), code)
    if not _PO.d:
        logger.warning("PO master index is empty after reindex()")

    _TAX_FULL, _TAX_RATE = Index(), Index()
    for r in load_rows("tax_master"):
        f = FIELDS["tax_master"]
        code = pick(r, f["code"])
        if not code:
            continue
        rate = norm_rate(pick(r, f["rate"]))
        _TAX_FULL.add((rate, norm_name(pick(r, f["name"]))), code)
        _TAX_RATE.add(rate, code)
    if not _TAX_FULL.d and not _TAX_RATE.d:
        logger.warning("Tax master index is empty after reindex()")


# ---------- public lookups ----------
def supplier(vat_id="", tax_id="", name=""):
    if not isinstance(vat_id, str) or not isinstance(tax_id, str) or not isinstance(name, str):
        logger.warning("supplier(): invalid input types: %s, %s, %s", type(vat_id), type(tax_id), type(name))
        return "", "invalid input"
    steps = [(_SUP_VAT.get(norm_id(v)), "vat exact") for v in (vat_id, tax_id) if v]
    codes, reason = _SUP_NAME.find(name)
    if reason:
        steps.append((codes, reason))
    return resolve(steps)


def buyer(vat_id="", name=""):
    if not isinstance(vat_id, str) or not isinstance(name, str):
        logger.warning("buyer(): invalid input types: %s, %s", type(vat_id), type(name))
        return {"company_code": "", "business_unit_code": "", "location_code": ""}, "invalid input"
    steps = []
    if vat_id:
        steps.append((_BOOK_VAT.get(norm_id(vat_id)), "vat exact"))
    if name:
        steps.append(_BOOK_NAME.find(name))
    for ids, reason in steps:
        if ids:
            rows = [_BOOK_ROWS[int(i)] for i in ids]
            out = {k: (rows[0][k] if all(x[k] == rows[0][k] for x in rows) else "") for k in rows[0]}
            return out, reason if len(rows) == 1 else f"{reason} ({len(rows)} rows, shared codes only)"
    return {"company_code": "", "business_unit_code": "", "location_code": ""}, "no match"


def payment_term(text=""):
    if not isinstance(text, str):
        logger.warning("payment_term(): invalid input type: %s", type(text))
        return "", "invalid input"
    steps = []
    if text:
        steps.append(_PT_TEXT.find(text))
        d = days_in(text)
        if d:
            steps.append((_PT_DAYS.get(d), f"days {d}"))
    return resolve(steps)


def po(po_number=""):
    if not isinstance(po_number, str):
        logger.warning("po(): invalid input type: %s", type(po_number))
        return "", "invalid input"
    parts = [po_number] + [p for p in re.split(r"[/,;\s]+", po_number) if p]
    return resolve([(_PO.get(norm_id(p)), "po exact") for p in dict.fromkeys(parts)])


def tax(name="", rate=""):
    if not isinstance(name, str) or not isinstance(rate, str):
        logger.warning("tax(): invalid input types: %s, %s", type(name), type(rate))
        return "", "invalid input"
    r, n = norm_rate(rate), norm_name(name)
    steps = [
        (_TAX_FULL.get((r, n)), "rate+name exact") if r and n else (set(), ""),
        (_TAX_RATE.get(r) if r else set(), "rate only") if r else (set(), ""),
    ]
    return resolve([s for s in steps if s[1]])


# module-load initialization
try:
    reindex()
except MasterDataError as exc:
    logger.error("Failed to initialize master-data indexes: %s", exc)
