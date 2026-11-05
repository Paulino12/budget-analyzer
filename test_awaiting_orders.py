import asyncio
import io
import os
from pathlib import Path
import unittest
from unittest.mock import patch

from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers

from awaiting_orders_core import OrderParseError, parse_orders, analyze_awaiting_orders_from_bytes
from awaiting_orders_api import analyze_outstanding_orders, AwaitingOrdersResponse


def page(number="123", supplier="Bidfood", centre="Home - Food", totals=True):
    return (f"Allmanhall Purchase Order\nOrder Number: {number} Account Number: A\n"
            "Order Date: 04-Nov-2026 Delivery Required: 06-Nov-2026\n"
            f"Supplier Name: {supplier}\nCentre: {centre}\n" +
            ("Net Value: 100.00\nVAT: 20.00\nTotal Value: 120.00\n" if totals else ""))


class OrdersTests(unittest.TestCase):
    def test_multipage_and_supplier_splits(self):
        pages = [page(totals=False), "Purchase Order\nNet Value: 100.00\nVAT: 20.00\nTotal Value: 120.00",
                 page("124", centre="Home - Bistro"),
                 page("125", supplier="NCB Foodservice Ltd") + "M70500 Bacon",
                 page("126", supplier="NCB Foodservice Ltd")]
        with patch("awaiting_orders_core.extract_pages", return_value=pages):
            result = analyze_awaiting_orders_from_bytes(b"pdf")
        self.assertEqual(result["grand_total"], "480.00")
        self.assertEqual(result["order_count"], 4)
        self.assertEqual([r["supplier"] for r in result["supplier_totals"]],
                         ["Bidfood (bistro)", "Bidfood (food)", "NCB Meat", "NCB Veg"])
        AwaitingOrdersResponse(**result)

    def test_invalid_bundles_fail_instead_of_returning_partial_total(self):
        cases = [[], ["invoice"], [page(), page()], [page(), page("124", totals=False)],
                 [page().replace("120.00", "121.00")], [page(), "unrelated page"]]
        for pages in cases:
            with self.subTest(pages=pages), self.assertRaises(OrderParseError):
                parse_orders(pages)

    def test_zero_value_and_thousands(self):
        self.assertEqual(str(parse_orders([page().replace("100.00", "1,100.00").replace("120.00", "1,120.00")])[0].total), "1120.00")
        self.assertEqual(parse_orders([page().replace("100.00", "0.00").replace("20.00", "0.00").replace("10.00", "0.00")])[0].total, 0)

    def test_upload_errors(self):
        for data, mime, status in [(b"", "application/pdf", 400), (b"x", "text/plain", 400),
                                   (b"not pdf", "application/pdf", 422), (b"%PDF-broken", "application/pdf", 422)]:
            upload = UploadFile(io.BytesIO(data), headers=Headers({"content-type": mime}))
            with self.subTest(status=status), self.assertRaises(HTTPException) as exc:
                asyncio.run(analyze_outstanding_orders(upload))
            self.assertEqual(exc.exception.status_code, status)

    def test_existing_routes_and_legacy_parser(self):
        from api import app
        from outstanding_orders_core import summarize_outstanding_orders_from_bytes
        self.assertTrue({"/invoices/analyze", "/invoices/export-xlsx", "/outstanding-orders/summary",
                         "/outstanding-orders/analyze", "/health"}.issubset({r.path for r in app.routes}))
        text = "Centre: Home - Food\n14/1 Bidfood LOC 123 05-Nov-2026 12.34\nReport Total: 12.34"
        with patch("outstanding_orders_core.extract_pdf_text_from_bytes", return_value=text):
            _, totals, total, report = summarize_outstanding_orders_from_bytes(b"pdf")
        self.assertEqual(str(totals["Bidfood (food)"]), "12.34")
        self.assertEqual(total, report)

    @unittest.skipUnless(os.environ.get("AWAITING_ORDERS_SAMPLE"), "Set AWAITING_ORDERS_SAMPLE to run private sample verification")
    def test_real_pdf_upload(self):
        data = Path(os.environ["AWAITING_ORDERS_SAMPLE"]).read_bytes()
        upload = UploadFile(io.BytesIO(data), headers=Headers({"content-type": "application/pdf"}))
        result = asyncio.run(analyze_outstanding_orders(upload))
        AwaitingOrdersResponse(**result)
        self.assertEqual(result["order_count"], 37)
        self.assertEqual((result["grand_net"], result["grand_vat"], result["grand_total"]),
                         ("5414.07", "228.46", "5642.53"))
        suppliers = {r["supplier"]: r for r in result["supplier_totals"]}
        self.assertEqual(len(suppliers), 9)
        self.assertEqual(suppliers["Bidfood (bistro)"]["total"], "1491.49")
        from awaiting_orders_excel import build_orders_workbook
        from openpyxl import load_workbook
        workbook = load_workbook(build_orders_workbook(result))
        orders = workbook["Orders"]
        self.assertEqual(orders.max_row, 39)  # header, 37 orders, total
        self.assertEqual(orders["I39"].value, 5642.53)
        self.assertEqual(orders["I2"].data_type, "n")
        self.assertEqual(orders["E2"].data_type, "d")
        self.assertEqual(orders.auto_filter.ref, "A1:I38")
        self.assertEqual(workbook["Supplier Summary"]["E11"].value, 5642.53)
        self.assertAlmostEqual(sum(orders.cell(r, 9).value for r in range(2, 39)), 5642.53)


if __name__ == "__main__":
    unittest.main()
