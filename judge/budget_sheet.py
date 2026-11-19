"""Budget spreadsheets from a plain-English request.

    python judge/budget_sheet.py "1 month of my $100k salary and a full-time postdoc at level 1, R21"
    python judge/budget_sheet.py --from-json my_budget.json        (no AI: build from a JSON file)

1. The AI reads agents/prompts/budget_spreadsheet.json (the rules) plus the request and replies
   with one JSON object of budget data.
2. check() tests that JSON against the rules. compute() works out every number again in Python.
3. build_xlsx() writes the spreadsheet with FORMULAS (not the AI's numbers), so it recalculates
   when someone changes an input. The AI's totals are only used to catch mistakes.
"""
import json
import math
import re
import sys
from datetime import datetime
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE if (HERE / "agents").is_dir() else HERE.parent
PROMPT_FILE = ROOT / "agents" / "prompts" / "budget_spreadsheet.json"
OUT_DIR = ROOT / "data" / "budgets"
# NIH NRSA postdoc stipends FY2026 (NOT-OD-26-044), levels 0-7. Same table as the prompt file.
NRSA = [63480, 63900, 64380, 66948, 69180, 71748, 74424, 77076]
ROLES = ("PI", "Co-Investigator", "Postdoc", "Graduate Student", "Research Staff", "Other")


class BudgetError(ValueError):
    pass


# ---------------------------------------------------------------- 1. the AI step

def load_prompt():
    return json.loads(PROMPT_FILE.read_text(encoding="utf-8"))


def ask_ai(request, model=None):
    """Send the prompt file's rules + the request to the AI; return its JSON as a dict."""
    import judge  # the LLM calls live in judge.py
    p = load_prompt()
    model = model or judge.load_agents()["judge"]["model"]
    system = ("\n".join(p["instructions"])
              + "\n\nOUTPUT FORMAT (reply with exactly this shape, filled in):\n" + json.dumps(p["output_format"]["schema"], indent=1)
              + "\n\nEXAMPLE REQUEST:\n" + p["example"]["request"]
              + "\nEXAMPLE REPLY:\n" + json.dumps(p["example"]["response"]))
    text = judge.chat_text(model, system, f"REQUEST: {request}\n\nReply with only the JSON object.")
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text.strip())
    try:
        return json.loads(text[text.index("{"): text.rindex("}") + 1])
    except ValueError:
        raise BudgetError(f"The AI didn't reply with budget JSON: {text[:200]}")


# ---------------------------------------------------------------- 2. check and compute

def compute(d):
    """Every number in the budget, worked out from the inputs (the same math as the formulas)."""
    n, r = d["project"]["years"], d["rates"]
    people = []
    for p in d["personnel"]:
        sal = []
        for y in range(n):
            if p["pay_basis"] == "nrsa":
                full = NRSA[min(int(p["nrsa_start_level"]) + y, 7)]
            else:
                full = min(p["base_salary"] * (1 + r["annual_increase"]) ** y, r["salary_cap"])
            sal.append(full * p["calendar_months"] / 12)
        people.append({"salary": sal, "fringe": [s * p["fringe_rate"] for s in sal]})
    other = [sum(c["amount"][y] for c in d.get("other_direct_costs", [])) for y in range(n)]
    equip = [sum(c["amount"][y] for c in d.get("other_direct_costs", []) if c["category"] == "Equipment") for y in range(n)]
    salaries = [sum(p["salary"][y] for p in people) for y in range(n)]
    fringe = [sum(p["fringe"][y] for p in people) for y in range(n)]
    direct = [salaries[y] + fringe[y] + other[y] for y in range(n)]
    modular = d["project"]["budget_type"] == "modular"
    requested = [math.ceil(direct[y] / 25000 - 1e-9) * 25000 if modular else direct[y] for y in range(n)]
    fa_base = [requested[y] - equip[y] for y in range(n)]
    fa = [b * r["fa_rate"] for b in fa_base]
    return {"people": people, "salaries": salaries, "fringe": fringe, "other_direct": other, "total_direct": direct,
            "modular_direct": requested if modular else None, "fa_base": fa_base, "fa": fa,
            "total": [requested[y] + fa[y] for y in range(n)]}


