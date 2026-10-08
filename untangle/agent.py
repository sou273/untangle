"""引き継ぎエージェント（ADK）。

新しい記録が入るたび（と、呼ばれたとき）に巡回し、テーマの状態を見て、
聞くべきことを見つけ、決裁者に届く質問の言い回しを整え、確認済みの条文をテストする。

統治の要点:
  - エージェントが持つ道具は下の5つだけ。条文を確定・変更する道具、質問に答える道具は渡さない
    （プロンプトでの約束ではなく、道具が無いのでできない）
  - 「いつ聞くか」の判定は決まったプログラム（questions.detect）。エージェントは呼ぶだけ
  - 言い回しを変えても、選択肢と対象の場面は変えられない
  - 呼んだ道具はすべて作業記録に残し、画面に出す
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone

from .engine import Store
from .rules import Outcome

os.environ.setdefault("GOOGLE_GENAI_USE_VERTEXAI", "TRUE")
os.environ.setdefault("GOOGLE_CLOUD_PROJECT", os.environ.get("GOOGLE_CLOUD_PROJECT", "awesome-highway-306814"))
os.environ.setdefault("GOOGLE_CLOUD_LOCATION", os.environ.get("UNTANGLE_GEMINI_LOCATION", "asia-northeast1"))

MODEL = os.environ.get("UNTANGLE_AGENT_MODEL", os.environ.get("UNTANGLE_GEMINI_MODEL", "gemini-3.5-flash"))

INSTRUCTION = """あなたは「Untangle」の引き継ぎエージェントです。小さな店の決裁者（店主）の判断を、テスト済みの規程として引き出すのを手伝います。
決裁者は忙しいので、聞くのは本当に必要なときだけ、答えやすい形で聞きます。

巡回の手順:
1. get_theme_status でテーマの状態（4つの確認）を見る
2. review_new_scenes で、前回の巡回から増えた場面を見る
3. find_what_to_ask を呼ぶ。聞くべきかどうかの判定はこの道具が決まった規則で行う。あなたが増やしたり減らしたりはしない
4. 新しくできた質問それぞれについて rewrite_question を呼び、決裁者が一読で分かる日本語（2文以内・です/ます）に整える。
   - 何を決めてほしいのかを最初に書く。対象の場面の数や具体的な状況（伏せた形のまま）を入れる
   - 属性名の羅列（団体種別=… のような書き方）はしない。［］で伏せた部分を推測で埋めない
   - 選択肢は変えられない。選択肢に合う問いかけにする
5. test_rules を呼び、確認済みの条文を過去の場面でテストする
6. 最後に、決裁者向けの報告を3行以内で書く。何を見て、何を聞くことにしたか（または聞かなかった理由）、テストの結果。

