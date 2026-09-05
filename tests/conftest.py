"""Shared fixtures.

fresh_db — a throw-away SQLite under tmp_path WITHOUT reloading app.database.models.

Why not importlib.reload(models): reloading re-executes the module and creates a second
declarative registry. String relationships like relationship("DrawingFile") are resolved
against the registry of the Base a class was declared on, so after two reloads in one
session the mapper configuration fails with
    InvalidRequestError: expression 'DrawingFile' failed to locate a name
— and only when a *later* test first touches a mapper (order-dependent, hard to bisect).
Resetting the lazily-built engine/session factory and pointing DATA_DIR at tmp achieves
the isolation without ever duplicating the registry.
"""
from __future__ import annotations

import importlib

import pytest

# Modules that bound get_session/init_db at import time. If a previous session did reload
# models, these hold a function whose globals point at the old module; rebinding makes the
# fixture robust even in that case.
_DB_CONSUMERS = (
    "app.database.repository", "app.analyzers.pipeline", "app.analyzers.compliance",
    "app.extractors.report_segmenter", "app.extractors.drawing.requirements_extractor",
    "app.extractors.scwep_parser", "app.report.excel_writer", "app.extractors.code_indexer",
)


@pytest.fixture()
def fresh_db(tmp_path, monkeypatch):
    """Yield (models, repository) bound to an empty SQLite in tmp_path."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    monkeypatch.setattr("app.config.DATA_DIR", data)
    from app.database import models as m
    monkeypatch.setattr(m, "DATA_DIR", data)
    monkeypatch.setattr(m, "_engine", None, raising=False)
    monkeypatch.setattr(m, "_SessionFactory", None, raising=False)
    m.init_db()
    for name in _DB_CONSUMERS:
        try:
            mod = importlib.import_module(name)
        except Exception:      # noqa: BLE001 - optional consumers
            continue
        for fn in ("get_session", "init_db"):
            if hasattr(mod, fn):
                monkeypatch.setattr(mod, fn, getattr(m, fn))
    from app.database import repository as r
    from app.analyzers import scwep_basis
    scwep_basis.reset_cache()
    return m, r
