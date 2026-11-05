"""Upload API for individual purchase orders awaiting delivery."""
from fastapi import APIRouter, File, HTTPException, UploadFile
from pydantic import BaseModel
from starlette.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse
from awaiting_orders_excel import build_orders_workbook

from awaiting_orders_core import OrderParseError, analyze_awaiting_orders_from_bytes

router = APIRouter()


class SupplierSummary(BaseModel):
    supplier: str
    order_count: int
    net_value: str
    vat: str
    total: str


class OrderDetail(BaseModel):
    order_number: str
    supplier: str
    supplier_raw: str
    centre: str
    order_date: str
    delivery_required: str
    net_value: str
    vat: str
    total: str


class AwaitingOrdersResponse(BaseModel):
    currency: str
    total_basis: str
    order_count: int
    grand_net: str
    grand_vat: str
    grand_total: str
    supplier_totals: list[SupplierSummary]
    orders: list[OrderDetail]


@router.post("/outstanding-orders/analyze", response_model=AwaitingOrdersResponse)
async def analyze_outstanding_orders(file: UploadFile = File(...)):
    """Sum uploaded purchase orders assumed to be outstanding, including VAT."""
    if file.content_type not in ("application/pdf", "application/octet-stream"):
        raise HTTPException(400, "Please upload a PDF file.")
    data = await file.read()
    if not data:
        raise HTTPException(400, "Uploaded file was empty.")
    try:
        return await run_in_threadpool(analyze_awaiting_orders_from_bytes, data)
    except OrderParseError as exc:
        raise HTTPException(422, str(exc)) from exc


@router.post("/outstanding-orders/export-xlsx")
async def export_awaiting_orders(file: UploadFile = File(...)):
    """Download order details and supplier totals as a numeric Excel workbook."""
    result = await analyze_outstanding_orders(file)
    workbook = await run_in_threadpool(build_orders_workbook, result)
    return StreamingResponse(
        workbook,
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        headers={"Content-Disposition": 'attachment; filename="awaiting_orders.xlsx"'},
    )
