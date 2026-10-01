"""Master-data lookups. Every function returns (code, reason); code is "" when there is no safe match."""
import json
import re
import unicodedata
from collections import defaultdict
from difflib import SequenceMatcher
from pathlib import Path

MASTER_DIR = Path("master_data")
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

LEGAL_WORDS = {
    "gmbh", "llc", "ltd", "limited", "inc", "incorporated", "corp", "corporation", "co", "company",
    "sa", "bv", "nv", "ag", "kg", "ug", "srl", "spa", "sas", "sarl", "plc", "llp", "pvt", "private",
    "pty", "bhd", "sdn", "oy", "ou", "as", "ltda", "lda", "the",
}


def norm_id(s):
    return re.sub(r"[^A-Z0-9]", "", str(s).upper())


def norm_name(s):
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    tokens = [t for t in re.sub(r"[^a-z0-9]+", " ", s).split() if t not in LEGAL_WORDS]
    return " ".join(tokens)


def norm_rate(s):
    try:
        return "%g" % float(str(s).replace("%", "").replace(",", ".").strip())
    except ValueError:
        return ""


def days_in(text):
    m = re.search(r"(\d{1,3})\s*(?:days?|d\b)", str(text), re.I) or re.search(r"net\D{0,3}(\d{1,3})", str(text), re.I)
    return m.group(1) if m else ""


def load_rows(name):
    path = MASTER_DIR / f"{name}.json"
    if not path.exists():
        return []
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        lists = [v for v in data.values() if isinstance(v, list)]
        data = lists[0] if lists else list(data.values())
    return [{k.lower(): v for k, v in r.items()} for r in data if isinstance(r, dict)]


def pick(row, names):
    for n in names:
        v = row.get(n)
        if v not in (None, ""):
            return str(v).strip()
    return ""


def flatten_books(rows):
    """chart_of_books rows are companies with business_units[] and locations[] inside.
    Emit one flat dict per (company, unit, location), carrying every name column so the
    same indexing loop works. Already-flat rows pass through untouched."""
    out = []
    for c in rows:
        if not isinstance(c.get("business_units"), list):
            out.append(c)
            continue
        for bu in c["business_units"]:
            for loc in bu.get("locations") or [{}]:
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

    def add(self, name, code):
        n = norm_name(name)
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
            return self.d[best_name] | set().union(*[self.d[c] for c in rivals]), f"name fuzzy {best:.2f}"
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
for r in load_rows("suppliers"):
    f = FIELDS["suppliers"]
    code = pick(r, f["code"])
    _SUP_VAT.add(norm_id(pick(r, f["vat"])), code)
    _SUP_NAME.add(pick(r, f["name"]), code)

_BOOK_ROWS = []
_BOOK_VAT, _BOOK_NAME = Index(), NameIndex()
for i, r in enumerate(flatten_books(load_rows("chart_of_books"))):
    f = FIELDS["chart_of_books"]
    _BOOK_ROWS.append({k: pick(r, f[k]) for k in ("company_code", "business_unit_code", "location_code")})
    _BOOK_VAT.add(norm_id(pick(r, f["vat"])), str(i))
    for n in f["name"]:
        _BOOK_NAME.add(str(r.get(n, "")), str(i))

_PT_TEXT, _PT_DAYS = NameIndex(), Index()
for r in load_rows("payment_terms"):
    f = FIELDS["payment_terms"]
    code, name = pick(r, f["code"]), pick(r, f["name"])
    aliases = next((r.get(a) for a in f["aliases"] if isinstance(r.get(a), list)), [])
    for t in [code, name] + [str(a) for a in aliases]:
        _PT_TEXT.add(t, code)
    _PT_DAYS.add(pick(r, f["days"]) or days_in(name) or days_in(code), code)

_PO = Index()
for r in load_rows("po_master"):
    f = FIELDS["po_master"]
    _PO.add(norm_id(pick(r, f["number"])), pick(r, f["code"]))

_TAX_FULL, _TAX_RATE = Index(), Index()
for r in load_rows("tax_master"):
    f = FIELDS["tax_master"]
    code, rate = pick(r, f["code"]), norm_rate(pick(r, f["rate"]))
    _TAX_FULL.add((rate, norm_name(pick(r, f["name"]))), code)
    _TAX_RATE.add(rate, code)


# ---------- public lookups ----------
def supplier(vat_id="", tax_id="", name=""):
    steps = [(_SUP_VAT.get(norm_id(v)), "vat exact") for v in (vat_id, tax_id) if v]
    codes, reason = _SUP_NAME.find(name)
    steps.append((codes, reason))
    return resolve(steps)


def buyer(vat_id="", name=""):
    steps = []
    if vat_id:
        steps.append((_BOOK_VAT.get(norm_id(vat_id)), "vat exact"))
    steps.append(_BOOK_NAME.find(name))
    for ids, reason in steps:
        if ids:
            rows = [_BOOK_ROWS[int(i)] for i in ids]
            out = {k: (rows[0][k] if all(x[k] == rows[0][k] for x in rows) else "") for k in rows[0]}
            return out, reason if len(rows) == 1 else f"{reason} ({len(rows)} rows, shared codes only)"
    return {"company_code": "", "business_unit_code": "", "location_code": ""}, "no match"


def payment_term(text=""):
    steps = [_PT_TEXT.find(text)]
    d = days_in(text)
    if d:
        steps.append((_PT_DAYS.get(d), f"days {d}"))
    return resolve(steps)


def po(po_number=""):
    parts = [po_number] + [p for p in re.split(r"[/,;\s]+", str(po_number)) if p]
    return resolve([(_PO.get(norm_id(p)), "po exact") for p in dict.fromkeys(parts)])


def tax(name="", rate=""):
    r, n = norm_rate(rate), norm_name(name)
    return resolve([(_TAX_FULL.get((r, n)), "rate+name exact"), (_TAX_RATE.get(r) if r else set(), "rate only")])
