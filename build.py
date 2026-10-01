import json
import re
import sys
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from pathlib import Path

import masters

WORK, OUT, NOTES = Path("work"), Path("output"), Path("notes")
TOL = Decimal("0.02")

NON_PAYABLE = re.compile(
    r"statement|reminder|dunning|overdue|remittance|payment advice|delivery note|packing list|quotation"
    r"|\bquote\b|pro.?forma|order confirmation|\bhold\b|mahnung|zahlungserinnerung", re.I)
CREDIT = re.compile(r"credit\s*(note|memo)|gutschrift|avoir|nota\s+de\s+cr", re.I)
INCL = re.compile(r"incl|inkl|tax included|vat included|gross price|\bttc\b", re.I)
EXCL = re.compile(r"excl|exkl|net price|ex\.? ?vat|\bht\b", re.I)
FREIGHT = re.compile(r"freight|shipping|carriage|transport|delivery|versand|fracht|porto", re.I)
CHARGE_FIELDS = (
    ("freight_charges", FREIGHT),
    ("insurance_charges", re.compile(r"insur|versicher", re.I)),
    ("excise_duties", re.compile(r"excise|duty|duties|levy", re.I)),
    ("extra_charges", re.compile(r".*")),
)
DATE_FORMATS = ["%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y", "%d.%m.%Y", "%d-%m-%Y", "%d/%m/%y", "%d.%m.%y",
                "%d %b %Y", "%d %B %Y", "%b %d, %Y", "%B %d, %Y", "%d-%b-%Y", "%d %b, %Y", "%Y/%m/%d"]
SYMBOLS = {"€": "EUR", "£": "GBP", "₹": "INR", "$": "USD"}


def at(lst, i):
    return str(lst[i]).strip() if isinstance(lst, list) and i < len(lst) and lst[i] is not None else ""


def to_dec(digits):
    seps = [c for c in digits if c in ".,"]
    if not seps:
        return Decimal(digits)
    last = max(digits.rfind("."), digits.rfind(","))
    head, tail = digits[:last], digits[last + 1:]
    head_int = re.sub(r"[.,]", "", head)
    decimal_last = len(set(seps)) == 2 or len(tail) != 3 or head_int in ("", "0", "00")
    if decimal_last:
        return Decimal(f"{head_int or '0'}.{tail or '0'}")
    return Decimal(re.sub(r"[.,]", "", digits))


def num(s):
    s = str(s or "")
    digits = re.sub(r"[^\d.,]", "", s)
    if not re.search(r"\d", digits):
        return None
    try:
        v = to_dec(digits)
    except InvalidOperation:
        return None
    neg = re.search(r"[-−–]\s*[\d.,]", s) or re.search(r"\d\s*[-−–]\s*$", s) or re.match(r"^\s*\(.*\)\s*$", s)
    return -v if neg else v


def readings(s):
    digits = re.sub(r"[^\d.,]", "", str(s or ""))
    out = [num(s)]
    try:
        if digits:
            out.append(Decimal(re.sub(r"[.,]", "", digits)))
            last = max(digits.rfind("."), digits.rfind(","))
            if last >= 0:
                out.append(Decimal(re.sub(r"[.,]", "", digits[:last]) + "." + (digits[last + 1:] or "0")))
    except InvalidOperation:
        pass
    return [x for x in dict.fromkeys(out) if x is not None]


def fmt(d):
    if d is None:
        return ""
    s = format(d.quantize(Decimal("0.000001")).normalize(), "f")
    if "." not in s:
        return s + ".00"
    return s + "0" if len(s.split(".")[1]) < 2 else s


def qfmt(d):
    return "" if d is None else format(d.normalize(), "f")


def r2(d):
    return d.quantize(Decimal("0.01"), ROUND_HALF_UP)


def money(s, credit):
    v = num(s)
    return "" if v is None else fmt(abs(v) if credit else v)


