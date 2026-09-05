"""1단계 매칭: 결정 키 정확 일치."""
from __future__ import annotations

from typing import Iterable, Optional

from app.config import matching_rules


def _normalize(s) -> str:
    """공백·하이픈·언더스코어 제거 후 대문자. NIS 'No. 12-005 PT' ↔ 엑셀 '12-005PT' 매칭용."""
    if s is None:
        return ""
    # 'No. ' 접두 제거 (PDF 헤더에서 자주 등장)
    text = str(s).strip()
    if text.lower().startswith("no."):
        text = text[3:].strip()
    return text.upper().replace(" ", "").replace("-", "").replace("_", "")


def find_match(
    billing_row: dict, candidate_reports: Iterable[dict]
) -> Optional[dict]:
    """모든 deterministic_keys 조합을 시도. 첫 일치하는 후보를 반환."""
    rules = matching_rules().get("deterministic_keys", [])
    for rule in rules:
        for cand in candidate_reports:
            if all(_normalize(billing_row.get(f)) == _normalize(cand.get(f)) for f in rule["fields"]):
                # 정확 일치 — 점수 100
                cand_out = dict(cand)
                cand_out["_match_rule"] = rule["name"]
                cand_out["_match_score"] = 100.0
                return cand_out
    return None
