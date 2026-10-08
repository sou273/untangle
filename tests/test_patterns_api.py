"""判断のまとまり: 伏せ残りのある文は Gemini に渡さない（架空の文）。"""
from fastapi.testclient import TestClient

from untangle import api, gemini


def test_patterns_holds_unmasked_text(monkeypatch):
    seen = []

    def fake(items):
        seen.extend(items)
        return {"patterns": [{"name": "定員", "rule": "r", "refs": [1, 99], "variation": "", "question": ""}], "no_judgment": []}

    monkeypatch.setattr(gemini, "group_patterns", fake)
    c = TestClient(api.app)
    r = c.post("/api/patterns", json={"items": [
        {"ref": 1, "staff": "スタッフA", "subject": "確定", "text": "基本料金は［名前］様がまとめてお支払いとのこと承りました。"},
        {"ref": 2, "staff": "スタッフA", "subject": "確定", "text": "田中様のご予約を承りました。"},
        {"ref": 3, "staff": "スタッフB", "subject": "確定", "text": "03-1234-5678 までお電話ください。"},
        {"ref": 4, "staff": "山本", "subject": "確定", "text": "承りました。"},
    ]}).json()
    assert [it["ref"] for it in seen] == [1]
    assert sorted(r["held"]) == [2, 3, 4]
    assert r["patterns"][0]["refs"] == [1]          # 送っていない番号は返さない


def test_adopt_makes_article_and_retests(monkeypatch):
    c = TestClient(api.app)
    h = {"X-Untangle-Session": "adopt-test"}
    c.post("/api/demo/reset", headers=h)
    body = {"name": "人数変更とキャンセル料", "situation": "お客様から減員の連絡があったとき", "points": [{
        "question": "12名を下回る減員に、キャンセル料をとりますか",
        "options": ["2週間前からとる", "とらない"], "chosen": "2週間前からとる", "reason": "部屋と講師を押さえているため",
        "examples": [
            {"ref": 1, "staff": "スタッフA", "text": "12名を下回る減員は2週間前からキャンセル料を頂戴します。", "decision": "2週間前からとる"},
            {"ref": 2, "staff": "スタッフB", "text": "減員によるキャンセル料は発生いたしません。", "decision": "とらない"},
            {"ref": 3, "staff": "スタッフA", "text": "田中様の減員は承りました。", "decision": "とらない"},
        ]}]}
    r = c.post("/api/patterns/adopt", json=body, headers=h).json()
    assert r["results"][0]["article"] == 1
    assert r["results"][0]["match"] == 1 and r["results"][0]["differ"] == 1   # 名前の残る実例は場面にしない
    # 決め直すと、同じ問いの決まりは置き換わる
    body["points"][0]["chosen"] = "とらない"
    r2 = c.post("/api/patterns/adopt", json=body, headers=h).json()
    assert r2["theme_id"] == r["theme_id"] and r2["results"][0]["match"] == 1
    themes = c.get("/api/themes", headers=h).json()
    assert any(t["name"] == "人数変更とキャンセル料" for t in themes)


def test_adopt_rejects_names_in_owner_text():
    c = TestClient(api.app)
    r = c.post("/api/patterns/adopt", headers={"X-Untangle-Session": "adopt-test2"}, json={
        "name": "減員", "points": [{"question": "田中様の減員はどうしますか", "options": ["とる"], "chosen": "とる"}]})
    assert r.status_code == 400


def test_resolve_before_rule_and_exception():
    c = TestClient(api.app)
    h = {"X-Untangle-Session": "resolve-test"}
    c.post("/api/demo/reset", headers=h)
    q = "仮予約の支払い期限はいつまでにしますか"
    body = {"name": "仮予約と支払い", "situation": "日本の電話番号が無いお客様の予約", "points": [{
        "question": q, "options": ["本日中", "3日後まで"], "chosen": "本日中",
        "examples": [
            {"ref": 1, "staff": "スタッフA", "text": "本日中のお支払いをお願いします。", "decision": "本日中"},
            {"ref": 2, "staff": "スタッフB", "text": "3日後までにお支払いください。", "decision": "3日後まで"},
            {"ref": 3, "staff": "スタッフA", "text": "団体なので3日後までで結構です。", "decision": "3日後まで"},
        ]}]}
    r = c.post("/api/patterns/adopt", json=body, headers=h).json()["results"][0]
    assert r["match"] == 1 and r["differ"] == 2
    assert sorted(x["ref"] for x in r["differ_items"]) == [2, 3]
    res = c.post("/api/patterns/resolve", headers=h, json={"name": "仮予約と支払い", "question": q, "items": [
        {"ref": 2, "staff": "スタッフB", "text": "3日後までにお支払いください。", "choice": "follow"},
        {"ref": 3, "staff": "スタッフA", "text": "団体なので3日後までで結構です。", "choice": "exception", "condition": "団体のとき"},
    ]}).json()
    assert res["resolved"] == [2, 3]
    assert res["differ"] == 0 and res["match"] == 2          # 決まりより前の1通は外れ、例外の1通は例外の条文に当たる
    assert [a["condition"] for a in res["articles"]] == [None, "団体のとき"]


