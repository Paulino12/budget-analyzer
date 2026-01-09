# invoices_core.py
from __future__ import annotations

"""
Invoices / Credit Notes parser for Allmanhall eCat PDFs.

We receive a single PDF that may contain many documents back-to-back:
- Invoices (Document Type: ECatInv)
- Credit Notes (Document Type: ECatCrd)

Each "document" can span multiple pages.
We detect document boundaries by pages that contain "Document Type:".

For each document we extract:
- supplier name (from "Supplier: ...")
- centre (from "Centre: ...") to split Bidfood Food/Bistro
- doc kind (invoice vs credit note)
- Amount Payable

Then we aggregate:
Supplier, Total, Credits, Difference (Total - Credits)

NCB split:
The PDF supplier label is "NCB Foodservice Ltd" for both Meat and Produce.
We infer Meat vs Veg using product-code pattern:
- If the document contains any line starting with "M" + digits (e.g. M70500),
  treat as NCB Meat.
- Otherwise treat as NCB Veg.
"""

from dataclasses import dataclass
from decimal import Decimal
import io
import re
from typing import Dict, Iterable, List, Optional, Tuple

import pdfplumber

# ---------- Regexes for export fields ----------
_UNIT_RE = re.compile(r"^Unit:\s*(.+)$", re.MULTILINE)
_SUPPLIER_REF_RE = re.compile(r"^Supplier Ref:\s*(.+)$", re.MULTILINE)

# ---------- Regexes for core parsing ----------
_DOC_TYPE_RE = re.compile(r"^Document Type:\s*(.+)$", re.MULTILINE)
_SUPPLIER_RE = re.compile(r"Supplier:\s*(\S+)\s+(.+?)(?:\s+Confirmed By:|$)")
_CENTRE_RE = re.compile(r"^Centre:\s*(.+)$", re.MULTILINE)
_AMOUNT_RE = re.compile(r"Amount Payable:\s*([\d,]+\.\d{2})")


# ---------- Models ----------


@dataclass(frozen=True)
class InvoiceDocument:
    """
    Represents one invoice/credit-note document as a block of pages.
    """

    supplier_raw: str
    centre_label: str
    is_credit_note: bool
    amount_payable: Decimal  # always positive magnitude
    full_text: str


@dataclass(frozen=True)
class InvoiceExportRow:
    """
    One row per document for Excel export (no combining).
    Credits are signed negative so Excel sums/pivots work naturally.
    """

    document_type: str  # "Invoice" or "Credit Note"
    supplier: str
    unit: str
    centre: str
    supplier_ref: str
    amount_payable: Decimal  # signed: credit notes negative


# ---------- Low-level PDF text extraction ----------


def extract_pages_text_from_bytes(pdf_bytes: bytes) -> List[str]:
    """
    Extract text per-page (preserves page boundaries for document segmentation).
    """
    pages: List[str] = []
    with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
        for page in pdf.pages:
            pages.append(page.extract_text() or "")
    return pages


# ---------- Field parsers ----------


def parse_supplier(text: str) -> Optional[str]:
    """Return supplier name as shown on the document (e.g. 'Bidfood')."""
    for ln in text.splitlines():
        m = _SUPPLIER_RE.search(ln)
        if m:
            return m.group(2).strip()
    return None


def parse_centre(text: str) -> str:
    """Return the full Centre line (or empty string)."""
    m = _CENTRE_RE.search(text)
    return m.group(1).strip() if m else ""


def parse_is_credit_note(text: str) -> bool:
    """
    Identify Credit Notes reliably.
    Example: 'Document Type: ECatCrd Credit Note Status: ...'
    """
    m = _DOC_TYPE_RE.search(text)
    if not m:
        return False
    doc_type_line = m.group(1).lower()
    return ("ecatcrd" in doc_type_line) or ("credit note" in doc_type_line)


def parse_amount_payable(text: str) -> Optional[Decimal]:
    """
    Amount Payable can appear on the last page of a multi-page document,
    so we search the whole document text and take the *last* match.
    """
    matches = list(_AMOUNT_RE.finditer(text))
    if not matches:
        return None
    raw = matches[-1].group(1).replace(",", "")
    return Decimal(raw)


def parse_unit(text: str) -> str:
    m = _UNIT_RE.search(text)
    return m.group(1).strip() if m else ""


def parse_supplier_ref(text: str) -> str:
    m = _SUPPLIER_REF_RE.search(text)
    return m.group(1).strip() if m else ""


def doc_type_label(is_credit_note: bool) -> str:
    return "Credit Note" if is_credit_note else "Invoice"


# ---------- Supplier normalization rules ----------


def looks_like_ncb_meat(doc_text: str) -> bool:
    """
    Heuristic for splitting NCB Foodservice into Meat vs Veg:
    - Meat invoices contain product codes starting with 'M' followed by digits (e.g. M70500).
    """
    return bool(re.search(r"(?m)^\s*M\d{2,}\b", doc_text))


