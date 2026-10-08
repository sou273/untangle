"""Untangle の API と画面の配信。Cloud Run で動かす。"""
from __future__ import annotations

import copy
import os
import re
import threading
import time
from collections import OrderedDict
from contextvars import ContextVar
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from . import exits, gemma, persist, seed, seed_izakaya
from .engine import Store
from .masking import DictEntry, Masker

app = FastAPI(title="Untangle", docs_url=None, redoc_url=None, openapi_url=None)   # 受け口の一覧は公開しない
STATIC = Path(__file__).parent / "static"

# デモの状態はブラウザごとに分ける（審査員が同時に触っても互いに干渉しない）。
# 画面が X-Untangle-Session に乱数の ID を付けて送る。古いものから捨てる。
MAX_SESSIONS = 200
SESSIONS: OrderedDict[str, Store] = OrderedDict()
_current: ContextVar[str] = ContextVar("session", default="default")
_lock = threading.RLock()  # S() の中から _fresh() がもう一度取るので再入可能にする


SHOP_SEEDS = [seed, seed_izakaya]   # デモの店（どちらも架空）


def _build_template() -> Store:
    st = seed.build()
    seed_izakaya.build(st)
    for m in SHOP_SEEDS:
        st.refresh_questions(m.THEME_ID)
    return st


# 初期データの巡回はサーバーごとに1回だけ裏で行い、各ブラウザはその写しから始める
TEMPLATE: Store = _build_template()


TEMPLATE_CACHE = "untangle_template_cache"   # 作業場とは別の場所（作業場の ID で開けないように）


def _template_key() -> str:
    """デモの巡回結果を左右するコードの指紋。これが変わったときだけ巡回し直す。"""
    import hashlib
    from . import gemini
    h = hashlib.sha256(gemini.MODEL.encode())
    for name in ["seed.py", "seed_izakaya.py", "engine.py", "rules.py", "questions.py", "agent.py", "models.py"]:
        h.update((Path(__file__).parent / name).read_bytes())
    return h.hexdigest()[:24]


def _patrol_template() -> None:
    """デモの初期データをエージェントに巡回させる。結果は保存して使い回し、起動のたびに AI を呼ばない。"""
    global TEMPLATE
    if os.environ.get("UNTANGLE_AGENT", "1") != "1":
        return
    key = _template_key()
    try:
        cached = persist.fetch(key, TEMPLATE_CACHE)
    except Exception as e:  # noqa: BLE001  保存先が読めなくても、巡回し直せば動く
        print(f"[untangle] 巡回結果の保存を読めませんでした: {e}", flush=True)
        cached = None
    if cached is not None:
        with _lock:
            TEMPLATE = cached
            SESSIONS.clear()
        print(f"[untangle] 保存済みの巡回結果を使います（{key}）", flush=True)
        return
    from .agent import patrol
    st = seed.build()
    seed_izakaya.build(st)
    for m, trigger in [(seed, "デモの初期データ（過去1年の160件）を取り込んだ"),
                       (seed_izakaya, "デモの初期データ（店主メモとやり取りの20件）を取り込んだ")]:
        run = patrol(st, m.THEME_ID, trigger)
        print(f"[untangle] {m.THEME_ID} の巡回: 道具{len(run.steps)}回・新しい質問{len(run.new_question_ids)}件", flush=True)
    with _lock:
        TEMPLATE = st
        SESSIONS.clear()  # 巡回前の写しで始めたブラウザも、次の操作から巡回後の状態にする
    try:
        persist.save(key, st, TEMPLATE_CACHE)
    except Exception as e:  # noqa: BLE001
        print(f"[untangle] 巡回結果を保存できませんでした: {e}", flush=True)


def _fresh() -> Store:
    with _lock:
        return copy.deepcopy(TEMPLATE)


# 自分の作業場（実データ）。X-Untangle-Workspace に推測できない ID を付けて送る。Firestore に保存する
_workspace: ContextVar[str | None] = ContextVar("workspace", default=None)
WORKSPACES: dict[str, Store] = {}


def _ws_store(wid: str) -> Store:
    from . import persist
    with _lock:
        st = WORKSPACES.get(wid)
        if st is None:
            st = persist.fetch(wid)
            if st is None:
                raise HTTPException(404, "作業場が見つかりません。リンクを確かめてください")
            WORKSPACES[wid] = st
        return st


def save() -> None:
    """作業場なら保存する（デモは保存しない）。"""
    wid = _workspace.get()
    if wid:
        from . import persist
        persist.save(wid, WORKSPACES[wid])


def S() -> Store:
    wid = _workspace.get()
    if wid:
        return _ws_store(wid)
    sid = _current.get()
    with _lock:
        st = SESSIONS.get(sid)
        if st is None:
            st = SESSIONS[sid] = _fresh()
            while len(SESSIONS) > MAX_SESSIONS:
                SESSIONS.popitem(last=False)
        else:
            SESSIONS.move_to_end(sid)
        return st


@app.on_event("startup")
def _startup() -> None:
    # 起動の中で済ませる（保存済みなら1秒ほど）。裏で動かすと CPU を常時割り当てておく必要があり、待機中も課金される
    _patrol_template()


@app.middleware("http")
async def _no_stale_ui(request: Request, call_next):
    """画面のファイルは毎回新しいものがないか確かめさせる（古い app.js のまま動かさない）。"""
    resp = await call_next(request)
    if request.url.path in ("/", "/rules") or request.url.path.startswith("/static/"):
        resp.headers["Cache-Control"] = "no-cache"
    return resp


# ---- 費用と悪用の歯止め（公開URLなので、誰が叩いても使える額に上限を付ける） ----
# AI（Gemini・Gemma・エージェント）を呼ぶ受け口。接続元ごと・全体で回数を数える
AI_PATHS = re.compile(r"^/api/(patterns(/points|/watch|/agent)?|themes/propose|intake(/preview|/structured|/approved)?|themes/[^/]+/exits/ai/run|masking/eval)$")
LIMITS = {
    "ai_ip_min": (int(os.environ.get("UNTANGLE_AI_PER_IP_MIN", "6")), 60),
    "ai_all_min": (int(os.environ.get("UNTANGLE_AI_ALL_MIN", "30")), 60),
    "ai_all_hour": (int(os.environ.get("UNTANGLE_AI_ALL_HOUR", "300")), 3600),
    "eval_all_hour": (3, 3600),          # 伏せ方の試験は Gemma を40回呼ぶので重い
    "ws_ip_hour": (5, 3600),
    "ws_all_day": (100, 86400),
}
MAX_BODY = 1_000_000
_hits: dict[str, list[float]] = {}
_hits_lock = threading.Lock()


