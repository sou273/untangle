"""費用と悪用の歯止め（架空の要求で）。"""
from fastapi.testclient import TestClient

from untangle import api


def test_ai_paths_are_rate_limited_per_ip(monkeypatch):
    api._hits.clear()
    monkeypatch.setitem(api.LIMITS, "ai_ip_min", (2, 60))
    c = TestClient(api.app)
    h = {"x-forwarded-for": "203.0.113.9"}
    codes = [c.post("/api/patterns", json={"items": []}, headers=h).status_code for _ in range(3)]
    assert codes[:2] == [400, 400] and codes[2] == 429          # 3回目は中身を見る前に止まる
    assert c.post("/api/patterns", json={"items": []}, headers={"x-forwarded-for": "203.0.113.10"}).status_code == 400
    api._hits.clear()


def test_docs_are_closed_and_body_size_limited():
    c = TestClient(api.app)
    assert c.get("/docs").status_code == 404 and c.get("/openapi.json").status_code == 404
    r = c.post("/api/patterns", content=b"{}", headers={"content-type": "application/json", "content-length": str(api.MAX_BODY + 1)})
    assert r.status_code == 413


def test_workspace_creation_is_limited(monkeypatch):
    api._hits.clear()
    monkeypatch.setitem(api.LIMITS, "ws_ip_hour", (0, 3600))
    c = TestClient(api.app)
    assert c.post("/api/workspaces", json={"shop": "架空の店"}, headers={"x-forwarded-for": "198.51.100.1"}).status_code == 429
    api._hits.clear()


def test_spoofed_forwarded_for_does_not_reset_the_count(monkeypatch):
    monkeypatch.setitem(api.LIMITS, "ai_ip_min", (1, 60))
    c = TestClient(api.app)
    assert c.post("/api/patterns", json={"items": []}, headers={"x-forwarded-for": "1.1.1.1, 203.0.113.50"}).status_code == 400
    assert c.post("/api/patterns", json={"items": []}, headers={"x-forwarded-for": "9.9.9.9, 203.0.113.50"}).status_code == 429
