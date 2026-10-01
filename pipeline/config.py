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

# --- Step 2: Sarvam DocAI (the read stage) ---------------------------------

SARVAM_BASE_URL = "https://api.sarvam.ai"

# read.py looks these names up in the environment. The key is never
# hardcoded, never sent anywhere but the api-subscription-key header, and
# never written to any file or log line. There is no saved Sarvam Config
# any more — the schema travels inline with every job, so the API key is
# the only required variable.
SARVAM_API_KEY_ENV = "SARVAM_API_KEY"

READ_TIMEOUT = 60.0  # per HTTP request, seconds
READ_MAX_RETRIES = 3  # extra attempts on 429/5xx/network only, never on 4xx
READ_RETRY_BACKOFF = 1.0  # seconds; doubled on each retry
READ_POLL_INTERVAL = 2.0  # seconds between job-status polls
READ_POLL_TIMEOUT = 300.0  # give up on one job after this many seconds

# Bump SCHEMA_VERSION whenever READ_SCHEMA changes. Cached read.json files
# tagged with an older version are refetched, so a stale capture can never
# mix with a new field list.
SCHEMA_VERSION = 2

# What Sarvam must return per PDF, sent inline as the `schema` form field
# (json.dumps of this dict) — no saved Config. Dashboard rules: every
# field carries "type" and "description"; maximum nesting depth is 4, so
# the shape is flat: only documents[] and unreadable_pages[] at the top,
# and the old printed_totals nesting is gone (its fields are flat on the
# document, "other" became other_totals). Every leaf is a string so
# numbers and dates come back as the exact printed text ("1.049.579,86",
# "02.02.2026"); only page_numbers, page_number and unreadable_pages are
# integers. read.py checks that the response carries the two top-level
# keys and fails loudly otherwise — it never renames, fills, or repairs
# fields, because a wrong schema must show up as a schema error, not as
# silent data munging. Step 3 parses; nothing before that does.
_PRINTED = "exact text as printed; null if not printed; never infer"