def _client_ip(request: Request) -> str:
    # 先頭は送る側が自由に書けるので、Google の受け口が付け足す末尾の値を使う
    fwd = request.headers.get("x-forwarded-for", "")
    return (fwd.split(",")[-1].strip() if fwd else (request.client.host if request.client else "")) or "unknown"


def _over(key: str, limit: int, window: int, now: float) -> bool:
    """窓の中の回数が上限に達していれば True。達していなければ1回数える。"""
    with _hits_lock:
        xs = [t for t in _hits.get(key, []) if now - t < window]
        if len(xs) >= limit:
            _hits[key] = xs
            return True
        xs.append(now)
        _hits[key] = xs
        return False


@app.middleware("http")
async def _guard(request: Request, call_next):
    from fastapi.responses import JSONResponse
    if request.method == "POST":
        if int(request.headers.get("content-length") or 0) > MAX_BODY:
            return JSONResponse({"detail": "送る量が大きすぎます"}, status_code=413)
        path, ip, now = request.url.path, _client_ip(request), time.monotonic()
        checks = []
        if AI_PATHS.match(path):
            checks += [(f"ai_ip_min:{ip}", *LIMITS["ai_ip_min"]), ("ai_all_min", *LIMITS["ai_all_min"]), ("ai_all_hour", *LIMITS["ai_all_hour"])]
        if path == "/api/masking/eval":
            checks.append(("eval_all_hour", *LIMITS["eval_all_hour"]))
        if path == "/api/workspaces":
            checks += [(f"ws_ip_hour:{ip}", *LIMITS["ws_ip_hour"]), ("ws_all_day", *LIMITS["ws_all_day"])]
        for key, limit, window in checks:
            if _over(key, limit, window, now):
                return JSONResponse({"detail": "混み合っています。少し時間をおいてから、もう一度押してください"}, status_code=429)
    return await call_next(request)


@app.middleware("http")
async def _session(request: Request, call_next):
    sid = request.headers.get("x-untangle-session", "default")[:64] or "default"
    wid = (request.headers.get("x-untangle-workspace") or "")[:64] or None
    token = _current.set(sid)
    wtoken = _workspace.set(wid)
    try:
        return await call_next(request)
    finally:
        _current.reset(token)
        _workspace.reset(wtoken)


# Gemini を呼ぶテストは費用がかかるので、ブラウザごとに間隔を空ける
AI_RUN_INTERVAL = int(os.environ.get("UNTANGLE_AI_RUN_INTERVAL", "60"))
_last_ai_run: dict[str, float] = {}

# デモ用の辞書（架空）。実運用では店の台帳から作る
# Gemma が使えるとき（UNTANGLE_OLLAMA_URL）は、台帳に無い固有名詞も Gemma で見つける
MASKER = Masker(detector=gemma.make_detector(timeout=150) if gemma.enabled() else None, entries=[
    DictEntry("佐藤", "ショップ担当A", "スタッフ"),
    DictEntry("田中", "ショップ担当B", "スタッフ"),
    DictEntry("店主", "決裁者", "スタッフ"),
    DictEntry("青葉台小学校", "学校（小）", "学校"),
    DictEntry("緑ヶ丘小学校", "学校（小）", "学校"),
    DictEntry("桜台小学校", "学校（小）", "学校"),
    DictEntry("山本先生", "教員", "お客様"),
    DictEntry("木村", "ホール担当A", "スタッフ"),
])


def masker() -> Masker:
    """作業場では、その店の辞書（画面から登録した実名）で伏せる。デモでは架空の辞書。"""
    if not _workspace.get():
        return MASKER
    entries = [DictEntry(d["surface"], d["replacement"], d.get("kind") or "辞書") for d in S().dictionary if d.get("surface")]
    return Masker(entries=entries, detector=MASKER.detector)


def _article(a):
    return {**a.model_dump(mode="json"), "sentence": exits.article_sentence(a)}


def _theme_summary(theme_id: str) -> dict:
    t = S().themes[theme_id]
    st = S().status(theme_id)
    open_q = [q for q in S().theme_questions(theme_id) if q.open]
    return {
        "id": t.id, "name": t.name, "scene_count": len(S().theme_scenes(theme_id)),
        "released_version": t.released_version, "complete": st.complete,
        "open_questions": len(open_q),
        "checks": [c.__dict__ for c in st.checks],
    }


def _shop_themes(shop: str | None) -> list[str]:
    return [tid for tid, t in S().themes.items() if not shop or t.shop == shop]


@app.get("/api/shops")
def shops():
    if _workspace.get():
        return [{"name": S().shop, "samples": [], "workspace": True}]
    seen: list[str] = []
    for t in S().themes.values():
        if t.shop not in seen:
            seen.append(t.shop)
    samples = {m.THEME.shop: m.SAMPLES for m in SHOP_SEEDS}
    return [{"name": n, "samples": [{"label": a, "text": b} for a, b in samples.get(n, [])]} for n in seen]


@app.get("/api/themes")
def themes(shop: str | None = None):
    return [{**_theme_summary(tid), "shop": S().themes[tid].shop} for tid in _shop_themes(shop)]


@app.get("/api/themes/{theme_id}")
def theme(theme_id: str):
    if theme_id not in S().themes:
        raise HTTPException(404, "テーマが見つかりません")
    st = S().status(theme_id)
    outcome = {r.scene.id: r.outcome.value for r in st.results}
    return {
        **_theme_summary(theme_id),
        "questions": [q.model_dump(mode="json") for q in S().themes[theme_id].questions],
        "criteria": S().themes[theme_id].criteria.model_dump(),
        "articles": [_article(a) for a in S().theme_articles(theme_id)],
        "scenes": [{**s.model_dump(mode="json"), "outcome": outcome.get(s.id)}
                   for s in S().theme_scenes(theme_id)],
    }


@app.get("/api/questions")
def questions(shop: str | None = None):
    out = []
    tids = set(_shop_themes(shop))
    for q in S().questions.values():
        if q.theme_id not in tids:
            continue
        d = q.model_dump(mode="json")
        d["scenes"] = [S().scenes[i].model_dump(mode="json") for i in q.scene_ids]
        if q.draft_article:
            d["draft_sentence"] = exits.article_sentence(q.draft_article)
        out.append(d)
    return out


class Answer(BaseModel):
    choice: str
    reason: str | None = None
    fact: dict | None = None


