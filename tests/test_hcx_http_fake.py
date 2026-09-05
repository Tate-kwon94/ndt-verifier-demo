"""가짜 HCX 서버로 hcx_client 의 실제 HTTP 왕복 회귀 검증.

mock 이 못 덮는 층(요청 본문·헤더·Bearer·응답 파싱·에러 분류·재시도)을
로컬 HTTP 서버로 상시 검증한다. 사내 HCX 없이 첫 호출 리스크를 최소화.
"""
from __future__ import annotations
import os
import threading
import time
from http.server import ThreadingHTTPServer

import pytest

import tests.fake_hcx_server as fake


@pytest.fixture()
def fake_server():
    srv = ThreadingHTTPServer(("127.0.0.1", 0), fake.Handler)
    port = srv.server_address[1]
    t = threading.Thread(target=srv.serve_forever, daemon=True)
    t.start()
    fake._log.clear()
    fake._rate_hits.clear()
    yield f"http://127.0.0.1:{port}"
    srv.shutdown()


def _env(monkeypatch, base, token="testkey"):
    from app.config import load_yaml
    monkeypatch.setenv("NDT_HCX_BASE_URL", base)
    monkeypatch.setenv("NDT_HCX_MOCK", "0")
    monkeypatch.setenv("NDT_HCX_BUDGET_BYPASS", "1")
    if token is None:
        monkeypatch.delenv("NDT_HCX_TOKEN", raising=False)
    else:
        monkeypatch.setenv("NDT_HCX_TOKEN", token)
    load_yaml.cache_clear()


def _call(mode, monkeypatch):
    import app.hcx_client as hc
    orig = hc._build_headers
    monkeypatch.setattr(hc, "_build_headers",
                        lambda provider=None: {**orig(provider), "X-Fake-Mode": mode})
    return hc.call("hcx_check", {"ping": 1}, force_refresh=True)


def test_v3_roundtrip_contract(fake_server, monkeypatch):
    _env(monkeypatch, fake_server)
    resp = _call("echo", monkeypatch)
    assert resp.parsed == {"pong": True}
    log = fake._log[-1]
    assert log["model"] in ("HCX-007", "HCX-005")            # URL 에 모델명
    assert log["path"].startswith("/v3/chat-completions/")
    assert log["auth_present"]                                # Bearer
    assert log["has_request_id"]                              # X-Request-Id
    assert log["camelCase_ok"]                                # topP/topK/repetitionPenalty
    # 기본 모델은 HCX-007(추론 계열) → 반드시 maxCompletionTokens 여야 한다.
    assert log["token_field"] == "maxCompletionTokens"


def test_auth_failure_fastfail(fake_server, monkeypatch):
    from app.hcx_client import HCXClientError
    _env(monkeypatch, fake_server, token=None)
    t0 = time.time()
    with pytest.raises(HCXClientError) as ei:
        _call("echo", monkeypatch)
    assert "인증" in str(ei.value) or "NDT_HCX_TOKEN" in str(ei.value)
    assert time.time() - t0 < 3          # 재시도 없이 빠른 실패


def test_rate_limit_retry_then_success(fake_server, monkeypatch):
    _env(monkeypatch, fake_server)
    resp = _call("rate2", monkeypatch)
    assert resp.parsed == {"pong": True}
    assert fake._rate_hits.get("k") == 3   # 2 실패 + 1 성공


def test_context_length_fastfail(fake_server, monkeypatch):
    from app.hcx_client import HCXClientError
    _env(monkeypatch, fake_server)
    with pytest.raises(HCXClientError) as ei:
        _call("context", monkeypatch)
    assert "context" in str(ei.value).lower() or "40003" in str(ei.value)


def test_token_extraction(fake_server, monkeypatch):
    import app.hcx_client as hc
    _env(monkeypatch, fake_server)
    before = hc.get_call_stats()["token_total"]
    _call("echo", monkeypatch)
    assert hc.get_call_stats()["token_total"] >= before + 123


def test_fake_server_rejects_max_tokens_for_reasoning_model(fake_server):
    """가짜 서버가 사내 실측 규칙을 실제로 흉내내는지 — '테스트의 테스트'.

    2026-09-02 이전에는 fake 서버가 maxTokens/maxCompletionTokens 중
    아무거나 받아들였고, 그래서 HCX-007 + maxTokens 버그를 테스트가
    전혀 잡지 못했다 (134개 전부 통과한 채로 사내에서 40001 발생).
    이 테스트가 없으면 그 안전망이 사라져도 아무도 모른다.
    """
    import httpx
    r = httpx.post(
        f"{fake_server}/v3/chat-completions/HCX-007",
        json={"messages": [{"role": "user", "content": "ping"}], "maxTokens": 100},
        headers={"Authorization": "Bearer testkey", "Content-Type": "application/json"},
        timeout=10.0,
    )
    assert r.status_code == 400
    assert r.json()["status"]["code"] == "40001"
    assert "maxTokens" in r.json()["status"]["message"]


def test_fake_server_accepts_completion_tokens_for_reasoning_model(fake_server):
    """같은 요청을 maxCompletionTokens 로 바꾸면 통과해야 한다 (거짓 차단 방지)."""
    import httpx
    r = httpx.post(
        f"{fake_server}/v3/chat-completions/HCX-007",
        json={"messages": [{"role": "user", "content": "ping"}], "maxCompletionTokens": 100},
        headers={"Authorization": "Bearer testkey", "Content-Type": "application/json"},
        timeout=10.0,
    )
    assert r.status_code == 200
    assert r.json()["status"]["code"] == "20000"


def test_non_reasoning_model_may_use_max_tokens(fake_server):
    """HCX-005 는 maxTokens 를 그대로 쓴다 — 실측되지 않은 방향은 막지 않는다."""
    import httpx
    r = httpx.post(
        f"{fake_server}/v3/chat-completions/HCX-005",
        json={"messages": [{"role": "user", "content": "ping"}], "maxTokens": 100},
        headers={"Authorization": "Bearer testkey", "Content-Type": "application/json"},
        timeout=10.0,
    )
    assert r.status_code == 200