def test_watch_accumulates_then_completes_and_opens_manual(monkeypatch):
    c = TestClient(api.app)
    h = {"X-Untangle-Session": "complete-test"}
    c.post("/api/demo/reset", headers=h)
    q = "減員にキャンセル料をとりますか"
    ex = [{"ref": i, "staff": "スタッフA", "text": f"減員は2週間前から料金をいただきます（{i}）。", "decision": "とる", "date": f"2026-09-{i:02d}"} for i in (1, 2)]
    r = c.post("/api/patterns/adopt", headers=h, json={"name": "減員", "situation": "減員の連絡", "points": [
        {"question": q, "options": ["とる", "とらない"], "chosen": "とる", "examples": ex}]}).json()
    assert r["status"]["complete"] is False and r["status"]["window"] == 10
    tid = r["theme_id"]
    assert c.get(f"/api/themes/{tid}/exits/manual", headers=h).status_code == 409

    def fake(rules, items):
        return {"results": [{"ref": it["ref"], "pattern": "減員", "checks": [{"question_no": 1, "verdict": "決まりどおり"}]} for it in items]}
    monkeypatch.setattr(gemini, "watch_rules", fake)
    items = [{"ref": 100 + i, "staff": "スタッフB", "text": f"減員は2週間前からキャンセル料です（{i}）。", "date": f"2026-10-{i:02d}"} for i in range(1, 11)]
    w = c.post("/api/patterns/watch", headers=h, json={"rules": [{"name": "減員", "situation": "減員の連絡", "questions": [q], "decisions": ["とる"]}], "items": items}).json()
    st = w["status"]["減員"]
    assert st["complete"] is True and st["released"] == 1 and st["remaining"] == 0
    assert c.get(f"/api/themes/{tid}/exits/manual", headers=h).status_code == 200
    assert c.get(f"/api/themes/{tid}/exits/ai", headers=h).status_code == 409      # 条件の無い決まりに AI 向けテストは出さない
    # 決裁者が基準を変えられる
    assert c.post("/api/patterns/criteria", headers=h, json={"name": "減員", "window": 20}).json()["window"] == 20


def test_owner_words_like_guardian_and_dates_pass():
    from untangle.api import _plain
    assert _plain("保護者様に付き添いをお願いする", 100)
    assert _plain("3/4以降は受ける", 100)


def test_board_is_saved_only_in_a_workspace(monkeypatch):
    c = TestClient(api.app)
    assert c.get("/api/patterns/board").json() == {}
    assert c.put("/api/patterns/board", json={"groups": []}).status_code == 403   # 試し（作業場なし）は保存しない
    from untangle import persist
    from untangle.engine import Store
    saved = {"ws-board-test": persist.dump(Store(shop="架空の店"))}
    monkeypatch.setattr(persist, "save", lambda wid, st, *a, **k: saved.__setitem__(wid, persist.dump(st)))
    monkeypatch.setattr(persist, "fetch", lambda wid, *a, **k: persist.load(saved[wid]) if wid in saved else None)
    api.WORKSPACES.clear()
    h = {"X-Untangle-Workspace": "ws-board-test"}
    assert c.put("/api/patterns/board", headers=h, json={"groups": [{"name": "減員", "ok": True, "ids": ["m1"]}], "savedAt": 1}).json() == {"ok": True}
    api.WORKSPACES.clear()                                                   # 読み直しても残っている
    assert c.get("/api/patterns/board", headers=h).json()["groups"][0]["name"] == "減員"
    assert c.put("/api/patterns/board", headers=h, json={"nope": 1}).status_code == 400