@app.post("/api/questions/{question_id}/answer")
def answer(question_id: str, body: Answer):
    if question_id not in S().questions:
        raise HTTPException(404, "質問が見つかりません")
    try:
        S().answer(question_id, body.choice, reason=body.reason, fact=body.fact, on=date.today())
    except ValueError as e:
        raise HTTPException(400, str(e))
    q = S().questions[question_id]
    S().refresh_questions(q.theme_id)
    if S().status(q.theme_id).complete and S().themes[q.theme_id].released_version is None:
        S().release(q.theme_id)
    save()
    return _theme_summary(q.theme_id)


@app.get("/api/themes/{theme_id}/exits/{kind}")
def exit_(theme_id: str, kind: str):
    t = S().themes.get(theme_id)
    if not t:
        raise HTTPException(404, "テーマが見つかりません")
    if t.source == "mail" and kind in ("ai", "spec"):
        # 送ったメールの決まりは条件の無い「いつでも○○」なので、AI 向けのテストも仕様書も中身が出ない。人向けのマニュアルだけ渡す
        raise HTTPException(409, "送ったメールから作った決まりは、スタッフ向けマニュアルだけを出します")
    if t.released_version is None:
        raise HTTPException(409, "まだ完了していないので、出口は出せません")
    arts, scenes, st = S().theme_articles(theme_id), S().theme_scenes(theme_id), S().status(theme_id)
    match kind:
        case "manual":
            return exits.manual(t, arts, st)
        case "ai":
            return exits.ai_instruction(t, arts, scenes, st)
        case "spec":
            return exits.spec(t, arts, scenes, st)
    raise HTTPException(404, "出口の種類が違います")


@app.post("/api/themes/{theme_id}/exits/ai/run")
def exit_ai_run(theme_id: str, request: Request):
    """出口②の指示を Gemini に渡し、同梱のテスト一式と規程に無い場面で正解数を数える。"""
    from .gemini import run_instruction_test
    ai = exit_(theme_id, "ai")
    sid = _client_ip(request)   # 誰でも付け替えられるセッションではなく、接続元ごとに間隔を空ける
    wait = AI_RUN_INTERVAL - (time.monotonic() - _last_ai_run.get(sid, -1e9))
    if wait > 0:
        raise HTTPException(429, f"続けて実行できません。{int(wait) + 1}秒後にもう一度押してください")
    _last_ai_run[sid] = time.monotonic()
    t = S().themes[theme_id]
    titles = {q.id: q.title for q in t.questions}
    boundary = seed.BOUNDARY_CASES if theme_id == seed.THEME_ID else []
    r = run_instruction_test(ai["instruction"], ai["tests"], titles, ai["options"], boundary, workers=16)
    crit = t.criteria
    r["passed"] = (r["normal_total"] - r["normal_ok"] <= crit.max_mismatches) and r["boundary_ok"] == r["boundary_total"]
    return r


class Intake(BaseModel):
    text: str
    shop: str | None = None
    theme_id: str | None = None


@app.post("/api/intake/preview")
def intake_preview(body: Intake):
    """貼り付けられたやり取りを伏せて見せる。元の文は保存しない。"""
    r = masker().mask(body.text)
    return {
        "masked": r.text,
        "rejected_reason": r.rejected_reason,
        "held": r.held,
        "residual_kinds": r.residual,
        "replacements": [x.__dict__ for x in r.replacements],
    }


@app.post("/api/intake")
def intake(body: Intake):
    """やり取りを伏せ、Gemini で場面として読み取り、テーマに加える。元の文は保存しない。

    伏せられなかった・消し残しの疑いがある文は Gemini に渡さず、決裁者の確認待ちにする。
    """
    from .gemini import read_scenes
    if _workspace.get():
        raise HTTPException(403, "作業場では、生の文はサーバーに送りません。端末の中で読み取ってから送ってください")
    r = masker().mask(body.text)
    out = {"masked": r.text, "rejected_reason": r.rejected_reason, "held": r.held,
           "residual_kinds": r.residual, "replacements": [x.__dict__ for x in r.replacements],
           "gemma": gemma.enabled()}
    if r.text is None or r.held:
        return {**out, "scenes": []}
    st = S()
    if body.theme_id and body.theme_id in st.themes:
        theme = st.themes[body.theme_id]
    else:
        tids = _shop_themes(body.shop if not _workspace.get() else None)
        if not tids:
            raise HTTPException(400, "まずテーマを立ててください")
        theme = st.themes[tids[0]]
    options = st.decision_options(theme.id)
    read = read_scenes(r.text, {"name": theme.name, "vocabulary": theme.vocabulary,
                                "questions": [q.model_dump() for q in theme.questions]}, options)
    if not read:
        return {**out, "scenes": [], "note": "判断が決まっていないやり取りなので、場面にはしませんでした"}
    before = {q.id for q in st.questions.values()}
    added = [st.add_scene(theme.id, x, source_ref="貼り付け") for x in read]
    run = None
    if os.environ.get("UNTANGLE_AGENT", "1") == "1":
        from .agent import patrol
        run = patrol(st, theme.id, f"記録が届いた（場面{len(added)}件）").to_dict()
    else:
        st.refresh_questions(theme.id)
    new_q = [q.model_dump(mode="json") for q in st.questions.values() if q.id not in before]
    ev = {x.scene.id: x.outcome.value for x in st.status(theme.id).results}
    save()
    return {**out, "scenes": [{**sc.model_dump(mode="json"), "outcome": ev.get(sc.id)} for sc in added],
            "new_questions": new_q, "agent_run": run}


# ---- 作業場の取り込み（生の文はサーバーに来ない） ----
UNKNOWN_VALUES = {"不明", ""}


def theme_enums(theme) -> dict[str, list[str]]:
    """項目ごとの選択肢（語彙の「・」区切り）。ここに無い値は受け取らない。"""
    import re as _re
    return {k: [x.strip() for x in _re.split(r"[・／/、,]", v) if x.strip()] for k, v in theme.vocabulary.items()}


@app.get("/api/themes/{theme_id}/schema")
def theme_schema(theme_id: str):
    """端末の中で読み取るための、項目と答えの選択肢。"""
    t = S().themes.get(theme_id)
    if not t:
        raise HTTPException(404, "テーマが見つかりません")
    return {"id": t.id, "name": t.name, "attributes": theme_enums(t),
            "questions": [{"id": q.id, "title": q.title, "answers": q.answers, "key_attributes": q.key_attributes}
                          for q in t.questions]}


class StructuredScene(BaseModel):
    question_id: str
    attributes: dict[str, str] = {}
    decision: str
    decider: str


