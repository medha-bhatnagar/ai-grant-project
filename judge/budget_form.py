"""Read a budget spreadsheet and fill out the NIH PHS 398 Modular Budget form.

    python judge/budget_form.py data/budgets/MyBudget.xlsx

It works on any spreadsheet made by budget_sheet.py, also after someone changed it in Excel: the
formulas are calculated here (pycel), so it never relies on numbers Excel may or may not have saved.
The result is one PDF: a filled form page per budget year, then a Personnel Justification page.
The template is forms/phs398_modular_budget.pdf (the official form, printed blank fields).
"""
import io
import re
import sys
from datetime import date, timedelta
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE / "forms").is_dir() else HERE.parent
TEMPLATE = ROOT / "forms" / "phs398_modular_budget.pdf"


class FormError(ValueError):
    pass


# ---------------------------------------------------------------- 1. read the spreadsheet

def read_budget(xlsx):
    """The numbers the form needs, calculated from the spreadsheet's formulas."""
    from openpyxl import load_workbook
    from pycel import ExcelCompiler

    ws = load_workbook(xlsx)["Budget"]
    calc = ExcelCompiler(filename=str(xlsx))
    value = lambda ref: calc.evaluate(f"Budget!{ref}")
    labels = {}
    for r in range(1, ws.max_row + 1):
        text = ws.cell(row=r, column=1).value
        if isinstance(text, str):
            labels.setdefault(text.strip(), r)

    def row_of(start):
        for text, r in labels.items():
            if text.startswith(start):
                return r
        return None

    head = row_of("BUDGET (US $)")
    if not head:
        raise FormError("This doesn't look like a budget spreadsheet from the Grant Assistant (no BUDGET table).")
    years = [ws.cell(row=head, column=c).column_letter for c in range(2, ws.max_column + 1)
             if str(ws.cell(row=head, column=c).value or "").startswith("Year ")]
    modular_row = row_of("Modular direct costs")
    if not modular_row:
        raise FormError("This is a detailed budget (over $250,000 a year), so it uses the R&R detailed budget "
                        "form, not the PHS 398 Modular Budget.")
    rate_row = row_of("Indirect (F&A) rate")
    start_row = row_of("Project start date")
    start = str(value(f"B{start_row}")) if start_row else "07/01/2027"
    m = re.fullmatch(r"\s*(\d{1,2})/(\d{1,2})/(\d{4})\s*", start)
    if not m:
        raise FormError(f"The project start date {start!r} isn't MM/DD/YYYY.")
    start = date(int(m[3]), int(m[1]), int(m[2]))

    per_year = []
    for y, c in enumerate(years):
        first = start.replace(year=start.year + y)
        per_year.append({"period": y + 1, "start": first, "end": first.replace(year=first.year + 1) - timedelta(days=1),
                         "direct": float(value(f"{c}{modular_row}")),
                         "fa_base": float(value(f"{c}{row_of('F&A base')}")),
                         "fa": float(value(f"{c}{row_of('Indirect (F&A) costs')}")),
                         "total": float(value(f"{c}{row_of('TOTAL DIRECT + INDIRECT')}"))})
    people = []
    for text, r in labels.items():
        hit = re.fullmatch(r"(.+) \((.+)\): calendar months per year", text)
        if hit:
            people.append({"name": hit[1], "role": hit[2], "months": float(value(f"B{r}"))})
    return {"years": per_year, "fa_rate": float(value(f"B{rate_row}")), "people": people,
            "title": str(ws["A1"].value or "Budget")}


# ---------------------------------------------------------------- 2. fill the form

