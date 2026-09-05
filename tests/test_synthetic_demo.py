"""End-to-end regression on the synthetic demo round.

This is the repo's proof that the whole pipeline — drawing-set ingest, SCWEP ingest,
billing parse, report segmentation, matching, compliance, basis gate, verdict — still
produces the six designed outcomes on nothing but synthetic documents and canned LLM
fixtures. If any stage drifts, this fails before a human notices a wrong verdict.

Mirrors app/main.py `review` call-for-call. Runs fully offline: NDT_HCX_MOCK=1,
temp DATA_DIR, documents generated into tmp_path.
"""
from __future__ import annotations

import importlib
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SYN = ROOT / "samples" / "synthetic"


@pytest.fixture()
def demo_env(tmp_path, monkeypatch, fresh_db):
    monkeypatch.setenv("NDT_HCX_MOCK", "1")
    m, r = fresh_db
    sys.path.insert(0, str(SYN))
    import make_synthetic as ms
    importlib.reload(ms)
    out = tmp_path / "synthetic"
    out.mkdir()
    monkeypatch.setattr(ms, "HERE", out)
    ms.main()
    return m, r, out


def test_six_designed_outcomes(demo_env):
    m, r, out = demo_env
    from sqlalchemy import select
    from app.analyzers import pipeline
    from app.extractors.drawing.requirements_extractor import ingest_folder
    from app.extractors import scwep_parser
    from app.extractors.excel_parser import parse_billing_xlsx
    from app.extractors.report_segmenter import normalize_and_ingest_segments, segment

    # 1) drawings + SCWEP
    ingest_folder(out / "drawings", as_of=date(2026, 9, 1))
    scwep_parser.ingest(out / "scwep" / "MD-SCWEP-P1-007.pdf")

    # 2) billing round (as main.review does)
    billing = out / "billing" / "round1_CP-P1.xlsx"
    reports = out / "reports" / "round1_reports.pdf"
    parsed = parse_billing_xlsx(billing, discipline_hint="CP-P1")
    with m.get_session() as s:
        br = r.create_billing_round(s, round_no=1, discipline="CP-P1", billing_date=date(2026, 9, 3),
                                    billing_xlsx_path=str(billing), reports_pdf_path=str(reports))
        s.flush()
        assert r.add_billing_items(s, br.id, parsed.rows) == 6
        s.commit(); round_id = br.id

    # 3) reports
    segments = segment(reports)
    assert len(segments) == 6
    with m.get_session() as s:
        b = s.get(m.BillingRound, round_id)
        meta = {"id": b.id, "round_no": b.round_no, "discipline": b.discipline, "billing_date": b.billing_date.isoformat()}
        assert normalize_and_ingest_segments(reports, segments, billing_round_meta=meta, session=s) == 6
        s.commit()

    # 4) review
    stats = pipeline.run(round_id)
    assert stats["processed"] == 6
    assert stats["basis_states"] == {"covered": 2, "unclear": 0, "not_submitted": 0, "no_basis_found": 1}
    assert stats["top_covering_doc"] == {"doc": "MD-SCWEP-P1-007", "rows": 2}

    with m.get_session() as s:
        items = {i.id: i.report_no for i in s.scalars(select(m.BillingItem))}
        got = {}
        for f in s.scalars(select(m.Finding)):
            rules = {x["rule"] for x in (f.citations_json or {}).get("findings", [])}
            got[items[f.billing_item_id]] = (f.verdict, rules)

    assert got["12-001RT"][0] == "OK"
    assert got["12-002UT"][0] == "OK"
    assert got["12-003PT"] == ("SUSPECT", {"billed_ndt_covered_by_scwep", "required_ndt_missing"})
    assert got["12-004MT"] == ("SUSPECT", {"billed_ndt_covered_by_scwep", "required_ndt_missing"})
    assert got["12-005VT"] == ("NONCOMPLIANT", {"billed_ndt_not_in_requirements", "required_ndt_missing"})
    assert got["12-006RT"] == ("NONCOMPLIANT", {"result_mismatch", "required_ndt_missing"})
    # the gate never lets an absent/low-confidence basis reach NONCOMPLIANT
    assert "billed_ndt_basis_not_submitted" not in got["12-005VT"][1]