def normalize_supplier_name(supplier_raw: str, centre_label: str, doc_text: str) -> str:
    """
    Apply your business labels:
    - Bidfood split by centre (Food/Bistro)
    - NCB Foodservice split by product-code heuristic (Meat/Veg)
    """
    name = supplier_raw.strip()

    if name.lower() == "bidfood":
        c = centre_label.lower()
        if "bistro" in c:
            return "Bidfood (bistro)"
        if "food" in c:
            return "Bidfood (food)"
        return "Bidfood"  # fallback

    # NCB split (since the supplier label is the same for meat/produce)
    if name.lower() == "ncb foodservice ltd":
        return "NCB Meat" if looks_like_ncb_meat(doc_text) else "NCB Veg"

    return name


# ---------- Document segmentation ----------


def _is_doc_start_page(page_text: str) -> bool:
    """A page that contains 'Document Type:' indicates a new document begins here."""
    return "Document Type:" in page_text


def iter_documents_from_pages(pages: List[str]) -> Iterable[InvoiceDocument]:
    """
    Build InvoiceDocument blocks by slicing pages between document-start pages.
    """
    start_idxs = [i for i, t in enumerate(pages) if _is_doc_start_page(t)]
    if not start_idxs:
        return  # nothing to yield

    # Add a sentinel "end" to slice the last document
    start_idxs.append(len(pages))

    for a, b in zip(start_idxs, start_idxs[1:]):
        doc_pages = pages[a:b]
        doc_text = "\n".join(doc_pages)

        supplier = parse_supplier(doc_pages[0]) or parse_supplier(doc_text) or "Unknown"
        centre = parse_centre(doc_pages[0]) or parse_centre(doc_text)
        is_credit = parse_is_credit_note(doc_pages[0]) or parse_is_credit_note(doc_text)

        amount = parse_amount_payable(doc_text)
        if amount is None:
            # If we can't find an amount payable, skip the doc.
            continue

        # Credit note amounts are sometimes positive in PDFs; keep magnitude positive here.
        if is_credit and amount < 0:
            amount = abs(amount)

        yield InvoiceDocument(
            supplier_raw=supplier,
            centre_label=centre,
            is_credit_note=is_credit,
            amount_payable=amount,
            full_text=doc_text,
        )


# ---------- Public API: Summary (combined) ----------


def summarize_invoices_from_bytes(
    pdf_bytes: bytes,
) -> Tuple[Dict[str, Decimal], Dict[str, Decimal]]:
    """
    Returns two dicts:
      - totals_by_supplier: invoices summed per supplier
      - credits_by_supplier: credit notes summed per supplier
    """
    pages = extract_pages_text_from_bytes(pdf_bytes)

    totals_by_supplier: Dict[str, Decimal] = {}
    credits_by_supplier: Dict[str, Decimal] = {}

    for doc in iter_documents_from_pages(pages):
        supplier = normalize_supplier_name(
            doc.supplier_raw, doc.centre_label, doc.full_text
        )

        if doc.is_credit_note:
            credits_by_supplier[supplier] = (
                credits_by_supplier.get(supplier, Decimal("0.00")) + doc.amount_payable
            )
        else:
            totals_by_supplier[supplier] = (
                totals_by_supplier.get(supplier, Decimal("0.00")) + doc.amount_payable
            )

    return totals_by_supplier, credits_by_supplier


# ---------- Public API: Export rows (one row per document) ----------


def iter_invoice_export_rows_from_bytes(pdf_bytes: bytes) -> Iterable[InvoiceExportRow]:
    """
    Yield one row per invoice/credit document found in the PDF.
    Credits are returned as NEGATIVE amounts (so Excel sums work naturally).
    """
    pages = extract_pages_text_from_bytes(pdf_bytes)

    for doc in iter_documents_from_pages(pages):
        unit = parse_unit(doc.full_text)
        supplier_ref = parse_supplier_ref(doc.full_text)

        supplier_norm = normalize_supplier_name(
            doc.supplier_raw,
            doc.centre_label,
            doc.full_text,
        )

        # Signed amount:
        # - Invoice: positive
        # - Credit Note: negative (important for Excel totals / pivots)
        amount_signed = (
            -doc.amount_payable if doc.is_credit_note else doc.amount_payable
        )

        yield InvoiceExportRow(
            document_type=doc_type_label(doc.is_credit_note),
            supplier=supplier_norm,
            unit=unit,
            centre=doc.centre_label,
            supplier_ref=supplier_ref,
            amount_payable=amount_signed,
        )


def export_rows_list_from_bytes(pdf_bytes: bytes) -> List[InvoiceExportRow]:
    """Convenience wrapper if you prefer a list."""
    return list(iter_invoice_export_rows_from_bytes(pdf_bytes))
