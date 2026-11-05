# Budget Analyzer (Henbrook House) — FastAPI Backend

This service powers the **Budget Analyzer** tool in `chef-context` (Next.js).  
It parses exported PDFs from the eProcurement system and returns:

1. **Outstanding Orders summary** (real-time committed spend)
2. **Invoices/Credit Notes summary** (what we actually pay; invoices minus credits)
3. **Invoices export to Excel** (one row per invoice/credit note for audit and pivots)

---

## What it does

### Outstanding Orders (PDF)

- Parses the Outstanding Orders PDF and aggregates spend **per supplier**.
- Detects **multiple Centre sections** inside the same PDF (Food + Bistro) and:
  - splits **Bidfood** into `Bidfood (FOOD)` / `Bidfood (BISTRO)` correctly.

### Invoices / Credit Notes (PDF)

- Segments the PDF into individual documents (each invoice/credit note may span multiple pages).
- Extracts per document:
  - Supplier
  - Centre (used to split Bidfood Food/Bistro)
  - Amount Payable
- Aggregates per supplier into:
  - `Total` (invoices)
  - `Credits` (credit notes)
  - `Difference` = `Total - Credits` (net payable)

### Excel Export (Invoices PDF -> XLSX)

- Produces a downloadable `.xlsx` file with **one row per document** (no combining).
- Credits are exported as **negative amounts** to make Excel totals/pivots natural.
- Columns:
  - Document Type
  - Supplier
  - Unit
  - Centre
  - Supplier Ref
  - Amount Payable

---

## Repo layout

Typical minimal structure:
budget_analyzer/
api.py
outstanding_orders_core.py
invoices_core.py
requirements.txt
README.md
.gitignore

---

## Requirements

- Python 3.11+ recommended (works on 3.10+ in most cases)
- Dependencies (see `requirements.txt`):
  - fastapi
  - uvicorn
  - python-multipart (required for `UploadFile`)
  - pdfplumber
  - openpyxl
  - pydantic (FastAPI dependency)

---

## Installation (local)

1. Create & activate a virtual environment:

### Windows (PowerShell)

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1

python -m pip install -r requirements.txt
Running locally

Run the API on port 10001 (matches your Next.js BUDGET_BACKEND_URL):

python -m uvicorn api:app --reload --port 10000


Open Swagger docs:

http://127.0.0.1:10001/docs

```