class StructuredIntake(BaseModel):
    theme_id: str
    scenes: list[StructuredScene]


def _patrol_or_refresh(st: Store, tid: str, trigger: str) -> dict | None:
    if os.environ.get("UNTANGLE_AGENT", "1") == "1":
        from .agent import patrol
        return patrol(st, tid, trigger).to_dict()
    st.refresh_questions(tid)
    return None


@app.post("/api/intake/structured")
def intake_structured(body: StructuredIntake):
    """自動の道: 端末の中で選ばれた選択肢の値だけを受け取る。選択肢に無い値が1つでもあれば、その場面は受け取らない。"""
    st = S()
    t = st.themes.get(body.theme_id)
    if not t:
        raise HTTPException(404, "テーマが見つかりません")
    enums = theme_enums(t)
    qs = {q.id: q for q in t.questions}
    accepted, rejected = [], []
    for i, sc in enumerate(body.scenes[:200]):
        problems = []
        q = qs.get(sc.question_id)
        if not q:
            problems.append("問い")
        elif sc.decision not in q.answers:
            problems.append("判断")
        if sc.decider not in ("owner", "staff"):
            problems.append("決めた人")
        attrs = {}
        for k, v in sc.attributes.items():
            if v in UNKNOWN_VALUES:
                continue
            if k not in enums or v not in enums[k]:
                problems.append(f"項目「{k}」")
            else:
                attrs[k] = v
        if problems:
            rejected.append({"index": i, "problems": problems})
            continue
        situation = "・".join(f"{k}={v}" for k, v in attrs.items()) or "（項目の値なし）"
        accepted.append(st.add_scene(t.id, {"question_id": q.id, "attributes": attrs, "decision": sc.decision,
                                             "decider": sc.decider, "situation": situation, "reason": None},
                                     source_ref="端末内AIで読み取り（値だけを受け取った）"))
    run = _patrol_or_refresh(st, t.id, f"端末から値が届いた（場面{len(accepted)}件）") if accepted else None
    save()
    res = {x.scene.id: x.outcome.value for x in st.status(t.id).results}
    return {"accepted": [{**a.model_dump(mode="json"), "outcome": res.get(a.id)} for a in accepted],
            "rejected": rejected, "agent_run": run}


class ApprovedIntake(BaseModel):
    theme_id: str
    masked_text: str
    approved: bool


@app.post("/api/intake/approved")
def intake_approved(body: ApprovedIntake):
    """人が確かめる道: 端末で伏せた文のうち、決裁者が「この形で送ってよい」と押したものだけを Gemini で読む。

    念のためサーバーでも、作業場の辞書と決まった規則で消し残しを検査し、疑いがあれば読まずに返す。
    """
    from .gemini import read_scenes
    if not body.approved:
        raise HTTPException(400, "決裁者が確かめたものだけを送ってください")
    st = S()
    t = st.themes.get(body.theme_id)
    if not t:
        raise HTTPException(404, "テーマが見つかりません")
    m = masker()
    rej = m.mask(body.masked_text)
    if rej.text is None:
        raise HTTPException(400, rej.rejected_reason or "取り込めない文です")
    left = Masker(entries=m.entries).residual_check(body.masked_text)
    if left:
        raise HTTPException(409, "まだ名前などが残っている疑いがあります: " + "・".join(sorted(set(left))))
    read = read_scenes(body.masked_text, {"name": t.name, "vocabulary": t.vocabulary,
                                          "questions": [q.model_dump() for q in t.questions]}, st.decision_options(t.id))
    added = [st.add_scene(t.id, x, source_ref="決裁者が確かめて送った文") for x in read]
    run = _patrol_or_refresh(st, t.id, f"決裁者が確かめた文が届いた（場面{len(added)}件）") if added else None
    save()
    res = {x.scene.id: x.outcome.value for x in st.status(t.id).results}
    return {"accepted": [{**a.model_dump(mode="json"), "outcome": res.get(a.id)} for a in added], "agent_run": run}


@app.get("/api/agent/runs")
def agent_runs(shop: str | None = None):
    tids = {t for t in _shop_themes(shop)}
    names = {S().themes[t].name for t in tids}
    return [r for r in reversed(S().agent_runs) if not shop or r.get("theme") in tids or r.get("theme_name") in names]


_EVAL_CACHE: dict[tuple, tuple[float, dict]] = {}


@app.post("/api/masking/eval")
def masking_eval(set: str = "holdout", ledger: bool = False):
    """伏せ方のテスト。架空の例文で、AIに届いてしまう名前（消し漏れ）と判断に要る語の消しすぎを数える。

    同じ条件の結果は1時間使い回す（Gemma を40回呼ぶため）。
    """
    from . import masking_eval as me
    key = (set, ledger, gemma.enabled())
    hit = _EVAL_CACHE.get(key)
    if hit and time.time() - hit[0] < 3600:
        return {**hit[1], "cached": True}
    cases = me.HOLDOUT if set == "holdout" else me.CASES
    entries = (me.LEDGER if ledger else [])  # 例文の名前は店の台帳に無い前提（最も厳しい条件）
    m = Masker(entries=entries, detector=gemma.make_detector(timeout=150) if gemma.enabled() else None)
    started = time.time()
    r = me.evaluate(m, cases, workers=4)
    out = {
        "set": set, "ledger": ledger, "gemma": gemma.enabled(), "cases": r.total,
        "names": sum(len(c[1]) for c in cases), "leaks": len(r.leaks), "over": len(r.over), "held": r.held,
        # 保留（Gemini に渡さなかった文）が残る間は、漏れを数えきれていないので合格にしない
        "passed": len(r.leaks) == 0 and len(r.over) == 0 and r.held == 0,
        "undecided": r.held > 0,
        "criteria": "AIに届いてしまう名前 0件・判断に要る語の消しすぎ 0件",
        "seconds": round(time.time() - started, 1), "rows": r.rows,
        "measured_at": time.strftime("%Y-%m-%d %H:%M", time.localtime()),
    }
    if not out["undecided"]:  # 判定できなかった結果は使い回さない
        _EVAL_CACHE[key] = (time.time(), out)
    return {**out, "cached": False}


DATA = Path(__file__).parent / "data"


@app.get("/api/masking/eval/latest")
def masking_eval_latest():
    """本番で実行した伏せ方テストの最新の記録（画面を開いたときに最初に見せる。3分待たせないため）。"""
    import json
    out = {}
    for f in sorted(DATA.glob("eval_*.json")):
        _, set_, led = f.stem.split("_")
        out[f"{set_}:{1 if led == 'true' else 0}"] = {**json.loads(f.read_text()), "recorded": True}
    return out


