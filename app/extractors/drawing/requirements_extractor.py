"""도면 폴더 적재 오케스트레이터.

흐름:
1. 폴더 내 PDF 들을 classifier 로 분류
2. grouper 로 (drawing_no, rev) 세트화
3. 각 세트의 DC/SD/BG 파일을 종류별 파서로 추출
4. set_combiner 로 통합 → 통합 요구사항 dict
5. DB 적재 (drawing_files, drawing_sets, requirements) + 이전 rev supersede
"""
from __future__ import annotations

import hashlib
import logging
from datetime import date
from pathlib import Path
from typing import Iterable, Optional

from app.database.models import DrawingFile, Requirement, get_session, init_db
from app.database.repository import (
    get_or_create_drawing_set,
    upsert_drawing_file,
)
from app.extractors.drawing.bg_parser import parse_bg
from app.extractors.drawing.classifier import classify
from app.extractors.drawing.dc_parser import parse_dc
from app.extractors.drawing.grouper import group as group_sets
from app.extractors.drawing.revision_manager import supersede_old_revisions
from app.extractors.drawing.sd_parser import parse_sd
from app.extractors.drawing.set_combiner import combine

logger = logging.getLogger(__name__)


# ─────────────────────────── Public ───────────────────────────


def ingest_folder(folder: Path, *, as_of: Optional[date] = None) -> dict:
    """폴더 내 모든 PDF 도면을 적재.

    반환: 통계 dict (총 파일 수, 완성 세트 수, 부분 세트 수, 적재된 requirement 수)
    """
    folder = Path(folder)
    pdf_files = sorted([p for p in folder.rglob("*.pdf") if p.is_file()])
    logger.info("Classifying %d PDF files under %s", len(pdf_files), folder)

    classifications = []
    for f in pdf_files:
        c = classify(f)
        # classifier 결과 + 파일 경로를 함께 보관
        c.__dict__["_file_path"] = f
        classifications.append(c)

    grouping = group_sets(classifications)
    sets = grouping.sets
    logger.info(
        "Grouped into %d drawing sets (unclassified=%d, duplicates=%d)",
        len(sets), len(grouping.unclassified), len(grouping.duplicate_conflicts),
    )

    init_db()
    total_requirements = 0
    complete_sets = 0
    partial_sets = 0

    with get_session() as session:
        for ds in sets:
            try:
                dc_res = _parse_if_present(ds, "DC", parse_dc)
                sd_res = _parse_if_present(ds, "SD", parse_sd)
                bg_res = _parse_if_present(ds, "BG", parse_bg)

                combined = combine(
                    drawing_no=ds.drawing_no,
                    set_revision=ds.revision,
                    dc_result=dc_res,
                    sd_result=sd_res,
                    bg_result=bg_res,
                )
                if combined is None:
                    logger.warning("Combine returned None for %s", ds.drawing_no)
                    continue

                db_set = get_or_create_drawing_set(session, ds.drawing_no, ds.revision)
                db_set.has_dc = "DC" in ds.files
                db_set.has_sd = "SD" in ds.files
                db_set.has_bg = "BG" in ds.files
                db_set.effective_from = as_of
                db_set.combined_json = combined
                db_set.conflicts_json = {
                    "conflicts": combined.get("conflicts", []),
                    "missing_joints": combined.get("missing_joints", []),
                }
                set_review_reasons = _aggregate_set_review_reasons(ds, combined)
                db_set.needs_review = bool(set_review_reasons)
                db_set.review_reasons_json = {"reasons": set_review_reasons} if set_review_reasons else None
                session.flush()

                # 개별 파일 적재
                results_by_type = {"DC": dc_res, "SD": sd_res, "BG": bg_res}
                for dtype, cls in ds.files.items():
                    fpath = cls.__dict__["_file_path"]
                    parsed = results_by_type.get(dtype) or {}
                    file_review_reasons = _aggregate_file_review_reasons(cls, parsed)
                    upsert_drawing_file(
                        session,
                        file_path=str(fpath),
                        file_hash=_file_hash(fpath),
                        drawing_no=ds.drawing_no,
                        drawing_type=dtype,
                        revision=cls.revision,
                        classification_confidence=cls.confidence,
                        extracted_json=parsed or None,
                        extraction_confidence=parsed.get("extraction_confidence"),
                        needs_review=bool(file_review_reasons),
                        review_reasons_json={"reasons": file_review_reasons} if file_review_reasons else None,
                        set_id=db_set.id,
                    )

                # requirements 적재 (Joint 단위)
                for joint in combined.get("joints", []):
                    req = Requirement(
                        drawing_set_id=db_set.id,
                        joint_no=joint["joint_no"],
                        weld_type=joint.get("weld_type"),
                        p_no_a=joint.get("p_no_a"),
                        p_no_b=joint.get("p_no_b"),
                        thickness_mm=joint.get("thickness_mm"),
                        wps_no=joint.get("wps_no"),
                        line_no=joint.get("line_no"),
                        safety_class=joint.get("safety_class"),
                        required_ndt_json={"items": joint.get("required_ndt", [])},
                        applicable_codes_json={"items": joint.get("applicable_codes", [])},
                        citations_json={"sources": [n.get("citation") for n in joint.get("required_ndt", [])]},
                    )
                    session.add(req)
                    total_requirements += 1

                # 이전 rev supersede
                supersede_old_revisions(session, drawing_no=ds.drawing_no, new_set_id=db_set.id, as_of=as_of)

                if ds.is_complete:
                    complete_sets += 1
                else:
                    partial_sets += 1
                    logger.warning(
                        "Partial drawing set %s (missing %s)",
                        ds.drawing_no, ds.missing,
                    )
            except Exception as e:
                logger.exception("Failed to ingest drawing set %s: %s", ds.drawing_no, e)

        # 분류 미확정 파일도 DB 에 기록 → 검토자가 직접 확인
        for cls in grouping.unclassified:
            fpath = cls.__dict__["_file_path"]
            upsert_drawing_file(
                session,
                file_path=str(fpath),
                file_hash=_file_hash(fpath),
                drawing_no=cls.drawing_no or f"UNCLASSIFIED-{fpath.name}",
                drawing_type=cls.drawing_type or "UNK",
                revision=cls.revision,
                classification_confidence=cls.confidence,
                extracted_json=None,
                extraction_confidence=None,
                needs_review=True,
                review_reasons_json={"reasons": cls.review_reasons or ["분류 실패 — 수동 확인 필요"]},
                set_id=None,
            )

        session.commit()

    return {
        "files_classified": len(classifications),
        "drawing_sets": len(sets),
        "complete_sets": complete_sets,
        "partial_sets": partial_sets,
        "unclassified_files": len(grouping.unclassified),
        "duplicate_conflicts": len(grouping.duplicate_conflicts),
        "requirements_ingested": total_requirements,
    }