def iso_date(s):
    s = re.sub(r"\s+", " ", str(s or "").strip())
    for f in DATE_FORMATS:
        try:
            return datetime.strptime(s, f).strftime("%Y-%m-%d")
        except ValueError:
            pass
    return ""


def currency(*texts):
    for t in texts:
        m = re.search(r"\b[A-Z]{3}\b", str(t or ""))
        if m:
            return m.group(0)
    for t in texts:
        for sym, code in SYMBOLS.items():
            if sym in str(t or ""):
                return code
    return ""


def tax_type(name):
    n = str(name).lower()
    if re.search(r"withhold|wht|quellen", n):
        return "WITHHOLDING"
    if "gst" in n:
        return "GST"
    if re.search(r"vat|mwst|ust|iva|tva|btw|sales tax|moms|kdv", n):
        return "VAT"
    return ""


def price_per(text):
    m = re.search(r"\d+", str(text or ""))
    d = Decimal(m.group(0)) if m else Decimal(1)
    return d if d > 0 else Decimal(1)


def fits(qty, p, per, dam, dpc, total):
    exp = qty * p / per
    if dpc:
        exp *= 1 - dpc / 100
    if dam:
        exp -= dam
    return abs(exp - total) <= TOL


def make_tax(name, rate, amount, credit, notes):
    r, a = num(rate), num(amount)
    code, why = masters.tax(name, rate)
    notes["taxes"].append(f"{name or '?'} {rate}: {why}")
    return {
        "tax_type": tax_type(name),
        "tax_name": name.strip(),
        "tax_rate": qfmt(r),
        "tax_amount": "" if a is None else fmt(abs(a) if credit else a),
        "tax_type_code": code,
    }


def build_lines(doc, credit, notes):
    absv = (lambda v: abs(v) if (credit and v is not None) else v)
    lines, calc = [], []
    for i in range(len(doc.get("line_descriptions") or [])):
        g = lambda k: at(doc.get(k), i)
        qty, total = num(g("line_quantities")), absv(num(g("line_totals")))
        dam, dpc = num(g("line_discounts")), num(g("line_discount_percents"))
        dam = abs(dam) if dam is not None else None
        per, raw, price = price_per(g("line_price_units")), g("line_unit_prices"), None
        for cand in readings(raw):
            cand = absv(cand)
            if qty is None or total is None:
                price = cand
                break
            if fits(qty, cand, per, dam, dpc, total):
                price = cand
                break
        if price is None and total is not None and qty != 0:
            notes["derived"].append(f"line {i + 1}: unit price taken from printed total ("
                                    + ("none printed" if not raw else f"printed '{raw}' does not foot") + ")")
            if qty is None:
                qty = Decimal(1)
                notes["derived"].append(f"line {i + 1}: quantity not printed, 1 used")
            price, per, dam, dpc = total / qty, Decimal(1), None, None
        elif price is not None and per > 1:
            notes["derived"].append(f"line {i + 1}: price printed per {per}, divided to per-unit")
        eff = None if price is None else price / per
        tname, trate, tamt = g("line_tax_names"), g("line_tax_rates"), g("line_tax_amounts")
        taxes = [make_tax(tname, trate, tamt, credit, notes)] if (tname or trate or tamt) else []
        desc, uom = g("line_descriptions"), g("line_uoms")
        item = "FREIGHT" if FREIGHT.search(desc) else ("SERVICE" if re.fullmatch(r"(?i)h|hr|hrs|hours?|days?|months?", uom) else "GOODS")
        lines.append({
            "description": desc, "item_type": item, "uom": uom,
            "quantity": qfmt(qty), "unit_price": fmt(eff), "total": fmt(total),
            "discount": fmt(dam), "discount_percentage": qfmt(dpc),
            "tax_rate": "", "tax_amount": "", "taxes": taxes,
        })
        calc.append({"p": eff, "T": total, "dam": dam, "rate": num(trate)})
    return lines, calc


