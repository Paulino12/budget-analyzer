from __future__ import annotations

from decimal import Decimal
from typing import List, Optional

from fastapi import FastAPI, UploadFile, File, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from fastapi.responses import StreamingResponse
from openpyxl import Workbook
from openpyxl.utils import get_column_letter
import io
from invoices_core import export_rows_list_from_bytes

from outstanding_orders_core import summarize_outstanding_orders_from_bytes
from invoices_core import summarize_invoices_from_bytes
from awaiting_orders_api import router as awaiting_orders_router




# ---------- Pydantic response models (API contracts) ----------


# ---------- Outstanding Orders API models ----------
class SupplierTotal(BaseModel):
    supplier: str
    value: str  # string to avoid float rounding issues (e.g. "2471.16")


class OutstandingOrdersSummaryResponse(BaseModel):
    centre: str
    supplier_totals: List[SupplierTotal]
    grand_total: str
    report_total: Optional[str] = None
    totals_match_report: Optional[bool] = None


# ---------- Invoices API models ----------
class InvoiceSupplierRow(BaseModel):
    supplier: str
    total: str
    credits: str
    difference: str


class InvoicesSummaryResponse(BaseModel):
    supplier_totals: List[InvoiceSupplierRow]
    grand_total: str
    grand_credits: str
    grand_difference: str


def money_str(d: Decimal) -> str:
    """
    Format Decimal as a plain 2dp string, NOT currency formatted.
    JSON consumers (Next.js) can format as £ later.
    """
    return f"{d:.2f}"


# ---------- FastAPI app ----------

app = FastAPI(title="Henbrook eProc Analysis API", version="0.1.0")
app.include_router(awaiting_orders_router)

# In dev, you can allow your Next.js origin. Tighten this for production.
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:3000",
        "http://127.0.0.1:3000",
        "https://chef-context.maryoctav.com", 
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health")
def health():
    return {"ok": True}


@app.post(
    "/outstanding-orders/summary", response_model=OutstandingOrdersSummaryResponse
)
async def outstanding_orders_summary(file: UploadFile = File(...)):
    """
    Upload the Outstanding Orders PDF and return totals per supplier.

    Expected content: a PDF export from your eProc system.
    """
    # Basic validation: make sure something PDF-like was uploaded
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file was empty.")

    try:
        centre, totals_by_supplier, grand_total, report_total = (
            summarize_outstanding_orders_from_bytes(pdf_bytes)
        )
    except Exception as e:
        # Don’t leak huge internals; give enough to debug.
        raise HTTPException(status_code=422, detail=str(e))

    # Sort totals descending for nice display
    supplier_totals = sorted(
        totals_by_supplier.items(),
        key=lambda kv: kv[1],
        reverse=True,
    )

    totals_match_report = None
    if report_total is not None:
        totals_match_report = report_total == grand_total

    return OutstandingOrdersSummaryResponse(
        centre=centre,
        supplier_totals=[
            SupplierTotal(supplier=s, value=money_str(v)) for s, v in supplier_totals
        ],
        grand_total=money_str(grand_total),
        report_total=money_str(report_total) if report_total is not None else None,
        totals_match_report=totals_match_report,
    )


@app.post("/invoices/analyze", response_model=InvoicesSummaryResponse)
async def invoices_analyze(file: UploadFile = File(...)):
    """
    Upload an Invoices/Credit Notes PDF and return:
      Supplier | Total | Credits | Difference
    where:
      Total = sum of invoices
      Credits = sum of credit notes
      Difference = Total - Credits
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file was empty.")

    try:
        totals, credits = summarize_invoices_from_bytes(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Merge keys so suppliers with only credits still appear
    suppliers = sorted(set(totals.keys()) | set(credits.keys()))

    rows: List[InvoiceSupplierRow] = []
    grand_total = Decimal("0.00")
    grand_credits = Decimal("0.00")

    for s in suppliers:
        t = totals.get(s, Decimal("0.00"))
        c = credits.get(s, Decimal("0.00"))
        d = t - c

        grand_total += t
        grand_credits += c

        rows.append(
            InvoiceSupplierRow(
                supplier=s,
                total=money_str(t),
                credits=money_str(c),
                difference=money_str(d),
            )
        )

    grand_difference = grand_total - grand_credits

    return InvoicesSummaryResponse(
        supplier_totals=rows,
        grand_total=money_str(grand_total),
        grand_credits=money_str(grand_credits),
        grand_difference=money_str(grand_difference),
    )


@app.post("/invoices/export-xlsx")
async def invoices_export_xlsx(file: UploadFile = File(...)):
    """
    Upload the invoices/credit notes PDF and download an Excel file
    with one row per document (no combining).
    Columns:
      Document Type, Supplier, Unit, Centre, Supplier Ref, Amount Payable
    """
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    pdf_bytes = await file.read()
    if not pdf_bytes:
        raise HTTPException(status_code=400, detail="Uploaded file was empty.")

    try:
        rows = export_rows_list_from_bytes(pdf_bytes)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Build the workbook in memory
    wb = Workbook()
    ws = wb.worksheets[0]  # always exists, typed better than wb.active for Pylance
    ws.title = "Invoices"

    headers = [
        "Document Type",
        "Supplier",
        "Unit",
        "Centre",
        "Supplier Ref",
        "Amount Payable",
    ]

    ws.append(headers)

    for r in rows:
        ws.append(
            [
                r.document_type,
                r.supplier,
                r.unit,
                r.centre,
                r.supplier_ref,
                float(r.amount_payable),
            ]
        )
    # Bold header row
    for col in range(1, len(headers) + 1):
        ws.cell(row=1, column=col).font = ws.cell(row=1, column=col).font.copy(
            bold=True
        )

    # Basic column widths
    for col, header in enumerate(headers, start=1):
        ws.column_dimensions[get_column_letter(col)].width = max(14, len(header) + 2)

    # Money formatting for last column (credits negative)
    money_col = len(headers)
    for row_idx in range(2, ws.max_row + 1):
        ws.cell(row=row_idx, column=money_col).number_format = (
            "£#,##0.00;[Red]-£#,##0.00"
        )

        # Return as download
        out = io.BytesIO()
        wb.save(out)
        out.seek(0)

        filename = "invoices_export.xlsx"
        return StreamingResponse(
            out,
            media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