# ---- 自分の作業場 ----
class NewWorkspace(BaseModel):
    shop: str


@app.post("/api/workspaces")
def create_workspace(body: NewWorkspace):
    """自分の作業場を作る。返す ID を知っている人だけが開ける（公開URLのまま、デモと分ける）。"""
    from . import persist
    wid = persist.new_id()
    st = Store(shop=body.shop.strip()[:40] or "自分の店")
    with _lock:
        WORKSPACES[wid] = st
    persist.save(wid, st)
    return {"id": wid, "shop": st.shop}


@app.get("/api/workspace")
def workspace_info():
    if not _workspace.get():
        return {"workspace": False}
    st = S()
    return {"workspace": True, "shop": st.shop, "themes": len(st.themes), "scenes": len(st.scenes),
            "dictionary": len(st.dictionary)}


class DictRow(BaseModel):
    surface: str
    replacement: str
    kind: str = ""


@app.get("/api/dictionary")
def get_dictionary():
    if not _workspace.get():
        return [{"surface": e.surface, "replacement": e.replacement, "kind": e.kind} for e in MASKER.entries]
    return S().dictionary


@app.put("/api/dictionary")
def put_dictionary(rows: list[DictRow]):
    if not _workspace.get():
        raise HTTPException(403, "デモの辞書は変えられません。自分の作業場で登録してください")
    S().dictionary = [r.model_dump() for r in rows if r.surface.strip() and r.replacement.strip()][:2000]
    save()
    return {"ok": True, "count": len(S().dictionary)}


class ThemeIdea(BaseModel):
    name: str
    description: str = ""
    examples: list[str] = []


@app.post("/api/themes/propose")
def propose(body: ThemeIdea):
    """テーマの案を出す。例のやり取りは伏せてから Gemini に渡す（伏せられない例は使わない）。"""
    from .gemini import propose_theme
    m = masker()
    masked, skipped = [], []
    for i, ex in enumerate(body.examples[:10], 1):
        r = m.mask(ex)
        if r.text and not r.held:
            masked.append(r.text)
        else:
            skipped.append({"no": i, "reason": r.rejected_reason or ("消し残しの疑い: " + "・".join(r.residual))})
    idea = propose_theme(body.name, body.description, masked)
    return {**idea, "used_examples": len(masked), "skipped": skipped, "masked_examples": masked}


class PatternItem(BaseModel):
    ref: int
    staff: str
    subject: str = ""
    text: str
    date: str | None = None          # メールを送った日（YYYY-MM-DD）。当て直しの順番に使う


class PatternRequest(BaseModel):
    items: list[PatternItem]


PATTERN_MAX_ITEMS = 300
STAFF_LABEL = re.compile(r"^(スタッフ[A-Z]{1,2}|自動|不明)$")


def _checked(items: list[PatternItem]) -> tuple[list[dict], list[int]]:
    """ブラウザで伏せた文を、決まった規則でもう一度検査する。何か伏せられた文・スタッフの実名が付いた文は外す。"""
    check = Masker([], detector=None)
    ok, held = [], []
    for it in items:
        text, subject = it.text[:800], it.subject[:120]
        if not STAFF_LABEL.match(it.staff):
            held.append(it.ref); continue
        r1, r2 = check.mask(text), check.mask(subject)
        if r1.text != text or r2.text != subject or r1.held or r2.held:
            held.append(it.ref); continue
        ok.append({"ref": it.ref, "staff": it.staff, "subject": subject, "text": text})
    return ok, held


def _plain(text: str, limit: int) -> str:
    """決裁者が画面で書いた短い文（パターン名・場面・問い・答え）。名前などが入っていれば受け付けない。"""
    text = (text or "").strip()[:limit]
    r = Masker([], detector=None).mask(text)
    if r.held or any(x.kind != "日付" for x in r.replacements):   # 日付（3/4以降 など）は決まりの言葉として通す
        raise HTTPException(400, f"名前や番号のようなものが含まれています: {text[:30]}")
    return text


def _day(v: str | None) -> date:
    try:
        return date.fromisoformat((v or "")[:10])
    except ValueError:
        return date.today()


MAIL_NOVELTY_WINDOW = 10   # 送ったメールのテーマは「直近10通に新しい話が無い」で完了（決裁者が変えられる）


def _pattern_status(st: Store, theme) -> dict:
    """完了までの残りと、完了していれば版を出す（送ったメールの流れは本体の質問を通らないので、ここで出す）。"""
    status = st.status(theme.id)
    if status.complete and theme.released_version is None:
        st.release(theme.id)
    ordered = sorted((x for x in st.theme_scenes(theme.id) if x.question_id and x.counts_for_test()), key=lambda x: x.occurred_on)
    seen, last_new = set(), -1
    for i, x in enumerate(ordered):
        if x.question_id not in seen:
            seen.add(x.question_id)
            last_new = i
    window = theme.criteria.novelty_window
    remaining = max(0, last_new + window + 1 - len(ordered)) if ordered else window
    return {"theme_id": theme.id, "complete": status.complete, "released": theme.released_version,
            "window": window, "remaining": remaining,
            "checks": [{"label": c.label, "ok": c.ok, "answer": c.answer} for c in status.checks]}


class PointsRequest(BaseModel):
    name: str
    situation: str = ""
    items: list[PatternItem]


@app.post("/api/patterns/points")
def pattern_points(body: PointsRequest):
    """第2段: 確かめたパターンについて、決裁者に決めてもらうことと、実例に出た答え方を並べる。"""
    from .gemini import decision_points
    if not body.items or len(body.items) > 100:
        raise HTTPException(400, "実例は1〜100通にしてください")
    name, situation = _plain(body.name, 60), _plain(body.situation, 200)
    ok, held = _checked(body.items)
    if not ok:
        return {"points": [], "held": held}
    out = decision_points(name, situation, ok)
    refs = {it["ref"] for it in ok}
    points = []
    for p in out.get("points", []):
        opts = [{"label": o["label"].strip(), "refs": sorted({r for r in o.get("refs", []) if r in refs})}
                for o in p.get("options", []) if o.get("label", "").strip()]
        if p.get("question", "").strip() and opts:
            points.append({"question": p["question"].strip(), "options": opts})
    return {"points": points, "held": held}


class AdoptExample(BaseModel):
    ref: int
    staff: str
    text: str
    decision: str
    date: str | None = None