def header_taxes(doc, credit, notes):
    names, rates, amts = (doc.get(k) or [] for k in ("header_tax_names", "header_tax_rates", "header_tax_amounts"))
    out = []
    for j in range(max(len(names), len(rates), len(amts))):
        n, r, a = at(names, j), at(rates, j), at(amts, j)
        if n or r or a:
            out.append(make_tax(n, r, a, credit, notes))
    return out


def header_repeats_lines(lines, header):
    line_rates = {t["tax_rate"] for l in lines for t in l["taxes"]}
    return bool(lines and header) and all(l["taxes"] for l in lines) and all(t["tax_rate"] and t["tax_rate"] in line_rates for t in header)


def charges(doc, credit, notes):
    out = {
        "freight_charges": money(doc.get("printed_freight_charges"), credit),
        "insurance_charges": money(doc.get("printed_insurance_charges"), credit),
        "extra_charges": money(doc.get("printed_extra_charges"), credit),
        "excise_duties": money(doc.get("printed_excise_duties"), credit),
    }
    labels, amounts = doc.get("charge_labels") or [], doc.get("charge_amounts") or []
    for j, label in enumerate(labels):
        amt = money(at(amounts, j), credit)
        if not amt:
            continue
        field = next(f for f, rx in CHARGE_FIELDS if rx.search(label))
        if out[field]:
            notes["warnings"].append(f"charge '{label}' {amt} not placed: {field} already filled")
        else:
            out[field] = amt
    return out


def apply_basis(doc, lines, calc, header, header_amounts, gross, notes):
    S = num(doc.get("printed_subtotal"))
    if not lines or any(c["T"] is None or c["p"] is None for c in calc):
        return
    header_rates = {num(t["tax_rate"]) for t in header if t["tax_rate"]}
    fallback = next(iter(header_rates)) if len(header_rates) == 1 else None
    rates = [c["rate"] if c["rate"] is not None else fallback for c in calc]
    if any(r is None for r in rates) or all(r == 0 for r in rates):
        return
    sum_t = sum(c["T"] for c in calc)
    net_sum = sum(c["T"] / (1 + r / 100) for c, r in zip(calc, rates))
    tol = Decimal("0.01") + Decimal("0.005") * len(calc)
    text = str(doc.get("price_basis_text") or "")
    evidence = ""
    if S is not None and abs(sum_t - S) > tol and abs(net_sum - S) <= tol:
        evidence = "printed subtotal equals lines with tax removed, not lines as printed"
    elif INCL.search(text) and not EXCL.search(text) and gross is not None and abs(sum_t + header_amounts - gross) <= tol:
        evidence = "document states prices include tax and lines add up to the gross"
    if not evidence:
        return
    for line, c, r in zip(lines, calc, rates):
        f = 1 + r / 100
        line["unit_price"] = fmt(c["p"] / f)
        line["total"] = fmt(r2(c["T"] / f))
        if c["dam"]:
            line["discount"] = fmt(c["dam"] / f)
    notes["price_basis"] = f"tax-inclusive prices converted to net: {evidence}"