def check(d):
    """Problems that stop the spreadsheet (list of strings), and warnings that don't."""
    problems, warnings = [], []
    try:
        n = int(d["project"]["years"])
        r = d["rates"]
        assert d["project"]["budget_type"] in ("modular", "detailed")
        for k in ("salary_cap", "annual_increase", "fa_rate"):
            float(r[k])
    except (KeyError, TypeError, ValueError, AssertionError) as e:
        return [f"the project or rates section is missing or wrong ({e})"], []
    if not 1 <= n <= 10:
        problems.append(f"years must be 1 to 10, not {n}")
    if not d.get("personnel"):
        problems.append("there is no one in personnel")
    for p in d.get("personnel", []):
        who = p.get("name", "?")
        if p.get("role") not in ROLES:
            problems.append(f"{who}: role must be one of {', '.join(ROLES)}")
        if not 0 <= float(p.get("calendar_months", -1)) <= 12:
            problems.append(f"{who}: calendar months must be 0 to 12")
        if not 0 <= float(p.get("fringe_rate", -1)) < 1:
            problems.append(f"{who}: fringe rate must be a fraction like 0.275")
        if p.get("pay_basis") == "nrsa":
            if p.get("nrsa_start_level") not in range(8):
                problems.append(f"{who}: NRSA level must be 0 to 7")
        elif p.get("pay_basis") == "salary":
            if not isinstance(p.get("base_salary"), (int, float)) or p["base_salary"] <= 0:
                problems.append(f"{who}: base salary is missing")
        else:
            problems.append(f"{who}: pay_basis must be 'salary' or 'nrsa'")
    for c in d.get("other_direct_costs", []):
        if len(c.get("amount", [])) != n:
            problems.append(f"{c.get('description', 'other cost')}: needs one amount per year")
    if problems:
        return problems, warnings
    mine = compute(d)
    ai = d.get("totals") or {}
    for key in ("total_direct", "fa", "total"):
        theirs = ai.get(key) or []
        for y in range(n):
            if y >= len(theirs) or theirs[y] is None or abs(theirs[y] - mine[key][y]) > 1:
                warnings.append(f"The AI's {key.replace('_', ' ')} for Year {y + 1} "
                                f"({theirs[y] if y < len(theirs) else 'missing'}) differed from the rules "
                                f"({mine[key][y]:,.2f}); the spreadsheet uses the correct formula.")
                break
    return problems, warnings


# ---------------------------------------------------------------- 3. the spreadsheet

