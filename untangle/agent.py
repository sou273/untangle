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