def _parse_if_present(ds, type_key: str, parser):
    if type_key not in ds.files:
        return None
    cls = ds.files[type_key]
    fpath = cls.__dict__["_file_path"]
    return parser(fpath, drawing_no=ds.drawing_no, revision=cls.revision)


def _aggregate_set_review_reasons(ds, combined: dict) -> list[str]:
    """드로잉 세트 단위 needs_review 사유 집계."""
    reasons: list[str] = []
    if not ds.is_complete:
        reasons.append(f"3파일 세트 누락: {', '.join(ds.missing)} — 검토자가 해당 파일 입수/연결 필요")
    if combined.get("needs_review"):
        reasons.extend(combined.get("review_reasons") or ["LLM 종합 단계에서 재확인 표시"])
    conflicts = combined.get("conflicts") or []
    if conflicts:
        reasons.append(f"DC/SD/BG 간 상충 {len(conflicts)}건 — 채택값 검증 필요")
    missing_joints = combined.get("missing_joints") or []
    if missing_joints:
        reasons.append(f"일부 종에서만 발견된 Joint {len(missing_joints)}건")
    if (combined.get("extraction_confidence") or 0) < 0.8:
        reasons.append("LLM 종합 신뢰도 낮음")
    return reasons


def _aggregate_file_review_reasons(cls, parsed: dict) -> list[str]:
    """개별 도면 파일 단위 needs_review 사유 집계."""
    reasons: list[str] = []
    if cls.confidence < 0.8:
        reasons.append(f"분류 신뢰도 낮음 ({cls.confidence:.2f}) — 종류 재확인 필요")
    if parsed and parsed.get("needs_review"):
        reasons.extend(parsed.get("review_reasons") or ["LLM 추출 단계 재확인 표시"])
    if parsed and (parsed.get("extraction_confidence") or 0) < 0.7:
        reasons.append(f"추출 신뢰도 낮음 ({parsed.get('extraction_confidence')})")
    if parsed and parsed.get("notes"):
        reasons.append(f"추출 노트: {parsed['notes']}")
    return reasons


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    h.update(path.read_bytes())
    return h.hexdigest()
