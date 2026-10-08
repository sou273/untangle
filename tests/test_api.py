from fastapi.testclient import TestClient

from untangle import api


def client():
    c = TestClient(api.app)
    c.post("/api/demo/reset")
    return c


def test_exits_are_locked_until_complete_then_open():
    c = client()
    assert c.get("/api/themes/dantai/exits/ai").status_code == 409
    for q in c.get("/api/questions", params={"shop": "切子体験工房（創吉）"}).json():
        choice = {"inconsistent": "子供が正しい", "fact": "事実を直す"}.get(q["kind"], "この条文でよい")
        body = {"choice": choice, "reason": "テスト"}
        if q["kind"] == "fact":
            body["fact"] = {"団体種別": "高校"}
        assert c.post(f"/api/questions/{q['id']}/answer", json=body).status_code == 200
    t = c.get("/api/themes", params={"shop": "切子体験工房（創吉）"}).json()[0]
    assert t["complete"] and t["released_version"] == 1
    ai = c.get("/api/themes/dantai/exits/ai").json()
    assert "決裁者に回します" in ai["instruction"] and len(ai["tests"]) > 100
    manual = c.get("/api/themes/dantai/exits/manual").json()
    assert manual["ask_owner"][-1] == "この規程に書いていない場面"


def test_intake_preview_masks_and_rejects_secrets():
    c = client()
    r = c.post("/api/intake/preview", json={"text": "佐藤：山本先生の携帯は090-1234-5678です"}).json()
    assert "佐藤" not in r["masked"] and "090" not in r["masked"]
    r = c.post("/api/intake/preview", json={"text": "パスワード: abc"}).json()
    assert r["masked"] is None and r["rejected_reason"]


def test_wrong_choice_is_rejected():
    c = client()
    q = c.get("/api/questions").json()[0]
    assert c.post(f"/api/questions/{q['id']}/answer", json={"choice": "勝手な答え"}).status_code == 400


def test_sessions_are_isolated():
    a, b = TestClient(api.app, headers={"X-Untangle-Session": "a"}), TestClient(api.app, headers={"X-Untangle-Session": "b"})
    a.post("/api/demo/reset"); b.post("/api/demo/reset")
    q = a.get("/api/questions").json()[0]
    choice = q["choices"][0]
    body = {"choice": choice}
    if q["kind"] == "fact":
        body["fact"] = {"団体種別": "高校"}
    assert a.post(f"/api/questions/{q['id']}/answer", json=body).status_code == 200
    assert any(x["answer"] for x in a.get("/api/questions").json())
    assert not any(x["answer"] for x in b.get("/api/questions").json())


def test_shops_are_listed_and_filter_themes():
    c = client()
    shops = [x["name"] for x in c.get("/api/shops").json()]
    assert shops == ["切子体験工房（創吉）", "居酒屋 とり松"]
    iz = c.get("/api/themes", params={"shop": "居酒屋 とり松"}).json()
    assert [t["name"] for t in iz] == ["予約の受け方"]
    qs = c.get("/api/questions", params={"shop": "居酒屋 とり松"}).json()
    assert qs and all(q["theme_id"] == "yoyaku" for q in qs)
