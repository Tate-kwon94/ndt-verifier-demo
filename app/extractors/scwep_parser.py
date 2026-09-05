"""SCWEP 시공·검사 절차서 추출 (HCX-007, 권위계층 2순위)."""
from __future__ import annotations

from pathlib import Path

from app.database.models import get_session, init_db
from app.database.repository import upsert_standard
from app.extractors.pdf_extractor import extract
from app.hcx_client import call

# config/prompts/scwep_extract.md 의 출력 형식 버전. 조건부 요구(conditional_ndt_requirements)
# 를 뽑기 시작한 시점이 2. 구 형식(1)로 적재된 문서는 과다청구 확정 근거로 쓰지 않는다.
SCWEP_SCHEMA_VERSION = 2


def ingest(file_path: Path) -> dict:
    file_path = Path(file_path)
    extracted = extract(file_path)
    text_full = "\n\n".join(f"---PAGE {p.page_index + 1}---\n{p.text}" for p in extracted.pages)
    resp = call(
        "scwep_extract",
        {"document_no": file_path.stem, "revision": None, "text_full": text_full},
    )
    parsed = resp.parsed or {}
    # 형식 버전을 **파서가 직접 각인**한다. LLM 이 빠뜨려도 무관해야 하기 때문이다.
    # 이 각인이 근거 게이트의 열쇠다 — 조건부 요구를 인지하는 프롬프트로 뽑힌 문서만
    # "절차서가 이 검사에 침묵했다" 고 주장할 수 있다 (app/analyzers/scwep_basis.py).
    if parsed:
        parsed.setdefault("_schema_version", SCWEP_SCHEMA_VERSION)

    init_db()
    with get_session() as s:
        upsert_standard(
            s,
            file_path=str(file_path),
            doc_type="scwep",
            document_no=parsed.get("document_no"),
            revision=parsed.get("revision"),
            extracted_json=parsed,
        )
        s.commit()
    return parsed
