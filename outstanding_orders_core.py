from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
import io
import re
from typing import Dict, Tuple, Optional

import pdfplumber


@dataclass(frozen=True)
class OutstandingOrderRow:
    """
    Represents one row from the Outstanding Orders table.

    We keep this internal (not returned directly), because the API only needs
    aggregated totals by supplier.
    """

    supplier_code: str
    supplier_name: str
    location_code: str
    order_number: str
    order_date: str
    order_value: Decimal


HEADER_MARKER = "Supplier Name Location Code Order Number Date Order Value"


def extract_pdf_text_from_bytes(pdf_bytes: bytes) -> str:
    """
    Extract text from a PDF provided as bytes.

    This is the key change that makes the logic web-app friendly:
    - Next.js uploads -> API receives bytes -> we parse bytes (no file path needed).
    """
    parts: list[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def parse_centre_label(text: str) -> str:
    """
    Attempt to capture the report 'Centre:' line.

    Example:
      Centre: 1970A/1 (CONN) Henbrook House Care Home - Food
    """
    m = re.search(r"^Centre:\s*(.+)$", text, flags=re.MULTILINE)
    return m.group(1).strip() if m else ""


def is_money_token(token: str) -> bool:
    """
    Accept common money tokens like:
      3176.99
      3,176.99
    """
    return bool(re.fullmatch(r"\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+(?:\.\d{2})", token))


def parse_order_row(line: str) -> OutstandingOrderRow | None:
    """
    Parse a single line from the PDF table.

    We parse from the RIGHT because Supplier Name can contain spaces.
    Expected line shape:
      <supplier_code> <supplier_name...> <location_code> <order_number> <date> <value>
    """
    tokens = line.split()
    if len(tokens) < 6:
        return None

    # Supplier code looks like "14/1"
    if not re.fullmatch(r"\d+/\d+", tokens[0]):
        return None

    # Last token must look like money (e.g., 451.58)
    if not is_money_token(tokens[-1]):
        return None

    supplier_code = tokens[0]
    order_value = Decimal(tokens[-1].replace(",", ""))
    order_date = tokens[-2]
    order_number = tokens[-3]
    location_code = tokens[-4]
    supplier_name = " ".join(tokens[1:-4])

    return OutstandingOrderRow(
        supplier_code=supplier_code,
        supplier_name=supplier_name,
        location_code=location_code,
        order_number=order_number,
        order_date=order_date,
        order_value=order_value,
    )


def normalize_supplier_name(raw_supplier_name: str, centre_label: str) -> str:
    """
    Enforce your business rule:
    - if supplier == Bidfood, split by centre into Bidfood (food) or Bidfood (bistro)

    We look at the centre label from the report header.
    """
    name = raw_supplier_name.strip()

    if name.lower() == "bidfood":
        c = centre_label.lower()
        if "bistro" in c:
            return "Bidfood (bistro)"
        if "food" in c:
            return "Bidfood (food)"
        return "Bidfood (unknown)"

    return name


def parse_report_total(text: str) -> Optional[Decimal]:
    """
    If the PDF includes a 'Report Total:' line, parse it so we can check our sum.
    """
    m = re.search(r"^Report Total:\s*([\d,]+\.\d{2})$", text, flags=re.MULTILINE)
    if not m:
        return None
    return Decimal(m.group(1).replace(",", ""))


def summarize_outstanding_orders_from_bytes(
    pdf_bytes: bytes,
) -> Tuple[str, Dict[str, Decimal], Decimal, Optional[Decimal]]:
    """
    Return:
      - centre_label (string)
      - totals_by_supplier (dict supplier -> Decimal)
      - grand_total (Decimal)
      - report_total (Decimal or None)

    IMPORTANT:
    This report can contain MULTIPLE centre sections (Food + Bistro).
    We therefore track the *current* centre while scanning lines so Bidfood is split correctly.
    """
    text = extract_pdf_text_from_bytes(pdf_bytes)

    # Clean up lines (remove blanks)
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    totals_by_supplier: Dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))

    current_centre = ""
    centres_seen: list[str] = []

    for ln in lines:
        # When we hit a new centre section, update the state.
        # Example in the PDF:
        #   Centre: 1970A/1 ... - Food
        #   Centre: 1970A/2 ... - Bistro
        if ln.startswith("Centre:"):
            current_centre = ln.replace("Centre:", "", 1).strip()
            if current_centre and current_centre not in centres_seen:
                centres_seen.append(current_centre)
            continue

        # Stop only at the FINAL totals/footer of the whole report
        if ln.startswith("Unit ") or ln.startswith("Report Total"):
            break

        # Parse a potential order row. If it isn't a row, skip.
        row = parse_order_row(ln)
        if not row:
            continue

        # Use the centre that was active when this row appeared
        supplier = normalize_supplier_name(row.supplier_name, current_centre)
        totals_by_supplier[supplier] += row.order_value

    grand_total = sum(totals_by_supplier.values(), Decimal("0.00"))
    report_total = parse_report_total(text)

    # For backward compatibility with your API/UI:
    # If there is one centre -> return it,
    # if multiple -> return a readable combined label (Food | Bistro)
    if len(centres_seen) == 1:
        centre_label_out = centres_seen[0]
    elif len(centres_seen) > 1:
        centre_label_out = " | ".join(centres_seen)
    else:
        centre_label_out = ""

    return centre_label_out, dict(totals_by_supplier), grand_total, report_total
