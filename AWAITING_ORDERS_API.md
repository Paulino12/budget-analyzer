# Purchase orders awaiting delivery

`POST /outstanding-orders/analyze` accepts a multipart PDF in the `file` field.
`POST /outstanding-orders/export-xlsx` accepts the same upload and downloads
`awaiting_orders.xlsx`, with Orders and Supplier Summary sheets. Amounts are
numeric currency cells; dates are Excel dates, with filters and frozen headers.
Run the existing service with `python -m uvicorn api:app --port 10000`.
The new route is available in `/docs`.

Example (PowerShell, using curl.exe):

```powershell
curl.exe -F "file=@C:\path\awaiting deliveries orders.pdf;type=application/pdf" http://localhost:10000/outstanding-orders/analyze
```

Response fields:

| Field | Meaning |
| --- | --- |
| `grand_total` | Final value including VAT, as a two-decimal string |
| `grand_net`, `grand_vat` | Separate net and VAT totals |
| `currency`, `total_basis` | `GBP`, `including_vat` |
| `order_count` | Number of purchase orders, not number of pages |
| `supplier_totals` | Supplier, order count, net_value, vat, total |
| `orders` | Order number, normalized/raw supplier, centre, order date, delivery_required, net_value, vat, total |

Dates retain the source format (`04-Nov-2026`). Monetary values are decimal
strings, matching the invoice API convention. Supplier labels reuse the invoice
normalization rules: Bidfood food/bistro; NCB Meat when an M-prefixed product
code is present, otherwise NCB Veg.

This route reads individual Allmanhall Purchase Order documents, including
continuation pages. It treats every uploaded order as outstanding. These PDFs
do not establish actual delivery status or remaining quantities after partial
delivery. There is no date filtering, storage, or cross-upload accumulation.
Upload a fresh bundle of outstanding orders for each analysis.

Missing/ambiguous totals, duplicate order numbers, unreadable PDFs, or totals
where net plus VAT differs from total return 422 instead of a partial result.
Empty uploads and unsupported content types return 400. Scans require OCR
before upload. Duplicate order numbers are rejected rather than silently
choosing a potentially revised version.

The existing `/outstanding-orders/summary` route continues to accept the older
table report. Invoice endpoints and their parser are unchanged. This change
does not wire up chef-context or deploy the service.

Tests: `python -m unittest -v test_awaiting_orders`.
Set `AWAITING_ORDERS_SAMPLE` to the supplied PDF path to additionally verify the
37-order reference bundle. No private PDF is committed to the repository.