def _page(year, totals, fa_rate):
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    money = lambda v: f"{v:,.2f}"

    def white(x0, y0, x1, y1):
        c.setFillColorRGB(1, 1, 1)
        c.rect(x0, y0, x1 - x0, y1 - y0, stroke=0, fill=1)
        c.setFillColorRGB(0, 0, 0)

    def amount(x_right, base, v, cover=None):
        if cover:
            white(*cover)
        c.setFont("Courier", 8)
        c.drawRightString(x_right, base, money(v))

    def words(x, base, text, size=8):
        c.setFont("Helvetica", size)
        c.drawString(x, base, text)

    # Budget period and dates (the template has Year 1's printed in, so cover them)
    digit_x = 253 + c.stringWidth("Budget Period: ", "Helvetica-Bold", 10)
    white(digit_x - 1, 714.5, digit_x + 14, 727)
    c.setFont("Helvetica-Bold", 10)
    c.drawString(digit_x, 717, str(year["period"]))
    white(184.3, 692.6, 249.9, 707.7)      # inside the date boxes only (edges measured from the form)
    white(318.5, 692.6, 384.2, 707.7)
    fmt = lambda d: f"{d.month}/{d.day}/{d.year}"
    words(186, 696.5, fmt(year["start"]), 12)
    words(320.5, 696.5, fmt(year["end"]), 12)
    # A. Direct costs
    amount(573, 664, year["direct"], (486, 661, 576, 672))
    amount(573, 632, year["direct"], (486, 629, 576, 640))
    # B. Indirect costs
    words(46, 582, "MTDC: Modified Total Direct Costs (on-campus research)")
    c.setFont("Courier", 8)
    c.drawCentredString(375, 586, f"{fa_rate * 100:.1f}")
    amount(478, 586, year["fa_base"])
    amount(573, 586, year["fa"])
    words(287, 533, "Department of Health and Human Services (DHHS),")
    words(287, 523, "Cost Allocation Services")
    words(287, 513, "POC name and phone: from UConn Health SPS")
    amount(573, 479, year["fa"])
    # C. Total
    amount(573, 449, year["total"], (486, 446, 576, 456))
    # Cumulative, entire project period
    amount(489, 327, totals["direct"], (387, 324, 492, 334))
    amount(489, 288, totals["direct"], (387, 285, 492, 295))
    amount(489, 268, totals["fa"])
    amount(489, 248, totals["total"], (387, 245, 492, 255))
    words(193, 185, "Personnel Justification (last page)")
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def _justification(people, years):
    from pypdf import PdfReader
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.units import inch
    from reportlab.pdfgen import canvas

    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    w, h = letter
    y = h - inch
    c.setFont("Helvetica-Bold", 12)
    c.drawString(inch, y, "Personnel Justification")
    y -= 24
    jobs = {"PI": "directs the project, oversees the design and analysis, supervises project staff and leads reporting",
            "Co-Investigator": "shares scientific leadership of the aims in their area of expertise",
            "Postdoc": "carries out the day-to-day research: experiments, data collection and analysis, and publications",
            "Graduate Student": "conducts research under supervision as part of their training",
            "Research Staff": "supports the research with technical and laboratory work"}
    for p in people:
        months = f"{p['months']:g} calendar month{'s' if p['months'] != 1 else ''} per year"
        body = (f"The {p['role']} ({months}) {jobs.get(p['role'], 'contributes to the project')}. "
                f"Effort is the same in each of the {years} budget years.")
        c.setFont("Helvetica-Bold", 10)
        c.drawString(inch, y, f"{p['name']}: {p['role']}")
        y -= 14
        c.setFont("Helvetica", 10)
        line = ""
        for word in body.split():
            if c.stringWidth(f"{line} {word}", "Helvetica", 10) > w - 2 * inch:
                c.drawString(inch, y, line.strip())
                y, line = y - 13, ""
            line += " " + word
        c.drawString(inch, y, line.strip())
        y -= 22
    c.setFont("Helvetica-Oblique", 9)
    c.drawString(inch, y, "Salaries, fringe (UConn Health research rate) and F&A follow the attached budget spreadsheet.")
    c.save()
    buf.seek(0)
    return PdfReader(buf).pages[0]


def fill_form(budget, out_pdf):
    from pypdf import PdfReader, PdfWriter

    years = budget["years"]
    totals = {k: sum(y[k] for y in years) for k in ("direct", "fa", "total")}
    writer = PdfWriter()
    for year in years:
        page = PdfReader(TEMPLATE).pages[0]
        page.merge_page(_page(year, totals, budget["fa_rate"]))
        writer.add_page(page)
    writer.add_page(_justification(budget["people"], len(years)))
    out_pdf = Path(out_pdf)
    out_pdf.parent.mkdir(parents=True, exist_ok=True)
    with out_pdf.open("wb") as f:
        writer.write(f)
    return out_pdf


def make_form(xlsx):
    """Spreadsheet in, filled form out (next to it, same name + _PHS398.pdf). Returns a summary."""
    xlsx = Path(xlsx)
    budget = read_budget(xlsx)
    out = fill_form(budget, xlsx.with_name(xlsx.stem + "_PHS398.pdf"))
    return {"file": out.name, "pages": len(budget["years"]) + 1,
            "per_year": [{"period": y["period"], "dates": f"{y['start']:%m/%d/%Y} - {y['end']:%m/%d/%Y}",
                          "direct": y["direct"], "fa": y["fa"], "total": y["total"]} for y in budget["years"]],
            "cumulative_total": sum(y["total"] for y in budget["years"])}


if __name__ == "__main__":
    import json
    if len(sys.argv) != 2:
        raise SystemExit(__doc__)
    print(json.dumps(make_form(sys.argv[1]), indent=2, default=str))
