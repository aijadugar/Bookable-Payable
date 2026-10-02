import json
from pathlib import Path

import build
import check

WORK = Path("work")


def usable(path):
    try:
        json.loads(path.read_text(encoding="utf-8"))
        return True
    except (OSError, ValueError):
        return False


def extract_missing():
    pending = [p for p in sorted(Path("documents").glob("*.pdf")) if not usable(WORK / f"{p.stem}.json")]
    if not pending:
        return
    import extract

    client = extract.Interfaze(api_key=extract.api_key())
    WORK.mkdir(exist_ok=True)
    for pdf in pending:
        try:
            merged = extract.extract_pdf(client, pdf)
        except Exception as e:
            print(f"{pdf.name}: extraction failed ({e})")
            continue
        (WORK / f"{pdf.stem}.json").write_text(json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"{pdf.name}: {len(merged['documents'])} documents extracted")


if __name__ == "__main__":
    extract_missing()
    build.main()
    check.main()
