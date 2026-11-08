#!/usr/bin/env python3
"""Generate a safe, internally consistent loan package for the DealPilot demo."""

from __future__ import annotations

import json
import zipfile
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen import canvas


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "output" / "pdf" / "cedar_loom_demo_package"
FONT_REGULAR = "/System/Library/Fonts/Supplemental/Arial.ttf"
FONT_BOLD = "/System/Library/Fonts/Supplemental/Arial Bold.ttf"

NAVY = colors.HexColor("#102A43")
BLUE = colors.HexColor("#137CBD")
TEAL = colors.HexColor("#00A6A6")
INK = colors.HexColor("#243B53")
MUTED = colors.HexColor("#627D98")
PALE = colors.HexColor("#EAF4F8")
LINE = colors.HexColor("#D9E2EC")
DEMO = colors.HexColor("#B83280")


def register_fonts():
    if Path(FONT_REGULAR).exists():
        pdfmetrics.registerFont(TTFont("DP-Regular", FONT_REGULAR))
        pdfmetrics.registerFont(TTFont("DP-Bold", FONT_BOLD))
        return "DP-Regular", "DP-Bold"
    return "Helvetica", "Helvetica-Bold"


REGULAR, BOLD = register_fonts()


def money(value: float) -> str:
    return f"${value:,.2f}"


def base_pdf(path: Path, title: str, eyebrow: str, doc_id: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter, pageCompression=1)
    c.setTitle(title)
    c.setAuthor("DealPilot Hackathon Demo")
    c.setProducer("DealPilot Synthetic Document Generator")
    width, height = letter

    c.setFillColor(NAVY)
    c.rect(0, height - 105, width, 105, fill=1, stroke=0)
    c.setFillColor(TEAL)
    c.rect(0, height - 105, 9, 105, fill=1, stroke=0)
    c.setFont(BOLD, 9)
    c.setFillColor(colors.HexColor("#9FB3C8"))
    c.drawString(38, height - 34, eyebrow.upper())
    c.setFillColor(colors.white)
    c.setFont(BOLD, 22)
    c.drawString(38, height - 68, title)
    c.setFont(REGULAR, 8)
    c.setFillColor(colors.HexColor("#D9E2EC"))
    c.drawRightString(width - 38, height - 34, doc_id)

    # A visible safety mark that does not obscure machine-readable text.
    c.saveState()
    c.setFillColor(colors.Color(DEMO.red, DEMO.green, DEMO.blue, alpha=0.08))
    c.translate(width / 2, height / 2)
    c.rotate(31)
    c.setFont(BOLD, 39)
    c.drawCentredString(0, 0, "FICTIONAL DEMO — NOT VALID")
    c.restoreState()
    return c


def footer(c, page="Page 1 of 1"):
    width, _ = letter
    c.setStrokeColor(LINE)
    c.line(38, 42, width - 38, 42)
    c.setFillColor(DEMO)
    c.setFont(BOLD, 7.5)
    c.drawString(38, 27, "SYNTHETIC HACKATHON DATA • NO REAL PERSON, BUSINESS, BANK, OR GOVERNMENT RECORD")
    c.setFillColor(MUTED)
    c.setFont(REGULAR, 8)
    c.drawRightString(width - 38, 27, page)


def section(c, y: float, label: str) -> float:
    c.setFillColor(PALE)
    c.roundRect(38, y - 20, 536, 24, 4, fill=1, stroke=0)
    c.setFillColor(BLUE)
    c.setFont(BOLD, 9)
    c.drawString(48, y - 12, label.upper())
    return y - 38


def label_value(c, x: float, y: float, label: str, value: str, width: float = 240) -> float:
    c.setFillColor(MUTED)
    c.setFont(BOLD, 7.7)
    c.drawString(x, y, label)
    c.setFillColor(INK)
    c.setFont(REGULAR, 10.3)
    c.drawString(x, y - 16, value)
    c.setStrokeColor(LINE)
    c.line(x, y - 23, x + width, y - 23)
    return y - 43


def note(c, y: float, text: str):
    c.setFillColor(colors.HexColor("#FFF4E5"))
    c.roundRect(38, y - 48, 536, 48, 6, fill=1, stroke=0)
    c.setFillColor(colors.HexColor("#8D5B00"))
    c.setFont(BOLD, 8)
    c.drawString(50, y - 17, "DEMO NOTICE")
    c.setFont(REGULAR, 8.3)
    c.drawString(50, y - 33, text)


def finish(c):
    footer(c)
    c.showPage()
    c.save()


