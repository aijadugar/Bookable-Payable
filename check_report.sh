#!/usr/bin/env bash
set -euo pipefail

REPORT="${1:-report.json}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# First, give permission "chmod +x check_report.sh". Then run ./check_report.sh report.json
PY=""
for cand in python3 python py; do
  if command -v "$cand" >/dev/null 2>&1 && "$cand" -c "import sys" >/dev/null 2>&1; then
    PY="$cand"
    break
  fi
done
[ -n "$PY" ] || { echo "❌ No working Python found (tried python3, python, py)"; exit 1; }

export PYTHONUTF8=1   # so the emoji print correctly on Windows

"$PY" - "$REPORT" "$SCRIPT_DIR" <<'PY'
import json, os, subprocess, sys, tempfile

report_path = sys.argv[1]
ERP = os.path.join(sys.argv[2], "erp.py")

def parse_num(v):
    """Parse '1.5', '1,5' or '1,234.56' into a float."""
    s = str(v).strip().replace(" ", "")
    if "," in s and "." in s:
        s = s.replace(",", "")          # comma = thousands separator
    else:
        s = s.replace(",", ".")         # comma = decimal separator
    return float(s)

# --- Load report -------------------------------------------------------
try:
    with open(report_path, "r", encoding="utf-8") as f:
        report = json.load(f)
except FileNotFoundError:
    sys.exit(f"❌ Report file not found: {report_path}")
except json.JSONDecodeError as e:
    sys.exit(f"❌ Report is not valid JSON: {e}")

files = report.get("files", {})
print("=" * 60)
print(f"📄 Checking report: {report_path}")
print(f"📁 Documents found: {len(files)}")
print("=" * 60)

total = ok = mismatch = 0

with tempfile.TemporaryDirectory() as tmpdir:
    for doc_name, doc in files.items():
        payables = doc.get("payables", [])
        print(f"\n▶ {doc_name}  ({len(payables)} payable(s))")

        for i, payable in enumerate(payables):
            total += 1
            label = f"  [{i}]"

            tmp = os.path.join(tmpdir, f"payable_{total}.json")
            with open(tmp, "w", encoding="utf-8") as out:
                json.dump(payable, out, ensure_ascii=False)

            try:
                result = subprocess.run(
                    [sys.executable, ERP, tmp],
                    capture_output=True, text=True, timeout=30,
                )
            except subprocess.TimeoutExpired:
                print(f"{label} ⏱  TIMEOUT: erp.py took longer than 30s")
                mismatch += 1
                continue

            if result.returncode != 0:
                msg = result.stderr.strip() or result.stdout.strip()
                print(f"{label} ❌ ERP ERROR: {msg}")
                mismatch += 1
                continue

            try:
                oracle = json.loads(result.stdout)
            except json.JSONDecodeError:
                print(f"{label} ❌ BAD OUTPUT from erp.py: {result.stdout.strip()!r}")
                mismatch += 1
                continue

            expected = payable.get("booked_gross")
            actual = oracle.get("will_book_gross")

            try:
                expected_f = parse_num(expected)
                actual_f = parse_num(actual)
            except (TypeError, ValueError):
                print(f"{label} ❌ UNPARSEABLE: expected={expected!r}, actual={actual!r}")
                mismatch += 1
                continue

            if abs(expected_f - actual_f) < 0.01:
                print(f"{label} ✅ OK        expected={expected}  actual={actual}")
                ok += 1
            else:
                diff = actual_f - expected_f
                print(f"{label} ⚠️  MISMATCH  expected={expected}  actual={actual}  (diff {diff:+.2f})")
                mismatch += 1

# --- Summary -----------------------------------------------------------
print("\n" + "=" * 60)
print("SUMMARY")
print(f"  Total checked : {total}")
print(f"  ✅ Matching    : {ok}")
print(f"  ❌ Problems    : {mismatch}")
if total:
    print(f"  Match rate    : {ok / total:.1%}")
print("=" * 60)
print("🎉 All payables match the ERP." if mismatch == 0 else "🚨 Some payables need attention.")

sys.exit(0 if mismatch == 0 else 1)
PY