"""도면 파일을 DC/SD/BG 로 분류 + 도면번호·rev 추출.

전략: 파일명 정규식이 명확히 식별하면 그대로 채택 (LLM 호출 없음).
모호하면 LLM(drawing_classify) 호출.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from app.extractors.pdf_extractor import extract
from app.hcx_client import call

# Meridian 원전 도면 파일명 패턴
#   예 (scan, rev 있음): MD.D.P000.1.0UGB95GML90&.052.DC.0001.E_C03_scan.pdf
#   예 (native, rev 없음): MD.D.P000.9.0UTF&&GQA49&.052.DC.0002.E.pdf
#
# 구조: MD.D.P000.<unit>.<KKS&masking>.052.<type>.<seq>.<lang>[_<rev>][_scan].pdf
#   drawing_no = 도면번호 (type 직전까지). KKS 코드 일부가 '&' 로 마스킹됨.
#   type       = DC | SD | BG
#   rev        = 'C01' 등 (옵션)
#   scan       = '_scan' 접미사 있으면 스캔본 (텍스트 레이어 없을 가능성 큼)
_FILENAME_PATTERN = re.compile(
    r"""
    ^
    (?P<drawing_no>MD\..+?)                # 'MD.' 로 시작, type 직전까지 (Meridian 프로젝트 명명규칙)
    \.
    (?P<type>DC|SD|BG|KE)                  # 도면 = DC/SD/BG, KE = SCWEP(시공 절차서, 도면 아님)
    \.
    (?P<seq>\d{1,5})
    \.
    (?P<lang>[A-Z])
    (?:_(?P<rev>C\d+))?                    # rev 옵션 (예: _C03)
    (?:_(?P<scan>scan))?                   # scan 접미사 옵션
    \.pdf$
    """,
    re.VERBOSE,
)
# KE 명명규칙은 WEP·SCWEP 둘 다 공유 — 둘 다 시공 절차 관련 서류로 도면 아님.
# 청구 엑셀의 Detailed Drawing 컬럼에 들어가면 시공사의 오기.
# 사용자 정책 (2026-05-21 확인): 모체 도면이 별도로 존재하므로 시공사에 수정 요청 필요.
_NON_DRAWING_TYPES = {"KE"}
# Fallback pattern — 다른 명명규칙(예: 향후 다른 원전 프로젝트)에 대비
_FILENAME_PATTERN_FALLBACK = re.compile(
    r"""
    ^
    (?P<drawing_no>[A-Z0-9][A-Z0-9\-\.&]*?)
    [-_\.]
    (?P<type>DC|SD|BG)
    (?:[-_\.](?:rev|REV|Rev|r|C)\.?(?P<rev>[A-Za-z0-9]+))?
    \.pdf$
    """,
    re.VERBOSE,
)


@dataclass
class Classification:
    drawing_type: Optional[str]      # "DC" | "SD" | "BG" | None (재확인 필요)
    drawing_no: Optional[str]
    revision: Optional[str]
    confidence: float
    source: str                     # "filename" | "llm" | "unknown"
    reasoning: Optional[str] = None
    needs_review: bool = False
    review_reasons: list[str] = None  # type: ignore[assignment]

    def __post_init__(self):
        if self.review_reasons is None:
            self.review_reasons = []


def classify(file_path: Path) -> Classification:
    """파일 1개 분류. **추론으로 채우지 않음** — 모호하면 needs_review 표시."""
    file_path = Path(file_path)

    fn_match = _FILENAME_PATTERN.match(file_path.name) or _FILENAME_PATTERN_FALLBACK.match(file_path.name)
    if fn_match:
        dtype = fn_match.group("type")
        needs_review = dtype in _NON_DRAWING_TYPES
        review_reasons = (
            [
                f"'{dtype}' 식별자 = WEP 또는 SCWEP (시공 절차 관련 서류, 도면 아님). "
                f"청구의 Detailed Drawing 컬럼에 들어가면 시공사의 오기 — 모체 도면번호로 수정 요청 필요"
            ]
            if needs_review else []
        )
        return Classification(
            drawing_type=dtype,
            drawing_no=fn_match.group("drawing_no"),
            revision=fn_match.group("rev"),
            confidence=0.99,
            source="filename",
            reasoning=f"Filename pattern matched: {file_path.name}",
            needs_review=needs_review,
            review_reasons=review_reasons,
        )

    # LLM fallback — title block 텍스트로 분류
    extracted = extract(file_path)
    title_block_text = "\n".join(
        p.text for p in extracted.pages[: min(2, len(extracted.pages))]
    )[:2000]
    resp = call(
        "drawing_classify",
        {
            "file_name": file_path.name,
            "text_excerpt": title_block_text,
            "page_count": len(extracted.pages),
        },
    )

    if resp.parsed is None:
        return Classification(
            drawing_type=None,
            drawing_no=None,
            revision=None,
            confidence=0.0,
            source="unknown",
            reasoning="LLM 분류 응답 파싱 실패",
            needs_review=True,
            review_reasons=["LLM 응답이 JSON 으로 파싱되지 않음 — 파일명·내용 재확인 필요"],
        )

    p = resp.parsed
    confidence = float(p.get("confidence", 0.0))
    review_reasons = list(p.get("review_reasons") or [])
    needs_review = bool(p.get("needs_review")) or confidence < 0.8

    if needs_review and not review_reasons:
        review_reasons.append(f"분류 신뢰도 낮음 ({confidence:.2f}) — 도면 종류·번호 재확인 필요")

    if p.get("drawing_type") not in ("DC", "SD", "BG", None):
        needs_review = True
        review_reasons.append(f"알 수 없는 도면 종류: {p.get('drawing_type')!r}")

    return Classification(
        drawing_type=p.get("drawing_type"),
        drawing_no=p.get("drawing_no"),
        revision=p.get("revision"),
        confidence=confidence,
        source="llm",
        reasoning=p.get("reasoning"),
        needs_review=needs_review,
        review_reasons=review_reasons,
    )