class AdoptPoint(BaseModel):
    question: str
    options: list[str]
    chosen: str
    reason: str = ""
    examples: list[AdoptExample] = []


class AdoptRequest(BaseModel):
    name: str
    situation: str = ""
    points: list[AdoptPoint]


@app.post("/api/patterns/adopt")
def pattern_adopt(body: AdoptRequest):
    """第2段: 決裁者の答えを Untangle のテーマの決まり（第N条）にし、実例で当て直す。

    実例は伏せた後の文だけを場面として残す（スタッフの答えはスタッフの判断として、答え合わせ側に回る）。
    """
    from .models import Article, ArticleStatus, Decider, Scene, Source, Theme, ThemeQuestion
    from .rules import run_question_test
    if not body.points:
        raise HTTPException(400, "決めたことがありません")
    st = S()
    name, situation = _plain(body.name, 60), _plain(body.situation, 200)
    theme = next((t for t in st.themes.values() if t.name == name and t.shop == st.shop), None)
    if theme is None:
        tid = st.next_id("theme")
        theme = Theme(id=tid, name=name, shop=st.shop, questions=[], source="mail")
        theme.criteria.novelty_window = MAIL_NOVELTY_WINDOW
        st.themes[tid] = theme
    out = []
    for p in body.points:
        question, chosen, reason = _plain(p.question, 200), _plain(p.chosen, 200), _plain(p.reason, 300)
        options = [_plain(o, 200) for o in p.options if o.strip()]
        if chosen not in options:
            options.append(chosen)
        tq = next((q for q in theme.questions if q.title == question), None)
        if tq is None:
            tq = ThemeQuestion(id=f"q{len(theme.questions) + 1}", title=question, key_attributes=[], answers=options)
            theme.questions.append(tq)
        else:
            tq.answers = list(dict.fromkeys([*tq.answers, *options]))
        ok, _ = _checked([PatternItem(ref=e.ref, staff=e.staff, text=e.text) for e in p.examples])
        dec = {e.ref: e.decision for e in p.examples}
        day = {e.ref: _day(e.date) for e in p.examples}
        have = {(x.question_id, x.situation) for x in st.theme_scenes(theme.id)}
        ref_of = {}
        for x in st.theme_scenes(theme.id):
            if x.question_id == tq.id:
                ref_of[x.id] = next((e["ref"] for e in ok if e["text"] == x.situation), None)
        for e in ok:
            if (tq.id, e["text"]) in have:   # 決め直したときに同じ実例を二重に入れない
                continue
            sc = Scene(id=st.next_id("in"), theme_id=theme.id, question_id=tq.id, occurred_on=day[e["ref"]],
                       situation=e["text"], decision=dec[e["ref"]], source=Source.CONVERSATION,
                       source_ref="送ったメール", decider=Decider.STAFF, decider_role=e["staff"])
            st.scenes[sc.id] = sc
            ref_of[sc.id] = e["ref"]
        # 同じ問いの古い決まりは置き換える（決裁者が決め直した）
        for a in [a for a in st.theme_articles(theme.id) if a.question_id == tq.id]:
            del st.articles[a.id]
        number = max((x.number for x in st.theme_articles(theme.id)), default=0) + 1
        art = Article(id=st.next_id("a"), theme_id=theme.id, question_id=tq.id, number=number, conditions=[],
                      decision=chosen, reason=reason or None, status=ArticleStatus.CONFIRMED,
                      evidence_scene_ids=[])
        st.articles[art.id] = art
        res = run_question_test(theme, tq.id, st.theme_articles(theme.id), st.theme_scenes(theme.id))
        counts: dict[str, int] = {}
        for r in res:
            counts[r.outcome.value] = counts.get(r.outcome.value, 0) + 1
        differ = [{"ref": ref_of.get(r.scene.id), "decision": r.scene.decision} for r in res
                  if r.outcome.value != "match" and ref_of.get(r.scene.id) is not None]
        out.append({"question": question, "article": number, "decision": chosen,
                    "match": counts.get("match", 0), "differ": sum(v for k, v in counts.items() if k != "match"),
                    "differ_items": differ})
    st.promote(theme.id)
    status = _pattern_status(st, theme)
    save()
    return {"theme_id": theme.id, "theme": theme.name, "results": out, "workspace": bool(_workspace.get()), "status": status}


class ResolveItem(BaseModel):
    ref: int
    staff: str
    text: str
    choice: str                      # "follow"（これからは決まりどおり）／ "exception"（この場合は例外）
    condition: str = ""              # 例外のときの条件（決裁者が書く）
    decision: str = ""               # 見張りで見つけた返事を例外にするときの、その場合の答え（決裁者が書く）


class ResolveRequest(BaseModel):
    name: str
    question: str
    items: list[ResolveItem]


EXCEPTION_ATTR = "例外の条件"


