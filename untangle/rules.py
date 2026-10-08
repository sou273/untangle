"""条文を場面に当てはめ、テストし、完了の4つの確認を数える。

ここは AI を使わない。合否を数えるのは決まったプログラムだけ。
AI の役目は場面から属性を読み取るところまで。
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Iterable

from .models import (
    ESCALATE,
    Article,
    ArticleStatus,
    Condition,
    Decider,
    Op,
    Question,
    Scene,
    Theme,
)


def _match(cond: Condition, attrs: dict[str, Any]) -> bool:
    present = cond.attribute in attrs and attrs[cond.attribute] is not None
    if cond.op == Op.EXISTS:
        return present if cond.value in (None, True) else not present
    if not present:
        return False
    v = attrs[cond.attribute]
    match cond.op:
        case Op.EQ:
            return v == cond.value
        case Op.NE:
            return v != cond.value
        case Op.IN:
            return v in cond.value
        case Op.GT:
            return v > cond.value
        case Op.GTE:
            return v >= cond.value
        case Op.LT:
            return v < cond.value
        case Op.LTE:
            return v <= cond.value
        case Op.CONTAINS:
            return cond.value in v
    raise ValueError(f"unknown op {cond.op}")


def applies(article: Article, attrs: dict[str, Any]) -> bool:
    return all(_match(c, attrs) for c in article.conditions)


@dataclass
class Evaluation:
    decision: str | None            # None なら当てはまる条文がない
    article: Article | None
    conflict: list[Article] = field(default_factory=list)  # 同順位で答えが割れた条文

    @property
    def covered(self) -> bool:
        return self.decision is not None


def _rank(a: Article) -> tuple[int, int]:
    return (a.priority, -a.specificity)


def evaluate(articles: Iterable[Article], question_id: str, attrs: dict[str, Any]) -> Evaluation:
    """問いに対する条文を当てはめる。優先順位→条件の細かさの順で1つ選ぶ。"""
    hits = [
        a for a in articles
        if a.question_id == question_id and a.status != ArticleStatus.NOT_A_RULE and applies(a, attrs)
    ]
    if not hits:
        return Evaluation(None, None)
    hits.sort(key=_rank)
    top = hits[0]
    tied = [a for a in hits if _rank(a) == _rank(top) and a.decision != top.decision]
    if tied:
        return Evaluation(None, None, conflict=[top, *tied])
    return Evaluation(top.decision, top)


class Outcome(StrEnum):
    MATCH = "match"
    UNCOVERED = "uncovered"            # 当てはまる条文がない
    ESCALATED = "escalated"            # 「決裁者に回す」に当たった（網羅に数える）
    RULE_MISS = "rule_miss"            # 規則の外れ
    EXTRACTION_MISS = "extraction_miss"  # 読み取りの外れ
    WAVER = "waver"                    # 決裁者の揺れ
    CONFLICT = "conflict"              # 条文同士の衝突


@dataclass
class SceneResult:
    scene: Scene
    outcome: Outcome
    expected: str | None
    article: Article | None


def signature(scene: Scene, key_attributes: list[str]) -> tuple:
    """型を見分ける鍵。同じ鍵なら「同じ条件」とみなす。人が確かめた属性があればそちらを使う。"""
    attrs = scene.verified_attributes if scene.verified_attributes is not None else scene.attributes
    return tuple((k, attrs.get(k)) for k in key_attributes)


def find_wavers(scenes: list[Scene], key_attributes: list[str], min_each: int = 2) -> dict[tuple, set[str]]:
    """同じ条件なのに判断が割れている型。

    どちらの判断も min_each 件以上あるときだけ揺れとみなす。1件だけの外れは揺れではなく、
    規則の外れか読み取りの外れの疑いとして扱う（スタッフへの事実確認に回る）。
    """
    by_sig: dict[tuple, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for s in scenes:
        if s.counts_for_test():
            by_sig[signature(s, key_attributes)][s.decision] += 1
    out: dict[tuple, set[str]] = {}
    for k, counts in by_sig.items():
        many = {d for d, n in counts.items() if n >= min_each}
        if len(many) > 1:
            out[k] = many
    return out


def run_question_test(theme: Theme, question_id: str, articles: list[Article], scenes: list[Scene]) -> list[SceneResult]:
    q = next(q for q in theme.questions if q.id == question_id)
    target = [s for s in scenes if s.question_id == question_id and s.counts_for_test()]
    wavers = find_wavers(target, q.key_attributes)
    results: list[SceneResult] = []
    for s in sorted(target, key=lambda s: s.occurred_on):
        ev = evaluate(articles, question_id, s.attributes)
        if ev.conflict:
            results.append(SceneResult(s, Outcome.CONFLICT, None, None))
            continue
        if not ev.covered:
            results.append(SceneResult(s, Outcome.UNCOVERED, None, None))
            continue
        if ev.decision == ESCALATE:
            results.append(SceneResult(s, Outcome.ESCALATED, ev.decision, ev.article))
            continue
        if ev.decision == s.decision:
            results.append(SceneResult(s, Outcome.MATCH, ev.decision, ev.article))
            continue
        # 外れの原因を切り分ける
        if s.verified_attributes is not None:
            ev2 = evaluate(articles, question_id, s.verified_attributes)
            if ev2.decision == s.decision:
                results.append(SceneResult(s, Outcome.EXTRACTION_MISS, ev.decision, ev.article))
                continue
        if signature(s, q.key_attributes) in wavers:
            results.append(SceneResult(s, Outcome.WAVER, ev.decision, ev.article))
            continue
        results.append(SceneResult(s, Outcome.RULE_MISS, ev.decision, ev.article))
    return results


@dataclass
class Check:
    label: str        # 画面に出す問いかけ
    ok: bool
    answer: str       # 画面に出す答え
    detail: str = ""


@dataclass
class ThemeStatus:
    checks: list[Check]
    results: list[SceneResult]

    @property
    def complete(self) -> bool:
        return all(c.ok for c in self.checks)


def theme_status(theme: Theme, articles: list[Article], scenes: list[Scene], questions: list[Question]) -> ThemeStatus:
    """完了の4つの確認を数える。"""
    crit = theme.criteria
    results: list[SceneResult] = []
    for q in theme.questions:
        results.extend(run_question_test(theme, q.id, articles, scenes))
    results.sort(key=lambda r: r.scene.occurred_on)

    total = len(results)
    uncovered = [r for r in results if r.outcome in (Outcome.UNCOVERED, Outcome.CONFLICT)]
    covered = total - len(uncovered)
    coverage = covered / total if total else 0.0
    excluded = sum(1 for s in scenes if s.theme_id == theme.id and s.question_id and not s.counts_for_test())
    c1 = Check(
        "答えられない場面は残っていないか",
        total > 0 and coverage >= crit.coverage_min and not any(r.outcome == Outcome.CONFLICT for r in results),
        f"{len(uncovered)}件残っています" if uncovered else "0件",
        f"{total}件中{covered}件に当てはまる条文がある" + (f"（例外・登録の誤りなど{excluded}件は除く）" if excluded else ""),
    )

    judged = [r for r in results if r.outcome not in (Outcome.UNCOVERED, Outcome.CONFLICT, Outcome.ESCALATED)]
    recent = judged[-crit.recent_window:]
    misses = [r for r in recent if r.outcome != Outcome.MATCH]
    kinds = defaultdict(int)
    for r in misses:
        kinds[r.outcome] += 1
    label_of = {Outcome.WAVER: "揺れ", Outcome.RULE_MISS: "規則の外れ", Outcome.EXTRACTION_MISS: "読み取りの外れ"}
    c2 = Check(
        "答えはあなたと同じか",
        len(recent) > 0 and len(misses) <= crit.max_mismatches,
        f"違う答え {len(misses)}件",
        "・".join(f"{label_of[k]}{n}" for k, n in kinds.items()) + f"（直近{len(recent)}件）" if misses else f"直近{len(recent)}件",
    )

    # 新しい型: 古い順に見て、初めて出た鍵を数える
    seen: set[tuple] = set()
    first_seen_idx: list[int] = []
    ordered = sorted((s for s in scenes if s.theme_id == theme.id and s.question_id), key=lambda s: s.occurred_on)
    keys = {q.id: q.key_attributes for q in theme.questions}
    for i, s in enumerate(ordered):
        sig = (s.question_id, signature(s, keys[s.question_id]))
        if sig not in seen:
            seen.add(sig)
            first_seen_idx.append(i)
    window_start = max(0, len(ordered) - crit.novelty_window)
    new_types = [i for i in first_seen_idx if i >= window_start]
    c3 = Check(
        "もう新しい話は出てこないか",
        len(ordered) >= crit.novelty_window and len(new_types) <= crit.max_new_types,
        "0件" if not new_types else f"最近{len(new_types)}件出ました",
        f"直近{min(crit.novelty_window, len(ordered))}件で新しい型{len(new_types)}",
    )

    open_q = [q for q in questions if q.theme_id == theme.id and q.open]
    owner_q = [q for q in open_q if q.to == Decider.OWNER]
    staff_q = len(open_q) - len(owner_q)
    c4 = Check(
        "あなたへの質問は残っていないか",
        not open_q,
        f"{len(owner_q)}件あります" if owner_q else "0件",
        f"ほかにスタッフへの確認{staff_q}件" if staff_q else "",
    )
    return ThemeStatus([c1, c2, c3, c4], results)