def make_id(path: Path):
    c = base_pdf(path, "Texas Driver License", "Guarantor identity", "DEMO-ID-45872190")
    y = 650
    note(c, y, "This is a generated test credential. It is not government-issued identification.")
    y = section(c, y - 72, "Identity details")
    y = label_value(c, 58, y, "Name", "Maya Elise Carter", 230)
    y = label_value(c, 58, y, "Address", "920 Fictional Grove Lane, Plano, TX 75025", 496)
    y = label_value(c, 58, y, "Date of birth", "1988-06-14", 230)
    y = label_value(c, 58, y, "DL number", "45872190", 230)
    label_value(c, 58, y, "Class / expires", "C / 2030-06-14", 230)
    finish(c)


def make_formation(path: Path):
    c = base_pdf(path, "Certificate of Formation", "Synthetic state filing", "DEMO-TX-804211")
    y = 650
    note(c, y, "Training facsimile only. Not filed with the Texas Secretary of State.")
    y = section(c, y - 72, "Domestic limited liability company")
    y = label_value(c, 58, y, "Entity name", "Cedar & Loom Workspace LLC", 496)
    y = label_value(c, 58, y, "Effective date", "2021-04-12", 230)
    y = label_value(c, 58, y, "Principal office", "4100 Fictional Parkway, Suite 220, Plano, TX 75024", 496)
    y = label_value(c, 58, y, "Registered agent", "Demo Registered Agents LLC", 496)
    label_value(c, 58, y, "Registered office", "100 Sample Plaza, Austin, TX 78701", 496)
    finish(c)


def make_ein(path: Path):
    c = base_pdf(path, "EIN Assignment Notice", "IRS-style synthetic notice • CP 575", "DEMO-CP575-123456789")
    y = 650
    note(c, y, "Generated for software testing. This notice is not from the Internal Revenue Service.")
    y = section(c, y - 72, "Employer identification number assigned")
    y = label_value(c, 58, y, "Name of business", "Cedar & Loom Workspace LLC", 496)
    y = label_value(c, 58, y, "Employer Identification Number", "12-3456789", 260)
    y = label_value(c, 58, y, "Business address", "4100 Fictional Parkway, Suite 220, Plano, TX 75024", 496)
    label_value(c, 58, y, "Notice date", "2021-04-16", 230)
    finish(c)


def make_financials(path: Path):
    c = base_pdf(path, "Management Financial Statements", "Profit & Loss • Balance Sheet", "DEMO-FS-2026-10")
    y = section(c, 650, "Profit & Loss • January 1–October 31, 2026")
    rows = [
        ("Net sales", 1_034_800), ("Cost of goods sold", -512_200),
        ("Gross profit", 522_600), ("Operating expenses", -367_900),
        ("Operating income", 154_700), ("Net income", 128_450),
    ]
    for label, value in rows:
        c.setFillColor(INK); c.setFont(BOLD if label in {"Gross profit", "Net income"} else REGULAR, 9.5)
        c.drawString(58, y, label); c.drawRightString(554, y, money(value)); y -= 24
    y = section(c, y - 4, "Balance Sheet • October 31, 2026")
    rows = [
        ("Cash", 131_750), ("Accounts receivable", 182_400), ("Inventory", 96_200),
        ("Property & equipment, net", 268_300), ("Total assets", 678_650),
        ("Current liabilities", 143_900), ("Long-term debt", 112_000),
        ("Members' equity", 422_750), ("Total liabilities & equity", 678_650),
    ]
    for label, value in rows:
        c.setFillColor(INK); c.setFont(BOLD if label.startswith("Total") else REGULAR, 9.2)
        c.drawString(58, y, label); c.drawRightString(554, y, money(value)); y -= 22
    c.setFillColor(MUTED); c.setFont(REGULAR, 8.5)
    c.drawString(58, 82, "Prepared by: Northstar Demo Accounting PLLC · 555-0148")
    finish(c)


def make_tax_return(path: Path):
    c = base_pdf(path, "2025 Business Tax Return", "Form 1065-style synthetic summary", "DEMO-TAX-2025")
    y = 650
    note(c, y, "Training summary only. It is not a filed federal or state Tax Return.")
    y = section(c, y - 72, "Tax Year 2025")
    y = label_value(c, 58, y, "Taxpayer", "Cedar & Loom Workspace LLC", 496)
    # Avoid the EIN-notice classifier phrase here; the tax-return classifier should own this file.
    y = label_value(c, 58, y, "Federal tax ID", "12-3456789", 260)
    y = label_value(c, 58, y, "Gross receipts or sales", "$1,112,600.00", 260)
    y = label_value(c, 58, y, "Ordinary business income", "$119,420.00", 260)
    y = label_value(c, 58, y, "Paid preparer", "Northstar Demo Accounting PLLC", 496)
    label_value(c, 58, y, "Preparer PTIN", "P00012345", 260)
    finish(c)


