"""청구회차 PDF → 개별 성적서 분할.

흐름:
1. PDF 추출 (텍스트 레이어 또는 OCR)
2. 페이지를 순회하며 헤더 영역(상단 N 줄)을 추출
3. 이전 페이지와의 헤더 비교 + 메타 변화(NDT 방법, 일자, 성적서번호)로 1차 분할 후보
4. 모호한 경계는 HCX (`report_segment` 프롬프트) 호출로 확정
5. 결과: [(start_page, end_page, tentative_id, meta)]
6. 각 segment 를 ocr_normalizer 로 정규화 → DB inspection_reports 적재
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date as date_type
from pathlib import Path
from typing import Optional

from app.extractors.ocr_normalizer import normalize_report
from app.extractors.pdf_extractor import ExtractedPDF, PageText, extract
from app.hcx_client import call

logger = logging.getLogger(__name__)


# ─────────────────────────── Data ───────────────────────────


@dataclass
class ReportSegment:
    start_page: int                  # 0-based
    end_page: int                    # 0-based, inclusive
    tentative_id: str
    meta: dict = field(default_factory=dict)
    segmentation_confidence: float = 0.0


# ─────────────────────────── Header extraction & heuristics ───────────────────────────


_HEADER_LINES = 8                    # 페이지 상단 N 줄을 헤더로 간주
_REPORT_NO_RE = re.compile(r"(report|inspection)\s*(no\.?|number)\s*[:\-]?\s*([A-Z0-9\-]+)", re.IGNORECASE)
_NDT_RE = re.compile(r"\b(VT|RT|UT|PT|MT|VMC)\b")
_DATE_RE = re.compile(r"\b(20\d{2})[\-/.](\d{1,2})[\-/.](\d{1,2})\b")

# NIS 양식 전용 헤더 패턴
# 예: "No. 12-005 PT dated 19.02.2024 10UMA"
#     "No.12-013VMC  dated 19.02.2024 10UMA"
_NIS_HEADER_RE = re.compile(
    r"No\.?\s*([0-9]{1,3}-[0-9]{1,4})\s*([A-Z]{2,4})\s+dated?\s+(\d{1,2}[\.\-/]\d{1,2}[\.\-/]\d{2,4})",
    re.IGNORECASE,
)
# 양식 자체를 식별하는 단서 (OCR 깨짐 견디기 위해 여러 패턴)
_NIS_FORM_FINGERPRINTS = (
    "EUNIS",
    "Conclusion on",
    "Welded Joints",
    "Meridian Contractor",
    "Meridian",
    "Dabaa NPP",
)


def _looks_like_nis_form(text: str) -> bool:
    """OCR 깨짐 견디기 위해 여러 지문 중 1개 이상이면 NIS 양식으로 간주."""
    return sum(1 for fp in _NIS_FORM_FINGERPRINTS if fp.lower() in text.lower()) >= 1


def _page_header(page: PageText) -> str:
    return "\n".join(page.text.splitlines()[:_HEADER_LINES])


def _extract_header_meta(header: str) -> dict:
    meta: dict = {}
    m = _REPORT_NO_RE.search(header)
    if m:
        meta["report_no"] = m.group(3)
    n = _NDT_RE.search(header)
    if n:
        meta["ndt_method"] = n.group(1).upper()
    d = _DATE_RE.search(header)
    if d:
        meta["inspection_date"] = f"{d.group(1)}-{int(d.group(2)):02d}-{int(d.group(3)):02d}"
    return meta


def _meta_signals_new_report(prev_meta: dict, curr_meta: dict) -> bool:
    if not prev_meta:
        return True
    for k in ("report_no", "ndt_method", "inspection_date"):
        if k in prev_meta and k in curr_meta and prev_meta[k] != curr_meta[k]:
            return True
    return False


# ─────────────────────────── Public ───────────────────────────


def segment_nis(extracted) -> list[ReportSegment]:
    """NIS 양식 전용. 페이지별 (report_no, ndt_method, date) 추출 후 segment 형성.

    - 정규식 헤더 매칭: 빠른 1차
    - 미매칭 페이지: LLM 호출로 헤더 재해석 (OCR 품질 낮음)
    - 동일 report_no 연속 페이지 → 1 segment (사용자 정책)
    - 동일 report_no 비연속 페이지 → 별도 segment + needs_review (사용자 정책)
    - 헤더 추출 완전 실패 → 단독 segment + needs_review
    """
    pages = extracted.pages
    per_page_meta: list[dict] = []

    for p in pages:
        meta = _extract_nis_header(p.text)
        if meta is None:
            # LLM 보조 — 텍스트가 너무 깨져서 정규식이 못 잡은 경우
            meta = _llm_nis_header(p.text, p.page_index)
        per_page_meta.append(meta or {})

    # report_no 별로 페이지 인덱스 모음
    by_report_no: dict[str, list[int]] = {}
    unidentified_pages: list[int] = []
    for i, m in enumerate(per_page_meta):
        rn = m.get("report_no_normalized")
        if rn:
            by_report_no.setdefault(rn, []).append(i)
        else:
            unidentified_pages.append(i)

    segments: list[ReportSegment] = []

    # 1) 식별된 report_no 처리
    for rn, page_list in by_report_no.items():
        # 연속 그룹 묶기 (사용자 정책: 연속이면 1 segment, 비연속이면 별도 + needs_review)
        groups = _group_consecutive(page_list)
        multi_disjoint = len(groups) > 1
        for grp in groups:
            first = grp[0]
            last = grp[-1]
            meta = per_page_meta[first]
            seg = ReportSegment(
                start_page=first,
                end_page=last,
                tentative_id=f"SEG-{meta.get('report_no_raw', rn)}-p{first + 1}",
                meta={
                    "report_no": meta.get("report_no_raw", rn),
                    "report_no_normalized": rn,
                    "ndt_method": meta.get("ndt_method"),
                    "inspection_date": meta.get("inspection_date"),
                    "source": meta.get("source"),
                },
                segmentation_confidence=(0.55 if multi_disjoint else (0.95 if meta.get("source") == "regex" else 0.75)),
            )
            if multi_disjoint:
                seg.meta["needs_review_reasons"] = [
                    f"동일 report_no '{rn}' 가 비연속 페이지 {[g[0] + 1 for g in groups]} 에 나타남 — 검토자 확인 필요"
                ]
            segments.append(seg)

    # 2) 미식별 페이지 — 인접 segment 의 연속으로 흡수 시도, 아니면 단독 + needs_review
    for p_idx in unidentified_pages:
        absorbed = _try_absorb(p_idx, segments)
        if not absorbed:
            segments.append(ReportSegment(
                start_page=p_idx,
                end_page=p_idx,
                tentative_id=f"SEG-UNKNOWN-p{p_idx + 1}",
                meta={"needs_review_reasons": ["헤더 추출 실패 (OCR 품질 낮음)"]},
                segmentation_confidence=0.2,
            ))

    segments.sort(key=lambda s: s.start_page)
    return segments


def _extract_nis_header(text: str) -> Optional[dict]:
    m = _NIS_HEADER_RE.search(text)
    if not m:
        return None
    raw = f"{m.group(1).strip()} {m.group(2).strip().upper()}"
    normalized = (m.group(1) + m.group(2)).upper().replace(" ", "").replace("-", "")
    method_token = m.group(2).strip().upper()
    method = "VT" if method_token == "VMC" else method_token
    return {
        "report_no_raw": raw,
        "report_no_normalized": normalized,
        "ndt_method": method,
        "inspection_date": _nis_date_to_iso(m.group(3)),
        "source": "regex",
    }


def _llm_nis_header(text: str, page_index: int) -> Optional[dict]:
    """OCR 깨진 페이지 — HCX 로 헤더 재해석.

    프롬프트 reuse: report_segment 가 인접 비교용이라 직접 안 맞음. 대신
    ocr_normalize 의 일부만 활용. 일단 간단히 LLM 에 헤더 영역을 보내고 JSON 받기.
    """
    from app.hcx_client import call

    # 페이지 상단만 보냄 (토큰 절약 + 헤더만 관심)
    head = "\n".join(text.splitlines()[:15])[:2000]
    resp = call(
        "report_segment",
        {
            "prev_page_header": "",
            "current_page_header": head,
            "prev_page_meta": {},
            "page_index": page_index,
        },
    )
    if resp.parsed is None:
        return None
    meta = resp.parsed.get("extracted_meta_if_new") or {}
    rn_raw = meta.get("tentative_report_no")
    if not rn_raw:
        return None
    method = (meta.get("ndt_method") or "").upper()
    if method == "VMC":
        method = "VT"
    return {
        "report_no_raw": rn_raw,
        "report_no_normalized": str(rn_raw).upper().replace(" ", "").replace("-", ""),
        "ndt_method": method or None,
        "inspection_date": meta.get("inspection_date"),
        "source": "llm",
    }


def _nis_date_to_iso(s: str) -> Optional[str]:
    """'19.02.2024' → '2024-02-19' (NIS 는 일.월.년)."""
    parts = re.split(r"[\.\-/]", s)
    if len(parts) != 3:
        return None
    try:
        d, m, y = int(parts[0]), int(parts[1]), int(parts[2])
        if y < 100:
            y += 2000
        return f"{y:04d}-{m:02d}-{d:02d}"
    except ValueError:
        return None


def _group_consecutive(sorted_pages: list[int]) -> list[list[int]]:
    """[40, 41, 76, 77, 100] → [[40,41], [76,77], [100]]"""
    if not sorted_pages:
        return []
    groups = [[sorted_pages[0]]]
    for p in sorted_pages[1:]:
        if p == groups[-1][-1] + 1:
            groups[-1].append(p)
        else:
            groups.append([p])
    return groups


def _try_absorb(p_idx: int, segments: list[ReportSegment]) -> bool:
    """미식별 페이지가 직전 segment 의 연속 페이지일 가능성을 검사해 흡수."""
    for seg in segments:
        if seg.end_page + 1 == p_idx and seg.segmentation_confidence > 0.5:
            seg.end_page = p_idx
            seg.meta.setdefault("absorbed_pages", []).append(p_idx + 1)
            seg.segmentation_confidence = min(seg.segmentation_confidence, 0.7)
            return True
    return False


def segment(pdf_path: Path) -> list[ReportSegment]:
    extracted = extract(pdf_path)
    if not extracted.pages:
        return []

    # NIS 양식이 우세하면 전용 분할기 사용 (정규식 + LLM 보조, 페이지 단위 처리)
    sample_pages = extracted.pages[: min(5, len(extracted.pages))]
    if sum(1 for p in sample_pages if _looks_like_nis_form(p.text)) >= 1:
        return segment_nis(extracted)

    # 일반 양식 — 기존 휴리스틱

    segments: list[ReportSegment] = []
    current_start = 0
    current_meta = _extract_header_meta(_page_header(extracted.pages[0]))
    current_conf_sum = 1.0
    current_conf_count = 1

    for i in range(1, len(extracted.pages)):
        prev_header = _page_header(extracted.pages[i - 1])
        curr_header = _page_header(extracted.pages[i])
        curr_meta = _extract_header_meta(curr_header)

        is_new, confidence = _decide_boundary(
            prev_header=prev_header,
            curr_header=curr_header,
            prev_meta=current_meta,
            curr_meta=curr_meta,
            page_index=i,
        )

        if is_new:
            segments.append(
                ReportSegment(
                    start_page=current_start,
                    end_page=i - 1,
                    tentative_id=_tentative_id(current_start, current_meta),
                    meta=current_meta,
                    segmentation_confidence=current_conf_sum / max(current_conf_count, 1),
                )
            )
            current_start = i
            current_meta = curr_meta
            current_conf_sum = confidence
            current_conf_count = 1
        else:
            current_conf_sum += confidence
            current_conf_count += 1
            # 메타 보강 (현재 segment 의 누적 정보로 갱신)
            for k, v in curr_meta.items():
                current_meta.setdefault(k, v)

    # 마지막 segment
    segments.append(
        ReportSegment(
            start_page=current_start,
            end_page=len(extracted.pages) - 1,
            tentative_id=_tentative_id(current_start, current_meta),
            meta=current_meta,
            segmentation_confidence=current_conf_sum / max(current_conf_count, 1),
        )
    )

    return segments


def _decide_boundary(
    *,
    prev_header: str,
    curr_header: str,
    prev_meta: dict,
    curr_meta: dict,
    page_index: int,
) -> tuple[bool, float]:
    """LLM 호출로 boundary 판정. 메타 변화가 명확하면 LLM 생략하고 즉시 새 segment."""
    if _meta_signals_new_report(prev_meta, curr_meta):
        return True, 0.95

    resp = call(
        "report_segment",
        {
            "prev_page_header": prev_header,
            "current_page_header": curr_header,
            "prev_page_meta": prev_meta,
            "page_index": page_index,
        },
    )
    if resp.parsed is None:
        return False, 0.5
    return bool(resp.parsed.get("is_new_report")), float(resp.parsed.get("confidence", 0.5))


def _tentative_id(start_page: int, meta: dict) -> str:
    rn = meta.get("report_no") or f"PAGE-{start_page + 1}"
    return f"SEG-{rn}"


# ─────────────────────────── DB ingest ───────────────────────────


def normalize_and_ingest_segments(
    pdf_path: Path,
    segments: list[ReportSegment],
    *,
    billing_round_meta: dict,
    session,
):
    """각 segment 를 OCR/정규화 후 inspection_reports 테이블에 적재."""
    from app.database.repository import add_inspection_report

    extracted = extract(pdf_path)
    pages_by_idx: dict[int, PageText] = {p.page_index: p for p in extracted.pages}

    saved = 0
    for seg in segments:
        page_texts = [pages_by_idx[i].text for i in range(seg.start_page, seg.end_page + 1)]
        variants = [pages_by_idx[i].ocr_variants for i in range(seg.start_page, seg.end_page + 1)]
        normalized = normalize_report(
            report_id=seg.tentative_id,
            billing_round_meta=billing_round_meta,
            pages_text=page_texts,
            ocr_variants_per_page=variants if any(variants) else None,
        )
        if normalized is None:
            logger.warning("Normalization returned None for segment %s", seg.tentative_id)
            # LLM 응답 파싱 실패 — 검토자가 확인하도록 needs_review 로 적재
            add_inspection_report(
                session,
                billing_round_id=billing_round_meta["id"],
                source_pdf=str(pdf_path),
                start_page=seg.start_page,
                end_page=seg.end_page,
                extracted_json=None,
                segmentation_confidence=seg.segmentation_confidence,
                needs_review=True,
                review_reasons_json={"reasons": ["LLM 정규화 응답 파싱 실패"]},
            )
            continue

        review_flags = _aggregate_review_flags(seg, normalized)
        # segment 메타의 report_no/method/date 가 결정적 (정규식). LLM normalize 결과보다 우선.
        # mock 환경/LLM 환각으로 normalize 결과가 segment 와 어긋나면 needs_review 표시.
        seg_report_no = (seg.meta or {}).get("report_no")
        seg_method = (seg.meta or {}).get("ndt_method")
        seg_date = _to_date((seg.meta or {}).get("inspection_date"))

        final_report_no = seg_report_no or normalized.get("report_no")
        final_method = seg_method or normalized.get("ndt_method")
        final_date = seg_date or _to_date(normalized.get("inspection_date"))

        if (seg_report_no and normalized.get("report_no")
                and seg_report_no.replace(" ", "").upper() != str(normalized.get("report_no", "")).replace(" ", "").upper()):
            review_flags["needs_review"] = True
            review_flags["reasons"].append(
                f"분할 추출 report_no='{seg_report_no}' 와 LLM 정규화 report_no='{normalized.get('report_no')}' 불일치"
            )

        add_inspection_report(
            session,
            billing_round_id=billing_round_meta["id"],
            source_pdf=str(pdf_path),
            start_page=seg.start_page,
            end_page=seg.end_page,
            report_no=final_report_no,
            ndt_method=final_method,
            inspection_date=final_date,
            inspector=normalized.get("inspector"),
            approver=normalized.get("approver"),
            procedure_no=normalized.get("procedure_no"),
            drawing_no=normalized.get("drawing_no"),
            extracted_json=normalized,
            segmentation_confidence=seg.segmentation_confidence,
            extraction_confidence=normalized.get("extraction_confidence"),
            needs_review=review_flags["needs_review"],
            review_reasons_json={"reasons": review_flags["reasons"]} if review_flags["reasons"] else None,
        )
        saved += 1
    return saved


def _aggregate_review_flags(seg: ReportSegment, normalized: dict) -> dict:
    reasons: list[str] = []
    # segment 단계에서 발견된 사유 (NIS 비연속 중복, OCR 깨짐 흡수 등)
    for r in (seg.meta or {}).get("needs_review_reasons", []) or []:
        reasons.append(f"[분할] {r}")
    if (seg.meta or {}).get("absorbed_pages"):
        absorbed = seg.meta["absorbed_pages"]
        reasons.append(f"[분할] 헤더 미식별 페이지 흡수: {absorbed} (NIS 양식이나 OCR 깨짐 추정)")
    if normalized.get("needs_review"):
        reasons.extend(normalized.get("review_reasons") or ["LLM이 재확인 표시"])
    if seg.segmentation_confidence < 0.7:
        reasons.append(f"성적서 경계 분할 신뢰도 낮음 ({seg.segmentation_confidence:.2f})")
    if (normalized.get("extraction_confidence") or 0) < 0.7:
        reasons.append("OCR/정규화 신뢰도 낮음")
    if normalized.get("ocr_concerns"):
        reasons.append(f"OCR 오인식 의심 {len(normalized['ocr_concerns'])}건")
    return {"needs_review": bool(reasons), "reasons": reasons}


def _to_date(s) -> Optional[date_type]:
    if not s:
        return None
    try:
        y, m, d = s.split("-")
        return date_type(int(y), int(m), int(d))
    except Exception:
        return None
