"""SQLite 스키마.

설계 메모:
- 단일 파일 (data/ndt.sqlite) — 사내 공유폴더 배치 가능
- SQLAlchemy 2.x 선언형
- 모든 LLM 추출 결과의 출처(citation: doc/section/page/quote) 보존
- 검토자 판정(`reviewer_notes`)은 누적되어 학습 데이터 역할도 함
"""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
from typing import Optional

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
)
from sqlalchemy.orm import (
    DeclarativeBase,
    Mapped,
    Session,
    mapped_column,
    relationship,
    sessionmaker,
)

from app.config import DATA_DIR


# ─────────────────────────── Engine / Session ───────────────────────────


_engine = None
_SessionFactory = None


def get_engine(db_path: Optional[Path] = None):
    global _engine
    if _engine is None:
        if db_path is None:
            DATA_DIR.mkdir(parents=True, exist_ok=True)
            db_path = DATA_DIR / "ndt.sqlite"
        _engine = create_engine(f"sqlite:///{db_path}", future=True)
    return _engine


def get_session(db_path: Optional[Path] = None) -> Session:
    global _SessionFactory
    if _SessionFactory is None:
        _SessionFactory = sessionmaker(bind=get_engine(db_path), expire_on_commit=False)
    return _SessionFactory()


def init_db(db_path: Optional[Path] = None) -> None:
    """존재하지 않으면 모든 테이블 생성."""
    Base.metadata.create_all(get_engine(db_path))


# ─────────────────────────── Models ───────────────────────────


class Base(DeclarativeBase):
    pass


class DrawingFile(Base):
    """개별 DC/SD/BG 파일."""

    __tablename__ = "drawing_files"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    file_hash: Mapped[str] = mapped_column(String, nullable=False)         # 변경 감지용
    drawing_no: Mapped[str] = mapped_column(String, index=True, nullable=False)
    drawing_type: Mapped[str] = mapped_column(String(4), nullable=False)   # "DC" | "SD" | "BG"
    revision: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    page_count: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)

    classification_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extracted_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    extraction_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    set_id: Mapped[Optional[int]] = mapped_column(ForeignKey("drawing_sets.id"), nullable=True, index=True)
    drawing_set: Mapped[Optional["DrawingSet"]] = relationship(back_populates="files")


