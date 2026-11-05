"""Excel export using the same openpyxl dependency as invoice exports."""
from datetime import datetime
from decimal import Decimal
from io import BytesIO

from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill
from openpyxl.utils import get_column_letter


def build_orders_workbook(result: dict) -> BytesIO:
    wb = Workbook()
    orders = wb.active
    orders.title = "Orders"
    orders.append(["Order Number", "Supplier", "Original Supplier", "Centre", "Order Date",
                   "Delivery Required", "Net Value", "VAT", "Total incl. VAT"])
    for row in result["orders"]:
        dates = []
        for key in ("order_date", "delivery_required"):
            try:
                dates.append(datetime.strptime(row[key], "%d-%b-%Y"))
            except ValueError:
                dates.append(row[key])
        orders.append([row["order_number"], row["supplier"], row["supplier_raw"], row["centre"],
                       *dates, *[Decimal(row[k]) for k in ("net_value", "vat", "total")]])
    summary = wb.create_sheet("Supplier Summary")
    summary.append(["Supplier", "Orders", "Net Value", "VAT", "Total incl. VAT"])
    for row in result["supplier_totals"]:
        summary.append([row["supplier"], row["order_count"],
                        *[Decimal(row[k]) for k in ("net_value", "vat", "total")]])

    for sheet, widths, money_columns in (
        (orders, [22, 32, 32, 55, 18, 20, 18, 18, 20], (7, 8, 9)),
        (summary, [32, 12, 18, 18, 20], (3, 4, 5)),
    ):
        sheet.freeze_panes = "A2"
        sheet.auto_filter.ref = sheet.dimensions
        # Treat source strings literally, including supplier names starting '='.
        for row in sheet.iter_rows():
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        totals = [None] * len(widths)
        totals[0] = "TOTAL"
        if sheet == summary:
            totals[1] = result["order_count"]
        for column, key in zip(money_columns, ("grand_net", "grand_vat", "grand_total")):
            totals[column - 1] = Decimal(result[key])
        sheet.append(totals)
        for column, width in enumerate(widths, 1):
            sheet.column_dimensions[get_column_letter(column)].width = width
        for row in sheet.iter_rows(min_row=2):
            for column in money_columns:
                row[column - 1].number_format = '"£"#,##0.00;[Red]-"£"#,##0.00'
        for cell in sheet[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="183B56")
        for cell in sheet[sheet.max_row]:
            cell.font = Font(bold=True)
            cell.fill = PatternFill("solid", fgColor="E8EFF5")
    for row in orders.iter_rows(min_row=2, max_row=orders.max_row - 1):
        for column in (5, 6):
            row[column - 1].number_format = "dd-mmm-yyyy"
    output = BytesIO()
    wb.save(output)
    output.seek(0)
    return output