def build_payable(doc):
    notes = {"invoice_number": doc.get("invoice_number", ""), "taxes": [], "derived": [], "warnings": []}
    type_text = f"{doc.get('title_text', '')} {doc.get('invoice_type_text', '')}"
    gross, due = num(doc.get("printed_gross_total")), num(doc.get("printed_amount_due"))
    credit = bool(CREDIT.search(type_text)) or (gross is not None and gross < 0)
    lines, calc = build_lines(doc, credit, notes)
    header = header_taxes(doc, credit, notes)
    if header_repeats_lines(lines, header):
        notes["warnings"].append("header tax block repeats the line-level taxes; kept on lines only")
        header = []
    ch = charges(doc, credit, notes)
    hdisc = num(doc.get("printed_discount_amount"))
    extra = sum((num(v) or 0) for v in ch.values()) - (abs(hdisc) if hdisc else 0)
    gross_val = gross if gross is not None else due
    apply_basis(doc, lines, calc, header, extra, abs(gross_val) if (credit and gross_val is not None) else gross_val, notes)

    vat, tax_id, name = doc.get("supplier_vat_id", ""), doc.get("supplier_tax_id", ""), doc.get("supplier_name", "")
    sup_id, notes["supplier"] = masters.supplier(vat, tax_id, name)
    buyer, notes["buyer"] = masters.buyer(doc.get("buyer_vat_id", ""), doc.get("buyer_name", ""))
    term, notes["payment_term"] = masters.payment_term(doc.get("payment_terms_text", ""))
    po_id, notes["po"] = masters.po(doc.get("po_number", ""))

    payable = {
        "invoice_number": str(doc.get("invoice_number", "")).strip(),
        "invoice_date": iso_date(doc.get("invoice_date")),
        "due_date": iso_date(doc.get("due_date")),
        "invoice_type": "CREDIT_MEMO" if credit else "INVOICE",
        "currency": currency(doc.get("currency_text"), doc.get("printed_gross_total")),
        "supplier": {"name": name, "supplier_id": sup_id, "address": doc.get("supplier_address", ""), "vat_id": vat or tax_id},
        "buyer": buyer,
        "payment_term_id": term,
        "po_number": doc.get("po_number", ""),
        "po_id": po_id,
        "gross_total": money(doc.get("printed_gross_total") or doc.get("printed_amount_due"), credit),
        "subtotal": money(doc.get("printed_subtotal"), credit),
        "total_tax_amount": money(doc.get("printed_total_tax"), credit),
        "discount_amount": money(doc.get("printed_discount_amount"), credit) and fmt(abs(hdisc)),
        **ch,
        "taxes": header,
        "line_items": lines,
    }
    return payable, notes


def has_prices(d):
    return any(str(p).strip() for p in d.get("line_unit_prices") or [])


def triage(doc, docs):
    title = doc.get("title_text", "")
    if NON_PAYABLE.search(f"{title} {doc.get('invoice_type_text', '')}"):
        return f"heading '{title}' is not a payable document"
    if num(doc.get("printed_gross_total")) is None and num(doc.get("printed_amount_due")) is None:
        return "no printed total"
    if not (doc.get("line_descriptions") or doc.get("po_number")):
        return "no lines and no PO"
    if not doc.get("po_number") and not has_prices(doc):
        if any(o is not doc and o.get("invoice_number") == doc.get("invoice_number")
               and o.get("po_number") and has_prices(o) for o in docs):
            return "summary of the detailed invoices in the same PDF (same invoice number, no PO, no unit prices)"
    return ""


def process(path):
    data = json.loads(path.read_text(encoding="utf-8"))
    docs = data.get("documents") or []
    payables, declined, notes = [], [], []
    for doc in docs:
        why = triage(doc, docs)
        pages = doc.get("page_numbers") or []
        if why:
            declined.append({"doc_type": doc.get("title_text") or "unknown", "reason": f"{why} (pages {pages})"})
            continue
        payable, note = build_payable(doc)
        payables.append(payable)
        notes.append(note)
    for p in data.get("unreadable_pages") or []:
        declined.append({"doc_type": "unreadable", "reason": f"page {p} has no readable content"})
    OUT.mkdir(exist_ok=True)
    NOTES.mkdir(exist_ok=True)
    result = {"file": f"{path.stem}.pdf", "payables": payables, "declined": declined}
    (OUT / f"{path.stem}.json").write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    (NOTES / f"{path.stem}.json").write_text(json.dumps(notes, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"{path.name}: {len(payables)} payables, {len(declined)} declined")


def main():
    paths = [Path(sys.argv[1])] if len(sys.argv) > 1 else sorted(WORK.glob("*.json"))
    for path in paths:
        process(path)


if __name__ == "__main__":
    main()