READ_SCHEMA = {
  "type": "object",
  "description": "Extraction result for one PDF.",
  "properties": {
    "documents": {
      "type": "array",
      "description": "Separate printed documents found in the PDF. Documents sharing an invoice number remain separate entries.",
      "items": {
        "type": "object",
        "description": "All information printed on one separate invoice, credit note, delivery note, or other document.",
        "properties": {
          "page_numbers": {
            "type": "array",
            "description": "1-based PDF pages containing this document.",
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
            "description": "Invoice number exactly as printed."
          },
          "invoice_date": {
            "type": "string",
            "description": "Invoice date exactly as printed."
          },
          "due_date": {
            "type": "string",
            "description": "Due date exactly as printed."
          },
          "payment_terms_text": {
            "type": "string",
            "description": "Payment terms exactly as printed."
          },
          "po_number": {
            "type": "string",
            "description": "Purchase order number exactly as printed."
          },
          "currency_text": {
            "type": "string",
            "description": "Currency symbol or code exactly as printed."
          },
          "price_basis_text": {
            "type": "string",
            "description": "Printed price basis such as net, gross, inclusive of VAT, or price unit."
          },

          "supplier_name": {
            "type": "string",
            "description": "Printed supplier or seller name for this document only."
          },
          "supplier_address": {
            "type": "string",
            "description": "Printed supplier address block."
          },
          "supplier_vat_id": {
            "type": "string",
            "description": "Printed supplier VAT ID."
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
            "description": "Printed buyer or bill-to name for this document only."
          },
          "buyer_address": {
            "type": "string",
            "description": "Printed buyer address block."
          },
          "buyer_vat_id": {
            "type": "string",
            "description": "Printed buyer VAT ID."
          },

          "line_descriptions": {
            "type": "array",
            "description": "Goods or service descriptions in exact printed order. One entry per printed line.",
            "items": {
              "type": "string",
              "description": "One printed goods or service line description."
            }
          },
          "line_quantities": {
            "type": "array",
            "description": "Line quantities in exact printed order, corresponding positionally to line_descriptions.",
            "items": {
              "type": "string",
              "description": "One printed line quantity."
            }
          },
          "line_uoms": {
            "type": "array",
            "description": "Line units of measure in exact printed order, corresponding positionally to line_descriptions.",
            "items": {
              "type": "string",
              "description": "One printed line unit of measure."
            }
          },
          "line_unit_prices": {
            "type": "array",
            "description": "Line unit prices in exact printed order.",
            "items": {
              "type": "string",
              "description": "One printed line unit price."
            }
          },
          "line_price_units": {
            "type": "array",
            "description": "Printed price units for lines in exact printed order.",
            "items": {
              "type": "string",
              "description": "One printed price unit."
            }
          },
          "line_discounts": {
            "type": "array",
            "description": "Line discount amounts in exact printed order.",
            "items": {
              "type": "string",
              "description": "One printed line discount."
            }
          },
          "line_discount_percents": {
            "type": "array",
            "description": "Line discount percentages in exact printed order.",
            "items": {
              "type": "string",
              "description": "One printed line discount percentage."
            }
          },
          "line_totals": {
            "type": "array",
            "description": "Line totals in exact printed order.",
            "items": {
              "type": "string",
              "description": "One printed line total."
            }
          },
          "line_tax_rates": {
            "type": "array",
            "description": "Line tax rates in exact printed order.",
            "items": {
              "type": "string",
              "description": "One printed line tax rate."
            }
          },
          "line_tax_amounts": {
            "type": "array",
            "description": "Line tax amounts in exact printed order.",
            "items": {
              "type": "string",
              "description": "One printed line tax amount."
            }
          },
          "line_page_numbers": {
            "type": "array",
            "description": "1-based PDF page number for each line, positionally corresponding to the line arrays.",
            "items": {
              "type": "integer",
              "description": "1-based PDF page number for one line."
            }
          },

          "header_tax_names": {
            "type": "array",
            "description": "Document-level tax names in printed order.",
            "items": {
              "type": "string",
              "description": "Printed tax name."
            }
          },
          "header_tax_rates": {
            "type": "array",
            "description": "Document-level tax rates corresponding positionally to header_tax_names.",
            "items": {
              "type": "string",
              "description": "Printed tax rate."
            }
          },
          "header_tax_amounts": {
            "type": "array",
            "description": "Document-level tax amounts corresponding positionally to header_tax_names.",
            "items": {
              "type": "string",
              "description": "Printed tax amount."
            }
          },
          "header_tax_base_amounts": {
            "type": "array",
            "description": "Document-level tax bases corresponding positionally to header_tax_names.",
            "items": {
              "type": "string",
              "description": "Printed tax base amount."
            }
          },

          "charge_labels": {
            "type": "array",
            "description": "Separately printed charge labels in printed order.",
            "items": {
              "type": "string",
              "description": "One printed charge label."
            }
          },
          "charge_amounts": {
            "type": "array",
            "description": "Charge amounts corresponding positionally to charge_labels.",
            "items": {
              "type": "string",
              "description": "One printed charge amount."
            }
          },

          "printed_subtotal": {
            "type": "string",
            "description": "Amount printed next to the subtotal label."
          },
          "printed_total_discount": {
            "type": "string",
            "description": "Amount printed next to the total discount label."
          },
          "printed_total_tax": {
            "type": "string",
            "description": "Amount printed next to the total tax label."
          },
          "printed_gross_total": {
            "type": "string",
            "description": "Printed gross total."
          },
          "printed_amount_due": {
            "type": "string",
            "description": "Printed amount due."
          },

          "other_total_labels": {
            "type": "array",
            "description": "Every other labelled total on the document in printed order. Nothing is merged or dropped.",
            "items": {
              "type": "string",
              "description": "One other printed total label."
            }
          },
          "other_total_amounts": {
            "type": "array",
            "description": "Amounts corresponding positionally to other_total_labels.",
            "items": {
              "type": "string",
              "description": "One other printed total amount."
            }
          },

          "notes": {
            "type": "array",
            "description": "Other printed remarks, preserving each remark separately.",
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
      "description": "1-based PDF page numbers containing no usable readable content.",
      "items": {
        "type": "integer",
        "description": "1-based PDF page number."
      }
    }
  }
}