@app.post("/api/patterns/resolve")
def pattern_resolve(body: ResolveRequest):
    """第3段: 決まりと違う答え方をしていた実例を片付ける。

    これからは決まりどおり → その実例は「決まりより前の答え方」として当て直しから外す（スタッフへ伝える側に回る）。
    この場合は例外 → 決裁者が書いた条件を項目にし、その条件の時はその答えとする条文を足す。
    """
    from .models import Condition, SceneFlag
    from .rules import run_question_test
    st = S()
    name, question = _plain(body.name, 60), _plain(body.question, 200)
    theme = next((t for t in st.themes.values() if t.name == name and t.shop == st.shop), None)
    tq = theme and next((q for q in theme.questions if q.title == question), None)
    if tq is None:
        raise HTTPException(404, "先にこの問いを決まりにしてください")
    scenes = {x.situation: x for x in st.theme_scenes(theme.id) if x.question_id == tq.id}
    ok, _ = _checked([PatternItem(ref=i.ref, staff=i.staff, text=i.text) for i in body.items])
    texts = {e["ref"]: e["text"] for e in ok}
    done = []
    from .models import Decider, Scene, Source
    for it in body.items:
        sc = scenes.get(texts.get(it.ref, ""))
        if sc is None and it.decision.strip() and it.ref in texts:
            # 見張りで見つけた、決まりを作った後の返事。場面として加える
            sc = Scene(id=st.next_id("in"), theme_id=theme.id, question_id=tq.id, occurred_on=date.today(),
                       situation=texts[it.ref], decision=_plain(it.decision, 200), source=Source.CONVERSATION,
                       source_ref="送ったメール（見張り）", decider=Decider.STAFF, decider_role=it.staff)
            st.scenes[sc.id] = sc
            scenes[sc.situation] = sc
        if sc is None:
            continue
        if it.choice == "follow":
            if SceneFlag.BEFORE_RULE not in sc.flags:
                sc.flags.append(SceneFlag.BEFORE_RULE)
        elif it.choice == "exception":
            cond = _plain(it.condition, 100)
            if not cond:
                raise HTTPException(400, "例外の条件を書いてください")
            theme.vocabulary.setdefault(EXCEPTION_ATTR, "決裁者が書いた例外の条件")
            sc.attributes[EXCEPTION_ATTR] = cond
            exists = any(a.question_id == tq.id and any(c.attribute == EXCEPTION_ATTR and c.value == cond for c in a.conditions)
                         for a in st.theme_articles(theme.id))
            if not exists:
                base = next((a for a in st.theme_articles(theme.id) if a.question_id == tq.id and not a.conditions), None)
                art = st._confirm_new_article(theme, tq.id, [Condition(attribute=EXCEPTION_ATTR, value=cond)], sc.decision,
                                              None, [sc.id], exception_of=base.id if base else None)
                if base:
                    art.priority = base.priority
        else:
            raise HTTPException(400, "選び方が違います")
        done.append(it.ref)
    st.promote(theme.id)
    res = run_question_test(theme, tq.id, st.theme_articles(theme.id), st.theme_scenes(theme.id))
    status = _pattern_status(st, theme)
    save()
    return {"status": status, "resolved": done, "match": sum(1 for r in res if r.outcome.value == "match"),
            "differ": sum(1 for r in res if r.outcome.value != "match"),
            "articles": [{"number": a.number, "decision": a.decision,
                          "condition": next((c.value for c in a.conditions if c.attribute == EXCEPTION_ATTR), None)}
                         for a in sorted(st.theme_articles(theme.id), key=lambda a: a.number) if a.question_id == tq.id]}


class WatchRule(BaseModel):
    name: str
    situation: str = ""
    questions: list[str]
    decisions: list[str]


class WatchRequest(BaseModel):
    rules: list[WatchRule]
    items: list[PatternItem]


def _watch_record(rules: list[dict], ok: list[dict], dates: dict) -> tuple[list[dict], dict]:
    """決まりを作った後の返事が決まりどおりかを Gemini に選ばせ、決まりどおりの返事は実例として積む。"""
    from .gemini import watch_rules
    from .models import Decider, Scene, Source
    out = watch_rules(rules, ok) if ok else {"results": []}
    refs = {it["ref"] for it in ok}
    results = [r for r in out.get("results", []) if r.get("ref") in refs]
    st = S()
    by_ref = {it["ref"]: it for it in ok}
    for r in results:
        rule = next((x for x in rules if x["name"] == r.get("pattern")), None)
        theme = rule and next((t for t in st.themes.values() if t.name == rule["name"] and t.shop == st.shop), None)
        if theme is None:
            continue
        have = {(x.question_id, x.situation) for x in st.theme_scenes(theme.id)}
        for c in r.get("checks", []):
            i = c.get("question_no", 0) - 1
            if c.get("verdict") != "決まりどおり" or not (0 <= i < len(rule["points"])):
                continue
            tq = next((q for q in theme.questions if q.title == rule["points"][i]["question"]), None)
            text = by_ref[r["ref"]]["text"]
            if tq is None or (tq.id, text) in have:
                continue
            sc = Scene(id=st.next_id("in"), theme_id=theme.id, question_id=tq.id, occurred_on=_day(dates.get(r["ref"])),
                       situation=text, decision=rule["points"][i]["decision"], source=Source.CONVERSATION,
                       source_ref="送ったメール（見張り）", decider=Decider.STAFF, decider_role=by_ref[r["ref"]]["staff"])
            st.scenes[sc.id] = sc
    status = {}
    for theme in {t.id: t for t in st.themes.values() if t.name in {x["name"] for x in rules} and t.shop == st.shop}.values():
        st.promote(theme.id)
        status[theme.name] = _pattern_status(st, theme)
    save()
    return results, status


class BoardTheme(BaseModel):
    name: str
    state: str
    remaining: int | None = None


class StewardRequest(BaseModel):
    rules: list[WatchRule] = []
    items: list[PatternItem] = []
    board: list[BoardTheme] = []


@app.post("/api/patterns/agent")
def pattern_agent(body: StewardRequest):
    """エージェントの巡回: テーマの状態を読み、新しい返事を見張り、決裁者がやることを洗い出してメモを書く。
    決まりを確定・片付ける道具は持たない（確定は決裁者だけ）。"""
    from .agent import steward_patrol
    if len(body.items) > PATTERN_MAX_ITEMS or len(body.board) > 50:
        raise HTTPException(400, "量が多すぎます")
    rules = []
    for r in body.rules:
        try:
            rules.append({"name": _plain(r.name, 60), "situation": _plain(r.situation, 200),
                          "points": [{"question": _plain(q, 200), "decision": _plain(d, 200)} for q, d in zip(r.questions, r.decisions)]})
        except HTTPException:
            continue
    board = []
    for b in body.board:
        try:
            board.append({"name": _plain(b.name, 60), "state": b.state[:20], "remaining": b.remaining})
        except HTTPException:
            continue
    ok, held = _checked(body.items)
    dates = {it.ref: it.date for it in body.items}
    run = steward_patrol(board, rules, ok,
                         watch=lambda: _watch_record(rules, ok, dates) if rules and ok else ([], {}),
                         plain=lambda t, n: _plain(t, n))
    return {**run, "held": held}


@app.post("/api/patterns/watch")
def pattern_watch(body: WatchRequest):
    """第3段の見張り: 決まりを作ってから送ったメールが、どのパターンに入り、決まりどおりかを確かめる。"""
    from .gemini import watch_rules
    if not body.rules or not body.items or len(body.items) > PATTERN_MAX_ITEMS:
        raise HTTPException(400, "決まりとメールを送ってください")
    rules = [{"name": _plain(r.name, 60), "situation": _plain(r.situation, 200),
              "points": [{"question": _plain(q, 200), "decision": _plain(d, 200)} for q, d in zip(r.questions, r.decisions)]}
             for r in body.rules]
    ok, held = _checked(body.items)
    if not ok:
        return {"results": [], "held": held}
    results, status = _watch_record(rules, ok, {it.ref: it.date for it in body.items})
    return {"results": results, "held": held, "status": status}


BOARD_MAX = 400_000   # ダッシュボードの状態の大きさの上限（文字数）


