"""Allmanhall purchase-order bundles, separate from the legacy summary report."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from decimal import Decimal
import io
import re

import pdfplumber

from invoices_core import normalize_supplier_name


class OrderParseError(ValueError):
    """An upload cannot be safely used to calculate an outstanding total."""


@dataclass(frozen=True)
class PurchaseOrder:
    order_number: str
    supplier: str
    supplier_raw: str
    centre: str
    order_date: str
    delivery_required: str
    net_value: Decimal
    vat: Decimal
    total: Decimal


def extract_pages(pdf_bytes: bytes) -> list[str]:
    if not pdf_bytes.lstrip().startswith(b"%PDF-"):
        raise OrderParseError("Please upload a valid PDF file.")
    try:
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            pages = []
            for page in pdf.pages:
                text = page.extract_text() or ""
                if re.search(r"(?m)^Order Number:", text):
                    # Supplier and delivery location occupy separate columns.
                    # Read them geometrically so names/locations may wrap.
                    words = page.extract_words()
                    left = next((w for w in words if w["text"] == "Supplier:"), None)
                    right = next((w for w in words if w["text"] == "Main"), None)
                    end = next((w for w in words if w["text"] == "Address:" and left and w["top"] > left["bottom"]), None)
                    if not left or not right or not end:
                        raise OrderParseError("Could not read supplier/location columns.")
                    for label, x0, x1 in (("Supplier Name", left["x0"], right["x0"] - 1),
                                          ("Centre", right["x0"], page.width)):
                        value = page.crop((x0, max(left["bottom"], right["bottom"]) + 0.5, x1, end["top"] - 0.5)).extract_text() or ""
                        text += "\n" + label + ": " + " ".join(value.split())
                pages.append(text)
            return pages
    except OrderParseError:
        raise
    except Exception as exc:
        raise OrderParseError("Could not read the PDF. Upload an unencrypted text-based purchase-order PDF.") from exc


def parse_orders(pages: list[str]) -> list[PurchaseOrder]:
    starts = [i for i, text in enumerate(pages) if re.search(r"(?m)^Order Number:", text)]
    if not starts or starts[0] != 0:
        raise OrderParseError("No complete purchase-order bundle found. Scanned PDFs and summary reports are not supported here.")
    orders = []
    seen = set()
    for start, stop in zip(starts, starts[1:] + [len(pages)]):
        text = "\n".join(pages[start:stop])
        if not all("Purchase Order" in p for p in pages[start:stop]):
            raise OrderParseError(f"Unrecognised page in order beginning on page {start + 1}.")

        def field(label: str, pattern: str = r"[^\n]+") -> str:
            match = re.search(r"(?m)^" + re.escape(label) + r":[ \t]*(" + pattern + r")", text)
            if not match:
                raise OrderParseError(f"Missing {label} in order beginning on page {start + 1}.")
            return match.group(1).strip()

        number = field("Order Number", r"\S+")
        if number in seen:
            raise OrderParseError(f"Duplicate order number {number}. Upload each order only once.")
        seen.add(number)
        supplier = field("Supplier Name")
        centre = field("Centre")
        dates = re.search(r"(?m)^Order Date:\s*(\S+)\s+Delivery Required:\s*(\S+)", text)
        if not dates:
            raise OrderParseError(f"Missing order/delivery dates for order {number}.")

        amounts = []
        for label in ("Net Value", "VAT", "Total Value"):
            matches = re.findall(r"(?m)^" + label + r":[ \t]*((?:\d{1,3}(?:,\d{3})+|\d+)\.\d{2})[ \t]*$", text)
            if len(matches) != 1:
                raise OrderParseError(f"Missing or ambiguous {label} for order {number}.")
            amounts.append(Decimal(matches[0].replace(",", "")))
        net, vat, total = amounts
        if net + vat != total:
            raise OrderParseError(f"Net value plus VAT does not equal total for order {number}.")
        orders.append(PurchaseOrder(number, normalize_supplier_name(supplier, centre, text),
                                    supplier, centre, dates[1], dates[2], net, vat, total))
    return orders


def analyze_awaiting_orders_from_bytes(pdf_bytes: bytes) -> dict:
    orders = parse_orders(extract_pages(pdf_bytes))
    suppliers = {}
    for order in orders:
        row = suppliers.setdefault(order.supplier, dict(supplier=order.supplier, order_count=0,
                                  net_value=Decimal("0.00"), vat=Decimal("0.00"), total=Decimal("0.00")))
        row["order_count"] += 1
        for key in ("net_value", "vat", "total"):
            row[key] += getattr(order, key)

    def serialize(row):
        return {key: f"{value:.2f}" if isinstance(value, Decimal) else value for key, value in row.items()}

    return dict(currency="GBP", total_basis="including_vat", order_count=len(orders),
                grand_net=f"{sum((o.net_value for o in orders), Decimal('0.00')):.2f}",
                grand_vat=f"{sum((o.vat for o in orders), Decimal('0.00')):.2f}",
                grand_total=f"{sum((o.total for o in orders), Decimal('0.00')):.2f}",
                supplier_totals=[serialize(suppliers[s]) for s in sorted(suppliers)],
                orders=[serialize(asdict(o)) for o in orders])
