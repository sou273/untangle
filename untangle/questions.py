"""いつ聞くかを決める。判定は決まったプログラムで行い、AI は質問の言い回しと条文案だけを作る。

聞くのは4つの場面だけ:
  ① 初めての型で、理由が書いていない
  ② 同じ条件なのに判断が違う
  ③ 規程と違う判断が出た（テスト合格・確認済みの条文に対して）
  ④ 答えられない場面が溜まった
事実の確認（読み取りの外れの疑い）はスタッフへ回す。
すでに理由が分かっている型、規程どおりの場面では聞かない。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field

from .models import Article, Decider, QuestionKind, Scene, Theme
from .rules import Outcome, SceneResult, find_wavers, signature


@dataclass
class Ask:
    kind: QuestionKind
    to: Decider
    question_id: str
    scene_ids: list[str]
    signature: tuple = ()
    note: str = ""
    extra: dict = field(default_factory=dict)


def detect(theme: Theme, scenes: list[Scene], results: list[SceneResult], articles: list[Article],
           asked: set[tuple], uncovered_min: int = 3) -> list[Ask]:
    """聞くべきことの一覧。asked は既に聞いた (kind, question_id, signature) の集合。"""
    asks: list[Ask] = []
    keys = {q.id: q.key_attributes for q in theme.questions}

    def add(a: Ask) -> None:
        # 値の無い項目は鍵から外す（型の見分け方に項目が加わっても、同じことを二度聞かない）
        k = (a.kind, a.question_id, tuple(p for p in a.signature if not (isinstance(p, tuple) and p[1] is None)))
        if k not in asked:
            asked.add(k)
            asks.append(a)

    for q in theme.questions:
        mine = [s for s in scenes if s.question_id == q.id and s.counts_for_test()]

        # ② 同じ条件なのに判断が違う
        wavers = find_wavers(mine, q.key_attributes)
        for sig, decisions in wavers.items():
            same = [s for s in mine if signature(s, q.key_attributes) == sig]
            picks = []
            for d in sorted(decisions):
                picks.append(next(s for s in same if s.decision == d).id)
            add(Ask(QuestionKind.INCONSISTENT, Decider.OWNER, q.id, picks, sig,
                    extra={"counts": {d: sum(1 for s in same if s.decision == d) for d in decisions}}))

        # ④ 答えられない場面が溜まった（型ごとに uncovered_min 件以上）／① 初めての型で理由なし
        uncovered: dict[tuple, list[Scene]] = defaultdict(list)
        for r in results:
            if r.scene.question_id == q.id and r.outcome == Outcome.UNCOVERED:
                uncovered[signature(r.scene, q.key_attributes)].append(r.scene)
        for sig, ss in uncovered.items():
            if sig in wavers:  # 揺れている型は②で聞くので、ここでは聞かない（同じことを二重に聞かない）
                continue
            if len(ss) >= uncovered_min:
                add(Ask(QuestionKind.UNCOVERED, Decider.OWNER, q.id, [s.id for s in ss], sig))
            elif not any(s.reason for s in ss):
                add(Ask(QuestionKind.NEW_TYPE, Decider.OWNER, q.id, [ss[-1].id], sig))

    usable = {a.id for a in articles if a.usable}
    for r in results:
        if r.outcome != Outcome.RULE_MISS or r.article is None:
            continue
        if r.scene.decider == Decider.STAFF and r.scene.verified_attributes is None:
            # スタッフの登録が規程と違う → まず事実を確かめる（読み取りの外れかもしれない）
            add(Ask(QuestionKind.FACT, Decider.STAFF, r.scene.question_id, [r.scene.id],
                    ("fact", r.scene.id), extra={"article": r.article.id}))
        elif r.article.id in usable:
            # ③ 規程と違う判断（使える条文に対してだけ）
            add(Ask(QuestionKind.AGAINST_RULE, Decider.OWNER, r.scene.question_id, [r.scene.id],
                    signature(r.scene, keys[r.scene.question_id]), extra={"article": r.article.id}))
    return asks
