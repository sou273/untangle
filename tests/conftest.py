"""テスト同士で、費用の歯止めの回数が積み重ならないようにする（歯止めのテストは自分で狭い上限を付ける）。"""
import pytest


@pytest.fixture(autouse=True)
def _fresh_limits(monkeypatch):
    from untangle import api
    api._hits.clear()
    for k in ("ai_ip_min", "ai_all_min", "ai_all_hour", "ws_ip_hour", "ws_all_day", "eval_all_hour"):
        monkeypatch.setitem(api.LIMITS, k, (10_000, api.LIMITS[k][1]))
    yield
    api._hits.clear()
