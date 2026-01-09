from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
import re

import pdfplumber


@dataclass(frozen=True)
class OutstandingOrderRow:
    supplier_code: str
    supplier_name: str
    location_code: str
    order_number: str
    order_date: str
    order_value: Decimal


HEADER_MARKER = "Supplier Name Location Code Order Number Date Order Value"


def extract_pdf_text(pdf_path: Path) -> str:
    """Extract all text from a PDF (page by page) into a single string."""
    parts: list[str] = []
    with pdfplumber.open(str(pdf_path)) as pdf:
        for page in pdf.pages:
            parts.append(page.extract_text() or "")
    return "\n".join(parts)


def parse_centre_label(text: str) -> str:
    """
    Example centre line:
      'Centre: 1970A/1 (CONN) Henbrook House Care Home - Food'
    """
    m = re.search(r"^Centre:\s*(.+)$", text, flags=re.MULTILINE)
    return (m.group(1).strip() if m else "")


def is_money_token(token: str) -> bool:
    # Accept "3176.99" and also "3,176.99"
    return bool(re.fullmatch(r"\d{1,3}(?:,\d{3})*(?:\.\d{2})|\d+(?:\.\d{2})", token))


def parse_order_row(line: str) -> OutstandingOrderRow | None:
    """
    Expected line shape (tokens):
      <supplier_code> <supplier_name...> <location_code> <order_number> <date> <value>

    We parse from the RIGHT because supplier_name can contain spaces.
    """
    tokens = line.split()
    if len(tokens) < 6:
        return None
    if not re.fullmatch(r"\d+/\d+", tokens[0]):
        return None
    if not is_money_token(tokens[-1]):
        return None

    supplier_code = tokens[0]
    order_value = Decimal(tokens[-1].replace(",", ""))
    order_date = tokens[-2]
    order_number = tokens[-3]
    location_code = tokens[-4]
    supplier_name = " ".join(tokens[1:-4])  # everything in the middle

    return OutstandingOrderRow(
        supplier_code=supplier_code,
        supplier_name=supplier_name,
        location_code=location_code,
        order_number=order_number,
        order_date=order_date,
        order_value=order_value,
    )


def normalize_supplier_name(supplier_name: str, centre_label: str) -> str:
    """
    Split Bidfood by centre:
      - if centre contains ' - Food' => Bidfood (food)
      - if centre contains ' - Bistro' (or 'Bistro') => Bidfood (bistro)
    """
    if supplier_name.strip().lower() == "bidfood":
        centre_lower = centre_label.lower()
        if "bistro" in centre_lower:
            return "Bidfood (bistro)"
        if " - food" in centre_lower or centre_lower.endswith(" food"):
            return "Bidfood (food)"
        # fallback if centre is unexpected
        return "Bidfood (unknown)"

    return supplier_name.strip()


def summarize_outstanding_orders(pdf_path: Path) -> tuple[str, dict[str, Decimal], Decimal | None]:
    text = extract_pdf_text(pdf_path)
    centre_label = parse_centre_label(text)

    # Grab only the lines after the header marker, up to the totals/footer.
    lines = [ln.strip() for ln in text.splitlines() if ln.strip()]

    try:
        start_idx = lines.index(HEADER_MARKER) + 1
    except ValueError:
        raise RuntimeError("Could not find the table header marker in the PDF text.")

    data_lines: list[str] = []
    for ln in lines[start_idx:]:
        # Stop once we hit the totals/footer section
        if ln.startswith("Centre ") or ln.startswith("Unit ") or ln.startswith("Report Total"):
            break
        data_lines.append(ln)

    totals_by_supplier: dict[str, Decimal] = defaultdict(lambda: Decimal("0.00"))
    for ln in data_lines:
        row = parse_order_row(ln)
        if not row:
            continue
        supplier = normalize_supplier_name(row.supplier_name, centre_label)
        totals_by_supplier[supplier] += row.order_value

    report_total = None
    m = re.search(r"^Report Total:\s*([\d,]+\.\d{2})$", text, flags=re.MULTILINE)
    if m:
        report_total = Decimal(m.group(1).replace(",", ""))

    # Convert defaultdict -> normal dict
    return centre_label, dict(totals_by_supplier), report_total


def format_gbp(d: Decimal) -> str:
    return f"£{d:,.2f}"


if __name__ == "__main__":
    import argparse

    default_pdf = Path(__file__).with_name("outstanding_orders.pdf")

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "pdf",
        nargs="?",
        default=str(default_pdf),
        help=f"Path to PDF (default: {default_pdf.name} in this folder)",
    )
    args = parser.parse_args()

    pdf = Path(args.pdf)
    if not pdf.exists():
        raise FileNotFoundError(f"PDF not found: {pdf.resolve()}")

    centre, totals, report_total = summarize_outstanding_orders(pdf)

    print(f"Centre: {centre}\n")
    grand_total = sum(totals.values(), Decimal("0.00"))

    for supplier, value in sorted(totals.items(), key=lambda kv: kv[1], reverse=True):
        print(f"{supplier:25s} {format_gbp(value)}")

    print("-" * 40)
    print(f"{'TOTAL':25s} {format_gbp(grand_total)}")

    if report_total is not None and report_total != grand_total:
        print(f"\n⚠ Report Total is {format_gbp(report_total)} but we summed {format_gbp(grand_total)}")