def build_xlsx(d, path):
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
    from openpyxl.utils import get_column_letter as col

    n, r = d["project"]["years"], d["rates"]
    modular = d["project"]["budget_type"] == "modular"
    wb = Workbook()
    ws = wb.active
    ws.title = "Budget"
    F, BLUE = "Arial", "0000FF"
    YELLOW = PatternFill("solid", fgColor="FFFF00")
    HEAD = PatternFill("solid", fgColor="1F4E8C")
    SUB = PatternFill("solid", fgColor="DCE6F1")
    BOX = Border(*(Side(style="thin", color="BFBFBF"),) * 4)
    MONEY, PCT = '$#,##0;($#,##0);"-"', '0.0%;(0.0%);"-"'

    def put(ref, value, *, bold=False, color="000000", fill=None, fmt=None, size=10, italic=False):
        c = ws[ref]
        c.value = value
        c.font = Font(name=F, bold=bold, color=color, size=size, italic=italic)
        if fill:
            c.fill = fill
        if fmt:
            c.number_format = fmt
        return c

    def header(row, labels):
        for j, h in enumerate(labels):
            c = ws.cell(row=row, column=1 + j, value=h)
            c.font = Font(name=F, bold=True, color="FFFFFF")
            c.fill = HEAD

    title = d["project"].get("title") or "Personnel budget"
    put("A1", f"{title} ({d['project'].get('mechanism', '')}, {n} years, {d['project']['budget_type']})", bold=True, size=13)
    put("A2", "Yellow cells with blue numbers are inputs you can change. Black numbers are formulas. US dollars per budget year. "
              "Made by the Grant Assistant from agents/prompts/budget_spreadsheet.json.", italic=True, color="595959")

    # INPUTS
    row = 4
    header(row, ["INPUTS", "Value", "Notes"])
    ref = {}

    def inp(key, label, value, fmt, note=""):
        nonlocal row
        row += 1
        put(f"A{row}", label)
        put(f"B{row}", value, color=BLUE, fill=YELLOW, fmt=fmt)
        put(f"C{row}", note, color="595959")
        ref[key] = f"$B${row}"

    inp("start", "Project start date (MM/DD/YYYY)", d["project"].get("start_date") or "07/01/2027", "@",
        "Year 1 starts here; each budget year is 12 months")
    for i, p in enumerate(d["personnel"]):
        who = f"{p['name']} ({p['role']})"
        if p["pay_basis"] == "nrsa":
            inp(f"lvl{i}", f"{who}: NRSA level in Year 1", int(p["nrsa_start_level"]), "0", "Rises by 1 each year, up to 7")
        else:
            inp(f"base{i}", f"{who}: annual base salary", p["base_salary"], MONEY)
        inp(f"mo{i}", f"{who}: calendar months per year", p["calendar_months"], "0.00", "12 = full time")
        inp(f"fr{i}", f"{who}: fringe rate", p["fringe_rate"], PCT)
    inp("cap", "NIH salary cap", r["salary_cap"], MONEY, "FY2026 Executive Level II (NOT-OD-26-034)")
    inp("inc", "Annual salary increase", r["annual_increase"], PCT)
    inp("fa", f"Indirect (F&A) rate, {r.get('fa_base', 'MTDC')} base", r["fa_rate"], PCT)

    # NRSA table
    row += 2
    header(row, ["NRSA POSTDOC STIPENDS (FY2026)", "Annual stipend", "Source: NIH NOT-OD-26-044"])
    t0 = row + 1
    for lvl, s in enumerate(NRSA):
        row += 1
        put(f"A{row}", f"Level {lvl}")
        put(f"B{row}", s, color=BLUE, fmt=MONEY)
    table = f"$B${t0}:$B${row}"

    # BUDGET
    row += 2
    years = [col(2 + y) for y in range(n)]
    tot = col(2 + n)
    header(row, ["BUDGET (US $)"] + [f"Year {y + 1}" for y in range(n)] + [f"Total ({n} years)"])
    rows = {}

    def line(key, label, make, fmt=MONEY, total=True, bold=False, shade=False):
        nonlocal row
        row += 1
        rows[key] = row
        put(f"A{row}", label, bold=bold, fill=SUB if shade else None)
        for y, c in enumerate(years):
            cell = put(f"{c}{row}", make(c, y), fmt=fmt, bold=bold, fill=SUB if shade else None)
            cell.border = BOX
        cell = put(f"{tot}{row}", f"=SUM({years[0]}{row}:{years[-1]}{row})" if total else None, fmt=fmt,
                   bold=True, fill=SUB if shade else None)
        cell.border = BOX

    line("yr", "Budget year number", lambda c, y: y + 1, fmt="0", total=False)
    yr = rows["yr"]
    sal_rows, fr_rows = [], []
    for i, p in enumerate(d["personnel"]):
        who = f"{p['name']} ({p['role']})"
        if p["pay_basis"] == "nrsa":
            line(f"l{i}", f"{who}: NRSA level this year", lambda c, y: f"=MIN({ref[f'lvl{i}']}+{c}{yr}-1,7)", fmt="0", total=False)
            line(f"f{i}", f"{who}: stipend for that level", lambda c, y: f"=INDEX({table},{c}{rows[f'l{i}']}+1)", total=False)
        else:
            line(f"f{i}", f"{who}: salary used (lower of base and cap)",
                 lambda c, y: f"=MIN({ref[f'base{i}']}*(1+{ref['inc']})^({c}{yr}-1),{ref['cap']})", total=False)
        line(f"s{i}", f"{who}: salary requested", lambda c, y: f"={c}{rows[f'f{i}']}*{ref[f'mo{i}']}/12")
        line(f"b{i}", f"{who}: fringe benefits", lambda c, y: f"={c}{rows[f's{i}']}*{ref[f'fr{i}']}")
        sal_rows.append(rows[f"s{i}"])
        fr_rows.append(rows[f"b{i}"])
    other_rows, equip_rows = [], []
    for k, c in enumerate(d.get("other_direct_costs", [])):
        line(f"o{k}", f"{c['category']}: {c.get('description', '')}", lambda cl, y: c["amount"][y])
        for cl in years:
            ws[f"{cl}{row}"].font = Font(name=F, color=BLUE)
        other_rows.append(row)
        if c["category"] == "Equipment":
            equip_rows.append(row)
    plus = lambda c, rs: "+".join(f"{c}{x}" for x in rs) or "0"
    line("sal", "Total salaries", lambda c, y: f"={plus(c, sal_rows)}", bold=True)
    line("frg", "Total fringe benefits", lambda c, y: f"={plus(c, fr_rows)}", bold=True)
    line("oth", "Total other direct costs", lambda c, y: f"={plus(c, other_rows)}", bold=True)
    line("dir", "TOTAL DIRECT COSTS", lambda c, y: f"={c}{rows['sal']}+{c}{rows['frg']}+{c}{rows['oth']}", bold=True, shade=True)
    req = rows["dir"]
    if modular:
        line("mod", "Modular direct costs (rounded up to $25,000)", lambda c, y: f"=CEILING({c}{rows['dir']},25000)", bold=True)
        req = rows["mod"]
    line("base", "F&A base (requested direct costs minus equipment)", lambda c, y: f"={c}{req}-({plus(c, equip_rows)})")
    line("fac", "Indirect (F&A) costs", lambda c, y: f"={c}{rows['base']}*{ref['fa']}", bold=True)
    line("all", "TOTAL DIRECT + INDIRECT", lambda c, y: f"={c}{req}+{c}{rows['fac']}", bold=True, shade=True)

    # Notes
    row += 2
    put(f"A{row}", "Notes", bold=True)
    notes = list(d.get("assumptions", [])) + [f"Question: {q}" for q in d.get("questions", [])] + [
        "Confirm the fringe and F&A rates with SPS before submitting."]
    for i, t in enumerate(notes, start=1):
        put(f"A{row + i}", f"{i}. {t}", color="595959")

    ws.column_dimensions["A"].width = 58
    for j in range(2, 3 + n):
        ws.column_dimensions[col(j)].width = 15
    ws.freeze_panes = "A4"
    for rr in range(5, ws.max_row + 1):
        ws[f"A{rr}"].alignment = Alignment(vertical="center")
    path.parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return path