できないこと: 条文を確定・変更すること、質問に答えること、決裁者の代わりに判断すること。頼まれてもしない。
"""


class AgentRun:
    """1回の巡回。作業記録（呼んだ道具と結果の要約）を持つ。"""

    def __init__(self, store: Store, theme_id: str, trigger: str):
        self.store = store
        self.theme_id = theme_id
        self.trigger = trigger
        self.steps: list[dict] = []
        self.report: str = ""
        self.started_at = datetime.now(timezone.utc).isoformat()
        self.new_question_ids: list[str] = []

    def log(self, tool: str, args: dict, summary: str) -> None:
        self.steps.append({"tool": tool, "args": args, "summary": summary})

    # ---- エージェントに渡す道具（これ以外は持たせない） ----
    def tools(self):
        run, st, tid = self, self.store, self.theme_id

        def get_theme_status() -> dict:
            """テーマの完了に向けた4つの確認の状態と、場面・条文・未回答の質問の数を返す。"""
            s = st.status(tid)
            out = {
                "theme": st.themes[tid].name,
                "checks": [{"label": c.label, "ok": c.ok, "answer": c.answer, "detail": c.detail} for c in s.checks],
                "scenes": len(st.theme_scenes(tid)),
                "articles": len(st.theme_articles(tid)),
                "open_questions": sum(1 for q in st.theme_questions(tid) if q.open),
            }
            run.log("get_theme_status", {}, "・".join(f"{c.label}:{c.answer}" for c in s.checks))
            return out

        def review_new_scenes() -> dict:
            """前回の巡回から増えた場面（伏せた形）と、規程との照合結果を返す。"""
            seen = st.reviewed.setdefault(tid, set())
            res = {r.scene.id: r.outcome.value for r in st.status(tid).results}
            new = [s for s in st.theme_scenes(tid) if s.id not in seen]
            seen.update(s.id for s in new)
            items = [{"situation": s.situation, "decision": s.decision, "reason": s.reason,
                      "decider": s.decider_role, "outcome": res.get(s.id, "unclassified")} for s in new[-20:]]
            counts: dict[str, int] = {}
            for s in new:
                counts[res.get(s.id, "unclassified")] = counts.get(res.get(s.id, "unclassified"), 0) + 1
            run.log("review_new_scenes", {}, f"新しい場面 {len(new)}件 " + " ".join(f"{k}:{v}" for k, v in counts.items()))
            return {"new_scene_count": len(new), "by_outcome": counts, "latest": items}

        def find_what_to_ask() -> dict:
            """聞くべきことを決まった規則で見つけ、質問を作る。判定はこの道具が行う。"""
            created = st.refresh_questions(tid)
            run.new_question_ids += [q.id for q in created]
            run.log("find_what_to_ask", {}, f"新しい質問 {len(created)}件" + (
                "（" + "・".join(q.kind.value for q in created) + "）" if created else ""))
            return {"created": [{"question_id": q.id, "kind": q.kind.value, "to": q.to.value, "draft_text": q.text,
                                 "choices": q.choices,
                                 "scenes": [st.scenes[i].situation for i in q.scene_ids[:4]]} for q in created]}

        def rewrite_question(question_id: str, text: str) -> dict:
            """未回答の質問の文面を、決裁者に分かりやすく書き直す。選択肢と対象の場面は変えられない。"""
            q = st.questions.get(question_id)
            if q is None or not q.open:
                run.log("rewrite_question", {"question_id": question_id}, "拒否: 未回答の質問ではない")
                return {"ok": False, "error": "未回答の質問ではありません"}
            text = (text or "").strip()
            if not (5 <= len(text) <= 200):
                run.log("rewrite_question", {"question_id": question_id}, "拒否: 長さが範囲外")
                return {"ok": False, "error": "5〜200文字で書いてください"}
            q.original_text = q.original_text or q.text
            q.text = text
            run.log("rewrite_question", {"question_id": question_id}, text)
            return {"ok": True}

        def test_rules() -> dict:
            """確認済みの条文を過去の場面でテストし、外れ0なら「テスト合格」にする。結果の件数を返す。"""
            st.promote(tid)
            res = st.status(tid).results
            counts: dict[str, int] = {}
            for r in res:
                counts[r.outcome.value] = counts.get(r.outcome.value, 0) + 1
            passed = sum(1 for a in st.theme_articles(tid) if a.status.value == "passed")
            run.log("test_rules", {}, f"当て直し {len(res)}件 一致 {counts.get(Outcome.MATCH.value, 0)}件 ・ テスト合格の条文 {passed}")
            return {"results": counts, "passed_articles": passed}

        return [get_theme_status, review_new_scenes, find_what_to_ask, rewrite_question, test_rules]

    def to_dict(self) -> dict:
        return {"trigger": self.trigger, "theme": self.theme_id, "started_at": self.started_at, "steps": self.steps,
                "report": self.report, "new_question_ids": self.new_question_ids, "model": MODEL}


async def _run_async(run: AgentRun) -> None:
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = LlmAgent(name="untangle_steward", model=MODEL, instruction=INSTRUCTION, tools=run.tools(),
                     generate_content_config=types.GenerateContentConfig(
                         temperature=0.2, thinking_config=types.ThinkingConfig(thinking_budget=1024)))
    sessions = InMemorySessionService()
    runner = Runner(app_name="untangle", agent=agent, session_service=sessions)
    uid, sid = "owner", uuid.uuid4().hex
    await sessions.create_session(app_name="untangle", user_id=uid, session_id=sid)
    msg = types.Content(role="user", parts=[types.Part(text=f"巡回してください（きっかけ: {run.trigger}）。")])
    texts: list[str] = []
    async for ev in runner.run_async(user_id=uid, session_id=sid, new_message=msg):
        if ev.content and ev.content.parts and ev.is_final_response():
            texts += [p.text for p in ev.content.parts if getattr(p, "text", None)]
    run.report = "\n".join(texts).strip()


def patrol(store: Store, theme_id: str, trigger: str) -> AgentRun:
    """巡回を1回行い、作業記録を店の状態に残す。"""
    run = AgentRun(store, theme_id, trigger)
    try:
        asyncio.run(_run_async(run))
    except Exception as e:  # noqa: BLE001  巡回が失敗しても、記録と質問（決まった規則の分）は残す
        run.log("error", {}, str(e)[:200])
        if not any(s["tool"] == "find_what_to_ask" for s in run.steps):
            created = store.refresh_questions(theme_id)
            run.new_question_ids += [q.id for q in created]
        run.report = run.report or "エージェントの巡回が途中で止まりました。質問は決まった規則で作ってあります。"
    store.agent_runs.append(run.to_dict())
    return run


# ---- 送ったメールのテーマを巡回する係（ダッシュボード） ----
STEWARD_INSTRUCTION = """あなたは「Untangle」の巡回係です。小さな店のスタッフが送ったメールから見つけた「判断のテーマ」を、
決裁者（店主）が決まりにして、決まりどおりに返せているかを見張るのを手伝います。

