"""作業場の保存（Firestore）。

作業場＝推測できない長い ID で区切った、1つの店のデータ一式（テーマ・場面・条文・質問・作業記録・伏せる辞書）。
保存するのは伏せた後の場面だけ（元のやり取りは保存しない）。伏せる辞書だけは実名を含むので、
作業場の ID を知っている人しか読めない場所に置く。

1つの作業場を JSON にまとめ、900KB ずつに分けて保存する（Firestore の1文書の上限は1MB）。
"""
from __future__ import annotations

import json
import os
import secrets
from datetime import datetime, timezone

from .engine import Store
from .models import Article, Question, Scene, Theme

COLLECTION = os.environ.get("UNTANGLE_WORKSPACES", "untangle_workspaces")
CHUNK = 900_000


def new_id() -> str:
    return secrets.token_urlsafe(24)


def _tuples(x):
    """JSON で配列になった、質問の重複よけの鍵（入れ子のタプル）を元に戻す。"""
    if isinstance(x, list):
        return tuple(_tuples(v) for v in x)
    return x


def dump(st: Store) -> dict:
    return {
        "version": 1,
        "themes": {k: v.model_dump(mode="json") for k, v in st.themes.items()},
        "articles": {k: v.model_dump(mode="json") for k, v in st.articles.items()},
        "scenes": {k: v.model_dump(mode="json") for k, v in st.scenes.items()},
        "questions": {k: v.model_dump(mode="json") for k, v in st.questions.items()},
        "asked": [list(a) for a in st.asked],
        "reviewed": {k: sorted(v) for k, v in st.reviewed.items()},
        "agent_runs": st.agent_runs[-50:],
        "dictionary": st.dictionary,
        "shop": st.shop,
        "pattern_board": st.pattern_board,
        "seq": st.seq,
    }


def load(d: dict) -> Store:
    st = Store()
    st.themes = {k: Theme.model_validate(v) for k, v in d.get("themes", {}).items()}
    st.articles = {k: Article.model_validate(v) for k, v in d.get("articles", {}).items()}
    st.scenes = {k: Scene.model_validate(v) for k, v in d.get("scenes", {}).items()}
    st.questions = {k: Question.model_validate(v) for k, v in d.get("questions", {}).items()}
    st.asked = {_tuples(a) for a in d.get("asked", [])}
    st.reviewed = {k: set(v) for k, v in d.get("reviewed", {}).items()}
    st.agent_runs = d.get("agent_runs", [])
    st.dictionary = d.get("dictionary", [])
    st.shop = d.get("shop", "")
    st.pattern_board = d.get("pattern_board", {})
    st.seq = d.get("seq", 0)
    return st


def _db():
    from google.cloud import firestore
    return firestore.Client(project=os.environ.get("GOOGLE_CLOUD_PROJECT", "awesome-highway-306814"))


def save(workspace_id: str, st: Store, collection: str = COLLECTION) -> None:
    body = json.dumps(dump(st), ensure_ascii=False)
    parts = [body[i:i + CHUNK] for i in range(0, len(body), CHUNK)] or [""]
    db = _db()
    ref = db.collection(collection).document(workspace_id)
    batch = db.batch()
    for i, part in enumerate(parts):
        batch.set(ref.collection("chunks").document(f"{i:04d}"), {"data": part})
    batch.set(ref, {"chunks": len(parts), "updated_at": datetime.now(timezone.utc)}, merge=True)
    batch.commit()
    # 前回より減った分の古い断片を消す
    for doc in ref.collection("chunks").list_documents():
        if int(doc.id) >= len(parts):
            doc.delete()


def exists(workspace_id: str) -> bool:
    return _db().collection(COLLECTION).document(workspace_id).get().exists


def fetch(workspace_id: str, collection: str = COLLECTION) -> Store | None:
    ref = _db().collection(collection).document(workspace_id)
    meta = ref.get()
    if not meta.exists:
        return None
    n = meta.to_dict().get("chunks", 0)
    body = "".join(ref.collection("chunks").document(f"{i:04d}").get().to_dict()["data"] for i in range(n))
    return load(json.loads(body)) if body else Store()
