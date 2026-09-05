"""CLI 진입점.

Examples:
  python -m app.main ingest-drawings samples/drawings/
  python -m app.main ingest-standards samples/scwep/ --type scwep
  python -m app.main ingest-standards samples/codes_standards/ --type code
  python -m app.main ingest-standards samples/contracts/ --type contract
  python -m app.main review \\
      --billing samples/billing/CP-M1_2차청구.xlsx \\
      --reports samples/reports/CP-M1_2차_성적서.pdf \\
      --round 2 --date 2026-06-30 --discipline CP-M1
  python -m app.main dashboard
"""
from __future__ import annotations

import logging
import subprocess
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Optional

import typer

from app.config import PROJECT_ROOT
from app.database.models import get_session, init_db
from app.database.repository import (
    add_billing_items,
    create_billing_round,
)
from app.logging_setup import configure_logging

app = typer.Typer(add_completion=False, no_args_is_help=True)


@app.command("ingest-drawings")
def ingest_drawings(
    folder: Path = typer.Argument(..., help="DC/SD/BG PDF 들이 있는 폴더"),
    as_of: Optional[str] = typer.Option(None, help="유효시작일 YYYY-MM-DD (기본 오늘)"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """도면 폴더를 적재. 3파일 세트 자동 그룹화 + 통합 요구사항 생성."""
    from app.extractors.drawing.requirements_extractor import ingest_folder

    from app import progress_writer as pw
    run_id = configure_logging(stage="ingest-drawings", verbose=verbose)
    pw.reset(); pw.step("ingest-drawings", "시작", folder.name)
    init_db()
    eff = date.fromisoformat(as_of) if as_of else date.today()
    try:
        stats = ingest_folder(folder, as_of=eff)
    except Exception as e:
        pw.error("ingest-drawings", f"{type(e).__name__}: {e}")
        raise
    pw.step("ingest-drawings", "완료")
    for k, v in stats.items():
        pw.kpi(k, v)
    typer.echo(f"✓ Ingested: {stats}")
    typer.echo(f"  run_id={run_id}  (로그: data/logs/..., 진행 요약: data/progress.log)")


@app.command("search-standards")
def search_standards(
    question: str = typer.Argument(..., help="한국어 질문 (예: 철근 용접부 VT 는 배치 표본검사인가)"),
    top_k: int = typer.Option(5, "--top-k", "-k"),
    bm25_only: bool = typer.Option(False, "--bm25-only", help="임베딩 없이 어휘 검색만"),
):
    """적재된 규격 문서에서 질문과 가장 가까운 조항을 보여준다 (검색 품질 확인용).

    임베딩(bge-m3)이 되면 source=hybrid, 안 되면 bm25 로 표시된다.
    """
    configure_logging(stage="search-standards")
    from app.extractors import code_indexer
    hits = code_indexer.search(question, top_k=top_k, hybrid=not bm25_only)
    if not hits:
        typer.echo("결과 없음 — 규격 문서가 적재되어 있는지(run_ingest_standards.bat) 확인하세요.")
        raise typer.Exit(1)
    typer.echo(f"질문: {question}")
    typer.echo(f"검색 방식: {hits[0]['source']}   (hybrid = BM25 + bge-m3 임베딩)\n")
    for i, h in enumerate(hits, 1):
        snippet = " ".join(h["text"].split())[:220]
        cos = f"  cos={h['cosine']:.3f}" if h.get("cosine") is not None else ""
        # 표 전사 청크는 신뢰도를 같이 보여준다. [재확인] 이 붙으면 원문 표를 사람이 봐야 한다.
        tag = ""
        if h.get("chunk_source") == "vlm_table":
            conf = h.get("confidence")
            conf_s = f"conf={conf:.2f}" if isinstance(conf, (int, float)) else "conf=?"
            tag = f"  [재확인 {conf_s}]" if h.get("needs_review") else f"  [표 전사 {conf_s}]"
        typer.echo(f"{i}. {h['doc']}  p.{h['page']}   bm25={h['bm25']:.2f}{cos}{tag}")
        typer.echo(f"   {snippet}…\n")


@app.command("reindex-standards")
def reindex_standards(verbose: bool = typer.Option(False, "--verbose", "-v")):
    """이미 적재된 규격 문서에 bge-m3 임베딩을 (다시) 계산. PDF 재추출은 안 함."""
    configure_logging(stage="reindex-standards", verbose=verbose)
    from app.extractors import code_indexer
    from app import embeddings
    if not embeddings.available():
        typer.echo("임베딩 사용 불가 — config\\hcx.yaml 의 embedding.enabled 와 NDT_STUDIO_TOKEN 확인.")
        raise typer.Exit(1)
    res = code_indexer.reindex_embeddings()
    typer.echo(f"✓ 임베딩 완료 {len(res.get('embedded', []))}건")
    for d in res.get("embedded", []):
        typer.echo(f"  • {d}")
    if res.get("failed"):
        typer.echo(f"✗ 실패 {len(res['failed'])}건: {', '.join(res['failed'])}")
        raise typer.Exit(1)


@app.command("ingest-standards")
def ingest_standards(
    folder: Path = typer.Argument(..., help="기준문서 폴더"),
    doc_type: str = typer.Option(..., "--type", help="scwep | code | contract"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """기준 문서(SCWEP/Code/Contract) 폴더 적재."""
    configure_logging(stage=f"ingest-{doc_type}", verbose=verbose)
    init_db()
    folder = Path(folder)
    files = sorted([p for p in folder.rglob("*.pdf") if p.is_file()])
    if not files:
        typer.echo("적재할 PDF가 없습니다.")
        raise typer.Exit(1)

    if doc_type == "scwep":
        from app.extractors import scwep_parser as mod
    elif doc_type == "code":
        from app.extractors import code_indexer as mod
    elif doc_type == "contract":
        from app.extractors import contract_parser as mod
    else:
        typer.echo(f"알 수 없는 --type: {doc_type}")
        raise typer.Exit(1)

    total = {"pages": 0, "table_like": 0, "transcribed": 0, "needs_review": 0, "vlm_failed": 0}
    for f in files:
        typer.echo(f"  • {f.name}")
        res = mod.ingest(f)
        ts = (res or {}).get("table_stats") if isinstance(res, dict) else None
        if ts:
            # 표 파이프라인 결과를 파일마다 바로 보여준다 — VLM실패 > 0 이면 ⑥ 연결 점검부터.
            typer.echo(f"      표: {ts['pages']}쪽 중 전사 {ts['transcribed']}, 재확인 {ts['needs_review']}, "
                       f"VLM실패 {ts['vlm_failed']} (표 후보 {ts['table_like']})")
            for k in total:
                total[k] += ts.get(k, 0)
    typer.echo(f"✓ {len(files)}개 적재 완료")
    if total["pages"]:
        typer.echo(f"  표 합계: 전사 {total['transcribed']} / 재확인 {total['needs_review']} / VLM실패 {total['vlm_failed']}")
        if total["vlm_failed"]:
            typer.echo("  ! VLM실패가 있습니다 — 그 페이지는 OCR 텍스트로 적재됐습니다. ⑥ HCX 연결 점검 후 다시 적재하세요.")


@app.command("review")
def review(
    billing: Path = typer.Option(..., help="청구 엑셀 .xlsx"),
    reports: Path = typer.Option(..., help="청구회차 성적서 PDF"),
    round_no: int = typer.Option(..., "--round", help="청구회차 번호 (1,2,...)"),
    date_str: str = typer.Option(..., "--date", help="청구일 YYYY-MM-DD"),
    discipline: str = typer.Option(..., help="CP-M1 | CP-P1 | CP-E1 | CP-A1"),
    verbose: bool = typer.Option(False, "--verbose", "-v"),
):
    """청구회차 1건 검토 — 엑셀 파싱 → 성적서 분할/정규화 → 3자 매칭 → 검토 엑셀 생성."""
    from app.analyzers.pipeline import run as run_review
    from app.extractors.excel_parser import parse_billing_xlsx
    from app.extractors.report_segmenter import normalize_and_ingest_segments, segment
    from app.report.excel_writer import write as write_excel

    from app import progress_writer as pw
    run_id = configure_logging(stage=f"review-r{round_no}-{discipline}", verbose=verbose)
    pw.step("review", "시작", f"{discipline} {round_no}차 {date_str}")
    typer.echo(f"  run_id={run_id}  (로그: data/logs/..., 진행 요약: data/progress.log)")
    init_db()
    billing = Path(billing)
    reports = Path(reports)
    billing_date = date.fromisoformat(date_str)

    # 1) 청구 회차 메타 + 엑셀 적재 (+ 분야별 시트의 Detailed Drawing 으로 보강)
    from app.extractors.excel_parser import enrich_from_per_method_sheets

    parsed = parse_billing_xlsx(billing, discipline_hint=discipline)
    enrichment = enrich_from_per_method_sheets(billing, discipline=discipline)

    # Total 시트 행에 drawing_no / welding_map 채우기 (report_no 기준 join)
    enriched_count = 0
    for r in parsed.rows:
        rn = r.get("report_no")
        if not rn:
            continue
        extra = enrichment.by_report_no.get(rn)
        if not extra:
            continue
        if not r.get("drawing_no") and extra.get("drawing_no"):
            r["drawing_no"] = extra["drawing_no"]
            enriched_count += 1
        # welding_map 은 raw_json 에 보관 (DB 컬럼 없음)
        if extra.get("welding_map"):
            r["raw_json"]["welding_map"] = extra["welding_map"]

    with get_session() as s:
        br = create_billing_round(
            s,
            round_no=round_no,
            discipline=discipline,
            billing_date=billing_date,
            billing_xlsx_path=str(billing),
            reports_pdf_path=str(reports),
        )
        s.flush()
        n = add_billing_items(s, br.id, parsed.rows)
        s.commit()
        round_id = br.id
        typer.echo(f"✓ 청구 행 {n}건 적재 (sheet={parsed.sheet_name}, header_row={parsed.header_row})")
        typer.echo(
            f"✓ 분야별 시트 {len(enrichment.source_sheets)}개에서 drawing_no/welding_map 보강 "
            f"→ {enriched_count}행 (총 {len(enrichment.by_report_no)}개 매핑)"
        )
        if parsed.warnings:
            for w in parsed.warnings:
                typer.echo(f"  ! {w}")
        for w in enrichment.warnings[:5]:
            typer.echo(f"  ! [enrich] {w}")

    # 2) 성적서 분할 + 정규화 + 적재
    from app.database.models import BillingRound

    segments = segment(reports)
    avg_conf = sum(seg.segmentation_confidence for seg in segments) / max(len(segments), 1)
    typer.echo(f"✓ 성적서 {len(segments)}건 분할 (평균 confidence {avg_conf:.2f})")
    with get_session() as s:
        br_loaded = s.get(BillingRound, round_id)
        meta = {
            "id": br_loaded.id,
            "round_no": br_loaded.round_no,
            "discipline": br_loaded.discipline,
            "billing_date": br_loaded.billing_date.isoformat(),
        }
        saved = normalize_and_ingest_segments(reports, segments, billing_round_meta=meta, session=s)
        s.commit()
        typer.echo(f"✓ 성적서 {saved}건 정규화/적재")

    # 3) 매칭 + 적합성 판정
    stats = run_review(round_id)
    typer.echo(f"✓ 검토 완료: {stats}")

    # 4) 검토 엑셀 출력
    out = write_excel(round_id)
    typer.echo(f"✓ 검토 엑셀: {out}")

    # 5) 진행 요약
    from app.hcx_client import get_call_stats
    pw.kpi("청구 행", stats.get("items_total"))
    pw.kpi("성적서", stats.get("reports"))
    pw.kpi("HCX 호출", get_call_stats()["total"])
    pw.step("review", "완료", f"검토 엑셀: {out.name}")


@app.command("criteria-guide")
def criteria_guide(
    discipline: str = typer.Option(..., help="CP-M1 | CP-P1 | CP-E1 | CP-A1"),
    out_dir: Optional[Path] = typer.Option(None, help="출력 폴더 (기본 data/outputs/)"),
):
    """검사기준 가이드 생성 (검토자 핸드북, 시공사 협의 근거)."""
    from app.report.criteria_guide import generate

    configure_logging(stage=f"criteria-guide-{discipline}")
    stats = generate(discipline=discipline, out_dir=out_dir)
    typer.echo(f"✓ 가이드 생성 완료:")
    for k, v in stats.items():
        typer.echo(f"  {k}: {v}")


@app.command("ceb-crosscheck")
def ceb_crosscheck_cmd(
    reports_pdf: Path = typer.Option(..., help="NIS Civil 성적서 묶음 PDF"),
    ledger: Path = typer.Option(..., help="NIS 지급물량표 xlsx"),
    claims: Path = typer.Option(..., help="CEB Fabrication 청구 xlsm"),
    out: Optional[Path] = typer.Option(None, help="결과 JSON 경로 (기본 data/outputs/)"),
):
    """CEB 3자 수량 교차검증 — 성적서 × CEB 청구 × NIS 지급물량표.

    수량 사슬: 성적서(부재수) × CEB(부재당 용접점) = 물량표 VT = CEB 청구 VT.
    성적서 PDF 는 OCR 디스크 캐시 사용 (최초 실행 시 수십 분 소요 가능).
    """
    from datetime import date
    from app.analyzers.ceb_crosscheck import run_crosscheck
    from app.config import DATA_DIR

    configure_logging(stage="ceb-crosscheck")
    out = out or (DATA_DIR / "outputs" / f"ceb_crosscheck_{date.today().isoformat()}.json")
    res = run_crosscheck(reports_pdf, ledger, claims, out_json=out)

    typer.echo(f"\n══ CEB 3자 수량 교차검증 ══")
    typer.echo(f"  성적서 파싱: {res.n_reports}건 (NIS {res.n_nis}건)")
    typer.echo(f"  물량표 조인: {len(res.joined)}건")
    typer.echo(f"  수량 대사 일치:   {len(res.qty_match)}건")
    typer.echo(f"  ⚠ 수량 대사 불일치: {len(res.qty_mismatch)}건")
    typer.echo(f"  수량 대사 불가:   {len(res.qty_incalculable)}건 (unit 미확보·복수성적서 셀 등)")
    typer.echo(f"  ⚠ 미지급 후보(물량표 없음): {len(res.ledger_missing)}건")
    typer.echo(f"  ⚠ 부재수 초과(성적서>도면): {len(res.piece_overrun)}건")
    typer.echo(f"  {res.coverage_note}")
    typer.echo(f"  상세: {out}")
    if res.qty_mismatch:
        typer.echo("\n  [수량 불일치 상위 5]")
        for r in res.qty_mismatch[:5]:
            typer.echo(f"   - {r['report_no']}: 기대 {r['expected_points']:.0f} vs 물량표 {r['ledger_vt']} ({r['diff_pct']}%)")


@app.command("dashboard")
def dashboard(
    host: str = typer.Option("127.0.0.1"),
    port: int = typer.Option(8501),
):
    """Streamlit 대시보드 실행 (localhost:8501)."""
    script = PROJECT_ROOT / "app" / "dashboard" / "streamlit_app.py"
    cmd = [
        sys.executable, "-m", "streamlit", "run", str(script),
        "--server.address", host, "--server.port", str(port),
        "--server.headless", "true",
    ]
    subprocess.run(cmd, check=False)


@app.command("ocr-check")
def ocr_check():
    """OCR(tesseract) 이 잡히는지 확인. 표 전사가 여기에 걸려 있어 적재 전에 본다."""
    import os as _os
    import subprocess
    from pathlib import Path
    from app.extractors.ocr_engine import resolve_tesseract

    configure_logging(stage="ocr-check")
    cmd_path, tessdata = resolve_tesseract()

    if not cmd_path:
        typer.echo("✗ tesseract 를 찾지 못했습니다.")
        typer.echo("  찾는 순서: 환경변수 NDT_TESSERACT_CMD → installer\\tesseract\\ → PATH")
        typer.echo("  번들에 들어 있어야 정상입니다. installer\\tesseract\\tesseract.exe 가 있는지 보세요.")
        typer.echo("  없으면 표 전사와 스캔 문서 OCR 이 통째로 동작하지 않습니다.")
        raise typer.Exit(1)

    env_cmd = _os.environ.get("NDT_TESSERACT_CMD")
    if env_cmd and env_cmd != cmd_path:
        typer.echo(f"환경변수  : {env_cmd}")
        typer.echo("            ↑ 이 경로에는 파일이 없어 무시했습니다. 고장은 아닙니다.")
        typer.echo("            set_env.bat 의 NDT_TESSERACT_CMD 줄이 낡은 것이니 지워도 됩니다.")
    typer.echo(f"실행 파일 : {cmd_path}" + ("  (번들)" if "installer" in (cmd_path or "") else "  (PATH)"))
    typer.echo(f"언어 데이터: {tessdata or '(기본 위치)'}")
    try:
        out = subprocess.run([cmd_path, "--version"], capture_output=True, text=True, timeout=30)
        typer.echo(f"버전      : {(out.stdout or out.stderr).splitlines()[0].strip()}")
    except Exception as e:      # noqa: BLE001
        typer.echo(f"✗ 실행에 실패했습니다: {type(e).__name__}: {e}")
        raise typer.Exit(1)

    langs = sorted(p.stem for p in Path(tessdata).glob("*.traineddata")) if tessdata else []
    typer.echo(f"언어      : {', '.join(langs) if langs else '(확인 못 함)'}")
    if "eng" not in langs:
        typer.echo("! eng.traineddata 가 없습니다 — 표 분류가 동작하지 않습니다.")
        raise typer.Exit(1)
    typer.echo("\n✓ OCR 정상. 표 전사를 쓸 수 있습니다.")


@app.command("hcx-check")
def hcx_check():
    """HCX 연결 점검 — 사내 최초 1회용. 토큰→방화벽→실호출 순서로 어디서 막히는지 판정."""
    import os
    import httpx
    from app.config import hcx_config
    from app.hcx_client import _mock_enabled, call

    configure_logging(stage="hcx-check")
    cfg = hcx_config()
    api_cfg = cfg.get("api", {})
    base_url = api_cfg.get("base_url", "")
    token_env = api_cfg.get("token_env", "NDT_HCX_TOKEN")
    ok = lambda m: typer.echo(f"  ✓ {m}")
    bad = lambda m: typer.echo(f"  ✗ {m}")

    typer.echo("HCX 연결 점검 (사내 최초 1회 권장)")
    typer.echo(f"  · 접속 주소: {base_url}")

    # 0) mock 모드
    if _mock_enabled():
        typer.echo("  · 현재 모의(mock) 모드 — 실서버에 접속하지 않고 파이프라인만 점검합니다.")
        resp = call("hcx_check", {"ping": 1}, force_refresh=True)
        ok(f"모의 응답 수신: {(resp.content or '')[:60]}")
        typer.echo("판정: 사외/리허설 환경 정상. 사내 실연결 점검은 NDT_HCX_MOCK 변수를 지우고 다시 실행.")
        return

    # 1) 토큰
    if not os.environ.get(token_env, ""):
        bad(f"API Key 미등록 — 환경변수 {token_env} 이 비어 있습니다.")
        typer.echo("판정: 가이드 1절대로 Windows [사용자 환경변수] 에 키 등록 → 로그아웃/재로그인 → 재실행.")
        raise typer.Exit(1)
    ok(f"API Key 등록됨 ({token_env})")

    # 2) 네트워크 도달 (방화벽) — 어떤 HTTP 응답이든 오면 도달 성공
    #
    # 2026-09-02 사내: curl 은 401 을 받는데 여기서만 ConnectTimeout 이 났다.
    # 원인 후보가 둘(프록시 환경변수 / 짧은 타임아웃)이라 둘 다 확인한다.
    #  - httpx 는 HTTPS_PROXY 등을 자동으로 따르므로 curl 과 경로가 달라질 수 있다
    #  - 사내는 TLS 재협상이 여러 번 돌아 8초가 빠듯하다 -> 25초
    proxy_vars = {k: v for k, v in os.environ.items()
                  if k.upper() in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY") and v}
    if proxy_vars:
        typer.echo(f"  · 프록시 환경변수 감지: {', '.join(sorted(proxy_vars))}")

    last_err: Optional[Exception] = None
    reached_without_proxy = False
    for trust_env in (True, False):
        try:
            httpx.get(base_url, timeout=25.0, verify=False, trust_env=trust_env)
            reached_without_proxy = not trust_env
            ok("서버 도달 성공 (방화벽 열림)"
               + (" — 프록시를 무시했을 때만 성공" if reached_without_proxy else ""))
            last_err = None
            break
        except Exception as e:      # noqa: BLE001 - 원인 종류를 그대로 보여준다
            last_err = e
        if not proxy_vars:
            break                   # 프록시가 없으면 두 번 시도할 이유가 없다

    if last_err is not None:
        bad(f"서버 도달 실패: {type(last_err).__name__} — {str(last_err)[:120]}")
        typer.echo(f"  · 접속 시도 주소: {base_url}")
        typer.echo("판정: 아래 순서로 확인하세요.")
        typer.echo("  1) PowerShell 에서 curl.exe 로 같은 주소가 열리는지 확인")
        typer.echo(f"     curl.exe -k -i -m 20 {base_url}")
        typer.echo("  2) 401 이 나오면 방화벽은 정상입니다. 이 창의 오류 종류를 개발자에게 알려주세요.")
        typer.echo("  3) 아무 응답도 없으면 방화벽 미개통 — 10.0.0.10:8443 아웃바운드 개통 요청.")
        raise typer.Exit(1)

    if reached_without_proxy:
        typer.echo("  · 프록시 환경변수가 접속을 막고 있습니다.")
        typer.echo("    config\\hcx.yaml 의 api.ignore_proxy 를 true 로 바꾸거나,")
        typer.echo("    환경변수 NDT_HCX_IGNORE_PROXY=1 을 등록하면 우회합니다.")

    # 3) 최소 실호출 (일일 카운트 1회 소모)
    try:
        resp = call("hcx_check", {"ping": 1}, force_refresh=True)
        ok(f"실호출 성공 — 모델={resp.model}, 응답: {(resp.content or '')[:60]}")
        typer.echo("판정: HCX 연결 정상. 이제 소량(성적서 5~10건) 검토로 넘어가세요.")
    except Exception as e:
        bad(f"실호출 실패: {e}")
        typer.echo("판정: 위 메시지에 인증(토큰)/한도/서버 원인이 표시됩니다.")
        typer.echo("      code=40001 이면 config\\hcx.yaml 의 models.reasoning_models 를 확인하세요.")
        typer.echo("      code=4010x 이면 환경변수 값에 키만 넣었는지 확인하세요 (Bearer·따옴표·공백 금지).")
        raise typer.Exit(1)


# ─────────────────────────── version ───────────────────────────


@app.command("version")
def version_cmd():
    """버전·환경 요약."""
    import platform, sys
    typer.echo("NDT Billing Verifier (synthetic demo)")
    typer.echo(f"  python : {sys.version.split()[0]}  {platform.system()} {platform.machine()}")
    typer.echo("  llm    : mock" if _mock_on() else "  llm    : live")


def _mock_on() -> bool:
    import os
    return os.environ.get("NDT_HCX_MOCK") == "1"


if __name__ == "__main__":
    app()