def make_request_letter(path: Path):
    c = base_pdf(path, "Equipment Loan Request", "Borrower request letter", "DEMO-REQ-2026-1108")
    c.setFillColor(INK); c.setFont(REGULAR, 10)
    lines = [
        "November 8, 2026", "", "DealPilot Commercial Lending", "Credit Intake Team", "", 
        "RE: Loan Request", "", "To the Credit Team:", "",
        "We are requesting $125,000 to purchase a CNC wood router, a delivery van, and", 
        "installation equipment. These assets will expand our capacity for commercial office", 
        "furniture projects while shortening lead times for existing customers.", "",
        "We request a 48-month term and would like funding by November 20, 2026.",
        "The enclosed package includes identity, formation, EIN, financial, tax, banking,", 
        "and vendor quote support.", "", "Sincerely,", "Maya Elise Carter", "Managing Member",
        "Cedar & Loom Workspace LLC",
    ]
    y = 650
    for line in lines:
        c.setFont(BOLD if line in {"RE: Loan Request", "Maya Elise Carter"} else REGULAR, 10)
        c.drawString(58, y, line); y -= 20 if line else 10
    finish(c)


def make_quote(path: Path):
    c = base_pdf(path, "Equipment QUOTATION", "Vendor proposal", "DEMO-Q-8814")
    y = 650
    c.setFillColor(INK); c.setFont(BOLD, 11)
    c.drawString(58, y, "Pioneer Fabrication Systems LLC · Demo vendor")
    y -= 22
    c.setFont(REGULAR, 9); c.setFillColor(MUTED)
    c.drawString(58, y, "Quote date: 2026-11-06  •  Valid through: 2026-12-06")
    y = section(c, y - 30, "Quoted equipment")
    rows = [
        ("CNC router system with dust collection", 72_500),
        ("Commercial delivery van", 41_000),
        ("Installation tools and safety package", 7_500),
        ("Freight, setup, and training", 4_000),
    ]
    for label, value in rows:
        c.setFillColor(INK); c.setFont(REGULAR, 9.5)
        c.drawString(58, y, label); c.drawRightString(554, y, money(value)); y -= 28
    c.setStrokeColor(BLUE); c.line(360, y + 8, 554, y + 8)
    c.setFont(BOLD, 12); c.setFillColor(NAVY)
    c.drawString(405, y - 12, "TOTAL")
    c.drawRightString(554, y - 12, "$125,000.00")
    y = section(c, y - 55, "Remittance instructions")
    c.setFillColor(INK); c.setFont(BOLD, 9); c.drawString(58, y, "Remit to:")
    c.setFont(REGULAR, 9.5); c.drawString(58, y - 20, "Pioneer Fabrication Systems LLC")
    c.drawString(58, y - 40, "Account ending 7719")
    c.drawString(58, y - 60, "Routing 111900659")
    finish(c)


