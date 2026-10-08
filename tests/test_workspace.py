"""自分の作業場: デモと分かれ、辞書で伏せ、テーマを立てられる（Firestore は差し替える）。"""
import pytest
from fastapi.testclient import TestClient

from untangle import api, persist


@pytest.fixture()
def client(monkeypatch):
    saved = {}
    monkeypatch.setattr(persist, "save", lambda wid, st: saved.__setitem__(wid, persist.dump(st)))
    monkeypatch.setattr(persist, "fetch", lambda wid: persist.load(saved[wid]) if wid in saved else None)
    api.WORKSPACES.clear()
    return TestClient(api.app), saved


def test_workspace_is_separate_from_demo_and_uses_its_own_dictionary(client):
    c, saved = client
    w = c.post("/api/workspaces", json={"shop": "テスト工房"}).json()
    H = {"X-Untangle-Workspace": w["id"]}
    assert c.get("/api/themes", headers=H).json() == []                 # デモのテーマは見えない
    assert c.get("/api/shops", headers=H).json()[0]["name"] == "テスト工房"
    assert c.post("/api/demo/reset", headers=H).status_code == 403       # 作業場は戻せない

    assert c.put("/api/dictionary", headers=H, json=[{"surface": "大西", "replacement": "スタッフA", "kind": "スタッフ"}]).json()["count"] == 1
    out = c.post("/api/intake/preview", headers=H, json={"text": "大西：子連れ2名です"}).json()["masked"]
    assert "大西" not in out and "スタッフA" in out
    assert c.put("/api/dictionary", json=[{"surface": "x", "replacement": "y"}]).status_code == 403  # デモの辞書は変えられない

    t = c.post("/api/themes", headers=H, json={
        "name": "予約の受け方",
        "questions": [{"title": "受けるか断るか", "answers": ["受ける", "断る"], "key_attributes": ["子供の年齢", "架空の項目"]}],
        "attributes": [{"name": "子供の年齢", "values": "3歳以下・4歳以上"}]}).json()
    theme = c.get(f"/api/themes/{t['id']}", headers=H).json()
    assert theme["questions"][0]["key_attributes"] == ["子供の年齢"]      # 語彙に無い項目は落とす

    api.WORKSPACES.clear()                                                # サーバーが入れ替わっても読み直せる
    info = c.get("/api/workspace", headers=H).json()
    assert info == {"workspace": True, "shop": "テスト工房", "themes": 1, "scenes": 0, "dictionary": 1}


def test_unknown_workspace_is_404(client):
    c, _ = client
    assert c.get("/api/themes", headers={"X-Untangle-Workspace": "nope"}).status_code == 404


def _theme(c, H):
    return c.post("/api/themes", headers=H, json={
        "name": "予約の受け方",
        "questions": [{"title": "受けるか断るか", "answers": ["受ける", "断る"], "key_attributes": ["曜日", "人数"]}],
        "attributes": [{"name": "曜日", "values": "平日・金土"}, {"name": "人数", "values": "5名以下・6〜10名"}]}).json()["id"]


def test_workspace_never_accepts_raw_text_and_only_accepts_enum_values(client):
    c, _ = client
    w = c.post("/api/workspaces", json={"shop": "テスト工房"}).json()
    H = {"X-Untangle-Workspace": w["id"]}
    tid = _theme(c, H)
    assert c.post("/api/intake", headers=H, json={"text": "佐藤：土曜8名", "theme_id": tid}).status_code == 403

    r = c.post("/api/intake/structured", headers=H, json={"theme_id": tid, "scenes": [
        {"question_id": "q1", "attributes": {"曜日": "金土", "人数": "6〜10名"}, "decision": "断る", "decider": "owner"},
        {"question_id": "q1", "attributes": {"曜日": "佐藤さん", "人数": "6〜10名"}, "decision": "断る", "decider": "owner"},
        {"question_id": "q1", "attributes": {"曜日": "平日"}, "decision": "店主の山田に確認", "decider": "owner"},
        {"question_id": "q1", "attributes": {"曜日": "平日", "人数": "不明"}, "decision": "受ける", "decider": "staff"},
    ]}).json()
    assert len(r["accepted"]) == 2
    assert [x["index"] for x in r["rejected"]] == [1, 2]                  # 選択肢に無い値は受け取らない
    assert r["accepted"][0]["situation"] == "曜日=金土・人数=6〜10名"       # 生の文は場面に入らない
    assert r["accepted"][1]["attributes"] == {"曜日": "平日"}              # 不明は落とす


def test_approved_path_rechecks_residual_names(client):
    c, _ = client
    w = c.post("/api/workspaces", json={"shop": "テスト工房"}).json()
    H = {"X-Untangle-Workspace": w["id"]}
    tid = _theme(c, H)
    c.put("/api/dictionary", headers=H, json=[{"surface": "大西", "replacement": "スタッフA", "kind": "スタッフ"}])
    r = c.post("/api/intake/approved", headers=H, json={"theme_id": tid, "masked_text": "大西：土曜8名", "approved": True})
    assert r.status_code == 409                                            # 辞書の名前が残っていれば読まない
    r = c.post("/api/intake/approved", headers=H, json={"theme_id": tid, "masked_text": "電話 090-1234-5678", "approved": True})
    assert r.status_code == 409
    assert c.post("/api/intake/approved", headers=H, json={"theme_id": tid, "masked_text": "x", "approved": False}).status_code == 400
