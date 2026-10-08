"""1つの店の状態と、その上の操作。保存先（メモリ / Firestore）とは切り離してある。

統治の要点: 条文を確定・変更できるのは決裁者の答え（answer）だけ。
エージェントが作れるのは質問と条文の「案」まで。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

from .models import (
    Article, ArticleStatus, Condition, Decider, Question, QuestionKind, Scene, SceneFlag, Theme,
)
from .questions import Ask, detect
from .rules import ThemeStatus, run_question_test, signature, theme_status


@dataclass
class Store:
    themes: dict[str, Theme] = field(default_factory=dict)
    articles: dict[str, Article] = field(default_factory=dict)
    scenes: dict[str, Scene] = field(default_factory=dict)
    questions: dict[str, Question] = field(default_factory=dict)
    asked: set[tuple] = field(default_factory=set)
    reviewed: dict[str, set[str]] = field(default_factory=dict)   # エージェントが見た場面（テーマごと）
    agent_runs: list[dict] = field(default_factory=list)           # エージェントの作業記録
    dictionary: list[dict] = field(default_factory=list)           # 伏せる辞書（表記→置き換え）。作業場ごとに画面から登録
    shop: str = ""                                                 # 作業場の店名
    # 送ったメールから見つけたテーマの進み具合（ダッシュボードの状態）。メールの本文は持たず、Gmail のメールIDと選んだ答えだけ
    pattern_board: dict = field(default_factory=dict)
    seq: int = 0

    def next_id(self, prefix: str) -> str:
        self.seq += 1
        return f"{prefix}{self.seq}"

    # ---- 読む ----
    def theme_articles(self, theme_id: str) -> list[Article]:
        return sorted((a for a in self.articles.values() if a.theme_id == theme_id), key=lambda a: a.number)

    def theme_scenes(self, theme_id: str) -> list[Scene]:
        return sorted((s for s in self.scenes.values() if s.theme_id == theme_id), key=lambda s: s.occurred_on)

    def theme_questions(self, theme_id: str) -> list[Question]:
        return [q for q in self.questions.values() if q.theme_id == theme_id]

    def status(self, theme_id: str) -> ThemeStatus:
        return theme_status(self.themes[theme_id], self.theme_articles(theme_id),
                            self.theme_scenes(theme_id), self.theme_questions(theme_id))

    def decision_options(self, theme_id: str) -> dict[str, list[str]]:
        """問いごとに、これまでに使われた答え（条文と場面から）。"""
        from .models import ESCALATE
        opts: dict[str, set[str]] = {q.id: set(q.answers) for q in self.themes[theme_id].questions}
        for a in self.theme_articles(theme_id):
            if a.decision != ESCALATE:
                opts[a.question_id].add(a.decision)
        for s in self.theme_scenes(theme_id):
            if s.question_id:
                opts[s.question_id].add(s.decision)
        return {k: sorted(v) for k, v in opts.items()}

    def add_scene(self, theme_id: str, read: dict, source_ref: str | None = None) -> Scene:
        """読み取った場面を加える（伏せた後の形だけ）。問いが分からなければ未分類。"""
        from .models import Source
        theme = self.themes[theme_id]
        qid = read.get("question_id")
        if qid not in {q.id for q in theme.questions}:
            qid = None
        decider = Decider.OWNER if read.get("decider") == "owner" else Decider.STAFF
        attrs = {k: v for k, v in (read.get("attributes") or {}).items() if k in theme.vocabulary}
        sc = Scene(id=self.next_id("in"), theme_id=theme_id, question_id=qid, occurred_on=date.today(),
                   situation=read.get("situation") or "", attributes=attrs, decision=read["decision"],
                   reason=read.get("reason"), source=Source.CONVERSATION, source_ref=source_ref,
                   decider=decider, decider_role="決裁者" if decider == Decider.OWNER else "スタッフ")
        self.scenes[sc.id] = sc
        return sc

    # ---- 聞き返し ----
    def refresh_questions(self, theme_id: str, wording=None) -> list[Question]:
        """聞くべきことを決まったプログラムで見つけ、質問にする。wording は文面を作る関数（AI）。"""
        theme = self.themes[theme_id]
        st = self.status(theme_id)
        asks = detect(theme, self.theme_scenes(theme_id), st.results, self.theme_articles(theme_id), self.asked)
        created = []
        for a in asks:
            q = self._question_from(theme, a, wording)
            self.questions[q.id] = q
            created.append(q)
        return created

    def _question_from(self, theme: Theme, a: Ask, wording) -> Question:
        scenes = [self.scenes[i] for i in a.scene_ids]
        draft = None
        if a.kind in (QuestionKind.UNCOVERED, QuestionKind.NEW_TYPE, QuestionKind.AGAINST_RULE):
            draft = self._draft_article(theme, a, scenes)
        text, choices = default_wording(a, scenes, draft)
        if wording:
            text, choices = wording(a, scenes, draft, text, choices)
        return Question(id=self.next_id("q"), theme_id=theme.id, kind=a.kind, to=a.to, text=text,
                        scene_ids=a.scene_ids, choices=choices, draft_article=draft)

    def _draft_article(self, theme: Theme, a: Ask, scenes: list[Scene]) -> Article:
        """型の鍵から条文案を作る（AI を使わない下書き。AI は後から言い回しを整えるだけ）。"""
        conds = [Condition(attribute=k, value=v) for k, v in a.signature if v is not None]
        decisions = [s.decision for s in scenes]
        decision = max(set(decisions), key=decisions.count)
        existing = self.theme_articles(theme.id)
        number = max((x.number for x in existing), default=0) + 1
        exception_of = a.extra.get("article") if a.kind == QuestionKind.AGAINST_RULE else None
        return Article(id=self.next_id("a"), theme_id=theme.id, question_id=a.question_id, number=number,
                       conditions=conds, decision=decision, exception_of=exception_of,
                       status=ArticleStatus.DRAFT, evidence_scene_ids=[s.id for s in scenes])

    # ---- 決裁者・スタッフの答え ----
    def answer(self, question_id: str, choice: str, reason: str | None = None,
               fact: dict | None = None, on: date | None = None) -> None:
        q = self.questions[question_id]
        if not q.open:
            raise ValueError("この質問には答え済みです")
        if choice not in q.choices:
            raise ValueError(f"選択肢にない答えです: {choice}")
        if choice == CHOICE_HIDDEN and not (reason and reason.strip()):
            raise ValueError("何が違ったかを一言で書いてください")
        q.answer, q.answered_on = choice, on or date.today()
        scenes = [self.scenes[i] for i in q.scene_ids]

        if choice == CHOICE_FREE:
            for s in scenes:
                s.flags.append(SceneFlag.FREE_JUDGMENT)
            return
        if choice == CHOICE_UNKNOWN:
            return
        if choice == CHOICE_EXCEPTION:
            for s in scenes:
                s.flags.append(SceneFlag.EXCEPTION)
            return

        match q.kind:
            case QuestionKind.INCONSISTENT:
                theme = self.themes[q.theme_id]
                tq = next(t for t in theme.questions if t.id == scenes[0].question_id)
                sig = signature(scenes[0], tq.key_attributes)
                same = [s for s in self.theme_scenes(q.theme_id)
                        if s.question_id == tq.id and s.counts_for_test() and signature(s, tq.key_attributes) == sig]
                if choice == CHOICE_HIDDEN:
                    self._split_by_hidden_condition(theme, tq, sig, same, reason)
                else:
                    right = choice.removesuffix("が正しい")
                    for s in same:
                        if s.decision != right:
                            s.flags.append(SceneFlag.REGISTRATION_ERROR)
                    drafts = [a for a in self.theme_articles(q.theme_id)
                              if a.question_id == tq.id and a.decision == right and a.status == ArticleStatus.DRAFT]
                    for art in drafts:
                        art.status, art.reason = ArticleStatus.CONFIRMED, reason or art.reason
                        art.version += 1
                    if not drafts:
                        self._confirm_new_article(theme, tq.id, [Condition(attribute=k, value=v) for k, v in sig if v is not None],
                                                  right, reason, [s.id for s in same])
            case QuestionKind.NEW_TYPE | QuestionKind.UNCOVERED | QuestionKind.AGAINST_RULE:
                if choice in (CHOICE_ACCEPT_RULE, CHOICE_ESCALATE) and q.draft_article:
                    art = q.draft_article.model_copy(deep=True)
                    if choice == CHOICE_ESCALATE:
                        from .models import ESCALATE
                        art.decision = ESCALATE
                    if art.exception_of:
                        base = self.articles[art.exception_of]
                        art.priority = base.priority  # 同じ順位＋条件が細かいので例外が勝つ
                    art.status, art.reason = ArticleStatus.CONFIRMED, reason
                    # 条番号は確定した時点で振る（同時に出た案が同じ番号にならないように）
                    art.number = max((x.number for x in self.theme_articles(q.theme_id)), default=0) + 1
                    self.articles[art.id] = art
                    for s in scenes:
                        s.reason = s.reason or reason
            case QuestionKind.FACT:
                for s in scenes:
                    s.verified_attributes = {**s.attributes, **(fact or {})}
                    s.attributes = dict(s.verified_attributes)  # 読み取りを直す
        self.promote(q.theme_id)

    def _confirm_new_article(self, theme: Theme, question_id: str, conds: list[Condition], decision: str,
                             reason: str | None, evidence: list[str], exception_of: str | None = None) -> Article:
        number = max((x.number for x in self.theme_articles(theme.id)), default=0) + 1
        art = Article(id=self.next_id("a"), theme_id=theme.id, question_id=question_id, number=number,
                      conditions=conds, decision=decision, reason=reason, exception_of=exception_of,
                      status=ArticleStatus.CONFIRMED, evidence_scene_ids=evidence)
        self.articles[art.id] = art
        return art

    def _split_by_hidden_condition(self, theme: Theme, tq, sig: tuple, same: list[Scene], reason: str | None) -> str | None:
        """「条件が違った」と答えられたとき、記録にある項目のうち判断をきれいに分けるものを探す。

        見つかれば、その項目を型の見分け方に加え、値ごとに条文を作る（決裁者が両方とも正しいと言ったので確認済み）。
        見つからなければ、理由だけを場面に残す。
        """
        for attr in theme.vocabulary:
            if attr in tq.key_attributes:
                continue
            groups: dict = {}
            for s in same:
                v = s.attributes.get(attr)
                if v is None:
                    break
                groups.setdefault(v, set()).add(s.decision)
            else:
                if len(groups) >= 2 and all(len(d) == 1 for d in groups.values()):
                    tq.key_attributes.append(attr)
                    base = [Condition(attribute=k, value=v) for k, v in sig if v is not None]
                    for v, ds in groups.items():
                        ev = [s.id for s in same if s.attributes.get(attr) == v]
                        self._confirm_new_article(theme, tq.id, [*base, Condition(attribute=attr, value=v)],
                                                  next(iter(ds)), reason, ev)
                    for s in same:
                        s.reason = s.reason or reason
                    return attr
        for s in same:
            s.reason = s.reason or reason
        return None

    def promote(self, theme_id: str) -> None:
        """確認済みの条文を過去の場面でテストし、外れ0なら「テスト合格」にする。"""
        theme = self.themes[theme_id]
        arts = self.theme_articles(theme_id)
        scenes = self.theme_scenes(theme_id)
        for q in theme.questions:
            results = run_question_test(theme, q.id, arts, scenes)
            by_article: dict[str, list] = {}
            for r in results:
                if r.article:
                    by_article.setdefault(r.article.id, []).append(r.outcome.value)
            for art in arts:
                if art.question_id != q.id or art.status != ArticleStatus.CONFIRMED:
                    continue
                outs = by_article.get(art.id, [])
                if outs and all(o in ("match", "escalated") for o in outs):
                    art.status = ArticleStatus.PASSED

    def release(self, theme_id: str) -> int:
        """4つの確認がそろったら規程の版を出す。"""
        if not self.status(theme_id).complete:
            raise ValueError("まだ完了していません")
        t = self.themes[theme_id]
        t.released_version = (t.released_version or 0) + 1
        return t.released_version


CHOICE_ACCEPT_RULE = "この条文でよい"
CHOICE_ESCALATE = "決裁者に回すことにする"
CHOICE_EXCEPTION = "今回だけの例外"
CHOICE_FREE = "その時の判断（規程にしない）"
CHOICE_UNKNOWN = "わからない"
CHOICE_HIDDEN = "条件が違った（一言で教える）"


def describe(sig: tuple) -> str:
    return "・".join(f"{k}={v}" for k, v in sig if v is not None)


def default_wording(a: Ask, scenes: list[Scene], draft: Article | None) -> tuple[str, list[str]]:
    """AI が使えないときの文面。AI はこれを言い換えるだけで、選択肢の意味は変えない。"""
    match a.kind:
        case QuestionKind.INCONSISTENT:
            counts = a.extra.get("counts", {})
            opts = [f"{d}が正しい" for d in sorted(counts)]
            detail = "、".join(f"{d}{n}件" for d, n in sorted(counts.items()))
            return (f"同じ条件（{describe(a.signature)}）なのに判断が分かれています（{detail}）。どちらが正しいですか。",
                    [*opts, CHOICE_HIDDEN, CHOICE_FREE])
        case QuestionKind.NEW_TYPE:
            return (f"初めての場面です（{describe(a.signature)}）。「{scenes[-1].decision}」とした決め手を教えてください。",
                    [CHOICE_ACCEPT_RULE, CHOICE_EXCEPTION, CHOICE_UNKNOWN])
        case QuestionKind.UNCOVERED:
            return (f"当てはまる条文のない場面が{len(scenes)}件たまりました（{describe(a.signature)}）。"
                    f"「{draft.decision if draft else ''}」とする条文でよいですか。",
                    [CHOICE_ACCEPT_RULE, CHOICE_ESCALATE, CHOICE_FREE])
        case QuestionKind.AGAINST_RULE:
            return (f"規程と違う判断がありました（{describe(a.signature)} → {scenes[0].decision}）。"
                    "例外として条文に加えますか。",
                    [CHOICE_ACCEPT_RULE, CHOICE_EXCEPTION, CHOICE_FREE])
        case QuestionKind.FACT:
            return ("登録が規程と違います。記録に書かれていない事実があれば教えてください。",
                    ["事実を直す", CHOICE_UNKNOWN])
    raise ValueError(a.kind)