def make_statement(path: Path, period_start: str, period_end: str, opening: float, deposits: float,
                   withdrawals: float, closing: float, statement_no: int):
    c = base_pdf(path, "BUSINESS CHECKING STATEMENT", "Mock Prairie Bank", f"DEMO-STMT-{statement_no:02d}")
    y = 650
    c.setFillColor(INK); c.setFont(BOLD, 10)
    c.drawString(58, y, "Cedar & Loom Workspace LLC")
    c.setFont(REGULAR, 9); c.drawRightString(554, y, "Account ending 4412")
    y -= 23
    c.setFillColor(MUTED); c.drawString(58, y, "4100 Fictional Parkway, Suite 220, Plano, TX 75024")
    y = section(c, y - 32, "Statement period")
    c.setFillColor(INK); c.setFont(BOLD, 10)
    c.drawString(58, y, f"{period_start} to {period_end}")
    y = section(c, y - 42, "Account summary")
    for label, value in [
        ("Opening balance", opening),
        ("Total deposits & credits", deposits),
        ("Total withdrawals & debits", withdrawals),
        ("Closing balance", closing),
    ]:
        c.setFillColor(MUTED); c.setFont(BOLD, 8); c.drawString(58, y, label)
        c.setFillColor(INK); c.setFont(REGULAR, 10); c.drawString(58, y - 15, money(value))
        c.setStrokeColor(LINE); c.line(58, y - 22, 300, y - 22); y -= 41
    y = section(c, y - 3, "Activity highlights")
    transactions = [
        (period_start, "ACH CUSTOMER RECEIPTS", deposits * 0.52),
        (period_start, "CARD & LOCKBOX RECEIPTS", deposits * 0.48),
        (period_end, "OPERATING PAYMENTS", -withdrawals),
    ]
    for date, desc, amount in transactions:
        c.setFillColor(INK); c.setFont(REGULAR, 8.7)
        c.drawString(58, y, date); c.drawString(145, y, desc); c.drawRightString(554, y, money(amount)); y -= 23
    finish(c)


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    documents = [
        ("01_guarantor_id_maya_carter.pdf", make_id),
        ("02_certificate_of_formation.pdf", make_formation),
        ("03_ein_assignment_cp575.pdf", make_ein),
        ("04_financial_statements_2026_ytd.pdf", make_financials),
        ("05_business_tax_return_2025.pdf", make_tax_return),
        ("06_loan_request_letter.pdf", make_request_letter),
        ("07_equipment_quote.pdf", make_quote),
    ]
    for filename, maker in documents:
        maker(OUT / filename)

    statements = [
        ("08_bank_statement_2026_05.pdf", "2026-05-01", "2026-05-31", 84_300, 98_400, 91_250, 91_450),
        ("09_bank_statement_2026_06.pdf", "2026-06-01", "2026-06-30", 91_450, 102_800, 96_500, 97_750),
        ("10_bank_statement_2026_07.pdf", "2026-07-01", "2026-07-31", 97_750, 109_600, 101_900, 105_450),
        ("11_bank_statement_2026_08.pdf", "2026-08-01", "2026-08-31", 105_450, 105_200, 100_100, 110_550),
        ("12_bank_statement_2026_09.pdf", "2026-09-01", "2026-09-30", 110_550, 111_900, 104_600, 117_850),
        ("13_bank_statement_2026_10.pdf", "2026-10-01", "2026-10-31", 117_850, 116_900, 103_000, 131_750),
    ]
    for idx, row in enumerate(statements, start=1):
        filename, start, end, opening, deposits, withdrawals, closing = row
        make_statement(OUT / filename, start, end, opening, deposits, withdrawals, closing, idx)

    application_data = {
        "legal_name": "Cedar & Loom Workspace LLC",
        "dba": "Cedar & Loom Office Interiors",
        "ein": "12-3456789",
        "formation_date": "2021-04-12",
        "business_address": "4100 Fictional Parkway, Suite 220, Plano, TX 75024",
        "industry": "Commercial furniture manufacturing and installation",
        "annual_revenue_stated": "1240000",
        "bank_account": "Mock Prairie Bank / account ending 4412",
        "amount_requested": "125000",
        "product": "Equipment Loan",
        "purpose": "Purchase CNC wood router, delivery van, and installation equipment to expand production capacity.",
        "term_months": "48",
        "needed_by": "2026-11-20T15:00",
        "guarantor_name": "Maya Elise Carter",
        "guarantor_dob": "1988-06-14",
        "guarantor_address": "920 Fictional Grove Lane, Plano, TX 75025",
        "guarantor_dl_number": "45872190",
        "purpose_category": "equipment",
    }
    (OUT / "APPLICATION_DATA.json").write_text(json.dumps(application_data, indent=2) + "\n")
    readme = """# Cedar & Loom fictional upload package

Everything in this folder is synthetic and intended only for the DealPilot hackathon demo.

1. Create a borrower account and start an application.
2. Copy the values from `APPLICATION_DATA.json` into the five-step form.
3. Upload all 13 PDFs together. The six bank statements are separate on purpose.
4. Review the detected document types, then submit.

Expected result: a complete, internally consistent equipment-loan file with no intentionally planted fraud signals.
"""
    (OUT / "README.md").write_text(readme)

    archive = OUT.parent / "cedar_loom_demo_package.zip"
    with zipfile.ZipFile(archive, "w", zipfile.ZIP_DEFLATED) as bundle:
        for item in sorted(OUT.iterdir()):
            bundle.write(item, arcname=f"cedar_loom_demo_package/{item.name}")
    print(f"Generated {len(list(OUT.glob('*.pdf')))} PDFs in {OUT}")
    print(f"Archive: {archive}")


if __name__ == "__main__":
    main()