@app.get("/api/patterns/board")
def pattern_board_get():
    """作業場に保存したテーマの進み具合。作業場が無ければ空（架空のメールの試しはブラウザだけに残す）。"""
    if not _workspace.get():
        return {}
    return S().pattern_board or {}


@app.put("/api/patterns/board")
async def pattern_board_put(request: Request):
    """テーマの進み具合を作業場に保存する（別の端末でも同じ状態が見えるように）。中身はメールID・選んだ答え・状態だけ。"""
    if not _workspace.get():
        raise HTTPException(403, "作業場が無いので保存できません")
    raw = await request.body()
    if len(raw) > BOARD_MAX * 4:
        raise HTTPException(413, "保存する量が大きすぎます")
    import json
    try:
        board = json.loads(raw)
    except ValueError:
        raise HTTPException(400, "形が違います")
    if not isinstance(board, dict) or not isinstance(board.get("groups"), list):
        raise HTTPException(400, "形が違います")
    if len(json.dumps(board, ensure_ascii=False)) > BOARD_MAX:
        raise HTTPException(413, "保存する量が大きすぎます")
    st = S()
    st.pattern_board = board
    save()
    return {"ok": True}


@app.get("/api/patterns/status")
def pattern_status(names: str = ""):
    """送ったメールのテーマの、完了までの残り（画面を開き直したとき用。AI は呼ばない）。"""
    st = S()
    want = {n for n in names.split("|") if n}
    return {t.name: _pattern_status(st, t) for t in st.themes.values() if t.source == "mail" and t.name in want and t.shop == st.shop}


class CriteriaRequest(BaseModel):
    name: str
    window: int


@app.post("/api/patterns/criteria")
def pattern_criteria(body: CriteriaRequest):
    """完了とみなす件数（直近何通に新しい話が無ければ完了か）を決裁者が変える。"""
    st = S()
    theme = next((t for t in st.themes.values() if t.name == body.name and t.shop == st.shop), None)
    if theme is None:
        raise HTTPException(404, "テーマが見つかりません")
    theme.criteria.novelty_window = max(3, min(50, body.window))
    status = _pattern_status(st, theme)
    save()
    return status


@app.post("/api/patterns")
def patterns(body: PatternRequest):
    """送ったメールの「ひな形に無い文」（ブラウザで伏せ、決裁者が送ると決めたもの）を判断のまとまりに分ける。

    生の文は受け取らない前提だが、念のため決まった規則でもう一度検査し、何か見つかった文は Gemini に渡さない。
    受け取った文はどこにも保存しない。
    """
    from .gemini import group_patterns
    if not body.items:
        raise HTTPException(400, "文がありません")
    if len(body.items) > PATTERN_MAX_ITEMS:
        raise HTTPException(400, f"一度に送れるのは{PATTERN_MAX_ITEMS}通までです")
    ok, held = _checked(body.items)
    if not ok:
        return {"patterns": [], "no_judgment": [], "held": held}
    out = group_patterns(ok)
    refs = {it["ref"] for it in ok}
    pats = []
    for p in out.get("patterns", []):
        rs = sorted({r for r in p.get("refs", []) if r in refs})
        if rs:
            pats.append({**p, "refs": rs})
    pats.sort(key=lambda p: -len(p["refs"]))
    return {"patterns": pats, "no_judgment": [r for r in out.get("no_judgment", []) if r in refs], "held": held}


class ThemeDraft(BaseModel):
    name: str
    questions: list[dict]
    attributes: list[dict]
    masked_examples: list[str] = []   # 案を出すときに伏せ済みの例（そのまま場面として取り込む）


@app.post("/api/themes")
def create_theme(body: ThemeDraft):
    """決裁者が直した案でテーマを立てる。"""
    from .models import Theme, ThemeQuestion
    if not _workspace.get():
        raise HTTPException(403, "デモではテーマを立てられません。自分の作業場で立ててください")
    st = S()
    vocab = {a["name"].strip(): a.get("values", "").strip() for a in body.attributes if a.get("name", "").strip()}
    qs = []
    for i, q in enumerate(body.questions, 1):
        keys = [k for k in q.get("key_attributes", []) if k in vocab]
        answers = [a.strip() for a in q.get("answers", []) if a.strip()]
        if q.get("title", "").strip() and answers:
            qs.append(ThemeQuestion(id=f"q{i}", title=q["title"].strip(), key_attributes=keys, answers=answers))
    if not qs:
        raise HTTPException(400, "問いを1つ以上、答えの選択肢つきで入れてください")
    tid = st.next_id("theme")
    st.themes[tid] = Theme(id=tid, name=body.name.strip() or "新しいテーマ", shop=st.shop,
                           questions=qs, vocabulary=vocab)
    added = _ingest_masked(st, tid, body.masked_examples[:10], "テーマを立てたときの例")
    save()
    return {"id": tid, "scenes": added}


def _ingest_masked(st: Store, tid: str, masked_texts: list[str], source: str) -> int:
    """伏せ済みの文をまとめて場面にし、エージェントの巡回は最後に1回だけ行う。"""
    if not masked_texts:
        return 0
    from concurrent.futures import ThreadPoolExecutor

    from .gemini import read_scenes
    theme = st.themes[tid]
    options = st.decision_options(tid)
    info = {"name": theme.name, "vocabulary": theme.vocabulary, "questions": [q.model_dump() for q in theme.questions]}
    with ThreadPoolExecutor(max_workers=4) as ex:
        reads = list(ex.map(lambda t: read_scenes(t, info, options), masked_texts))
    n = 0
    for scenes in reads:
        for x in scenes:
            st.add_scene(tid, x, source_ref=source)
            n += 1
    if os.environ.get("UNTANGLE_AGENT", "1") == "1":
        from .agent import patrol
        patrol(st, tid, f"{source}（場面{n}件）")
    else:
        st.refresh_questions(tid)
    return n


@app.post("/api/demo/reset")
def reset():
    if _workspace.get():
        raise HTTPException(403, "自分の作業場は戻せません")
    with _lock:
        SESSIONS[_current.get()] = _fresh()
    return {"ok": True}


@app.get("/")
def dashboard():
    """トップページ＝ダッシュボード（送ったメールから判断のテーマを見つけ、状態ごとに進める）。"""
    return FileResponse(STATIC / "mail-scan.html")


@app.get("/rules")
def rules_page():
    """テーマ・条文・3つの出口（これまでの本体の画面。団体予約のデモもここ）。"""
    return FileResponse(STATIC / "index.html")


if STATIC.exists():
    app.mount("/static", StaticFiles(directory=STATIC), name="static")


def main():
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", "8080")))