# ---------------------------------------------------------------- all together

def make_budget(request=None, data=None, out_dir=OUT_DIR):
    """Request (AI) or data (a JSON dict) -> checked spreadsheet. Returns a summary dict."""
    d = data if data is not None else ask_ai(request)
    problems, warnings = check(d)
    if problems:
        raise BudgetError("The budget data has problems: " + "; ".join(problems))
    slug = re.sub(r"[^A-Za-z0-9]+", "_", d["project"].get("title") or "budget").strip("_")[:50] or "budget"
    path = build_xlsx(d, Path(out_dir) / f"{slug}_{datetime.now():%Y%m%d_%H%M%S}.xlsx")
    m = compute(d)
    n = d["project"]["years"]
    return {"file": path.name, "path": str(path), "years": n, "budget_type": d["project"]["budget_type"],
            "people": [f"{p['name']} ({p['role']}, {p['calendar_months']} calendar months)" for p in d["personnel"]],
            "per_year": [{"year": y + 1, "total_direct": round(m["total_direct"][y], 2),
                          "requested_direct": round((m["modular_direct"] or m["total_direct"])[y], 2),
                          "fa": round(m["fa"][y], 2), "total": round(m["total"][y], 2)} for y in range(n)],
            "grand_total": round(sum(m["total"]), 2), "assumptions": d.get("assumptions", []),
            "questions": d.get("questions", []), "warnings": warnings}


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    if len(sys.argv) == 3 and sys.argv[1] == "--from-json":
        result = make_budget(data=json.loads(Path(sys.argv[2]).read_text(encoding="utf-8")))
    elif len(sys.argv) == 2:
        result = make_budget(request=sys.argv[1])
    else:
        raise SystemExit(__doc__)
    print(json.dumps(result, indent=2))
