"""Generate a fully synthetic demo round — nothing here is a real document.

Produces, under samples/synthetic/:
  drawings/  MD.D.P000.1.0KBA10&&&&.052.{DC,SD,BG}.0001.E.pdf   (one drawing set, 3 sheets)
  scwep/     MD-SCWEP-P1-007.pdf                                  (site procedure with a conditional PT clause)
  reports/   round1_reports.pdf                                   (6 one-page inspection reports, NIS form)
  billing/   round1_CP-P1.xlsx                                    (billing sheet the contractor would submit)

The six billing rows are chosen to walk every verdict path once:
  12-001 RT  in drawing, report ACC               -> OK
  12-002 UT  in drawing, report ACC               -> OK
  12-003 PT  not in drawing; SCWEP: lug removal   -> SUSPECT, basis covered (cite SCWEP)
  12-004 MT  not in drawing; SCWEP: repair weld   -> SUSPECT, basis covered
  12-005 VT  not in drawing; SCWEP silent         -> NONCOMPLIANT via basis gate (no_basis_found)
  12-006 RT  billing says REJ, report says ACC    -> NONCOMPLIANT via hard rule (result_mismatch)
Run with NDT_HCX_MOCK=1 so every LLM stage returns the canned fixtures in tests/fixtures/hcx_mock.json.
"""
from __future__ import annotations

import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from pdfmini import write_pdf  # noqa: E402

DRAWING_BODY = "MD.D.P000.1.0KBA10&&&&.052"
DRAWING_FULL = f"{DRAWING_BODY}.DC.0001.E"
DATE = "03.09.2026"
ROUND_DATE = "2026-09-03"

# report_no suffix == NDT method, exactly as the inspector numbers its reports ("12-001RT"),
# so the deterministic matcher (report_no equality after normalisation) links each billing row
# to its own report page. WELDER matches the canned ocr_normalize fixture (DW001).
WELDER = "DW001"
ROWS = [  # (report_no, method, billing_result, note)
    ("12-001RT", "RT", "A", "in drawing"),
    ("12-002UT", "UT", "A", "in drawing"),
    ("12-003PT", "PT", "A", "lug removal -> SCWEP conditional"),
    ("12-004MT", "MT", "A", "repair weld -> SCWEP conditional"),
    ("12-005VT", "VT", "A", "SCWEP silent -> over-billing"),
    ("12-006RT", "RT", "R", "billing REJ vs report ACC"),
]


def drawings(out: Path) -> None:
    for typ, title, body in [
        ("DC", "DESIGN CRITERIA", ["Material: P-1 carbon steel  Class 1", "Design pressure 158 bar  Design temp 350 C",
                                   "Applicable code: ASME Sec.III Div.1 NB"]),
        ("SD", "SPECIFICATION DRAWING", ["Joint FW12  BW  WPS-001  t=25 mm  Line L-001",
                                         "Required NDT: RT 100%  UT 100%", "Acceptance: ASME Sec.VIII Div.1 UW-51 / Sec.V Art.5"]),
        ("BG", "BILL OF GOODS", ["Item 1  Pipe 219.1 x 8.18  P-1  6 m", "Item 2  Elbow 90deg LR  P-1  2 ea"]),
    ]:
        write_pdf(out / f"{DRAWING_BODY}.{typ}.0001.E.pdf", [[
            "MERIDIAN NPP  (synthetic demo drawing - not a real document)",
            f"Drawing No.: {DRAWING_BODY}.{typ}.0001.E     Rev. -", title, "", *body]])


def scwep(out: Path) -> None:
    write_pdf(out / "MD-SCWEP-P1-007.pdf", [[
        "MERIDIAN NPP  SITE CONSTRUCTION & WELDING EXAMINATION PROCEDURE  (synthetic)",
        "Doc No. MD-SCWEP-P1-007  Rev. B     Scope: CP-P1 piping, systems JEC/KBA, carbon steel", "",
        "4. Visual examination", "All welds shall be visually examined (VT) upon completion.", "",
        "5. Radiographic examination", "Class 2 girth welds shall be RT 10% minimum.", "",
        "12. Temporary attachments",
        "After removal of temporary lugs and attachments, the affected area shall be examined by PT.", "",
        "13. Repair welds", "Repair welds shall be re-examined by MT after completion.",
    ]])


def reports(out: Path) -> None:
    pages = []
    for rno, method, _res, _note in ROWS:
        pages.append([
            "EUNIS LLC  -  Construction Laboratory        (synthetic demo report)",
            "Conclusion on Welded Joints Examination",
            f"No. {rno[:-2]} {method} dated {DATE} 10UMA",
            f"Customer: Meridian Contractor     Drawing: {DRAWING_FULL}",
            "KKS code: 10KBA10BR001",
            "Joint      Welder     Result", f"FW12      {WELDER}   ACC",
            "Inspector: A. Demo    Approver: B. Demo",
        ])
    write_pdf(out / "round1_reports.pdf", pages)


def billing(out: Path) -> None:
    import openpyxl
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "CP-P1"
    ws.append(["Meridian NPP - NDT Inspection Service Detail List (synthetic)"])
    ws.append(["No", "Unit", "BLDG", "Application No", "Report Number", "Date of Testing", "Type",
               "Result", "Welder ID", "Confirmation No.", "Section", "Dimension", "Drawing No."])
    for i, (rno, method, res, _note) in enumerate(ROWS, start=1):
        ws.append([i, "1", "10UMA", f"APP-2026-{i:03d}", rno, ROUND_DATE, method, res, WELDER, "FW12",
                   "S-1", "219.1x8.18", DRAWING_FULL])
    out.mkdir(parents=True, exist_ok=True)
    wb.save(out / "round1_CP-P1.xlsx")


def main() -> None:
    base = HERE
    drawings(base / "drawings"); scwep(base / "scwep"); reports(base / "reports"); billing(base / "billing")
    n = sum(1 for _ in base.rglob("*") if _.is_file() and _.suffix in (".pdf", ".xlsx"))
    print(f"synthetic documents written under {base}: {n} files")


if __name__ == "__main__":
    main()
