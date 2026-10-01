import base64
import io
import json
import os
import sys
from pathlib import Path

from interfaze import Interfaze
from pypdf import PdfReader, PdfWriter

BATCH_PAGES = 10
PAGE_FIELDS = ("page_numbers", "line_page_numbers")

SCHEMA = r"""
{
  "type": "object",
  "description": "Extraction result for one PDF. Copy only what is printed on the page. Never calculate, convert, round, infer or fill in a value that is not printed; use an empty string instead. Keep numbers exactly as printed (separators, minus signs, brackets); do not reformat them.",
  "properties": {
    "documents": {
      "type": "array",
      "description": "Separate printed documents found in the PDF. Keep separate documents separate, even when invoice numbers match.",
      "items": {
        "type": "object",
        "description": "Raw printed information from one document.",
        "properties": {
          "page_numbers": {
            "type": "array",
            "description": "1-based PDF pages containing this document. A document that continues over several pages is ONE entry. Pages with only packing or weight tables belong to the document before them.",
            "items": {
              "type": "integer",
              "description": "1-based PDF page number."
            }
          },
          "title_text": {
            "type": "string",
            "description": "Document heading exactly as printed."
          },
          "invoice_number": {
            "type": "string",
            "description": "Invoice or credit memo number exactly as printed."
          },
          "invoice_date": {
            "type": "string",
            "description": "Invoice date exactly as printed."
          },
          "due_date": {
            "type": "string",
            "description": "Due date exactly as printed."
          },
          "invoice_type_text": {
            "type": "string",
            "description": "Printed document type wording, such as invoice, credit note, or credit memo. Copy the wording as printed; do not decide the type yourself."
          },
          "payment_terms_text": {
            "type": "string",
            "description": "Payment terms exactly as printed, such as 'Net 30' or '30 days' (used later to match the payment-terms master)."
          },
          "po_number": {
            "type": "string",
            "description": "Purchase order number exactly as printed, including every slash-separated part (used later to match the PO master). Empty if none is printed."
          },
          "currency_text": {
            "type": "string",
            "description": "Printed currency symbol or code."
          },
          "price_basis_text": {
            "type": "string",
            "description": "Any printed statement about whether prices are net/excl. tax or gross/incl. tax (for example 'prices include VAT', 'excl. VAT', 'net price'), or a price unit. Copy the sentence as printed; empty if none."
          },
          "supplier_name": {
            "type": "string",
            "description": "Printed supplier or seller name exactly as printed (used later to match the supplier master)."
          },
          "supplier_address": {
            "type": "string",
            "description": "Printed supplier address block."
          },
          "supplier_vat_id": {
            "type": "string",
            "description": "Printed supplier VAT ID exactly as printed, including country prefix (used later to match the supplier master)."
          },
          "supplier_tax_id": {
            "type": "string",
            "description": "Printed supplier tax ID."
          },
          "supplier_iban": {
            "type": "string",
            "description": "Printed supplier IBAN."
          },
          "buyer_name": {
            "type": "string",
            "description": "Printed buyer or bill-to name exactly as printed (used later to match the buyer organisation)."
          },
          "buyer_address": {
            "type": "string",
            "description": "Printed buyer address block."
          },
          "buyer_vat_id": {
            "type": "string",
            "description": "Printed buyer VAT ID."
          },
          "line_item_numbers": {
            "type": "array",
            "description": "Printed item or position number of each line, exactly as printed. Same length as line_descriptions; use an empty string where the document prints nothing for that line.",
            "items": {
              "type": "string",
              "description": "One printed item number."
            }
          },
          "line_descriptions": {
            "type": "array",
            "description": "Printed line descriptions in order. One entry per printed line row.",
            "items": {
              "type": "string",
              "description": "One printed line description."
            }
          },
          "line_quantities": {
            "type": "array",
            "description": "Quantity from the quantity column of each row, exactly as printed, without the unit. Same length as line_descriptions; use an empty string where the document prints nothing for that line.",
            "items": {
              "type": "string",
              "description": "One printed quantity."
            }
          },
          "line_uoms": {
            "type": "array",
            "description": "Units of measure corresponding positionally to line_descriptions. Same length as line_descriptions; use an empty string where the document prints nothing for that line.",
            "items": {
              "type": "string",
              "description": "One printed unit of measure."
            }
          },
          "line_unit_prices": {
            "type": "array",
            "description": "Printed unit prices in line order, exactly as printed, even if they look tax-inclusive. Do not remove tax or recalculate. Same length as line_descriptions; empty string where no unit price is printed.",
            "items": {
              "type": "string",
              "description": "One printed unit price."
            }
          },
          "line_price_units": {
            "type": "array",
            "description": "Printed price units in line order, such as per hour or per 100 units. Same length as line_descriptions; use an empty string where the document prints nothing for that line.",
            "items": {
              "type": "string",
              "description": "One printed price unit."
            }
          },
          "line_discounts": {
            "type": "array",
            "description": "Printed line discount amounts in line order. Same length as line_descriptions; use an empty string where the document prints nothing for that line.",
            "items": {
              "type": "string",
              "description": "One printed discount amount."
            }
          },
          "line_discount_percents": {
            "type": "array",
            "description": "Printed line discount percentages in line order. Same length as line_descriptions; use an empty string where the document prints nothing for that line.",
            "items": {
              "type": "string",
              "description": "One printed discount percentage."
            }
          },
          "line_totals": {
            "type": "array",
            "description": "Printed line extension amounts in line order, copying digits and separators exactly as printed. Same length as line_descriptions; use an empty string where the document prints nothing for that line.",
            "items": {
              "type": "string",
              "description": "One printed line total."
            }
          },
          "line_tax_names": {
            "type": "array",
            "description": "Tax name printed ON that line row (for example in a per-line tax column), aligned to lines. Only fill this when the tax is printed at line level. A tax stated once in the totals block is a header tax; do not copy it onto the lines. Same length as line_descriptions; empty string where none.",
            "items": {
              "type": "string",
              "description": "Printed tax name for a line."
            }
          },
          "line_tax_rates": {
            "type": "array",
            "description": "Tax rate printed on that line row, as printed (the % sign may be left out). Line-level only; never copy a header rate onto lines. Same length as line_descriptions; empty string where none.",
            "items": {
              "type": "string",
              "description": "Printed line tax rate."
            }
          },
          "line_tax_amounts": {
            "type": "array",
            "description": "Tax amount printed on that line row. Keep the sign exactly as printed (a minus sign or brackets means a deduction such as withholding). Leave empty if no amount is printed; never calculate it. Same length as line_descriptions.",
            "items": {
              "type": "string",
              "description": "Printed line tax amount."
            }
          },
          "line_page_numbers": {
            "type": "array",
            "description": "1-based PDF page number for each line, aligned to line_descriptions.",
            "items": {
              "type": "integer",
              "description": "1-based PDF page number for one line."
            }
          },
          "header_tax_names": {
            "type": "array",
            "description": "Tax names stated once at document level (totals or tax-summary block), one entry per distinct tax or rate, in printed order. If the document shows three rates in its tax summary, list three entries. Do not list taxes that are printed per line here. Include withholding or other deductions printed as a tax.",
            "items": {
              "type": "string",
              "description": "One printed header-level tax name."
            }
          },
          "header_tax_rates": {
            "type": "array",
            "description": "Header tax rates aligned to header_tax_names, as printed (the % sign may be left out).",
            "items": {
              "type": "string",
              "description": "One printed header tax rate."
            }
          },
          "header_tax_amounts": {
            "type": "array",
            "description": "Header tax amounts aligned to header_tax_names, as printed. Keep the sign exactly as printed (minus sign or brackets for withholding or deductions). Leave empty if no amount is printed; never calculate it.",
            "items": {
              "type": "string",
              "description": "One printed header tax amount."
            }
          },
          "header_tax_base_amounts": {
            "type": "array",
            "description": "Printed tax base (taxable amount) aligned to header_tax_names. Empty if not printed.",
            "items": {
              "type": "string",
              "description": "One printed header tax base."
            }
          },
          "printed_discount_amount": {
            "type": "string",
            "description": "Printed document-level discount amount."
          },
          "printed_freight_charges": {
            "type": "string",
            "description": "Printed document-level freight charges."
          },
          "printed_insurance_charges": {
            "type": "string",
            "description": "Printed document-level insurance charges."
          },
          "printed_extra_charges": {
            "type": "string",
            "description": "Other printed document-level charges."
          },
          "printed_excise_duties": {
            "type": "string",
            "description": "Printed document-level excise duties."
          },
          "charge_labels": {
            "type": "array",
            "description": "Other separately printed charge labels in order.",
            "items": {
              "type": "string",
              "description": "One printed charge label."
            }
          },
          "charge_amounts": {
            "type": "array",
            "description": "Charge amounts aligned to charge_labels.",
            "items": {
              "type": "string",
              "description": "One printed charge amount."
            }
          },
          "printed_total_quantity": {
            "type": "string",
            "description": "Total quantity printed in the totals row, without unit or footnote marks. Empty string if none is printed."
          },
          "printed_subtotal": {
            "type": "string",
            "description": "Printed subtotal."
          },
          "printed_total_discount": {
            "type": "string",
            "description": "Printed total discount."
          },
          "printed_total_tax": {
            "type": "string",
            "description": "Printed total tax amount, as printed. Empty if the document prints none."
          },
          "printed_gross_total": {
            "type": "string",
            "description": "The final total the document says is owed, including tax, as printed. For a credit memo copy it as printed, including any minus sign."
          },
          "printed_amount_due": {
            "type": "string",
            "description": "Printed amount due."
          },
          "other_total_labels": {
            "type": "array",
            "description": "Other labelled totals in printed order.",
            "items": {
              "type": "string",
              "description": "One printed total label."
            }
          },
          "other_total_amounts": {
            "type": "array",
            "description": "Amounts aligned to other_total_labels.",
            "items": {
              "type": "string",
              "description": "One printed total amount."
            }
          },
          "notes": {
            "type": "array",
            "description": "Other printed remarks, preserving each separately.",
            "items": {
              "type": "string",
              "description": "One printed remark."
            }
          }
        }
      }
    },
    "unreadable_pages": {
      "type": "array",
      "description": "1-based PDF pages containing no usable readable content.",
      "items": {
        "type": "integer",
        "description": "1-based PDF page number."
      }
    }
  }
}
"""


