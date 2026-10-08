"""出口の3形式。すべて条文から自動で作る（手では直さない。直すのは条文）。

載せるのはテスト合格・決裁者が確認済みの条文だけ。どの形式にも「決裁者に回す」境目を書く。
② AIへの指示 と ③ 自動化の仕様書 には、テスト一式（伏せた後の場面と正解）を同梱する。
"""
from __future__ import annotations

from .models import ESCALATE, Article, Condition, Op, Scene, Theme
from .rules import ThemeStatus, evaluate

OP_WORD = {Op.EQ: "が{v}", Op.NE: "が{v}以外", Op.IN: "が{v}のいずれか", Op.GT: "が{v}より多い",
           Op.GTE: "が{v}以上", Op.LT: "が{v}より少ない", Op.LTE: "が{v}以下", Op.CONTAINS: "に「{v}」を含む"}


def cond_text(c: Condition) -> str:
    if c.op == Op.EXISTS:
        return f"{c.attribute}{'の記載がある' if c.value in (None, True) else 'の記載がない'}"
    if c.op == Op.EQ and c.value in ("あり", "なし"):
        return f"{c.attribute}が{'ある' if c.value == 'あり' else 'ない'}"
    v = "・".join(map(str, c.value)) if isinstance(c.value, (list, tuple)) else c.value
    return c.attribute + OP_WORD[c.op].format(v=v)


def article_sentence(a: Article) -> str:
    act = "スタッフが決めずに決裁者へ回す" if a.decision == ESCALATE else f"「{a.decision}」とする"
    if not a.conditions:   # 条件の無い決まり（送ったメールのパターンから決めたもの等）
        return f"いつでも、{act}。"
    cond = "、".join(cond_text(c) for c in a.conditions)
    return f"{cond}の場合は、{act}。"


def _usable(articles: list[Article]) -> list[Article]:
    return [a for a in articles if a.usable]


def _header(theme: Theme, status: ThemeStatus) -> dict:
    return {
        "theme": theme.name,
        "version": theme.released_version,
        "checks": [{"label": c.label, "ok": c.ok, "answer": c.answer} for c in status.checks],
    }


def manual(theme: Theme, articles: list[Article], status: ThemeStatus) -> dict:
    """① スタッフ向けマニュアル。問いごとに「こういうとき → こうする（理由）」。"""
    sections = []
    escalations = []
    for q in theme.questions:
        items = []
        for a in sorted(_usable(articles), key=lambda a: a.number):
            if a.question_id != q.id:
                continue
            if a.decision == ESCALATE:
                escalations.append(article_sentence(a).replace("の場合は、スタッフが決めずに決裁者へ回す。", ""))
                continue
            items.append({"number": a.number, "text": article_sentence(a), "reason": a.reason})
        if items:
            sections.append({"title": q.title, "items": items})
    escalations.append("この規程に書いていない場面")
    return {**_header(theme, status), "sections": sections, "ask_owner": escalations}


def _test_set(theme: Theme, articles: list[Article], scenes: list[Scene]) -> list[dict]:
    usable = _usable(articles)
    rows = []
    for s in scenes:
        if not s.counts_for_test() or not s.question_id:
            continue
        ev = evaluate(usable, s.question_id, s.attributes)
        if not ev.covered:
            continue
        rows.append({"id": s.id, "question": s.question_id, "situation": s.situation,
                     "attributes": s.attributes, "expected": ev.decision,
                     "article": ev.article.number if ev.article else None})
    return rows


def ai_instruction(theme: Theme, articles: list[Article], scenes: list[Scene], status: ThemeStatus) -> dict:
    """② AIへの指示。どのAIに渡してもよい文章と、テスト一式。"""
    lines = [f"あなたは「{theme.name}」の判断を補助します。次の規程どおりに判断してください。", ""]
    for q in theme.questions:
        arts = [a for a in sorted(_usable(articles), key=lambda a: (a.priority, -a.specificity, a.number))
                if a.question_id == q.id]
        if not arts:
            continue
        lines.append(f"■ {q.title}（上にある条文ほど優先）")
        for a in arts:
            lines.append(f"[第{a.number}条] {article_sentence(a)}")
        lines.append("")
    lines += [
        "守ること:",
        "- 答えには根拠の条文番号を付けること",
        "- 規程に当てはまらない場面は推測で決めず「決裁者に回します」と答えること",
        "- 「決裁者に回す」と書かれた場面は、自分で決めないこと",
    ]
    options = {q.id: sorted({a.decision for a in _usable(articles) if a.question_id == q.id and a.decision != ESCALATE})
               for q in theme.questions}
    return {**_header(theme, status), "instruction": "\n".join(lines), "options": options,
            "tests": _test_set(theme, articles, scenes)}


def spec(theme: Theme, articles: list[Article], scenes: list[Scene], status: ThemeStatus) -> dict:
    """③ 自動化の仕様書。判断表・入力項目の定義・境目・テスト一式・任せ方の段階。"""
    table = []
    for a in sorted(_usable(articles), key=lambda a: (a.question_id, a.priority, -a.specificity, a.number)):
        table.append({"question": a.question_id, "priority": a.priority, "article": a.number,
                      "conditions": [c.model_dump() for c in a.conditions], "decision": a.decision,
                      "text": article_sentence(a)})
    return {
        **_header(theme, status),
        "decision_table": table,
        "inputs": theme.vocabulary,
        "boundary": [r["text"] for r in table if r["decision"] == ESCALATE] + ["当てはまる条文が無い場面"],
        "tests": _test_set(theme, articles, scenes),
        "rollout": ["見学（AIは何もしない）", "案を出す（人が決める）", "AIが決めて人が事後に確認する", "自動（例外だけ人へ）"],
        "promotion_rule": f"直近{theme.criteria.recent_window}件で人の修正0件なら次の段へ。境目の場面は必ず人へ回す",
    }