class DrawingSet(Base):
    """동일 도면번호의 DC+SD+BG 3파일 묶음 (1 논리적 도면).

    사용자 정책 (2026-05-21 확인): 같은 도면번호면 1세트로 묶음.
    각 파일의 rev 는 독립 진화 가능 (예: BG=C01 인데 DC/SD=None/C00).
    set_revision 은 종류별 rev 합본 표시 (예: 'DC=-,SD=-,BG=C01').
    """

    __tablename__ = "drawing_sets"
    __table_args__ = (UniqueConstraint("drawing_no", name="uq_drawing_set_no"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    drawing_no: Mapped[str] = mapped_column(String, index=True, nullable=False)
    set_revision: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    has_dc: Mapped[bool] = mapped_column(Boolean, default=False)
    has_sd: Mapped[bool] = mapped_column(Boolean, default=False)
    has_bg: Mapped[bool] = mapped_column(Boolean, default=False)

    effective_from: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    superseded_at: Mapped[Optional[datetime]] = mapped_column(DateTime, nullable=True)

    combined_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    conflicts_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    files: Mapped[list["DrawingFile"]] = relationship(back_populates="drawing_set")
    requirements: Mapped[list["Requirement"]] = relationship(back_populates="drawing_set")


class Requirement(Base):
    """통합 검사 요구사항 (Joint 단위). drawing_set 우선, 상위 문서 보강."""

    __tablename__ = "requirements"

    id: Mapped[int] = mapped_column(primary_key=True)
    drawing_set_id: Mapped[int] = mapped_column(ForeignKey("drawing_sets.id"), index=True)
    joint_no: Mapped[str] = mapped_column(String, index=True, nullable=False)

    weld_type: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    p_no_a: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    p_no_b: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    thickness_mm: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    wps_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    line_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    safety_class: Mapped[Optional[str]] = mapped_column(String, nullable=True)

    required_ndt_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    applicable_codes_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    citations_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    drawing_set: Mapped["DrawingSet"] = relationship(back_populates="requirements")


class StandardDocument(Base):
    """SCWEP / Code / Contract 등 상위 권위 문서."""

    __tablename__ = "standard_documents"

    id: Mapped[int] = mapped_column(primary_key=True)
    file_path: Mapped[str] = mapped_column(String, unique=True, nullable=False)
    doc_type: Mapped[str] = mapped_column(String, nullable=False)   # "scwep" | "code" | "contract"
    document_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    revision: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    extracted_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    chunks_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)   # code_indexer 가 채움
    ingested_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class BillingRound(Base):
    """청구 회차 메타데이터 (1차/2차/...)."""

    __tablename__ = "billing_rounds"

    id: Mapped[int] = mapped_column(primary_key=True)
    round_no: Mapped[int] = mapped_column(Integer, nullable=False)
    discipline: Mapped[str] = mapped_column(String, nullable=False)   # "CP-M1" | "CP-P1" | ...
    billing_date: Mapped[date] = mapped_column(Date, nullable=False)
    billing_xlsx_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    reports_pdf_path: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    billing_items: Mapped[list["BillingItem"]] = relationship(back_populates="billing_round")
    inspection_reports: Mapped[list["InspectionReport"]] = relationship(back_populates="billing_round")


class BillingItem(Base):
    """청구 엑셀 한 행."""

    __tablename__ = "billing_items"

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_round_id: Mapped[int] = mapped_column(ForeignKey("billing_rounds.id"), index=True)
    row_index: Mapped[int] = mapped_column(Integer, nullable=False)

    billing_no: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    # 청구 엑셀에 적힌 성적서번호 (예: '12-005PT'). NIS PDF 와 결정매칭 키.
    report_no: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    # Weld Joint No (예: 'FW12'). 사용자 확인: 고유.
    joint_no: Mapped[str] = mapped_column(String, index=True, nullable=False)
    line_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    welder_id: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    ndt_method: Mapped[str] = mapped_column(String, index=True, nullable=False)
    drawing_no: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    inspection_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    result: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    # 호기/건물 — 청구회차 내 위치 식별용
    unit: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    bldg: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    # 치수 (예: '3040x20')
    dimension: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    quantity: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    unit_price: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    amount: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    raw_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    billing_round: Mapped["BillingRound"] = relationship(back_populates="billing_items")


class InspectionReport(Base):
    """청구회차 PDF 에서 분할된 개별 성적서."""

    __tablename__ = "inspection_reports"

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_round_id: Mapped[int] = mapped_column(ForeignKey("billing_rounds.id"), index=True)

    source_pdf: Mapped[str] = mapped_column(String, nullable=False)
    start_page: Mapped[int] = mapped_column(Integer, nullable=False)
    end_page: Mapped[int] = mapped_column(Integer, nullable=False)

    report_no: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    ndt_method: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)
    inspection_date: Mapped[Optional[date]] = mapped_column(Date, nullable=True)
    inspector: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    approver: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    procedure_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    drawing_no: Mapped[Optional[str]] = mapped_column(String, index=True, nullable=True)

    extracted_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    segmentation_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    extraction_confidence: Mapped[Optional[float]] = mapped_column(Float, nullable=True)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    billing_round: Mapped["BillingRound"] = relationship(back_populates="inspection_reports")


class Match(Base):
    """청구 ↔ 성적서 매칭 결과."""

    __tablename__ = "matches"

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_item_id: Mapped[int] = mapped_column(ForeignKey("billing_items.id"), unique=True, index=True)
    inspection_report_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("inspection_reports.id"), nullable=True, index=True
    )
    matched_joint_no: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    match_method: Mapped[str] = mapped_column(String, nullable=False)  # "deterministic" | "fuzzy" | "llm" | "none"
    match_score: Mapped[Optional[float]] = mapped_column(Float, nullable=True)
    reasoning: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    discrepancies_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)


class Finding(Base):
    """적합성 판정 결과 (근거 인용 포함)."""

    __tablename__ = "findings"

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_item_id: Mapped[int] = mapped_column(ForeignKey("billing_items.id"), index=True)
    verdict: Mapped[str] = mapped_column(String, nullable=False)       # "OK" | "SUSPECT" | "NONCOMPLIANT"
    risk_score: Mapped[int] = mapped_column(Integer, nullable=False)
    summary: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    explanation_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    citations_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    recommended_action: Mapped[Optional[str]] = mapped_column(Text, nullable=True)

    needs_review: Mapped[bool] = mapped_column(Boolean, default=False)
    review_reasons_json: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)

    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())


class ReviewerNote(Base):
    """검토자가 대시보드/엑셀에서 남긴 판정·메모. 학습 데이터로도 활용."""

    __tablename__ = "reviewer_notes"

    id: Mapped[int] = mapped_column(primary_key=True)
    billing_item_id: Mapped[int] = mapped_column(ForeignKey("billing_items.id"), index=True)
    reviewer: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    verdict_override: Mapped[Optional[str]] = mapped_column(String, nullable=True)
    note: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