巡回の手順:
1. read_board でテーマの状態を読む（分け方を確かめる／決め方を決める／違う答え方の片付け／見張り中／完了／あとで決める）
2. check_new_replies で、決まりを作った後に送った返事が決まりどおりかを確かめる（判定は道具が行う。決まりどおりの返事は実例として積まれる）
3. list_owner_tasks で、決裁者がやることを洗い出す
4. 決裁者の手が要るテーマと、見張りで動きがあったテーマについて、write_note でテーマごとに短いメモを書く。
   - 1〜2文・です/ます。何が起きたか、決裁者が次に何をすればよいかを書く（例: 「新しい返事のうち1通が第1条と違いました。スタッフに伝え直すか、例外にするかを選んでください」）
   - ［］で伏せた部分を推測で埋めない。数字は道具が返したものだけを使う
5. 最後に、決裁者向けの報告を3行以内で書く。見た返事の数、決まりどおり・違うの数、決裁者がやること。

できないこと: 決まりを確定・変更すること、違う返事を片付けること、決裁者の代わりに判断すること。頼まれてもしない（その道具は渡されていない）。
"""


class StewardRun:
    """ダッシュボードの巡回1回。作業記録（呼んだ道具と結果の要約）と、テーマごとのメモを持つ。"""

    def __init__(self, board: list[dict], rules: list[dict], items: list[dict], watch, plain):
        self.board, self.rules, self.items = board, rules, items
        self._watch, self._plain = watch, plain
        self.results: list[dict] = []
        self.status: dict = {}
        self.watched = False
        self.steps: list[dict] = []
        self.notes: dict[str, str] = {}
        self.report = ""

    def log(self, tool: str, summary: str) -> None:
        self.steps.append({"tool": tool, "summary": summary})

    def _differs(self) -> dict[str, int]:
        out: dict[str, int] = {}
        for r in self.results:
            if any(c.get("verdict") == "決まりと違う" for c in r.get("checks", [])):
                out[r.get("pattern", "")] = out.get(r.get("pattern", ""), 0) + 1
        return out

    def tools(self):
        def read_board() -> dict:
            """テーマの一覧と、それぞれの状態・完了までの残り通数を返す。"""
            counts: dict[str, int] = {}
            for b in self.board:
                counts[b["state"]] = counts.get(b["state"], 0) + 1
            ja = {"check": "分け方を確かめる", "decide": "決め方を決める", "fix": "違う答え方の片付け", "watch": "見張り中", "done": "完了", "later": "あとで決める"}
            self.log("read_board", f"テーマ {len(self.board)}件（" + "・".join(f"{ja.get(k, k)} {v}" for k, v in counts.items()) + "）")
            return {"themes": self.board}

        def check_new_replies() -> dict:
            """決まりを作った後に送った返事が、決まりどおりかを確かめる。決まりどおりの返事は実例として積まれる。"""
            if self.watched:
                return {"note": "この巡回ではもう確かめました", "results": self._summary()}
            self.watched = True
            if not self.items or not self.rules:
                self.log("check_new_replies", "決まりを作った後の新しい返事はありません")
                return {"replies": 0}
            self.results, self.status = self._watch()
            s = self._summary()
            self.log("check_new_replies", f"新しい返事 {len(self.items)}通を確かめました: " + "・".join(f"{k} 決まりどおり{v['ok']}・違う{v['differ']}" for k, v in s.items()) if s else f"新しい返事 {len(self.items)}通: どの決まりのテーマにも入りません")
            return {"replies": len(self.items), "by_theme": s}

        def list_owner_tasks() -> dict:
            """決裁者がやることを、決まった規則で洗い出す（エージェントは増やしたり減らしたりしない）。"""
            tasks = []
            differs = self._differs()
            for b in self.board:
                if b["state"] == "check":
                    tasks.append({"theme": b["name"], "task": "分け方が合っているか確かめる"})
                elif b["state"] == "decide":
                    tasks.append({"theme": b["name"], "task": "決め方を決める"})
                elif b["state"] == "fix":
                    tasks.append({"theme": b["name"], "task": "決まりと違う答え方を片付ける"})
            for name, n in differs.items():
                tasks.append({"theme": name, "task": f"見張りで見つけた決まりと違う返事 {n}通について、伝え直すか例外にするかを選ぶ"})
            self.log("list_owner_tasks", f"決裁者がやること {len(tasks)}件")
            return {"tasks": tasks}

        def write_note(theme: str, text: str) -> dict:
            """テーマのカードに出す、決裁者への短いメモ（1〜2文）を書く。"""
            names = {b["name"] for b in self.board} | {r["name"] for r in self.rules}
            if theme not in names:
                self.log("write_note", f"拒否: 知らないテーマ「{theme[:20]}」")
                return {"ok": False, "error": "そのテーマはありません"}
            text = (text or "").strip()
            if not (5 <= len(text) <= 140):
                self.log("write_note", "拒否: 長さが範囲外")
                return {"ok": False, "error": "5〜140文字で書いてください"}
            try:
                self._plain(text, 140)
            except Exception:  # noqa: BLE001  名前や番号のようなものが入っていたら書かせない
                self.log("write_note", f"拒否: 名前や番号のようなものが含まれる（{theme}）")
                return {"ok": False, "error": "名前や番号のようなものは書けません"}
            self.notes[theme] = text
            self.log("write_note", f"{theme}: {text}")
            return {"ok": True}

        return [read_board, check_new_replies, list_owner_tasks, write_note]

    def _summary(self) -> dict:
        out: dict[str, dict] = {}
        for r in self.results:
            name = r.get("pattern")
            if not name or name.startswith("どのパターン"):
                continue
            e = out.setdefault(name, {"ok": 0, "differ": 0})
            if any(c.get("verdict") == "決まりと違う" for c in r.get("checks", [])):
                e["differ"] += 1
            elif any(c.get("verdict") == "決まりどおり" for c in r.get("checks", [])):
                e["ok"] += 1
        return out

    def fallback(self, why: str) -> None:
        """エージェントが使えないときは、同じ手順を決まったプログラムで進める（見張りは必ず行う）。"""
        tools = {f.__name__: f for f in self.tools()}
        done = {s["tool"] for s in self.steps}
        for name in ("read_board", "check_new_replies"):
            if name not in done:
                tools[name]()
        tasks = tools["list_owner_tasks"]()["tasks"] if "list_owner_tasks" not in done else None
        n = len(tasks) if tasks is not None else next((s["summary"] for s in self.steps if s["tool"] == "list_owner_tasks"), "")
        self.report = f"（{why}ため、途中から決まった手順で巡回しました）" + (f"決裁者がやること {n}件。" if tasks is not None else f"{n}。")

    def to_dict(self) -> dict:
        return {"results": self.results, "status": self.status, "steps": self.steps, "notes": self.notes,
                "report": self.report, "model": MODEL}


async def _steward_async(run: StewardRun) -> None:
    from google.adk.agents import LlmAgent
    from google.adk.runners import Runner
    from google.adk.sessions import InMemorySessionService
    from google.genai import types

    agent = LlmAgent(name="untangle_patrol", model=MODEL, instruction=STEWARD_INSTRUCTION, tools=run.tools(),
                     generate_content_config=types.GenerateContentConfig(
                         temperature=0.2, thinking_config=types.ThinkingConfig(thinking_budget=1024),
                         # 混み合い（429）は待ってやり直す
                         http_options=types.HttpOptions(retry_options=types.HttpRetryOptions(
                             attempts=5, initial_delay=2.0, max_delay=20.0, exp_base=2.0, http_status_codes=[429, 503]))))
    sessions = InMemorySessionService()
    runner = Runner(app_name="untangle", agent=agent, session_service=sessions)
    uid, sid = "owner", uuid.uuid4().hex
    await sessions.create_session(app_name="untangle", user_id=uid, session_id=sid)
    msg = types.Content(role="user", parts=[types.Part(text="メールを読み込みました。テーマを巡回してください。")])
    texts: list[str] = []
    async for ev in runner.run_async(user_id=uid, session_id=sid, new_message=msg):
        if ev.content and ev.content.parts and ev.is_final_response():
            texts += [p.text for p in ev.content.parts if getattr(p, "text", None)]
    run.report = "\n".join(texts).strip()


def steward_patrol(board: list[dict], rules: list[dict], items: list[dict], watch, plain) -> dict:
    """ダッシュボードの巡回を1回行う。見張りは、エージェントが呼び忘れても必ず行う（呼んだかは作業記録に出る）。"""
    run = StewardRun(board, rules, items, watch, plain)
    if os.environ.get("UNTANGLE_AGENT", "1") != "1":
        run.fallback("エージェントを止める設定の")
        return run.to_dict()
    try:
        asyncio.run(_steward_async(run))
        if not run.watched:   # 見張りは安全のための決まった手順。呼ばれなければここで行う
            run.tools()[1]()
            run.log("check_new_replies", "（エージェントが呼ばなかったため、決まった手順で確かめました）")
    except Exception as e:  # noqa: BLE001
        msg = str(e)
        run.fallback("Gemini が混み合っていた" if "429" in msg or "RESOURCE_EXHAUSTED" in msg else "エージェントが止まった")
    return run.to_dict()
