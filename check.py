import copy
import json
import re
from collections import Counter
from decimal import Decimal, InvalidOperation
from pathlib import Path

import build
from erp import erp_book

WORK, OUT, NOTES = Path("work"), Path("output"), Path("notes")
TOL = Decimal("0.01")
SYMBOLS = {"€": "EUR", "£": "GBP", "₹": "INR", "$": "USD"}
AMOUNT_FIELDS = ("printed_gross_total", "printed_amount_due", "printed_subtotal", "printed_total_tax")


def dec(s):
    try:
        return Decimal(str(s).strip())
    except (InvalidOperation, ValueError):
        return None


def load(path):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def find_doc(work_docs, payable):
    for d in work_docs:
        if d.get("invoice_number", "").strip() == payable["invoice_number"] and d.get("po_number", "") == payable["po_number"]:
            return d
    return None


def printed_currencies(doc):
    texts = [doc.get(k, "") for k in AMOUNT_FIELDS + ("currency_text",)] + list(doc.get("other_total_amounts") or [])
    found = set()
    for t in texts:
        found.update(re.findall(r"\b[A-Z]{3}\b", str(t)))
        found.update(code for sym, code in SYMBOLS.items() if sym in str(t))
    return found


def cross_checks(payable, doc):
    out = {}
    lines_sum = sum((dec(l["total"]) or 0) for l in payable["line_items"])
    sub = dec(payable["subtotal"])
    if sub is not None:
        diff = lines_sum - sub
        out["lines_vs_subtotal"] = "ok" if abs(diff) <= TOL * (len(payable["line_items"]) + 1) else f"lines sum {lines_sum} vs subtotal {sub} (diff {diff})"
    if doc is None:
        return out
    printed_qty = dec(str(doc.get("printed_total_quantity", "")).replace(",", "").replace("*", ""))
    if printed_qty is not None:
        qty_sum = sum((dec(l["quantity"]) or 0) for l in payable["line_items"])
        out["quantity_vs_printed"] = "ok" if qty_sum == printed_qty else f"quantities sum {qty_sum} vs printed {printed_qty}"
    codes = printed_currencies(doc)
    if codes:
        out["currency_vs_printed"] = "ok" if codes == {payable["currency"]} else f"payable {payable['currency'] or '(blank)'} vs printed {sorted(codes)}"
    return out


def check_payable(payable, note, work_docs):
    doc = find_doc(work_docs, payable)
    entry = {"invoice_number": payable["invoice_number"], "po_number": payable["po_number"], "work_doc": "matched" if doc else "missing"}
    raw = (doc.get("printed_gross_total") or doc.get("printed_amount_due")) if doc else ""
    printed = build.num(raw)
    emitted = dec(payable["gross_total"])
    entry["printed_gross"] = raw if printed is not None else payable["gross_total"]
    anchor = printed if printed is not None else emitted
    if anchor is None:
        entry.update(status="skipped", detail="no printed gross and no emitted gross")
        return entry
    if printed is not None and emitted is not None and abs(printed - emitted) > TOL:
        entry["emitted_vs_printed"] = f"emitted {emitted} vs printed '{raw}' ({printed})"
    try:
        booked = Decimal(str(erp_book(copy.deepcopy(payable))["will_book_gross"]))
    except Exception as e:
        entry.update(status="skipped", detail=f"erp_book failed: {e}")
        return entry
    diff = abs(booked) - abs(anchor)
    entry.update(status="ok" if abs(diff) <= TOL else "mismatch", booked_gross=str(booked), difference=str(diff))
    entry["cross_checks"] = cross_checks(payable, doc)
    entry["build_notes"] = note
    return entry


def main():
    files, counts = {}, Counter()
    for path in sorted(OUT.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        notes = load(NOTES / path.name) or []
        work_docs = (load(WORK / path.name) or {}).get("documents", [])
        entries = []
        for i, payable in enumerate(data["payables"]):
            e = check_payable(payable, notes[i] if i < len(notes) else {}, work_docs)
            counts[e["status"]] += 1
            entries.append(e)
        files[path.stem] = {"payables": entries, "declined": data["declined"]}
        print(f"{path.stem}: " + ", ".join(e["status"] for e in entries) + (f" | {len(data['declined'])} declined" if data['declined'] else ""))
    Path("report.json").write_text(json.dumps({"summary": dict(counts), "files": files}, indent=2, ensure_ascii=False), encoding="utf-8")
    print(dict(counts))


if __name__ == "__main__":
    main()