def api_key():
    key = os.environ.get("INTERFAZE_API_KEY", "")
    if not key and Path(".env").exists():
        for line in Path(".env").read_text().splitlines():
            if line.startswith("INTERFAZE_API_KEY="):
                key = line.split("=", 1)[1].strip().strip('"')
    return key


def page_batches(reader):
    total = len(reader.pages)
    for start in range(0, total, BATCH_PAGES):
        writer = PdfWriter()
        for page in reader.pages[start : start + BATCH_PAGES]:
            writer.add_page(page)
        buf = io.BytesIO()
        writer.write(buf)
        buf.seek(0)
        yield start, buf


def parsed_result(message):
    parsed = getattr(message, "parsed", None)
    if parsed is None:
        return json.loads(message.content)
    if isinstance(parsed, dict):
        return parsed
    if hasattr(parsed, "model_dump"):
        return parsed.model_dump()
    return json.loads(json.dumps(parsed))


def run_job(client, name, data):
    file_b64 = base64.b64encode(data.read()).decode()
    response = client.chat.completions.parse(
        messages=[
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Extract the invoice information from the document based on the schema. Return every separate printed document found.",
                    },
                    {
                        "type": "file",
                        "file": {
                            "filename": name,
                            "file_data": f"data:application/pdf;base64,{file_b64}",
                        },
                    },
                ],
            }
        ],
        response_format={
            "type": "json_schema",
            "json_schema": {
                "name": "invoice_extraction",
                "schema": json.loads(SCHEMA),
            },
        },
    )
    return parsed_result(response.choices[0].message)


def offset_pages(item, start):
    for field in PAGE_FIELDS:
        if isinstance(item.get(field), list):
            item[field] = [p + start for p in item[field]]
    return item


def extract_pdf(client, path):
    reader = PdfReader(str(path))
    merged = {"documents": [], "unreadable_pages": []}
    for batch, (start, data) in enumerate(page_batches(reader), start=1):
        name = f"{path.stem}_{batch}.pdf"
        result = run_job(client, name, data)
        merged["documents"].extend(offset_pages(doc, start) for doc in result["documents"])
        merged["unreadable_pages"].extend(p + start for p in result["unreadable_pages"])
    return merged


def main():
    client = Interfaze(api_key=api_key())
    out_dir = Path("work")
    out_dir.mkdir(exist_ok=True)
    if len(sys.argv) > 1:
        paths = [Path(sys.argv[1])]
    else:
        paths = sorted(Path("documents").glob("*.pdf"))
    for path in paths:
        merged = extract_pdf(client, path)
        out_path = out_dir / f"{path.stem}.json"
        out_path.write_text(
            json.dumps(merged, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        print(f"{path.name}: {len(merged['documents'])} documents -> {out_path}")


if __name__ == "__main__":
    main()
